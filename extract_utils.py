"""Shared local extraction helpers for private output files."""
import os
import re
import sys


def safe_filename(value):
    """Make a portable folder name, including Windows device-name protection."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')
    value = value[:80].rstrip(' .') or 'conversation'
    if re.fullmatch(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])',
                    value.split('.', 1)[0], re.IGNORECASE):
        value = '_' + value
    return value


class ExtractionWriteError(Exception):
    """An output write failed; the message reports the cleanup result."""


def write_outputs(outputs):
    """Create private files exclusively; remove this run's files if any write fails."""
    created = []
    try:
        for path, contents in outputs:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
            fd = os.open(path, flags, 0o600)
            created.append(path)
            try:
                remaining = memoryview(contents)
                while remaining:
                    written = os.write(fd, remaining[:1048576])
                    if written <= 0:
                        raise OSError('write returned no bytes')
                    remaining = remaining[written:]
            finally:
                os.close(fd)
    except BaseException as exc:
        cleanup_failed = []
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                cleanup_failed.append(path.name)
        if cleanup_failed:
            message = 'Could not remove partial output: ' + ', '.join(cleanup_failed)
        else:
            message = 'Files created by this attempt were removed'
        if not isinstance(exc, OSError):
            if cleanup_failed:
                print(message, file=sys.stderr)
            raise
        raise ExtractionWriteError(f'Cannot write extraction ({type(exc).__name__}). {message}.') from exc
