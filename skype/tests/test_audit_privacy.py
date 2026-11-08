"""Regression test for private text in synthetic API errors."""
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import test_parallel as helpers
mod = helpers.mod


class PrivacyTests(unittest.TestCase):
    def test_error_does_not_log_client_exception_text(self):
        class Client:
            def __init__(self, **kwargs): self.responses = self
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def create(self, **kwargs): raise RuntimeError('synthetic secret <message>')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = helpers.ParallelTests()
            source = helper.fixture(root)
            args = helper.args(source, root / 'out', workers=1, max_batches=1)
            output = io.StringIO()
            with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=Client)}), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy'}), patch.object(mod.time, 'sleep'), \
                 contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit): mod.analyze(args, mod.load(source))
            self.assertIn('RuntimeError', output.getvalue())
            self.assertNotIn('synthetic secret', output.getvalue())

    def test_batch_format_details_and_http_status_are_safe_and_useful(self):
        class ApiError(Exception):
            status_code = 429
        for exc, expected in [(mod.BatchFormatError('Invalid JSON at character 8'), 'Invalid JSON at character 8'),
                              (ApiError('secret content'), 'HTTP 429')]:
            with self.subTest(expected=expected):
                summary = mod.batch_error_summary(exc)
                self.assertIn(expected, summary)
                self.assertNotIn('secret content', summary)

    def test_worker_logs_batch_format_details_and_http_status(self):
        class ApiError(Exception):
            status_code = 429
        for exc, expected in [(mod.BatchFormatError('Invalid JSON at character 8'), 'Invalid JSON at character 8'),
                              (ApiError('secret content'), 'HTTP 429')]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); helper = helpers.ParallelTests(); source = helper.fixture(root)
                args = helper.args(source, root / 'out', workers=1, max_batches=1)
                output = io.StringIO()
                with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=helpers.FakeClient)}), \
                     patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy'}), \
                     patch.object(mod, 'request_batch', side_effect=exc), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    with self.assertRaises(SystemExit):
                        mod.analyze(args, mod.load(source))
                self.assertIn(expected, output.getvalue())
                self.assertNotIn('secret content', output.getvalue())

    def test_incomplete_details_do_not_leak_response_content(self):
        class Response:
            status = 'incomplete'
            incomplete_details = types.SimpleNamespace(reason='secret content')
            output_text = ''
            usage = None
        client = types.SimpleNamespace(responses=types.SimpleNamespace(create=lambda **_: Response()))
        args = types.SimpleNamespace(sender='s', model='test', context=0)
        msgs = [{'sender': 's', 'text': 'synthetic', 'key': 'm0000000', 'time': '', 'name': 'synthetic'}]
        with patch.object(mod.time, 'sleep'), self.assertRaises(mod.BatchFormatError) as caught:
            mod.request_batch(client, args, msgs, (1, 0, 0, 1, 1))
        self.assertNotIn('secret content', mod.batch_error_summary(caught.exception))
        self.assertIn('status=incomplete', str(caught.exception))

class CacheIntegrityTests(unittest.TestCase):
    def test_corrupt_run_config_is_an_expected_analysis_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run_config.json'
            path.write_text('{', encoding='utf-8')
            with self.assertRaisesRegex(mod.AnalysisError, 'Invalid run configuration'):
                mod.read_run_config(path)

    def test_legacy_cache_shapes_remain_usable_without_api_calls(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); helper = helpers.ParallelTests(); source = helper.fixture(root)
            args = helper.args(source, root / 'out'); args.output.mkdir(); (args.output / 'batches').mkdir()
            msgs = mod.load(source)
            path = args.output / 'batches' / 'batch-00001.json'
            mod.write_json(args.output / 'run_config.json',
                           mod.fingerprint(source, args.sender, args.model, args.batch_size, args.context))
            argv = ['skype_analyze.py', '--input', str(source), '--output', str(args.output),
                    '--sender', args.sender, '--model', args.model, '--batch-size', str(args.batch_size),
                    '--context', str(args.context), '--report-only']
            records = [
                {'batch': 1, 'start': 0, 'end': 10, 'findings': [], 'usage': {}, 'segments': 1},
                {'batch': 1, 'start': 0, 'end': 10, 'findings': [], 'usage': {}},
                {'batch': 1, 'start': 0, 'end': 10, 'findings': []},
            ]
            for record in records:
                with self.subTest(keys=sorted(record)):
                    mod.write_json(path, record)
                    with patch.dict(sys.modules, {'openai': None}), patch.object(sys, 'argv', argv), \
                         contextlib.redirect_stdout(io.StringIO()):
                        mod.main()
                    self.assertEqual(json.loads((args.output / 'summary.json').read_text())['covered_messages'], 10)
            skipped = {'batch': 1, 'start': 0, 'end': 10, 'findings': [],
                       'usage': {}, 'skipped_no_target': True}
            mod.write_json(path, skipped)
            self.assertEqual(mod.read_cached_batch(path, 1, 0, 10, msgs, 'absent'), skipped)

    def test_bad_cache_is_not_counted_as_coverage(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); helper = helpers.ParallelTests(); source = helper.fixture(root)
            args = helper.args(source, root / 'out'); args.output.mkdir(); (args.output / 'batches').mkdir()
            msgs = mod.load(source); batch = mod.plan_batches(msgs, args.batch_size, args.context)
            path = args.output / 'batches' / 'batch-00001.json'
            for bad in ('{', '{}', json.dumps({'batch': 1, 'start': 0, 'end': 9, 'findings': []}),
                        json.dumps({'batch': 1, 'start': 0, 'end': 10, 'findings': [], 'skipped_no_target': True})):
                with self.subTest(bad=bad):
                    path.write_text(bad, encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'Invalid cached batch'):
                        mod.make_report(args, msgs, batch)

    def test_cache_without_configuration_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); helper = helpers.ParallelTests(); source = helper.fixture(root)
            args = helper.args(source, root / 'out'); args.output.mkdir(); (args.output / 'batches').mkdir()
            (args.output / 'batches' / 'batch-00001.json').write_text('{}', encoding='utf-8')
            with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=helpers.FakeClient)}), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'dummy'}):
                with self.assertRaisesRegex(ValueError, 'without run_config'):
                    mod.analyze(args, mod.load(source))

    def test_unexpected_value_error_is_not_hidden_by_cli_wrapper(self):
        import subprocess
        script = Path(mod.__file__).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.json'
            path.write_text('{}', encoding='utf-8')
            code = ("import json,runpy,sys; "
                    "json.loads=lambda *a,**k: (_ for _ in ()).throw(ValueError('synthetic bug')); "
                    f"sys.argv=[{str(script)!r},'--input',{str(path)!r},'--list-senders']; "
                    f"runpy.run_path({str(script)!r},run_name='__main__')")
            result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Traceback', result.stderr)
            self.assertIn('ValueError: synthetic bug', result.stderr)
