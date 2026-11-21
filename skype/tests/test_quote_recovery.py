"""A quote that differs only in whitespace or Unicode form is kept as the message's own text."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import test_parallel as helpers

mod = helpers.mod
CONTENT = 'You  are wrong\nabout this'


class QuoteRecoveryTests(unittest.TestCase):
    def report(self, quote):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'conversation.json'
            messages = [{'id': str(i), 'from': '8:live:contact', 'displayName': 'Contact',
                         'originalarrivaltime': f'2024-01-01T00:0{i}:00Z', 'content': content}
                        for i, content in enumerate(['before', CONTENT, 'after'])]
            source.write_text(json.dumps({'conversations': [{'id': '8:live:contact',
                                                             'MessageList': messages}]}),
                              encoding='utf-8')
            msgs = mod.load(source)
            output = root / 'out'
            (output / 'batches').mkdir(parents=True)
            batches = mod.plan_batches(msgs, 10, 2)
            mod.write_json(output / 'batches' / 'batch-00001.json', {
                'batch': 1, 'start': 0, 'end': 3, 'usage': {},
                'findings': [{'message_key': msgs[1]['key'], 'quote': quote, 'category': 'other',
                              'severity': 'low', 'confidence': 'high', 'reason': 'Test reason',
                              'caveat': ''}]})
            args = helpers.ParallelTests().args(source, output)
            with contextlib.redirect_stdout(io.StringIO()):
                mod.make_report(args, msgs, batches)
            read = lambda name: json.loads((output / name).read_text(encoding='utf-8'))
            return read('findings.json'), read('rejected_findings.json')

    def test_exact_quote_is_kept_and_marked_exact(self):
        findings, rejected = self.report('are wrong')
        self.assertEqual(rejected, [])
        self.assertEqual((findings[0]['quote'], findings[0]['quote_match']), ('are wrong', 'exact'))

    def test_whitespace_only_difference_keeps_the_source_text(self):
        findings, rejected = self.report('You are wrong about this')
        self.assertEqual(rejected, [])
        self.assertEqual((findings[0]['quote'], findings[0]['quote_match']), (CONTENT, 'normalized'))

    def test_reworded_quote_is_still_rejected(self):
        findings, rejected = self.report('You are mistaken about this')
        self.assertEqual(findings, [])
        self.assertEqual([r['reason'] for r in rejected], ['Quote not verbatim in source text'])


if __name__ == '__main__':
    unittest.main()
