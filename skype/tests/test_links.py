"""Offline regression tests for ChatLens backlinks and report regeneration."""
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / 'skype_analyze.py'
spec = importlib.util.spec_from_file_location('skype_analyze', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class LinkTests(unittest.TestCase):
    def test_permalinks_use_unmodified_ids(self):
        base = 'http://10.0.0.10:5173/'
        url = mod.chatlens_permalink(base, '8:live:.cid.0123456789abcdef', '1700000000001')
        self.assertEqual(url, 'http://10.0.0.10:5173/#conversationId=8%3Alive%3A.cid.0123456789abcdef&messageId=1700000000001')

    def test_subpath_and_existing_query(self):
        url = mod.chatlens_permalink(
            'https://example.org/chatlens/?theme=dark&messageId=old#something',
            '8:live:.cid.0123456789abcdef', '1700000000001')
        p = urlsplit(url)
        self.assertEqual(p.path, '/chatlens/')
        self.assertEqual(parse_qs(p.query), {'theme': ['dark']})
        self.assertEqual(parse_qs(p.fragment), {
            'conversationId': ['8:live:.cid.0123456789abcdef'],
            'messageId': ['1700000000001']})

    def test_missing_id_falls_back_to_archive(self):
        self.assertEqual(mod.chatlens_permalink('http://10.0.0.10:5173/', 'conv', ''), '')

    def test_invalid_base(self):
        with self.assertRaises(ValueError):
            mod.chatlens_permalink('file:///tmp/index.html', 'conv', '123')

    def test_report_only_regenerates_both_link_styles(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            source = d / 'conversation.json'
            output = d / 'results'
            (output / 'batches').mkdir(parents=True)
            data = {'userId': '8:self', 'conversations': [{
                'id': '8:live:.cid.0123456789abcdef',
                'MessageList': [
                    {'id': '1700000000001', 'from': '8:live:.cid.0123456789abcdef',
                     'displayName': 'Example Contact', 'originalarrivaltime': '2024-08-20T09:00:00Z',
                     'messagetype': 'RichText', 'content': 'Μια δοκιμαστική φράση'},
                    {'id': '1700000000002', 'from': '8:me.example',
                     'displayName': '8:me.example', 'originalarrivaltime': '2024-08-20T09:01:00Z',
                     'messagetype': 'RichText', 'content': 'Απάντηση'},
                ]}]}
            source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            sender = '8:live:.cid.0123456789abcdef'
            model = 'gpt-5-mini'
            mod.write_json(output / 'run_config.json', mod.fingerprint(source, sender, model, 60, 5))
            mod.write_json(output / 'batches' / 'batch-00001.json', {
                'batch': 1, 'start': 0, 'end': 2, 'findings': [{
                    'message_key': 'm0000000', 'quote': 'Μια δοκιμαστική φράση',
                    'category': 'other', 'severity': 'low', 'confidence': 'high',
                    'reason': 'Δοκιμαστική επισήμανση', 'caveat': ''
                }], 'usage': {}})
            command = [sys.executable, str(SCRIPT), '--input', str(source),
                       '--sender', sender, '--model', model, '--output', str(output),
                       '--report-only', '--chatlens-url', 'http://10.0.0.10:5173/']
            process = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            expected = mod.chatlens_permalink('http://10.0.0.10:5173/',
                                                '8:live:.cid.0123456789abcdef', '1700000000001')
            html_report = (output / 'report.html').read_text(encoding='utf-8')
            self.assertIn(expected, html_report.replace('&amp;', '&'))
            self.assertIn('target="chatlens"', html_report)
            self.assertNotIn('target="_blank"', html_report)
            self.assertNotIn('noopener', html_report)
            self.assertNotIn('noreferrer', html_report)
            self.assertIn(expected, (output / 'report.md').read_text(encoding='utf-8'))
            self.assertIn('history.html#m0000000', (output / 'report.md').read_text(encoding='utf-8'))
            self.assertIn('history.html#m0000000', html_report)
            self.assertIn('id="m0000000"', (output / 'history.html').read_text(encoding='utf-8'))
            findings = json.loads((output / 'findings.json').read_text(encoding='utf-8'))
            self.assertEqual(findings[0]['chatlens_url'], expected)
            self.assertEqual(findings[0]['source_url'], 'history.html#m0000000')
            with (output / 'findings.csv').open(encoding='utf-8-sig', newline='') as fh:
                self.assertEqual(next(csv.DictReader(fh))['chatlens_url'], expected)
            # Re-run report generation without ChatLens configuration: no stale deep-links.
            command2 = command[:]
            command2 = command2[:-2]
            process2 = subprocess.run(command2, capture_output=True, text=True)
            self.assertEqual(process2.returncode, 0, process2.stdout + process2.stderr)
            self.assertEqual(json.loads((output / 'findings.json').read_text(encoding='utf-8'))[0]['chatlens_url'], '')


    def test_special_characters_and_base_fragment(self):
        url = mod.chatlens_permalink(
            'https://example.org/chatlens/?note=a%20b&conversationId=old#messageId=old',
            '8:live:user@server', 'id+/&=# 5')
        self.assertEqual(
            url,
            'https://example.org/chatlens/?note=a%20b'
            '#conversationId=8%3Alive%3Auser%40server&messageId=id%2B%2F%26%3D%23%205'
        )
        p = urlsplit(url)
        self.assertEqual(parse_qs(p.fragment), {
            'conversationId': ['8:live:user@server'], 'messageId': ['id+/&=# 5']
        })
        self.assertEqual(parse_qs(p.query), {'note': ['a b']})

    def test_different_messages_change_only_fragment(self):
        base = 'http://10.0.0.10:5173/'
        first = mod.chatlens_permalink(base, '8:contact', '1700000000001')
        second = mod.chatlens_permalink(base, '8:contact', '1700000000002')
        self.assertEqual(first.split('#')[0], second.split('#')[0])
        self.assertNotEqual(first.split('#')[1], second.split('#')[1])
        self.assertFalse(urlsplit(first).query)

if __name__ == '__main__':
    unittest.main()
