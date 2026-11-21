"""Synthetic tests for the findings triage script (Skype and Viber output)."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import triage_findings as triage


def finding(key, severity, category, confidence='high', source='skype', ident=None,
            time='2024-01-01T00:00:00Z', **over):
    item = {'message_key': key, 'quote': 'synthetic quote ' + key, 'category': category,
            'severity': severity, 'confidence': confidence, 'reason': 'synthetic reason ' + key,
            'caveat': 'synthetic caveat', 'quote_match': 'exact', 'time': time,
            f'{source}_id': ident or key[1:], 'source_url': 'history.html#' + key,
            'chatlens_url': f'http://example.test/#messageId={key}'}
    item.update(over)
    return item


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_run(self, name, findings):
        folder = self.root / name
        folder.mkdir()
        (folder / 'findings.json').write_text(json.dumps(findings), encoding='utf-8')
        return folder

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            triage.main([str(a) for a in argv])
        return out.getvalue()

    def rows(self, *argv):
        return list(csv.DictReader(io.StringIO(self.run_main('--format', 'csv', *argv))))

    def sample_run(self):
        return self.write_run('skype-analysis-1', [
            finding('ma', 'low', 'threat'),
            finding('mb', 'high', 'sarcasm'),
            finding('mc', 'high', 'threat'),
            finding('md', 'medium', 'belittling'),
            finding('me', 'high', 'threat', confidence='low'),
        ])

    def test_order_is_severity_then_category_then_confidence(self):
        keys = [r['message_key'] for r in self.rows(self.sample_run())]
        self.assertEqual(keys, ['mc', 'me', 'mb', 'md', 'ma'])

    def test_filters_and_limit(self):
        run = self.sample_run()
        keys = lambda *a: {r['message_key'] for r in self.rows(run, *a)}
        self.assertEqual(keys('--severity', 'high'), {'mb', 'mc', 'me'})
        self.assertEqual(keys('--severity', 'high,low'), {'ma', 'mb', 'mc', 'me'})
        self.assertEqual(keys('--category', 'threat'), {'ma', 'mc', 'me'})
        self.assertEqual(keys('--min-confidence', 'high'), {'ma', 'mb', 'mc', 'md'})
        self.assertEqual([r['message_key'] for r in self.rows(run, '--limit', '2')], ['mc', 'me'])

    def test_ties_go_by_run_order_then_time(self):
        skype = self.write_run('skype-analysis-1', [
            finding('m2', 'high', 'threat', time='2024-01-02T00:00:00Z'),
            finding('m1', 'high', 'threat', time='2024-01-01T00:00:00Z')])
        viber = self.write_run('viber-analysis-22', [
            finding('m3', 'high', 'threat', source='viber', time='2023-01-01T00:00:00Z')])
        self.assertEqual([r['message_key'] for r in self.rows(skype, viber)], ['m1', 'm2', 'm3'])
        self.assertEqual([r['message_key'] for r in self.rows(viber, skype)], ['m3', 'm1', 'm2'])

    def test_skype_and_viber_runs_are_told_apart_and_ties_follow_the_given_order(self):
        skype = self.write_run('skype-analysis-1', [finding('m1', 'high', 'threat', ident='S1')])
        viber = self.write_run('viber-analysis-22', [
            finding('m2', 'high', 'threat', source='viber', ident='V2')])
        rows = self.rows(viber, skype)
        self.assertEqual([(r['run'], r['source'], r['message_id']) for r in rows],
                         [('viber-analysis-22', 'viber', 'V2'), ('skype-analysis-1', 'skype', 'S1')])
        rows = self.rows(skype, viber)
        self.assertEqual([r['source'] for r in rows], ['skype', 'viber'])

    def test_findings_json_path_and_link_fallback(self):
        run = self.write_run('skype-analysis-1', [
            finding('m1', 'high', 'threat', chatlens_url=''),
            finding('m2', 'high', 'threat', chatlens_url='http://example.test/#x')])
        rows = {r['message_key']: r for r in self.rows(run / 'findings.json')}
        self.assertEqual(rows['m1']['link'], str(run / 'history.html#m1'))
        self.assertEqual(rows['m2']['link'], 'http://example.test/#x')

    def test_text_output_has_no_message_text_unless_asked(self):
        run = self.sample_run()
        plain = self.run_main(run)
        self.assertNotIn('synthetic quote', plain)
        self.assertNotIn('synthetic reason', plain)
        self.assertIn('5 of 5 findings', plain)
        self.assertIn('http://example.test/#messageId=mc', plain)
        self.assertNotIn('http://example.test', self.run_main(run, '--no-links'))
        shown = self.run_main(run, '--quotes', '--reasons')
        self.assertIn('synthetic quote mc', shown)
        self.assertIn('synthetic reason mc', shown)
        self.assertEqual(self.run_main(run, '--summary-only').count('Reading order'), 0)

    def test_message_text_is_made_safe_for_the_terminal(self):
        run = self.write_run('skype-analysis-1', [finding(
            'm1', 'high', 'threat', quote='bad\x1b[31m red‮ text\nnext\ud800 line')])
        shown = self.run_main(run, '--quotes')
        self.assertNotIn('\x1b', shown)
        self.assertNotIn('‮', shown)
        self.assertNotIn('\ud800', shown)
        self.assertIn('bad [31m red text next� line', shown)

    def test_long_text_is_cut(self):
        run = self.write_run('skype-analysis-1', [finding('m1', 'high', 'threat', quote='x' * 500)])
        line = [l for l in self.run_main(run, '--quotes', '--text-length', '50').splitlines()
                if l.strip().startswith('"x')][0]
        self.assertEqual(line.strip(), '"' + 'x' * 49 + '…"')

    def test_csv_has_no_message_text_and_cells_are_formula_safe(self):
        run = self.write_run('skype-analysis-1', [finding('m1', 'high', 'threat', ident='=1+1')])
        output = self.run_main('--format', 'csv', run, '--quotes', '--reasons')
        self.assertNotIn('synthetic quote', output)
        self.assertNotIn('synthetic reason', output)
        [row] = csv.DictReader(io.StringIO(output))
        self.assertEqual(row['message_id'], "'=1+1")

    def test_csv_cells_are_cleaned_of_surrogates_and_control_characters(self):
        run = self.write_run('skype-analysis-1', [
            finding('m1', 'high', 'threat', ident='a\ud800b\x1b[0m')])
        output = self.run_main('--format', 'csv', run)
        self.assertNotIn('\ud800', output)
        self.assertNotIn('\x1b', output)
        [row] = csv.DictReader(io.StringIO(output))
        self.assertEqual(row['message_id'], 'a�b [0m')

    def test_summary_table_counts(self):
        records, _runs = triage.collect([self.sample_run()])
        lines = triage.summary_lines(records)
        self.assertEqual(lines[0].split(), ['high', 'medium', 'low', 'total'])
        self.assertEqual(lines[1].split(), ['threat', '2', '0', '1', '3'])
        self.assertEqual(lines[-1].split(), ['total', '3', '1', '1', '5'])

    def test_unknown_values_are_listed_last(self):
        run = self.write_run('skype-analysis-1', [
            finding('m1', 'weird', 'threat'), finding('m2', 'low', 'new_category'),
            finding('m3', 'low', 'other')])
        self.assertEqual([r['message_key'] for r in self.rows(run)], ['m3', 'm2', 'm1'])

    def test_empty_findings(self):
        run = self.write_run('skype-analysis-1', [])
        self.assertIn('No findings match.', self.run_main(run))

    def test_bad_input_is_a_one_line_error(self):
        (self.root / 'broken').mkdir()
        (self.root / 'broken' / 'findings.json').write_text('{', encoding='utf-8')
        (self.root / 'object').mkdir()
        (self.root / 'object' / 'findings.json').write_text('{}', encoding='utf-8')
        for path, expected in ((self.root / 'missing', 'Cannot read'),
                               (self.root / 'broken', 'not valid JSON'),
                               (self.root / 'object', 'not a list of findings')):
            with self.subTest(path=path.name), self.assertRaises(SystemExit) as caught:
                self.run_main(path)
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('Traceback', str(caught.exception))

    def test_invalid_options_are_rejected(self):
        run = self.sample_run()
        for argv in (['--severity', 'urgent'], ['--category', 'rude'], ['--limit', '0'],
                     ['--text-length', '5']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.run_main(run, *argv)


if __name__ == '__main__':
    unittest.main()
