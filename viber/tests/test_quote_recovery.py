"""A quote that differs only in whitespace or Unicode form is kept as the message's own text."""
import tempfile
import types
import unittest
from pathlib import Path

from test_viber_analyze import fake_msgs, finding, mod

TEXT = 'You  are wrong\nabout this'


class QuoteRecoveryTests(unittest.TestCase):
    def collect(self, text, quote):
        msgs = fake_msgs(3)
        msgs[1]['text'] = text
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / 'batches').mkdir()
            batches = mod.plan_batches(msgs, 60, 0)
            mod.write_json(out / 'batches' / 'batch-00001.json',
                           {'batch': 1, 'start': 0, 'end': 3, 'usage': {},
                            'findings': [finding('m0000001', quote)]})
            args = types.SimpleNamespace(output=out, sender='s', conversation_id='viber:1')
            findings, issues, _covered, _usage = mod.collect_findings(args, msgs, batches)
        return findings, issues

    def test_exact_quote_is_kept_and_marked_exact(self):
        findings, issues = self.collect(TEXT, 'are wrong')
        self.assertEqual(issues, [])
        self.assertEqual((findings[0]['quote'], findings[0]['quote_match']), ('are wrong', 'exact'))

    def test_whitespace_only_difference_keeps_the_source_text(self):
        findings, issues = self.collect(TEXT, 'You are wrong about this')
        self.assertEqual(issues, [])
        self.assertEqual((findings[0]['quote'], findings[0]['quote_match']), (TEXT, 'normalized'))

    def test_reworded_quote_is_still_rejected(self):
        findings, issues = self.collect(TEXT, 'You are mistaken about this')
        self.assertEqual(findings, [])
        self.assertEqual([i['reason'] for i in issues], ['Quote not verbatim in source text'])


if __name__ == '__main__':
    unittest.main()
