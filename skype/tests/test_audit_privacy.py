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

class CacheIntegrityTests(unittest.TestCase):
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
