"""Synthetic tests for shared extraction output safety."""
import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import extract_utils as util


class ExtractionOutputTests(unittest.TestCase):
    def test_private_exclusive_output_and_portable_names(self):
        self.assertTrue(util.safe_filename('NUL.txt').startswith('_'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'output'
            util.write_outputs([(path, b'synthetic\n')])
            self.assertEqual(path.read_bytes(), b'synthetic\n')
            if os.name == 'posix':
                self.assertEqual(path.stat().st_mode & 0o077, 0)
            with self.assertRaises(util.ExtractionWriteError):
                util.write_outputs([(path, b'replacement')])
            self.assertEqual(path.read_bytes(), b'synthetic\n')

    def test_failed_second_write_rolls_back_created_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp) / 'first', Path(tmp) / 'second']
            original = os.write
            calls = []
            def fail(fd, payload):
                calls.append(1)
                if len(calls) == 2:
                    original(fd, payload[:1])
                    raise OSError(errno.ENOSPC, 'synthetic failure')
                return original(fd, payload)
            with patch.object(os, 'write', side_effect=fail):
                with self.assertRaises(util.ExtractionWriteError):
                    util.write_outputs([(p, b'synthetic') for p in paths])
            self.assertFalse(any(p.exists() for p in paths))
