"""Regression checks for offline audit findings, using synthetic data only."""
import json
import tempfile
import types
import unittest
from pathlib import Path

from test_viber_analyze import mod, fake_msgs, quiet


class CacheIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.msgs = fake_msgs(2)
        self.args = types.SimpleNamespace(output=self.root, sender='s', model='test', batch_size=2,
                                          context=0, workers=1, max_batches=0, chatlens_url=None,
                                          source_sha256='0' * 64, conversation_id='viber:1')
        (self.root / 'batches').mkdir()
        self.path = self.root / 'batches' / 'batch-00001.json'
        mod.write_json(self.root / 'run_config.json', mod.fingerprint(self.args))

    def test_corrupt_and_wrong_batch_cannot_be_counted_as_covered(self):
        bad = ['{', '{}', json.dumps({'batch': 2, 'start': 0, 'end': 2, 'findings': []}),
               json.dumps({'batch': 1, 'start': 0, 'end': 2, 'findings': [], 'skipped_no_target': True}),
               json.dumps({'batch': 1, 'start': 0, 'end': 2, 'findings': [None]})]
        for raw in bad:
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Invalid cached batch'):
                    mod.collect_findings(self.args, self.msgs, [(0, 0, 2, 2)])
                with quiet(), self.assertRaisesRegex(ValueError, 'Invalid cached batch'):
                    mod.analyze(self.args, self.msgs)

    def test_cache_without_config_is_never_adopted(self):
        (self.root / 'run_config.json').unlink()
        self.path.write_text('{}', encoding='utf-8')
        with quiet(), self.assertRaisesRegex(ValueError, 'without run_config'):
            mod.analyze(self.args, self.msgs)
        self.assertFalse((self.root / 'run_config.json').exists())

    def test_corrupt_run_config_is_an_expected_analysis_error(self):
        path = self.root / 'run_config.json'
        path.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(mod.AnalysisError, 'Invalid run configuration'):
            mod.read_run_config(path)

class ExportRobustnessTests(unittest.TestCase):
    def test_nul_is_preserved_and_surrogates_identify_the_message_without_text(self):
        messages = mod.build_messages({}, {'id': 7, 'messages': [{'kind': 'text', 'text': 'a\x00b'}]})
        self.assertEqual(messages[0]['text'], 'a\x00b')
        with self.assertRaises(mod.AnalysisError) as caught:
            mod.build_messages({}, {'id': 7, 'messages': [{'kind': 'text', 'text': 'secret\ud800'}]})
        self.assertIn('Chat \'7\', message index 0: text', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_other_chats_and_unused_contacts_do_not_block_the_selected_chat(self):
        data = {'format': 'viberpc-export', 'version': 1,
                'contacts': {'9': {'name': 'unused\ud800'}},
                'chats': [{'id': 1, 'messages': [{'kind': 'text', 'text': 'synthetic'}]},
                          {'id': 2, 'messages': [{'kind': 'text', 'text': 'bad\ud800'}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'export.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            read, _ = mod.read_export(path)
            self.assertEqual(len(mod.list_chats(read)), 2)
            self.assertEqual(mod.build_messages(read, read['chats'][0])[0]['text'], 'synthetic')
            with self.assertRaisesRegex(mod.AnalysisError, "Chat '2', message index 0"):
                mod.build_messages(read, read['chats'][1])

    def test_used_contact_surrogate_is_reported_without_its_name(self):
        data = {'contacts': {'2': {'name': 'secret\ud800'}}}
        chat = {'id': 1, 'messages': [{'kind': 'text', 'text': 'synthetic', 'authorId': 2}]}
        with self.assertRaises(mod.AnalysisError) as caught:
            mod.build_messages(data, chat)
        self.assertIn("Chat '1', message index 0: name", str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_wrong_collection_types_have_clear_errors(self):
        with self.assertRaisesRegex(ValueError, 'contactIds must be a list'):
            mod.build_messages({'self': {'contactIds': 5}}, {'messages': []})
        with self.assertRaisesRegex(ValueError, 'messages must be a list'):
            mod.build_messages({}, {'messages': 5})

class SurvivorRegressions(CacheIntegrityTests):
    def test_cache_bounds_must_match(self):
        mod.write_json(self.path, {'batch': 1, 'start': 0, 'end': 1, 'findings': []})
        with self.assertRaisesRegex(ValueError, 'Invalid cached batch'):
            mod.collect_findings(self.args, self.msgs, [(0, 0, 2, 2)])

    def test_cache_checked_before_sending_other_batches(self):
        self.msgs.extend(fake_msgs(2))
        self.path.write_text('{}', encoding='utf-8')
        class Client:
            calls = 0
            @property
            def responses(self):
                self.calls += 1
                raise RuntimeError('synthetic failure')
        client = Client()
        with quiet(), self.assertRaisesRegex(ValueError, 'Invalid cached batch'):
            mod.analyze(self.args, self.msgs, client=client)
        self.assertEqual(client.calls, 0)

    def test_call_has_no_author_even_when_outgoing(self):
        message = mod.build_messages({}, {'messages': [{'kind': 'call', 'outgoing': True, 'authorId': 8}]})[0]
        self.assertEqual((message['sender'], message['name']), ('', ''))

    def test_history_and_finding_quote_are_html_escaped(self):
        self.msgs[0]['text'] = '<script>alert(1)</script> & "x"'
        item = {'message_key': self.msgs[0]['key'], 'quote': '<script>', 'category': 'other',
                'severity': 'low', 'confidence': 'low', 'reason': '', 'caveat': ''}
        mod.write_json(self.path, {'batch': 1, 'start': 0, 'end': 2, 'findings': [item]})
        with quiet():
            mod.make_report(self.args, self.msgs, [(0, 0, 2, 2)])
        for name in ('history.html', 'report.html'):
            page = (self.root / name).read_text(encoding='utf-8')
            self.assertNotIn('<script>', page)
            self.assertIn('&lt;script&gt;', page)


class InterruptTests(unittest.TestCase):
    def test_sigint_during_completion_exits_130_and_caches_inflight(self):
        import signal
        from unittest.mock import patch
        from test_viber_analyze import FakeClient
        with tempfile.TemporaryDirectory() as tmp:
            args = types.SimpleNamespace(output=Path(tmp), sender='s', model='test', batch_size=2,
                                         context=0, workers=1, max_batches=0, chatlens_url=None,
                                         source_sha256='0' * 64, conversation_id='viber:1')
            messages = fake_msgs(4)
            original_wait = mod.wait
            def interrupt_then_wait(*a, **kw):
                signal.raise_signal(signal.SIGINT)
                return original_wait(*a, **kw)
            with quiet() as (_out, err), patch.object(mod, 'wait', side_effect=interrupt_then_wait):
                with self.assertRaises(SystemExit) as caught:
                    mod.analyze(args, messages, client=FakeClient(lambda p: []))
            self.assertEqual(caught.exception.code, 130)
            self.assertEqual(err.getvalue().count('Interrupted: finishing active requests'), 1)
            self.assertTrue((args.output / 'batches' / 'batch-00001.json').exists())
            self.assertFalse((args.output / 'batches' / 'batch-00002.json').exists())

class PrivacyTests(unittest.TestCase):
    def test_client_exception_text_is_not_logged(self):
        from test_viber_analyze import FakeClient
        with tempfile.TemporaryDirectory() as tmp:
            args = types.SimpleNamespace(output=Path(tmp), sender='s', model='test', batch_size=2,
                                         context=0, workers=1, max_batches=0, chatlens_url=None,
                                         source_sha256='0' * 64, conversation_id='viber:1')
            def fail(_payload):
                raise RuntimeError('synthetic secret <message>')
            with quiet() as (out, err), self.assertRaises(SystemExit):
                mod.analyze(args, fake_msgs(2), client=FakeClient(fail))
            self.assertNotIn('synthetic secret', out.getvalue() + err.getvalue())
            self.assertIn('RuntimeError', err.getvalue())

    def test_batch_format_details_and_http_status_are_safe_and_useful(self):
        class ApiError(Exception):
            status_code = 429
        cases = [(mod.BatchFormatError("Invalid JSON: Expecting ',' delimiter at character 8"),
                  "Invalid JSON: Expecting ',' delimiter at character 8"),
                 (ApiError('secret content'), 'HTTP 429')]
        for exc, expected in cases:
            with self.subTest(expected=expected):
                summary = mod.batch_error_summary(exc)
                self.assertIn(expected, summary)
                self.assertNotIn('secret content', summary)

    def test_worker_logs_batch_format_details_and_http_status(self):
        from unittest.mock import patch
        class ApiError(Exception):
            status_code = 429
        for exc, expected in [(mod.BatchFormatError('Invalid JSON at character 8'), 'Invalid JSON at character 8'),
                              (ApiError('secret content'), 'HTTP 429')]:
            with tempfile.TemporaryDirectory() as tmp, quiet() as (_out, err), \
                 patch.object(mod, 'request_batch', side_effect=exc):
                args = types.SimpleNamespace(workers=1)
                mod.run_tasks(None, args, [], [(0, 0, 1, 1)], [(1, 0, 0, 1, 1)], Path(tmp))
                self.assertIn(expected, err.getvalue())
                self.assertNotIn('secret content', err.getvalue())

    def test_incomplete_details_do_not_leak_response_content(self):
        from test_viber_analyze import FakeClient, FakeResponse
        response = FakeResponse('', status='incomplete', details=types.SimpleNamespace(reason='secret content'))
        args = types.SimpleNamespace(sender='s', model='test', context=0)
        with quiet(), self.assertRaises(mod.BatchFormatError) as caught:
            mod.request_batch(FakeClient(lambda _: response), args, fake_msgs(1), (1, 0, 0, 1, 1))
        self.assertNotIn('secret content', mod.batch_error_summary(caught.exception))
        self.assertIn('status=incomplete', str(caught.exception))

class SenderListingTests(unittest.TestCase):
    def test_sender_listing_groups_and_limits_names(self):
        from unittest.mock import patch
        messages = [{'sender': 'viber:2', 'name': f'name{i}'} for i in range(7)]
        messages += [{'sender': 'viber:3', 'name': 'other'}]
        with quiet() as (out, _err), patch.object(mod, 'read_export', return_value=({'chats': [{'id': 1}]}, 'hash')), \
                patch.object(mod, 'build_messages', return_value=messages):
            mod.main(['--input', 'synthetic.json', '--list-senders'])
        self.assertIn("7  'viber:2'   display names: name0, name1, name2, name3, name4", out.getvalue())
        self.assertIn("1  'viber:3'   display names: other", out.getvalue())
        self.assertNotIn('name5', out.getvalue())

class DocumentationTests(unittest.TestCase):
    def test_skype_link_names_match_the_public_interface(self):
        text = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
        self.assertNotIn('skypelens_url', text)
        self.assertNotIn('--skypelens-url', text)
        self.assertIn('same `chatlens_url` (`--chatlens-url`)', text)

class ReadExportRobustnessTests(unittest.TestCase):
    def test_bad_messages_collection_is_rejected_before_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.json'
            path.write_text(json.dumps({'format': 'viberpc-export', 'version': 1,
                                        'chats': [{'id': 1, 'messages': 5}]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'chat.messages must be a list'):
                mod.read_export(path)

    def test_unexpected_value_error_is_not_hidden_by_cli_wrapper(self):
        import subprocess
        import sys
        script = Path(mod.__file__).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.json'
            path.write_text('{}', encoding='utf-8')
            code = ("import json,runpy,sys; "
                    "json.loads=lambda *a,**k: (_ for _ in ()).throw(ValueError('synthetic bug')); "
                    f"sys.argv=[{str(script)!r},'--input',{str(path)!r},'--list-chats']; "
                    f"runpy.run_path({str(script)!r},run_name='__main__')")
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Traceback', result.stderr)
            self.assertIn('ValueError: synthetic bug', result.stderr)
