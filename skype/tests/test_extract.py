"""Offline integration tests for synthetic Skype extraction and analysis."""
import json
import contextlib
import errno
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_parallel import mod as analyze


SCRIPT = Path(__file__).resolve().parents[1] / 'skype_extract.py'
spec = importlib.util.spec_from_file_location('skype_extract', SCRIPT)
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


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

    def folder(self, data, index=0, output=None):
        return (output or self.output) / extract.folder_name(data['conversations'][index], index,
                                                             data['conversations'])

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
        folder = self.folder(data)
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
        path = self.folder(data) / 'conversation.json'
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
                folder = self.folder(data, output=output)
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 [f'conversation-{first}.md', f'conversation-{second}.md',
                                  'conversation-undated.md', 'conversation.json'])
                self.assertEqual(json.loads((folder / 'conversation.json').read_text(encoding='utf-8'))
                                 ['conversations'][0], data['conversations'][0])
                markdown = (folder / f'conversation-{first}.md').read_text(encoding='utf-8')
                self.assertIn('Earlier', markdown)
                self.assertNotIn('Later', markdown)

    def test_same_named_conversations_have_distinct_stable_folders(self):
        data = export_data()
        self.write_export(data)
        ambiguous = self.run_extract('--contact', 'Synthetic Chat', '--output-dir', str(self.output))
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn('Found 2 matching conversations', ambiguous.stderr)
        self.assertFalse(self.output.exists())
        first = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(first.returncode, 0, first.stderr)
        first_folder = self.folder(data, 0)
        original = {p.name: p.read_bytes() for p in first_folder.iterdir()}
        second = self.run_extract('--contact', 'synthetic:2', '--output-dir', str(self.output))
        self.assertEqual(second.returncode, 0, second.stderr)
        second_folder = self.folder(data, 1)
        self.assertNotEqual(first_folder, second_folder)
        self.assertTrue((second_folder / 'conversation.json').exists())
        self.assertEqual({p.name: p.read_bytes() for p in first_folder.iterdir()}, original)
        rerun = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertNotEqual(rerun.returncode, 0)
        self.assertIn('Output already exists', rerun.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in first_folder.iterdir()}, original)

    def test_existing_markdown_without_json_is_not_overwritten(self):
        data = export_data()
        self.write_export(data)
        folder = self.folder(data)
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

    def test_missing_or_duplicate_ids_can_be_extracted_by_index(self):
        data = {'conversations': [{'MessageList': []}, {'MessageList': []},
                                  {'id': 'dup', 'MessageList': []}, {'id': 'dup', 'MessageList': []}]}
        self.write_export(data)
        listing = self.run_extract('--list')
        self.assertEqual(listing.returncode, 0, listing.stderr)
        self.assertIn('[index: 1]', listing.stdout)
        self.assertIn('[index: 4]', listing.stdout)
        for index in range(len(data['conversations'])):
            result = self.run_extract('--index', str(index + 1), '--output-dir', str(self.output))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((self.folder(data, index) / 'conversation.json').exists())
        self.assertEqual(len({self.folder(data, i) for i in range(4)}), 4)
        invalid = self.run_extract('--index', '0', '--output-dir', str(self.output))
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('--index', invalid.stderr)

    def test_windows_reserved_folder_names_are_sanitized(self):
        for name in ('CON', 'nul.txt', 'COM1', 'lpt9.txt', 'aux', 'CONIN$', 'COM¹'):
            with self.subTest(name=name):
                self.assertTrue(extract.safe_filename(name).startswith('_'))
        self.assertEqual(extract.safe_filename('normal name'), 'normal name')

    def test_non_object_conversation_is_a_clean_error(self):
        self.write_export({'conversations': [None]})
        for args in (('--list',), ('--contact', 'synthetic')):
            with self.subTest(args=args):
                result = self.run_extract(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Every conversation must be an object', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_surrogate_in_message_leaves_no_output_and_can_be_retried(self):
        data = export_data()
        data['conversations'][0]['MessageList'][0]['content'] = 'synthetic\ud83d'
        self.source.write_text(json.dumps(data), encoding='utf-8')
        result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unpaired Unicode surrogate', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(self.folder(data).exists())
        self.write_export(export_data())
        retry = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(retry.returncode, 0, retry.stderr)

    def test_failed_later_write_removes_only_new_partial_files(self):
        data = export_data()
        self.write_export(data)
        folder = self.folder(data)
        folder.mkdir(parents=True)
        keep = folder / 'unrelated.txt'
        keep.write_text('synthetic existing', encoding='utf-8')
        real_write = os.write
        calls = []
        def fail_second_write(fd, content):
            calls.append(1)
            if len(calls) == 2:
                real_write(fd, content[:4])
                raise OSError(errno.ENOSPC, 'synthetic disk full')
            return real_write(fd, content)
        argv = [str(SCRIPT), str(self.source), '--contact', 'synthetic:1', '--output-dir', str(self.output)]
        err = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(extract.os, 'write', side_effect=fail_second_write), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                extract.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('Files created by this attempt were removed', err.getvalue())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['unrelated.txt'])
        self.assertEqual(keep.read_text(encoding='utf-8'), 'synthetic existing')
        retry = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(retry.returncode, 0, retry.stderr)

    def test_interrupt_during_write_removes_partial_files(self):
        data = export_data()
        self.write_export(data)
        real_write = os.write
        calls = []
        def interrupt_second_write(fd, content):
            calls.append(1)
            if len(calls) == 2:
                real_write(fd, content[:4])
                raise KeyboardInterrupt
            return real_write(fd, content)
        argv = [str(SCRIPT), str(self.source), '--contact', 'synthetic:1', '--output-dir', str(self.output)]
        with patch.object(sys, 'argv', argv), patch.object(extract.os, 'write', side_effect=interrupt_second_write), \
             contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                extract.main()
        self.assertEqual(list(self.folder(data).iterdir()), [])

    def test_output_uses_lf_and_private_permissions(self):
        data = export_data()
        self.write_export(data)
        result = self.run_extract('--contact', 'synthetic:1', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        for path in self.folder(data).iterdir():
            self.assertNotIn(b'\r\n', path.read_bytes())
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_markdown_uses_analyzer_sender_and_timestamp_fields(self):
        data = {'conversations': [{'id': 'synthetic', 'displayName': 'Synthetic', 'MessageList': [
            {'id': '9', 'creator': 'synthetic:creator', 'imdisplayname': 'Synthetic Name',
             'composetime': '2024-01-01T00:00:00Z', 'content': 'First'},
            {'id': '1', 'sender': 'synthetic:sender', 'composetime': '2024-01-01T00:00:00Z',
             'content': 'Second'},
        ]}]}
        self.write_export(data)
        result = self.run_extract('--contact', 'synthetic', '--output-dir', str(self.output), '--split', 'month')
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = self.folder(data)
        markdown = (folder / 'conversation-2024-01.md').read_text(encoding='utf-8')
        messages = analyze.load(folder / 'conversation.json')
        for source, parsed in zip(data['conversations'][0]['MessageList'], messages):
            self.assertEqual(extract.sender_id(source), parsed['sender'])
            self.assertEqual(extract.sender_name(source), parsed['name'])
            self.assertEqual(extract.timestamp(source), parsed['time'])
        self.assertIn('Synthetic Name', markdown)
        self.assertIn('Sender ID: `synthetic:creator`', markdown)
        self.assertIn('Sender ID: `synthetic:sender`', markdown)
        self.assertLess(markdown.index('First'), markdown.index('Second'))

    def test_message_field_helpers_match_analyzer(self):
        cases = [
            {},
            {'from': 'synthetic:from', 'creator': 'synthetic:creator', 'sender': 'synthetic:sender',
             'displayName': 'Synthetic Display', 'imdisplayname': 'Synthetic Alternate',
             'originalarrivaltime': '2024-01', 'composetime': '2023-01', 'createdTime': '2022-01'},
            {'creator': 'synthetic:creator', 'imdisplayname': 'Synthetic Alternate', 'composetime': '2023-01'},
            {'sender': 'synthetic:sender', 'createdTime': '2022-01'},
            {'composeTime': '2021-01'},
        ]
        for message in cases:
            with self.subTest(message=message):
                self.assertEqual(extract.sender_id(message), analyze.sender_id(message))
                self.assertEqual(extract.sender_name(message), analyze.sender_name(message))
                self.assertEqual(extract.timestamp(message), analyze.timestamp(message))
