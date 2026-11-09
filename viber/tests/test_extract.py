"""Offline tests for synthetic single-chat Viber extraction."""
import builtins
import contextlib
import errno
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_viber_analyze import FakeClient, finding, mod as analyze, quiet

SCRIPT = Path(__file__).resolve().parents[1] / 'viber_extract.py'
spec = importlib.util.spec_from_file_location('viber_extract', SCRIPT)
extract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


def export_data():
    return {
        'format': 'viberpc-export', 'version': 1, 'exportedAt': '2024-01-01T00:00:00Z',
        'self': {'contactIds': [1], 'name': 'Synthetic Self'},
        'contacts': {'1': {'name': 'Synthetic Self'}, '2': {'name': 'Synthetic Peer'},
                     '3': {'name': 'Unrelated Synthetic'}},
        'other_metadata': {'synthetic': True},
        'chats': [
            {'id': 10, 'title': 'Synthetic Peer', 'type': 'direct', 'memberIds': [2],
             'messages': [
                 {'id': 101, 'timestamp': 1706745600000, 'authorId': 2, 'outgoing': False,
                  'kind': 'image', 'caption': 'Synthetic caption',
                  'media': {'path': 'media/synthetic/image.png', 'status': 'original'},
                  'replyTo': {'text': 'Synthetic earlier', 'messageId': 102}},
                 {'id': 102, 'timestamp': 1672531200000, 'authorId': 1, 'outgoing': True,
                  'kind': 'text', 'text': 'Synthetic earlier'},
                 {'id': 103, 'timestamp': None, 'kind': 'call', 'peerId': 2},
                 {'id': 104, 'timestamp': 1706745600001, 'kind': 'system', 'text': 'Synthetic event'},
             ],
             'reactionEvents': [{'id': 200, 'authorId': 2, 'targetId': 101, 'reaction': '1'}],
             'synthetic_extra': {'preserve': True}},
            {'id': 11, 'title': 'Other Direct', 'type': 'direct', 'memberIds': [3],
             'messages': [{'id': 201, 'authorId': 3, 'kind': 'text',
                           'text': 'Unrelated synthetic message'}], 'reactionEvents': []},
            {'id': 12, 'title': 'Synthetic Peer Group', 'type': 'group', 'memberIds': [2, 3],
             'messages': [{'id': 301, 'authorId': 3, 'kind': 'text', 'text': 'Group synthetic'}]},
        ],
    }


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'viber_export.json'
        self.output = self.root / 'extracted'

    def write_source(self, data):
        self.source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def run_extract(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), str(self.source), *args],
                              capture_output=True, text=True)

    def folder(self, data, index=0, output=None):
        return (output or self.output) / extract.folder_name(data['chats'][index])

    def test_extract_preserves_selected_chat_and_only_referenced_contacts(self):
        data = export_data()
        self.write_source(data)
        source_bytes = self.source.read_bytes()
        listed = self.run_extract('--list')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn('viber:10', listed.stdout)
        self.assertNotIn('Synthetic earlier', listed.stdout)
        result = self.run_extract('--chat', 'viber:10', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Synthetic earlier', result.stdout)
        folder = self.folder(data)
        path = folder / 'viber_export.json'
        extracted, _digest = analyze.read_export(path)
        self.assertEqual(extracted['chats'], [data['chats'][0]])
        self.assertEqual(extracted['self'], data['self'])
        self.assertEqual(extracted['other_metadata'], data['other_metadata'])
        self.assertEqual(set(extracted['contacts']), {'1', '2'})
        self.assertNotIn('Unrelated synthetic', path.read_text(encoding='utf-8'))
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(analyze.build_messages(data, data['chats'][0]),
                         analyze.build_messages(extracted, extracted['chats'][0]))
        markdown = (folder / 'conversation.md').read_text(encoding='utf-8')
        self.assertIn('Synthetic earlier', markdown)
        self.assertIn('Synthetic caption', markdown)
        self.assertIn('Synthetic event', markdown)
        self.assertIn('media/synthetic/image.png', markdown)
        self.assertNotIn('Unrelated synthetic message', markdown)
        self.assertFalse((folder / 'media').exists())
        if os.name == 'posix':
            for item in folder.iterdir():
                self.assertEqual(item.stat().st_mode & 0o077, 0)

    def test_contact_id_or_name_selects_direct_chat_only(self):
        data = export_data()
        self.write_source(data)
        for index, query in enumerate(('viber:2', '2', 'synthetic peer')):
            with self.subTest(query=query):
                output = self.output / str(index)
                result = self.run_extract('--contact', query, '--output-dir', str(output))
                self.assertEqual(result.returncode, 0, result.stderr)
                path = self.folder(data, output=output) / 'viber_export.json'
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['chats'][0]['id'], 10)

    def test_ambiguous_contact_requires_chat_id(self):
        data = export_data()
        data['chats'].append({'id': 13, 'title': 'Synthetic Peer', 'type': 'direct',
                              'memberIds': [2], 'messages': []})
        self.write_source(data)
        result = self.run_extract('--contact', 'viber:2', '--output-dir', str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Found 2 matching direct chats', result.stderr)
        self.assertIn('viber:10', result.stderr)
        self.assertIn('viber:13', result.stderr)
        self.assertFalse(self.output.exists())
        chosen = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertEqual(chosen.returncode, 0, chosen.stderr)
        other = self.run_extract('--chat', '13', '--output-dir', str(self.output))
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertNotEqual(self.folder(data, 0), self.folder(data, 3))
        self.assertTrue((self.folder(data, 3) / 'viber_export.json').exists())

    def test_explicit_group_keeps_every_participant(self):
        data = export_data()
        self.write_source(data)
        result = self.run_extract('--chat', '12', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.folder(data, 2) / 'viber_export.json'
        selected = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(selected['chats'], [data['chats'][2]])
        self.assertEqual(set(selected['contacts']), {'1', '2', '3'})

    def test_call_and_reaction_contacts_are_retained(self):
        data = export_data()
        data['contacts']['4'] = {'name': 'Synthetic Event Participant'}
        data['chats'][0]['messages'][2]['peerId'] = 4
        data['chats'][0]['reactionEvents'][0]['authorId'] = 4
        self.write_source(data)
        result = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.folder(data) / 'viber_export.json'
        selected = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(set(selected['contacts']), {'1', '2', '4'})

    def test_unknown_contact_and_duplicate_chat_ids_are_rejected(self):
        data = export_data()
        self.write_source(data)
        missing = self.run_extract('--contact', 'missing-synthetic', '--output-dir', str(self.output))
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('Found 0 matching direct chats', missing.stderr)
        self.assertFalse(self.output.exists())
        data['chats'][1]['id'] = 10
        self.write_source(data)
        duplicate = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn('Found 2 chats with id', duplicate.stderr)
        self.assertFalse(self.output.exists())

    def test_malformed_contacts_are_not_copied_unfiltered(self):
        data = export_data()
        data['contacts'] = ['unrelated synthetic data']
        self.write_source(data)
        result = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Export contacts must be an object', result.stderr)
        self.assertNotIn('unrelated synthetic data', result.stderr)
        self.assertFalse(self.output.exists())

    def test_split_only_changes_markdown(self):
        data = export_data()
        self.write_source(data)
        for split, first, second in [('year', '2023', '2024'), ('month', '2023-01', '2024-02')]:
            with self.subTest(split=split):
                output = self.output / split
                result = self.run_extract('--chat', '10', '--output-dir', str(output), '--split', split)
                self.assertEqual(result.returncode, 0, result.stderr)
                folder = self.folder(data, output=output)
                self.assertEqual(sorted(p.name for p in folder.iterdir()),
                                 [f'conversation-{first}.md', f'conversation-{second}.md',
                                  'conversation-undated.md', 'viber_export.json'])
                self.assertEqual(json.loads((folder / 'viber_export.json').read_text(encoding='utf-8'))
                                 ['chats'][0], data['chats'][0])
                first_md = (folder / f'conversation-{first}.md').read_text(encoding='utf-8')
                self.assertIn('Synthetic earlier', first_md)
                self.assertNotIn('Synthetic caption', first_md)

    def test_no_overwrite_and_encoding_error_leave_no_partial_files(self):
        data = export_data()
        self.write_source(data)
        first = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertEqual(first.returncode, 0, first.stderr)
        folder = self.folder(data)
        original = {p.name: p.read_bytes() for p in folder.iterdir()}
        second = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('Output already exists', second.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, original)
        data['chats'][0]['messages'][0]['caption'] = 'synthetic\ud800'
        self.source.write_text(json.dumps(data), encoding='utf-8')
        bad_output = self.root / 'invalid'
        failed = self.run_extract('--chat', '10', '--output-dir', str(bad_output))
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('unpaired Unicode surrogate', failed.stderr)
        self.assertNotIn('Traceback', failed.stderr)
        self.assertFalse(bad_output.exists())

    def test_failed_second_write_cleans_up_and_preserves_unrelated_files(self):
        data = export_data()
        self.write_source(data)
        folder = self.folder(data)
        folder.mkdir(parents=True)
        keep = folder / 'unrelated.txt'
        keep.write_text('synthetic', encoding='utf-8')
        real_write = os.write
        calls = []
        def fail_second_write(fd, content):
            calls.append(1)
            if len(calls) == 2:
                real_write(fd, content[:4])
                raise OSError(errno.ENOSPC, 'synthetic disk full')
            return real_write(fd, content)
        err = io.StringIO()
        with patch.object(os, 'write', side_effect=fail_second_write), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                extract.main([str(self.source), '--chat', '10', '--output-dir', str(self.output)])
        self.assertIn('Files created by this attempt were removed', err.getvalue())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['unrelated.txt'])
        self.assertEqual(keep.read_text(encoding='utf-8'), 'synthetic')

    def test_listing_and_extraction_have_no_openai_import_or_network(self):
        data = export_data()
        self.write_source(data)
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name == 'openai' or name.startswith('openai.'):
                raise AssertionError('openai must not be imported')
            return original_import(name, *args, **kwargs)
        out = io.StringIO()
        with patch('builtins.__import__', side_effect=guarded_import), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('network access')), \
             contextlib.redirect_stdout(out):
            extract.main([str(self.source), '--list'])
            extract.main([str(self.source), '--chat', '10', '--output-dir', str(self.output)])
        self.assertIn('viber:10', out.getvalue())
        self.assertTrue((self.folder(data) / 'viber_export.json').exists())

    def test_extracted_chat_runs_analysis_with_a_fake_client(self):
        data = export_data()
        self.write_source(data)
        result = self.run_extract('--chat', '10', '--output-dir', str(self.output))
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.folder(data) / 'viber_export.json'
        extracted, digest = analyze.read_export(path)
        chat = analyze.find_chat(extracted, None)
        messages = analyze.build_messages(extracted, chat)
        args = types.SimpleNamespace(input=path, output=self.root / 'analysis', sender='viber:2',
                                     model='synthetic-model', batch_size=10, context=2, workers=1,
                                     max_batches=0, chatlens_url=None, source_sha256=digest,
                                     conversation_id='viber:10')
        client = FakeClient(lambda payload: [finding(m['key'], m['text']) for m in payload['messages']
                            if m['role'] == 'TARGET' and m['sender_id'] == 'viber:2' and m['text']])
        with quiet():
            analyze.analyze(args, messages, client=client)
        summary = json.loads((args.output / 'summary.json').read_text(encoding='utf-8'))
        findings = json.loads((args.output / 'findings.json').read_text(encoding='utf-8'))
        self.assertTrue(summary['complete'])
        self.assertEqual(summary['total_messages'], 4)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['quote'], 'Synthetic caption')
