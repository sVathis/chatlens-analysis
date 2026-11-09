"""Synthetic input failures produce useful messages without tracebacks."""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from test_parallel import mod


class ExpectedInputErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'synthetic.json'

    def write_source(self, value):
        self.source.write_text(json.dumps(value), encoding='utf-8')

    def test_load_raises_analysis_error_for_wrong_export_shapes(self):
        cases = [([], 'Expected Skype export object'),
                 ({}, 'Expected top-level conversations list'),
                 ({'conversations': []}, 'Expected one extracted conversation, found 0'),
                 ({'conversations': [{}, {}]}, 'Expected one extracted conversation, found 2')]
        for value, expected in cases:
            with self.subTest(value=value):
                self.write_source(value)
                with self.assertRaisesRegex(mod.AnalysisError, expected) as caught:
                    mod.load(self.source)
                self.assertIsInstance(caught.exception, ValueError)

    def test_missing_conversation_raises_analysis_error(self):
        self.write_source({'conversations': []})
        with self.assertRaisesRegex(mod.AnalysisError, 'Expected exactly one extracted Skype conversation'):
            mod.get_conversation_id(self.source)

    def test_report_without_conversation_id_raises_analysis_error(self):
        self.write_source({'conversations': [{'MessageList': []}]})
        args = types.SimpleNamespace(input=self.source, output=self.root, chatlens_url='https://example.test/')
        with self.assertRaisesRegex(mod.AnalysisError, 'No original conversation ID'):
            mod.make_report(args, [], [])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(Path(mod.__file__)), '--input', str(self.source), *args],
                              capture_output=True, text=True)

    def test_cli_reports_invalid_exports_and_bad_json_without_traceback(self):
        cases = [('[]', 'Expected Skype export object'),
                 ('{}', 'Expected top-level conversations list'),
                 ('{"conversations": []}', 'Expected one extracted conversation, found 0'),
                 ('{"conversations": [{}, {}]}', 'Expected one extracted conversation, found 2'),
                 ('{"private synthetic text":', 'Expecting value')]
        for content, expected in cases:
            with self.subTest(expected=expected):
                self.source.write_text(content, encoding='utf-8')
                result = self.run_cli('--list-senders')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertNotIn('private synthetic text', result.stderr)
                self.assertEqual(len(result.stderr.strip().splitlines()), 1)

    def test_report_only_missing_id_is_reported_without_traceback(self):
        self.write_source({'conversations': [{'MessageList': [
            {'from': 'synthetic', 'content': 'synthetic message'}]}]})
        output = self.root / 'output'
        output.mkdir()
        mod.write_json(output / 'run_config.json',
                       mod.fingerprint(self.source, 'synthetic', 'gpt-6-luna', 60, 5))
        result = self.run_cli('--sender', 'synthetic', '--output', str(output),
                              '--report-only', '--chatlens-url', 'https://example.test/')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No original conversation ID', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertEqual(len(result.stderr.strip().splitlines()), 1)
