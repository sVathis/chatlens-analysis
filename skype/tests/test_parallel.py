"""No-network regression tests of parallel resume and deterministic cache behavior."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skype_analyze.py'
spec = importlib.util.spec_from_file_location('skype_analyze', SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FakeResponse:
    output_text = '{"findings":[]}'
    status = 'completed'
    usage = None

class ParallelTracker:
    active = 0
    peak = 0
    calls = 0
    fail_batch = None
    lock = threading.Lock()

    @classmethod
    def reset(cls):
        cls.active = cls.peak = cls.calls = 0
        cls.fail_batch = None

class FakeResponses:
    def create(self, **kwargs):
        payload = json.loads(kwargs['input'])
        keys = [m['key'] for m in payload['messages'] if m['role']=='TARGET']
        # Batches have disjoint target windows, use first key as a stable proxy
        first = int(keys[0][1:]) if keys else 0
        with ParallelTracker.lock:
            ParallelTracker.active += 1
            ParallelTracker.calls += 1
            ParallelTracker.peak = max(ParallelTracker.peak, ParallelTracker.active)
        try:
            time.sleep(.025)
            if ParallelTracker.fail_batch is not None and first == ParallelTracker.fail_batch:
                raise RuntimeError('simulated failure')
            return FakeResponse()
        finally:
            with ParallelTracker.lock:
                ParallelTracker.active -= 1

class FakeClient:
    def __init__(self, **kwargs):
        self.responses = FakeResponses()
    def __enter__(self): return self
    def __exit__(self, *args): return None

class ParallelTests(unittest.TestCase):
    def fixture(self, folder):
        p = folder/'conversation.json'
        p.write_text(json.dumps({'conversations':[{'id':'8:live:contact','MessageList':[
            {'id':str(1000+i),'from':'8:live:contact','displayName':'Contact',
             'originalarrivaltime':f'2024-01-{i//20+1:02d}T00:{i%20:02d}:00Z',
             'content':f'Test content {i}'} for i in range(80)]}]}),encoding='utf8')
        return p
    def args(self, path, output, workers=4, max_batches=0):
        return types.SimpleNamespace(input=path,output=output,sender='8:live:contact',
            model='gpt-5-mini',batch_size=10,context=2,workers=workers,
            max_batches=max_batches,skypelens_url='http://10.0.0.10:5173/')
    def run_fake(self, args, msgs):
        with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=FakeClient)}), \
             patch.dict(os.environ, {'OPENAI_API_KEY':'dummy'}):
            mod.analyze(args,msgs)
    def test_parallel_cache_resume_and_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            src=self.fixture(base)
            msgs=mod.load(src)
            ParallelTracker.reset()
            first=self.args(src,base/'results',workers=4,max_batches=3)
            self.run_fake(first,msgs)
            self.assertGreater(ParallelTracker.peak,1)
            self.assertEqual(ParallelTracker.calls,3)
            self.assertEqual(len(list((base/'results'/'batches').glob('batch-*.json'))),3)
            self.assertEqual(json.loads((base/'results'/'summary.json').read_text())['covered_messages'],30)
            # Using a different number of workers must reuse existing run_config.
            later=self.args(src,base/'results',workers=2)
            self.run_fake(later,msgs)
            self.assertEqual(ParallelTracker.calls,8)
            self.assertEqual(json.loads((base/'results'/'summary.json').read_text())['covered_messages'],80)
            self.assertEqual(len(list((base/'results'/'batches').glob('batch-*.json'))),8)
            self.run_fake(later,msgs)
            self.assertEqual(ParallelTracker.calls,8)
    def test_failures_leave_completed_batches_resumable(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            src=self.fixture(base)
            msgs=mod.load(src)
            ParallelTracker.reset()
            ParallelTracker.fail_batch=10
            args=self.args(src,base/'results',workers=2,max_batches=3)
            with self.assertRaises(SystemExit) as cm:
                self.run_fake(args,msgs)
            self.assertEqual(cm.exception.code,2)
            files=list((base/'results'/'batches').glob('batch-*.json'))
            self.assertGreaterEqual(len(files),1)
            self.assertFalse((base/'results'/'batches'/'batch-00002.json').exists())
            ParallelTracker.fail_batch=None
            self.run_fake(self.args(src,base/'results',workers=3),msgs)
            self.assertEqual(json.loads((base/'results'/'summary.json').read_text())['covered_messages'],80)

if __name__ == '__main__': unittest.main()
