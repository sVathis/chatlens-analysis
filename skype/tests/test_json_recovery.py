"""Offline tests: malformed or truncated JSON must never silently lose coverage."""
import importlib.util
import json
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skype_analyze.py'
spec = importlib.util.spec_from_file_location('skype_robust', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FakeResponse:
    def __init__(self, content, status='completed', details=None, usage=None):
        self.output_text = content
        self.status = status
        self.incomplete_details = details
        self.usage = usage

class FakeUsage:
    def model_dump(self):
        return {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}

class FakeClient:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        d = json.loads(kwargs['input'])
        target = [x for x in d['messages'] if x['role'] == 'TARGET']
        self.calls.append((len(target), kwargs['max_output_tokens']))
        if self.kind == 'malformed' and len(target) > 30:
            return FakeResponse('{"findings":[{"bad":"trunc', usage=FakeUsage())
        if self.kind == 'incomplete' and len(target) > 30:
            return FakeResponse('', 'incomplete', {'reason': 'max_output_tokens'}, FakeUsage())
        if self.kind == 'always_bad':
            return FakeResponse('{', usage=FakeUsage())
        return FakeResponse(json.dumps({'findings': [
            {'message_key': x['key'], 'quote': x['text'][:10], 'category': 'other',
             'severity': 'low', 'confidence': 'high', 'reason': 'Test', 'caveat': ''}
            for x in target
        ]}), usage=FakeUsage())

class RobustJsonTests(unittest.TestCase):
    def setUp(self):
        self.msgs = [
            {'key':f'm{i:07d}', 'source_index':i, 'skype_id':str(i),
             'sender':'contact', 'name':'Contact', 'time':f'2023-10-01T00:{i%60:02d}:00Z',
             'text':f'Message {i}', 'type':'RichText'}
            for i in range(70)
        ]
        self.args = types.SimpleNamespace(model='gpt-5-mini',sender='contact',context=5)

    def test_malformed_json_automatically_splits_batch(self):
        client = FakeClient('malformed')
        with patch.object(mod.time, 'sleep', return_value=None):
            record = mod.request_batch(client, self.args, self.msgs, (1, 0, 0, 65, 60))
        self.assertEqual(record['start'], 0)
        self.assertEqual(record['end'], 60)
        self.assertEqual(record['segments'], 2)
        self.assertEqual(len(record['findings']), 60)
        self.assertEqual(len({f['message_key'] for f in record['findings']}), 60)
        self.assertEqual(record['usage']['total_tokens'], 60)
        self.assertEqual([x[1] for x in client.calls[:3]], [6000, 12000, 18000])
        self.assertEqual([x[0] for x in client.calls[3:]], [30, 30])

    def test_incomplete_response_splits_and_recovers(self):
        client = FakeClient('incomplete')
        with patch.object(mod.time, 'sleep', return_value=None):
            record = mod.request_batch(client, self.args, self.msgs, (1, 0, 0, 65, 60))
        self.assertEqual(len(record['findings']), 60)
        self.assertEqual(record['segments'], 2)

    def test_single_message_invalid_response_is_not_marked_complete(self):
        client = FakeClient('always_bad')
        with patch.object(mod.time, 'sleep', return_value=None):
            with self.assertRaises(mod.BatchFormatError):
                mod.request_batch(client, self.args, self.msgs, (1, 0, 0, 1, 1))
        self.assertEqual(len(client.calls), 3)

    def test_context_is_not_duplicated_in_recovered_targets(self):
        client = FakeClient('malformed')
        with patch.object(mod.time, 'sleep', return_value=None):
            record = mod.request_batch(client, self.args, self.msgs, (2, 5, 0, 70, 65))
        self.assertEqual(len(record['findings']), 60)
        self.assertEqual({f['message_key'] for f in record['findings']},
                         {f'm{i:07d}' for i in range(5, 65)})

if __name__ == '__main__': unittest.main()
