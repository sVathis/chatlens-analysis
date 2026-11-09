"""Offline integration tests for synthetic Skype extraction and analysis."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_parallel import mod as analyze


SCRIPT = Path(__file__).resolve().parents[1] / 'skype_extract.py'


def export_data(field='MessageList'):
    return {
        'userId': 'synthetic-account',
        'exportDate': '2024-01-01',
        'conversations': [
            {'id': 'synthetic:1', 'displayName': 'Synthetic Chat', 'metadata': {'extra': True},
             field: [
                 {'id': '2', 'from': 'synthetic:sender', 'content': '<p>Later</p>',
                  'originalarrivaltime': '2024-02-01T00:00:00Z', 'attachments': [{'id': 'synthetic'}]},
                 {'id': '1', 'from': 'synthetic:sender', 'content': 'Earlier',
                  'originalarrivaltime': '2023-01-01T00:00:00Z'},
                 {'id': '3', 'from': 'synthetic:sender', 'content': 'Undated'},
             ]},
            {'id': 'synthetic:2', 'displayName': 'Synthetic Chat', 'MessageList': [
                {'id': '4', 'from': 'synthetic:other', 'content': 'Other'}]},
        ],
    }


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'messages.json'
        self.output = self.root / 'output'

    def write_export(self, data):
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def run_extract(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.source), *args],
                              capture_output=True, text=True)

    def test_list_and_exact_id_extraction_preserve_source_and_message_order(self):
        data = export_data()
        self.write_export(data)
        source_bytes = self.source.read_bytes()
        listed = self.run_extract('--list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn('synthetic:1', listed.stdout)
        self.assertIn('synthetic:2', listed.stdout)
        self.assertFalse(self.output.exists())

        result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = self.output / 'Synthetic Chat'
        extracted = json.loads((folder / 'conversation.json').read_text(encoding='utf-8'))
        self.assertEqual(extracted['userId'], data['userId'])
        self.assertEqual(extracted['conversations'], [data['conversations'][0]])
        self.assertEqual(self.source.read_bytes(), source_bytes)
        markdown = (folder / 'conversation.md').read_text(encoding='utf-8')
        self.assertLess(markdown.index('Earlier'), markdown.index('Later'))
        loaded = analyze.load(folder / 'conversation.json')
        self.assertEqual([(m['skype_id'], m['source_index'], m['key']) for m in loaded],
                         [('3', 2, 'm0000002'), ('1', 1, 'm0000001'), ('2', 0, 'm0000000')])

    def test_messages_alias_is_preserved_and_analyzed(self):
        data = export_data('messages')
        self.write_export(data)
        result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.output / 'Synthetic Chat' / 'conversation.json'
        extracted = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(extracted['conversations'][0], data['conversations'][0])
        self.assertNotIn('MessageList', extracted['conversations'][0])
        self.assertEqual({m['skype_id'] for m in analyze.load(path)}, {'1', '2', '3'})

    def test_markdown_split_does_not_split_or_reorder_json(self):
        data = export_data()
        self.write_export(data)
        for split, first, second in [('year', '2023', '2024'), ('month', '2023-01', '2024-02')]:
            with self.subTest(split=split):
                output = self.output / split
                result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(output),
                                          '--split', split)
                self.assertEqual(result.returncode, 0, result.stderr)
                folder = output / 'Synthetic Chat'
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 [f'conversation-{first}.md', f'conversation-{second}.md',
                                  'conversation-undated.md', 'conversation.json'])
                self.assertEqual(json.loads((folder / 'conversation.json').read_text(encoding='utf-8'))
                                 ['conversations'][0], data['conversations'][0])
                markdown = (folder / f'conversation-{first}.md').read_text(encoding='utf-8')
                self.assertIn('Earlier', markdown)
                self.assertNotIn('Later', markdown)

    def test_ambiguous_selection_and_existing_output_are_safe(self):
        self.write_export(export_data())
        ambiguous = self.run_extract('--contact', 'Synthetic Chat', '--output-dir', str(self.output))
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn('Found 2 matching conversations', ambiguous.stderr)
        self.assertFalse(self.output.exists())
        first = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(first.returncode, 0, first.stderr)
        folder = self.output / 'Synthetic Chat'
        original = {p.name: p.read_bytes() for p in folder.iterdir()}
        second = self.run_extract('--contact', 'synthetic:2', '--output-dir', str(self.output))
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('Output already exists', second.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, original)

    def test_existing_markdown_without_json_is_not_overwritten(self):
        self.write_export(export_data())
        folder = self.output / 'Synthetic Chat'
        folder.mkdir(parents=True)
        old = folder / 'conversation.md'
        old.write_text('existing synthetic content', encoding='utf-8')
        result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Output already exists', result.stderr)
        self.assertEqual(old.read_text(encoding='utf-8'), 'existing synthetic content')
        self.assertFalse((folder / 'conversation.json').exists())

    def test_invalid_utf8_reports_a_clear_error(self):
        self.source.write_bytes(b'\xff')
        result = self.run_extract('--list')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid UTF-8', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
