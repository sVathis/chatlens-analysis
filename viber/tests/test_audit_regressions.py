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

class ExportRobustnessTests(unittest.TestCase):
    def test_invalid_unicode_is_rejected_without_echoing_text(self):
        for value in ('prefix\x00suffix', 'prefix\ud800suffix'):
            with self.subTest(value=repr(value)), self.assertRaisesRegex(ValueError, 'NUL or an unpaired'):
                mod.build_messages({}, {'messages': [{'kind': 'text', 'text': value}]})

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
        import os
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
                os.kill(os.getpid(), signal.SIGINT)
                return original_wait(*a, **kw)
            with quiet(), patch.object(mod, 'wait', side_effect=interrupt_then_wait):
                with self.assertRaises(SystemExit) as caught:
                    mod.analyze(args, messages, client=FakeClient(lambda p: []))
            self.assertEqual(caught.exception.code, 130)
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
