"""Offline tests for viber_analyze.py: no network, no API key, synthetic data only.

Run from the repository root:  python -m unittest discover -s viber/tests -v

The OpenAI client is replaced by FakeClient, which answers from a function the test gives it.
The export used is tests/fixtures/viber_export_sample.json (made-up people and messages);
chat 10 is direct, chat 20 a group, chat 30 a community.
"""
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'viber_analyze.py'
SAMPLE = HERE / 'fixtures' / 'viber_export_sample.json'
spec = importlib.util.spec_from_file_location('viber_analyze', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CSV_COLUMNS = ['time', 'message_key', 'viber_id', 'sender_name', 'quote', 'category', 'severity',
               'confidence', 'reason', 'caveat', 'source_url', 'chatlens_url']
CHATLENS = 'http://10.0.0.10:5173/'


# ---------------------------------------------------------------- helpers


def sample():
    return json.loads(SAMPLE.read_text(encoding='utf-8'))


def export_with(messages, contacts=None, self_ids=(1,), chat_id=7):
    """A one-chat export around the given messages."""
    if contacts is None:
        contacts = {'1': {'name': 'Me'}, '2': {'name': 'Alice'}}
    return {'format': 'viberpc-export', 'version': 1,
            'self': {'contactIds': list(self_ids), 'name': 'Me'},
            'contacts': contacts,
            'chats': [{'id': chat_id, 'title': 'Test chat', 'type': 'direct', 'memberIds': [2],
                       'messages': messages}]}


def fake_msgs(n, sender='s'):
    """n messages as build_messages makes them, all from one sender."""
    return [{'key': f'm{i:07d}', 'source_index': i, 'viber_id': str(1000 + i), 'sender': sender,
             'name': 'Name', 'time': f'2023-10-01T00:{i % 60:02d}:00.000Z', 'kind': 'text',
             'text': f'Message {i}', 'reply_to': ''} for i in range(n)]


def finding(key, quote, **over):
    item = {'message_key': key, 'quote': quote, 'category': 'other', 'severity': 'low',
            'confidence': 'high', 'reason': 'Test reason', 'caveat': ''}
    item.update(over)
    return item


def targets(payload):
    return [m for m in payload['messages'] if m['role'] == 'TARGET']


class FakeUsage:
    def model_dump(self):
        return {'input_tokens': 10, 'output_tokens': 20}


class FakeResponse:
    def __init__(self, output_text, status='completed', details=None, usage=None):
        self.output_text = output_text
        self.status = status
        self.incomplete_details = details
        self.usage = usage


class FakeClient:
    """Stands in for the OpenAI client. answer(payload) returns a list of findings, a
    FakeResponse, or raises. Records every request."""

    def __init__(self, answer, usage=None):
        self.answer = answer
        self.usage = usage
        self.payloads = []
        self.kwargs = []
        self.lock = threading.Lock()
        self.responses = self

    @property
    def calls(self):
        return len(self.payloads)

    def create(self, **kwargs):
        payload = json.loads(kwargs['input'])
        with self.lock:
            self.payloads.append(payload)
            self.kwargs.append(kwargs)
        result = self.answer(payload)
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(json.dumps({'findings': result}, ensure_ascii=False), usage=self.usage)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@contextlib.contextmanager
def quiet():
    """Silence the script's progress output and skip its retry delays."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            patch.object(mod.time, 'sleep', return_value=None):
        yield out, err


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class TempCase(unittest.TestCase):
    def setUp(self):
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.tmp = Path(holder.name)

    def args(self, **over):
        values = dict(input=self.tmp / 'export.json', output=self.tmp / 'out', sender='viber:3',
                      model='test-model', batch_size=4, context=2, workers=1, max_batches=0,
                      chatlens_url=None, source_sha256='0' * 64, conversation_id='viber:20')
        values.update(over)
        return types.SimpleNamespace(**values)


# ---------------------------------------------------------------- reading the export


class IsoTimeTests(unittest.TestCase):
    def test_epoch_milliseconds_become_utc_iso(self):
        self.assertEqual(mod.iso_time(1700000000000), '2023-11-14T22:13:20.000Z')
        self.assertEqual(mod.iso_time(1700000000123), '2023-11-14T22:13:20.123Z')
        self.assertEqual(mod.iso_time(1700000000000.0), '2023-11-14T22:13:20.000Z')
        self.assertEqual(mod.iso_time(0), '1970-01-01T00:00:00.000Z')

    def test_unusable_values_give_an_empty_time(self):
        for bad in (None, True, 'x', [], float('nan'), float('inf'), 10 ** 30):
            with self.subTest(bad=bad):
                self.assertEqual(mod.iso_time(bad), '')

    def test_iso_times_sort_like_the_instants(self):
        times = [mod.iso_time(ms) for ms in (999, 1000, 1001, 60000, 1700000000000)]
        self.assertEqual(times, sorted(times))


class ReadExportTests(TempCase):
    def write(self, content, name='export.json'):
        path = self.tmp / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        return path

    def test_reads_the_export_and_hashes_the_file(self):
        raw = SAMPLE.read_bytes()
        data, digest = mod.read_export(self.write(raw))
        self.assertEqual(data['format'], 'viberpc-export')
        self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_accepts_a_byte_order_mark(self):
        data, _ = mod.read_export(self.write(b'\xef\xbb\xbf' + SAMPLE.read_bytes()))
        self.assertEqual(len(data['chats']), 3)

    def test_rejects_what_is_not_a_viber_export(self):
        good = {'format': 'viberpc-export', 'version': 1, 'chats': []}
        cases = {
            'not json': 'not json at all',
            'not utf-8': b'\xff\xfe\x00',
            'a list': '[]',
            'other format': json.dumps({**good, 'format': 'something-else'}),
            'no chats': json.dumps({'format': 'viberpc-export', 'version': 1}),
            'chats not a list': json.dumps({**good, 'chats': {}}),
            'version 0': json.dumps({**good, 'version': 0}),
            'version 2': json.dumps({**good, 'version': 2}),
            'version as text': json.dumps({**good, 'version': '1'}),
            'version true': json.dumps({**good, 'version': True}),
            'no version': json.dumps({'format': 'viberpc-export', 'chats': []}),
        }
        for name, content in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    mod.read_export(self.write(content))

    def test_accepts_version_one(self):
        data, _ = mod.read_export(self.write(json.dumps({'format': 'viberpc-export', 'version': 1, 'chats': []})))
        self.assertEqual(data['chats'], [])
        self.assertEqual(mod.SUPPORTED_VERSION, 1)


class ChatSelectionTests(unittest.TestCase):
    def test_list_chats_is_sorted_by_message_count(self):
        data = sample()
        lines = mod.list_chats(data)
        self.assertEqual(len(lines), 3)
        self.assertIn('viber:10', lines[0])
        self.assertIn('viber:20', lines[1])
        self.assertIn('Weekend Plans', lines[1])
        self.assertIn('viber:30', lines[2])
        self.assertIn('community', lines[2])
        for line, chat in zip(lines, sorted(data['chats'], key=lambda c: -len(c['messages']))):
            self.assertTrue(line.startswith(f"{len(chat['messages']):>7}"))

    def test_find_chat_accepts_the_id_with_or_without_prefix(self):
        data = sample()
        for wanted in ('20', 'viber:20', ' 20 ', ' viber:20'):
            with self.subTest(wanted=wanted):
                self.assertEqual(mod.find_chat(data, wanted)['id'], 20)

    def test_unknown_or_missing_chat_is_an_error(self):
        data = sample()
        for wanted in ('99', 'viber:99', 'twenty', None, ''):
            with self.subTest(wanted=wanted):
                with self.assertRaises(ValueError):
                    mod.find_chat(data, wanted)

    def test_the_only_chat_needs_no_id(self):
        data = sample()
        data['chats'] = data['chats'][:1]
        self.assertEqual(mod.find_chat(data, None)['id'], 10)

    def test_duplicate_ids_are_an_error(self):
        data = sample()
        data['chats'][1]['id'] = 10
        with self.assertRaises(ValueError):
            mod.find_chat(data, '10')


class BuildMessagesTests(unittest.TestCase):
    def build(self, chat_id):
        data = sample()
        return mod.build_messages(data, mod.find_chat(data, str(chat_id)))

    def test_group_chat_keys_ids_senders_and_names(self):
        msgs = self.build(20)
        self.assertEqual([m['key'] for m in msgs], [f'm{i:07d}' for i in range(9)])
        # 209 and 208 share a timestamp: they keep the order of the export
        self.assertEqual([m['viber_id'] for m in msgs],
                         ['201', '202', '203', '204', '206', '207', '209', '208', '210'])
        self.assertEqual([m['sender'] for m in msgs],
                         ['viber:3', 'viber:self', 'viber:2', '', 'viber:3', 'viber:3', 'viber:3',
                          'viber:3', 'viber:self'])
        self.assertEqual([m['name'] for m in msgs[:4]], ['Bob Example', 'Me Synthetic', 'Alice Example', ''])
        self.assertEqual([m['source_index'] for m in msgs], list(range(9)))

    def test_group_chat_text_kind_and_time(self):
        msgs = self.build(20)
        self.assertEqual(msgs[0]['text'], 'Who is in?')
        # Text is kept exactly as exported, whitespace included
        self.assertEqual(msgs[4]['text'], '  padded text  \nline two\n')
        self.assertEqual(msgs[3]['kind'], 'call')
        self.assertEqual(msgs[3]['text'], '')
        self.assertEqual(msgs[0]['time'], mod.iso_time(1700001000000))
        self.assertTrue(msgs[0]['time'].endswith('Z'))

    def test_replies_are_kept_as_context_text(self):
        msgs = self.build(20)
        self.assertEqual(msgs[0]['reply_to'], '')
        self.assertEqual(msgs[1]['reply_to'], 'Who is in?')
        self.assertEqual(msgs[2]['reply_to'], 'Earlier message not exported')

    def test_only_text_like_kinds_and_captions_have_text(self):
        by_id = {m['viber_id']: m for m in self.build(10)}
        self.assertEqual(by_id['101']['text'], 'Hello there\nsecond line')
        self.assertEqual(by_id['104']['text'], 'Look at this')      # a photo's caption
        self.assertEqual(by_id['109']['text'], 'https://example.com/a')   # a link
        for empty in ('105', '106', '107', '108', '110', '111', '113', '115', '117'):
            with self.subTest(viber_id=empty):
                self.assertEqual(by_id[empty]['text'], '')

    def test_direct_chat_senders(self):
        by_id = {m['viber_id']: m for m in self.build(10)}
        self.assertEqual(by_id['101']['sender'], 'viber:2')
        self.assertEqual(by_id['102']['sender'], 'viber:self')
        self.assertEqual(by_id['105']['sender'], 'viber:self')
        self.assertEqual(by_id['113']['sender'], '')     # a call belongs to nobody
        self.assertEqual(by_id['113']['name'], '')

    def test_community_post_has_text(self):
        by_id = {m['viber_id']: m for m in self.build(30)}
        self.assertEqual(by_id['301']['text'], 'Promo text')
        self.assertEqual(by_id['301']['sender'], 'viber:2')

    def test_caption_is_added_to_the_text_of_a_link(self):
        data = export_with([{'id': 1, 'timestamp': 1000, 'authorId': 2, 'outgoing': False,
                             'kind': 'link', 'text': 'https://x.example', 'caption': 'look'}])
        [msg] = mod.build_messages(data, data['chats'][0])
        self.assertEqual(msg['text'], 'https://x.example\nlook')

    def test_blank_text_counts_as_no_text(self):
        data = export_with([{'id': 1, 'timestamp': 1000, 'authorId': 2, 'kind': 'text', 'text': ' \n '}])
        [msg] = mod.build_messages(data, data['chats'][0])
        self.assertEqual(msg['text'], '')

    def test_an_author_among_the_self_ids_is_you_even_without_outgoing(self):
        data = export_with([{'id': 1, 'timestamp': 1000, 'authorId': 1, 'kind': 'text', 'text': 'a'},
                            {'id': 2, 'timestamp': 2000, 'authorId': 2, 'outgoing': True,
                             'kind': 'text', 'text': 'b'}])
        msgs = mod.build_messages(data, data['chats'][0])
        self.assertEqual([m['sender'] for m in msgs], ['viber:self', 'viber:self'])
        self.assertEqual(msgs[0]['name'], 'Me')

    def test_name_falls_back_to_the_number_and_then_the_id(self):
        data = export_with(
            [{'id': i, 'timestamp': i * 1000, 'authorId': a, 'outgoing': False, 'kind': 'text', 'text': 't'}
             for i, a in ((1, 2), (2, 3), (3, 9))],
            contacts={'2': {'name': 'Alice'}, '3': {'number': '+10000000003'}})
        names = [m['name'] for m in mod.build_messages(data, data['chats'][0])]
        self.assertEqual(names, ['Alice', '+10000000003', 'viber:9'])

    def test_a_message_without_an_author_belongs_to_nobody(self):
        data = export_with([{'id': 1, 'timestamp': 1000, 'kind': 'text', 'text': 'a'}])
        [msg] = mod.build_messages(data, data['chats'][0])
        self.assertEqual((msg['sender'], msg['name']), ('', ''))

    def test_keys_follow_the_export_even_when_messages_are_skipped_or_unsorted(self):
        data = export_with([
            {'id': 1, 'timestamp': 5000, 'authorId': 2, 'kind': 'text', 'text': 'late'},
            'not a message',
            {'id': 3, 'timestamp': 1000, 'authorId': 2, 'kind': 'text', 'text': 'early'},
            {'id': 4, 'authorId': 2, 'kind': 'text', 'text': 'no time'},
        ])
        msgs = mod.build_messages(data, data['chats'][0])
        # No time sorts first, then by time; the key is the position in the export
        self.assertEqual([(m['key'], m['text']) for m in msgs],
                         [('m0000003', 'no time'), ('m0000002', 'early'), ('m0000000', 'late')])
        self.assertEqual(msgs[0]['time'], '')


# ---------------------------------------------------------------- batching and the request


class PlanBatchesTests(unittest.TestCase):
    def test_every_message_is_scored_in_exactly_one_window(self):
        for n, size, context in [(10, 3, 2), (9, 3, 0), (1, 60, 5), (7, 10, 4), (120, 60, 5)]:
            with self.subTest(n=n, size=size, context=context):
                batches = mod.plan_batches(list(range(n)), size, context)
                scored = [i for start, _left, _right, end in batches for i in range(start, end)]
                self.assertEqual(scored, list(range(n)))
                for start, left, right, end in batches:
                    self.assertEqual(left, max(0, start - context))
                    self.assertEqual(right, min(n, start + size + context))
                    self.assertEqual(end, min(n, start + size))

    def test_no_messages_no_batches(self):
        self.assertEqual(mod.plan_batches([], 5, 1), [])


class PayloadTests(unittest.TestCase):
    def test_roles_fields_and_reply(self):
        data = sample()
        msgs = mod.build_messages(data, mod.find_chat(data, '20'))
        parsed = json.loads(mod.payload(msgs, 2, 0, 6, 4, 'viber:3'))
        self.assertEqual(parsed['analyze_only_sender_id'], 'viber:3')
        self.assertEqual([m['role'] for m in parsed['messages']],
                         ['CONTEXT', 'CONTEXT', 'TARGET', 'TARGET', 'CONTEXT', 'CONTEXT'])
        self.assertEqual([m['key'] for m in parsed['messages']], [f'm{i:07d}' for i in range(6)])
        first, second = parsed['messages'][0], parsed['messages'][1]
        self.assertEqual(set(first), {'role', 'key', 'time', 'sender_id', 'sender_name', 'kind', 'text'})
        self.assertEqual(second['reply_to'], 'Who is in?')
        # Nothing that identifies a message outside this analysis is sent
        for m in parsed['messages']:
            self.assertNotIn('viber_id', m)

    def test_long_text_is_cut_and_greek_is_not_escaped(self):
        msgs = fake_msgs(2)
        msgs[0]['text'] = 'x' * 40000
        msgs[0]['reply_to'] = 'y' * 5000
        msgs[1]['text'] = 'Ελληνικά'
        raw = mod.payload(msgs, 0, 0, 2, 2, 's')
        parsed = json.loads(raw)
        self.assertEqual(len(parsed['messages'][0]['text']), 30000)
        self.assertEqual(len(parsed['messages'][0]['reply_to']), 2000)
        self.assertIn('Ελληνικά', raw)


class FingerprintTests(TempCase):
    def test_what_changes_the_results_changes_the_fingerprint(self):
        base = mod.fingerprint(self.args())
        self.assertEqual(set(base), {'source_sha256', 'conversation_id', 'sender', 'model', 'batch_size',
                                     'context', 'prompt_version'})
        self.assertEqual(base['prompt_version'], mod.PROMPT_VERSION)
        for field, value in [('source_sha256', '1' * 64), ('conversation_id', 'viber:10'),
                             ('sender', 'viber:2'), ('model', 'other'), ('batch_size', 5), ('context', 3)]:
            with self.subTest(field=field):
                self.assertNotEqual(mod.fingerprint(self.args(**{field: value})), base)

    def test_what_does_not_change_the_results_keeps_it(self):
        base = mod.fingerprint(self.args())
        changed = self.args(workers=8, max_batches=2, chatlens_url=CHATLENS, output=self.tmp / 'x')
        self.assertEqual(mod.fingerprint(changed), base)


class HasTargetTests(unittest.TestCase):
    def test_needs_the_sender_and_some_text(self):
        msgs = fake_msgs(4)
        msgs[1]['sender'] = 'other'
        msgs[2]['text'] = ''
        self.assertTrue(mod.has_target(msgs, 's', 0, 1))
        self.assertFalse(mod.has_target(msgs, 's', 1, 2))   # someone else
        self.assertFalse(mod.has_target(msgs, 's', 2, 3))   # no text
        self.assertTrue(mod.has_target(msgs, 's', 1, 4))


class RequestBatchTests(unittest.TestCase):
    def setUp(self):
        self.msgs = fake_msgs(70)
        self.args = types.SimpleNamespace(model='test-model', sender='s', context=5)

    @staticmethod
    def findings_for(payload):
        return [finding(m['key'], m['text'][:9]) for m in targets(payload)]

    def request(self, client, batch=(1, 0, 0, 65, 60), msgs=None):
        with quiet():
            return mod.request_batch(client, self.args, msgs or self.msgs, batch)

    def test_sends_a_strict_schema_and_asks_not_to_store(self):
        client = FakeClient(self.findings_for)
        self.request(client)
        kwargs = client.kwargs[0]
        self.assertEqual(kwargs['model'], 'test-model')
        self.assertEqual(kwargs['instructions'], mod.INSTRUCTIONS)
        self.assertIs(kwargs['store'], False)
        fmt = kwargs['text']['format']
        self.assertEqual((fmt['type'], fmt['name'], fmt['strict']), ('json_schema', 'viber_findings', True))
        self.assertEqual(fmt['schema'], mod.SCHEMA)
        self.assertEqual(kwargs['max_output_tokens'], 6000)
        self.assertEqual(set(mod.SCHEMA['properties']['findings']['items']['required']),
                         set(mod.SCHEMA['properties']['findings']['items']['properties']))

    def test_a_good_answer_is_one_call(self):
        client = FakeClient(self.findings_for, usage=FakeUsage())
        record = self.request(client)
        self.assertEqual(client.calls, 1)
        self.assertEqual((record['batch'], record['start'], record['end'], record['segments']), (1, 0, 60, 1))
        self.assertEqual(len(record['findings']), 60)
        self.assertEqual(record['usage'], {'input_tokens': 10, 'output_tokens': 20})

    def bad_when_large(self, bad):
        def answer(payload):
            return bad if len(targets(payload)) > 30 else self.findings_for(payload)
        return answer

    def check_split(self, bad):
        client = FakeClient(self.bad_when_large(bad), usage=FakeUsage())
        record = self.request(client)
        # Three attempts at the whole window (with a growing output limit), then two halves
        self.assertEqual([len(targets(p)) for p in client.payloads], [60, 60, 60, 30, 30])
        self.assertEqual([k['max_output_tokens'] for k in client.kwargs], [6000, 12000, 18000, 6000, 6000])
        self.assertEqual((record['batch'], record['start'], record['end'], record['segments']), (1, 0, 60, 2))
        self.assertEqual(len(record['findings']), 60)
        self.assertEqual(len({f['message_key'] for f in record['findings']}), 60)
        self.assertEqual(record['usage'], {'input_tokens': 20, 'output_tokens': 40})

    def test_truncated_json_is_retried_and_split(self):
        self.check_split(FakeResponse('{"findings":[{"bad":"trunc', usage=FakeUsage()))

    def test_an_incomplete_response_is_retried_and_split(self):
        self.check_split(FakeResponse('', 'incomplete', {'reason': 'max_output_tokens'}, FakeUsage()))

    def test_an_empty_answer_is_retried_and_split(self):
        self.check_split(FakeResponse('', usage=FakeUsage()))

    def test_a_wrongly_shaped_answer_is_retried_and_split(self):
        for text in ('[]', '{"findings": 3}', '{"findings": ["x"]}', '{"other": []}'):
            with self.subTest(text=text):
                self.check_split(FakeResponse(text, usage=FakeUsage()))

    def test_the_split_keeps_context_around_each_half(self):
        client = FakeClient(self.bad_when_large(FakeResponse('{')))
        record = self.request(client, batch=(2, 5, 0, 70, 65))
        self.assertEqual({f['message_key'] for f in record['findings']}, {f'm{i:07d}' for i in range(5, 65)})
        halves = client.payloads[-2:]
        self.assertEqual([len(targets(p)) for p in halves], [30, 30])
        self.assertEqual([len(p['messages']) for p in halves], [40, 40])

    def test_a_half_without_the_sender_is_not_sent(self):
        msgs = fake_msgs(70)
        for m in msgs[30:]:
            m['sender'] = 'other'
        client = FakeClient(self.bad_when_large(FakeResponse('{')))
        record = self.request(client, msgs=msgs)
        self.assertEqual(client.calls, 4)    # three failures, then the half that has the sender
        self.assertEqual(record['segments'], 1)

    def test_a_single_message_that_stays_invalid_is_never_marked_done(self):
        client = FakeClient(lambda payload: FakeResponse('{'))
        with self.assertRaises(mod.BatchFormatError):
            self.request(client, batch=(1, 0, 0, 1, 1))
        self.assertEqual(client.calls, 3)

    def test_other_errors_are_retried_and_then_raised(self):
        def boom(payload):
            raise RuntimeError('simulated outage')
        client = FakeClient(boom)
        with self.assertRaises(RuntimeError):
            self.request(client)
        self.assertEqual(client.calls, 3)

    def test_a_transient_error_is_survived(self):
        state = {'failed': False}

        def answer(payload):
            if not state['failed']:
                state['failed'] = True
                raise RuntimeError('rate limited')
            return self.findings_for(payload)
        client = FakeClient(answer)
        record = self.request(client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(record['findings']), 60)


# ---------------------------------------------------------------- the whole analysis


def flag_only_copy(payload):
    """Flags "Only copy"; also returns three findings that must fail verification."""
    found = []
    for m in targets(payload):
        if 'Only copy' in m['text']:
            found.append(finding(m['key'], 'Only copy', category='direct_insult', severity='high',
                                 reason='<script>alert(1)</script>', caveat='caveat'))
    if targets(payload) and targets(payload)[0]['key'] == 'm0000000':
        found.append(finding('m0000000', 'a quote that is not in the message'))
        found.append(finding('m0000001', 'I am'))            # a TARGET, but written by someone else
        found.append(finding('m0000004', 'padded text'))     # only CONTEXT in this batch
    return found


class AnalyzeTests(TempCase):
    def setUp(self):
        super().setUp()
        data = sample()
        self.msgs = mod.build_messages(data, mod.find_chat(data, '20'))

    def run_analyze(self, args=None, answer=flag_only_copy, **client_options):
        args = args or self.args()
        client = FakeClient(answer, **client_options)
        with quiet():
            mod.analyze(args, self.msgs, client=client)
        return client

    def test_findings_are_verified_and_everything_is_written(self):
        client = self.run_analyze(usage=FakeUsage())
        out = self.tmp / 'out'
        self.assertEqual(client.calls, 2)     # batch 3 has no text from the sender: never sent
        for name in ('run_config.json', 'findings.json', 'findings.csv', 'rejected_findings.json',
                     'summary.json', 'report.html', 'report.md', 'history.html'):
            self.assertTrue((out / name).is_file(), name)
        self.assertEqual(sorted(p.name for p in (out / 'batches').iterdir()),
                         ['batch-00001.json', 'batch-00002.json', 'batch-00003.json'])
        self.assertTrue(read_json(out / 'batches' / 'batch-00003.json')['skipped_no_target'])

        [item] = read_json(out / 'findings.json')
        self.assertEqual((item['message_key'], item['viber_id'], item['source_index']), ('m0000005', '207', 5))
        self.assertEqual((item['sender_id'], item['sender_name']), ('viber:3', 'Bob Example'))
        self.assertEqual(item['conversation_id'], 'viber:20')
        self.assertEqual((item['category'], item['severity'], item['quote']), ('direct_insult', 'high', 'Only copy'))
        self.assertEqual(item['source_url'], 'history.html#m0000005')
        self.assertEqual(item['chatlens_url'], '')
        self.assertEqual([c['key'] for c in item['context']], [f'm{i:07d}' for i in range(3, 8)])
        self.assertEqual(item['context'][0]['kind'], 'call')

        reasons = sorted(r['reason'] for r in read_json(out / 'rejected_findings.json'))
        self.assertEqual(reasons, ['Outside TARGET or wrong sender', 'Outside TARGET or wrong sender',
                                   'Quote not verbatim in source text'])

        summary = read_json(out / 'summary.json')
        self.assertEqual(summary, {
            'conversation_id': 'viber:20', 'total_messages': 9, 'covered_messages': 9,
            'total_sender_messages': 5, 'covered_sender_messages': 5, 'verified_findings': 1,
            'rejected_model_findings': 3, 'complete': True,
            'usage': {'input_tokens': 20, 'output_tokens': 40}})
        self.assertEqual(read_json(out / 'run_config.json'), mod.fingerprint(self.args()))

    def test_csv_has_the_documented_columns_and_a_byte_order_mark(self):
        self.run_analyze()
        path = self.tmp / 'out' / 'findings.csv'
        self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))
        with path.open(encoding='utf-8-sig', newline='') as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
        self.assertEqual(reader.fieldnames, CSV_COLUMNS)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['viber_id'], rows[0]['quote'], rows[0]['chatlens_url']), ('207', 'Only copy', ''))

    def test_reports_escape_text_and_mark_empty_messages(self):
        self.run_analyze()
        out = self.tmp / 'out'
        page = (out / 'report.html').read_text(encoding='utf-8')
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertIn('Message ID:</b> 207', page)
        self.assertIn('history.html#m0000005', page)
        self.assertNotIn('chatlens-link', page)      # no --chatlens-url: no ChatLens link
        # The call in the context has no text: it shows the same marker as the Skype reports
        self.assertIn('[Κενό κείμενο / call]', page)
        self.assertIn('> [Κενό κείμενο / call]', (out / 'report.md').read_text(encoding='utf-8'))
        history = (out / 'history.html').read_text(encoding='utf-8')
        self.assertIn('<article id="m0000005" class="message flagged">', history)
        self.assertIn('<article id="m0000000" class="message">', history)
        self.assertIn('↩ Who is in?', history)           # the reply shows what it answers
        self.assertIn('Κενό κείμενο / call', history)
        self.assertIn('Viber ID: 207', history)

    def test_chatlens_links_use_identifiers_only(self):
        self.run_analyze(self.args(chatlens_url=CHATLENS))
        out = self.tmp / 'out'
        expected = 'http://10.0.0.10:5173/#conversationId=viber%3A20&messageId=207'
        [item] = read_json(out / 'findings.json')
        self.assertEqual(item['chatlens_url'], expected)
        self.assertNotIn('Only', item['chatlens_url'])
        with (out / 'findings.csv').open(encoding='utf-8-sig', newline='') as handle:
            self.assertEqual(next(csv.DictReader(handle))['chatlens_url'], expected)
        page = (out / 'report.html').read_text(encoding='utf-8')
        self.assertIn(expected.replace('&', '&amp;'), page)
        self.assertIn('target="chatlens"', page)
        self.assertNotIn('target="_blank"', page)
        self.assertNotIn('noopener', page)       # either would open a new tab per click
        self.assertNotIn('noreferrer', page)
        self.assertIn(f'({expected})', (out / 'report.md').read_text(encoding='utf-8'))

    def test_a_second_run_uses_the_cache(self):
        self.run_analyze()
        before = read_json(self.tmp / 'out' / 'findings.json')
        client = self.run_analyze(self.args(workers=3, max_batches=1))    # neither changes the fingerprint
        self.assertEqual(client.calls, 0)
        self.assertEqual(read_json(self.tmp / 'out' / 'findings.json'), before)

    def test_a_changed_setting_is_refused(self):
        self.run_analyze()
        for field, value in [('model', 'other'), ('sender', 'viber:2'), ('batch_size', 5),
                             ('context', 1), ('source_sha256', '1' * 64), ('conversation_id', 'viber:10')]:
            with self.subTest(field=field):
                with self.assertRaises(SystemExit) as caught:
                    self.run_analyze(self.args(**{field: value}))
                self.assertIn('NEW --output', str(caught.exception.code))

    def test_max_batches_limits_the_new_batches_and_a_rerun_finishes(self):
        first = self.run_analyze(self.args(max_batches=1))
        self.assertEqual(first.calls, 1)
        partial = read_json(self.tmp / 'out' / 'summary.json')
        self.assertEqual((partial['covered_messages'], partial['complete']), (5, False))
        second = self.run_analyze()
        self.assertEqual(second.calls, 1)      # only the batch that was left
        done = read_json(self.tmp / 'out' / 'summary.json')
        self.assertEqual((done['covered_messages'], done['complete']), (9, True))

    def test_no_openai_package_is_needed_when_a_client_is_given(self):
        with patch.dict(sys.modules, {'openai': None}):      # `from openai import ...` now fails
            self.run_analyze()

    def test_the_reports_can_be_rebuilt_without_the_cache_changing(self):
        self.run_analyze()
        args = self.args(chatlens_url=CHATLENS)
        batches = mod.plan_batches(self.msgs, args.batch_size, args.context)
        with quiet():
            mod.make_report(args, self.msgs, batches)
        [item] = read_json(self.tmp / 'out' / 'findings.json')
        self.assertTrue(item['chatlens_url'].startswith(CHATLENS))
        with quiet():
            mod.make_report(self.args(), self.msgs, batches)     # no stale link
        [item] = read_json(self.tmp / 'out' / 'findings.json')
        self.assertEqual(item['chatlens_url'], '')


class WorkersAndFailureTests(TempCase):
    def setUp(self):
        super().setUp()
        self.msgs = fake_msgs(80)

    def args80(self, name, **over):
        return self.args(sender='s', conversation_id='viber:7', batch_size=10, context=2,
                         output=self.tmp / name, **over)

    @staticmethod
    def flag_sevens(payload):
        return [finding(m['key'], m['text']) for m in targets(payload) if m['text'].endswith('7')]

    def analyze(self, args, answer):
        client = FakeClient(answer)
        with quiet():
            mod.analyze(args, self.msgs, client=client)
        return client

    def test_parallel_workers_give_the_same_findings(self):
        self.analyze(self.args80('one', workers=1), self.flag_sevens)
        client = self.analyze(self.args80('three', workers=3), self.flag_sevens)
        self.assertEqual(client.calls, 8)
        one = [f['message_key'] for f in read_json(self.tmp / 'one' / 'findings.json')]
        three = [f['message_key'] for f in read_json(self.tmp / 'three' / 'findings.json')]
        self.assertEqual(len(one), 8)
        self.assertEqual(one, three)

    def test_a_failed_batch_leaves_the_others_cached_and_exits_with_2(self):
        def fail_second(payload):
            if targets(payload)[0]['key'] == 'm0000010':
                raise RuntimeError('simulated outage')
            return []
        args = self.args80('out')
        with self.assertRaises(SystemExit) as caught:
            self.analyze(args, fail_second)
        self.assertEqual(caught.exception.code, 2)
        batches = self.tmp / 'out' / 'batches'
        self.assertTrue((batches / 'batch-00001.json').exists())
        self.assertFalse((batches / 'batch-00002.json').exists())     # never recorded as "no findings"
        self.assertFalse(read_json(self.tmp / 'out' / 'summary.json')['complete'])
        # Re-running the same command retries only what is missing
        client = self.analyze(args, lambda payload: [])
        self.assertEqual(client.calls, 7)
        self.assertTrue(read_json(self.tmp / 'out' / 'summary.json')['complete'])


# ---------------------------------------------------------------- the OpenAI client


class OpenClientTests(unittest.TestCase):
    def test_without_the_package_it_says_how_to_install_it(self):
        with patch.dict(sys.modules, {'openai': None}):
            with self.assertRaises(SystemExit) as caught:
                mod.open_client()
        self.assertIn('pip install openai', str(caught.exception.code))

    def test_without_a_key_it_asks_for_one(self):
        with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=object)}), \
                patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as caught:
                mod.open_client()
        self.assertIn('OPENAI_API_KEY', str(caught.exception.code))

    def test_with_a_key_it_creates_a_client_with_timeout_and_retries(self):
        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
        with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=FakeOpenAI)}), \
                patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy'}):
            client = mod.open_client()
        self.assertEqual(client.kwargs, {'timeout': 120, 'max_retries': 2})


# ---------------------------------------------------------------- the command line


class MainTests(TempCase):
    def setUp(self):
        super().setUp()
        self.export = self.tmp / 'viber_export.json'
        self.export.write_bytes(SAMPLE.read_bytes())

    def argv(self, *extra, export=None):
        return ['--input', str(export or self.export), *extra]

    def run_main(self, *extra, client=None, export=None):
        """Run main(); the API client, if one is needed, is the fake one."""
        def no_api():
            raise AssertionError('the API client must not be created')
        opener = patch.object(mod, 'open_client', return_value=client) if client else \
            patch.object(mod, 'open_client', side_effect=no_api)
        with opener, quiet() as (out, err):
            mod.main(self.argv(*extra, export=export))
        return out.getvalue(), err.getvalue()

    def fails(self, *extra, export=None):
        with quiet() as (out, err):
            with self.assertRaises(SystemExit) as caught:
                mod.main(self.argv(*extra, export=export))
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_list_chats_needs_no_client(self):
        out, _ = self.run_main('--list-chats')
        for expected in ('viber:10', 'viber:20', 'viber:30', 'Weekend Plans', 'Synthetic Community'):
            self.assertIn(expected, out)

    def test_list_senders(self):
        out, _ = self.run_main('--chat', '20', '--list-senders')
        self.assertIn("5  'viber:3'", out)
        self.assertIn('Bob Example', out)
        self.assertIn("2  'viber:self'", out)
        self.assertIn('Me Synthetic', out)
        self.assertIn("1  'viber:2'", out)
        self.assertIn("1  ''", out)       # the call

    def test_a_single_chat_export_needs_no_chat_option(self):
        data = sample()
        data['chats'] = [data['chats'][1]]
        single = self.tmp / 'single.json'
        single.write_text(json.dumps(data), encoding='utf-8')
        out, _ = self.run_main('--list-senders', export=single)
        self.assertIn("'viber:3'", out)

    def test_argument_errors(self):
        cases = {
            'sender is required': (['--chat', '20'], 'sender'),
            'sender has no messages': (['--chat', '20', '--sender', 'viber:99'], 'sender'),
            'chat is required for several chats': (['--sender', 'viber:3'], 'chat'),
            'unknown chat': (['--chat', '99', '--sender', 'viber:3'], 'chat'),
            'workers': (['--chat', '20', '--sender', 'viber:3', '--workers', '0'], 'workers'),
            'too many workers': (['--chat', '20', '--sender', 'viber:3', '--workers', '17'], 'workers'),
            'max batches': (['--chat', '20', '--sender', 'viber:3', '--max-batches', '-1'], 'max-batches'),
            'batch size': (['--chat', '20', '--sender', 'viber:3', '--batch-size', '0'], 'batch-size'),
            'context': (['--chat', '20', '--sender', 'viber:3', '--context', '31'], 'context'),
            'chatlens url': (['--chat', '20', '--sender', 'viber:3', '--chatlens-url', 'localhost:5173'],
                             'base_url'),
        }
        for name, (extra, word) in cases.items():
            with self.subTest(name):
                self.assertIn(word, self.fails(*extra))

    def test_a_missing_or_broken_export_is_a_clean_error(self):
        self.fails('--list-chats', export=self.tmp / 'missing.json')
        broken = self.tmp / 'broken.json'
        broken.write_text('not json', encoding='utf-8')
        self.assertIn('not valid JSON', self.fails('--list-chats', export=broken))

    def test_end_to_end_then_report_only_adds_links_without_the_api(self):
        common = ['--chat', '20', '--sender', 'viber:3', '--output', str(self.tmp / 'result'),
                  '--model', 'test-model', '--batch-size', '4', '--context', '2']
        client = FakeClient(flag_only_copy)
        self.run_main(*common, client=client)
        self.assertEqual(client.calls, 2)
        [item] = read_json(self.tmp / 'result' / 'findings.json')
        self.assertEqual((item['viber_id'], item['chatlens_url']), ('207', ''))
        self.assertEqual(read_json(self.tmp / 'result' / 'run_config.json')['source_sha256'],
                         hashlib.sha256(SAMPLE.read_bytes()).hexdigest())

        self.run_main(*common, '--chatlens-url', CHATLENS, '--report-only')     # no client: would fail
        [item] = read_json(self.tmp / 'result' / 'findings.json')
        self.assertEqual(item['chatlens_url'], 'http://10.0.0.10:5173/#conversationId=viber%3A20&messageId=207')
        self.run_main(*common, '--report-only')
        [item] = read_json(self.tmp / 'result' / 'findings.json')
        self.assertEqual(item['chatlens_url'], '')

        # A different setting no longer matches the cache
        other = [*common[:-4], '--batch-size', '5', '--context', '2']
        self.assertIn('differs', self.fails(*other, '--report-only'))

    def test_report_only_needs_a_previous_analysis(self):
        err = self.fails('--chat', '20', '--sender', 'viber:3', '--output', str(self.tmp / 'nothing'),
                         '--report-only')
        self.assertIn('run_config.json', err)

    def test_greek_text_survives_the_whole_pipeline(self):
        text = 'Είσαι ηλίθιος, ξέρεις;'
        data = export_with([
            {'id': 1, 'timestamp': 1700000000000, 'authorId': 1, 'outgoing': True, 'kind': 'text', 'text': 'Γεια'},
            {'id': 2, 'timestamp': 1700000060000, 'authorId': 2, 'outgoing': False, 'kind': 'text', 'text': text},
        ])
        path = self.tmp / 'greek.json'
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

        def answer(payload):
            return [finding(m['key'], 'ηλίθιος') for m in targets(payload) if 'ηλίθιος' in m['text']]
        client = FakeClient(answer)
        out = self.tmp / 'greek-out'
        self.run_main('--sender', 'viber:2', '--output', str(out), '--model', 'test-model', client=client,
                      export=path)
        self.assertTrue(any(text in m['text'] for p in client.payloads for m in p['messages']))
        [item] = read_json(out / 'findings.json')
        self.assertEqual(item['quote'], 'ηλίθιος')
        self.assertEqual(item['conversation_id'], 'viber:7')
        with (out / 'findings.csv').open(encoding='utf-8-sig', newline='') as handle:
            self.assertEqual(next(csv.DictReader(handle))['quote'], 'ηλίθιος')
        self.assertIn('ηλίθιος', (out / 'report.md').read_text(encoding='utf-8'))
        self.assertIn(text, (out / 'history.html').read_text(encoding='utf-8'))


class ShownTextTests(unittest.TestCase):
    def test_text_or_a_marker_with_the_kind(self):
        self.assertEqual(mod.shown_text({'text': 'hi', 'kind': 'text'}), 'hi')
        self.assertEqual(mod.shown_text({'text': '', 'kind': 'image'}), '[Κενό κείμενο / image]')
        self.assertEqual(mod.shown_text({'text': '', 'kind': ''}), '[Κενό κείμενο]')


if __name__ == '__main__':
    unittest.main()
