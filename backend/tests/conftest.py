"""Shared pytest setup: hermetic DB + offline mock LLM, backend on sys.path."""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["LLM_PROVIDER"] = "mock"

# One DB + studies folder per process. Two pytest runs at once (agent + human,
# or two tools) must never delete each other's files, so the paths carry the
# pid: "no such table" / "database is locked" came from a shared /tmp name.
_PID = os.getpid()
_TEMP_DIR = os.path.abspath(tempfile.gettempdir())
DB_PATH = os.path.join(_TEMP_DIR, f"chronokg_pytest_{_PID}.db")
STUDIES_DIR = os.path.join(_TEMP_DIR, f"chronokg_pytest_studies_{_PID}")
os.environ["CHRONOKG_DB_PATH"] = DB_PATH
os.environ["CHRONOKG_STUDIES_DIR"] = STUDIES_DIR

# Files this process created; cleanup never touches another run's paths.
_OWN_DIR_GUARD = f"chronokg_pytest_studies_{_PID}"


def _cleanup_process_files():
    """Remove only this process's DB files and studies folder (idempotent)."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(DB_PATH + suffix)
        except OSError:
            pass
    # Guard: only rmtree the exact per-process folder directly inside tempdir.
    if (
        os.path.dirname(os.path.abspath(STUDIES_DIR)) == _TEMP_DIR
        and os.path.basename(STUDIES_DIR) == _OWN_DIR_GUARD
    ):
        shutil.rmtree(STUDIES_DIR, ignore_errors=True)


def pytest_sessionfinish(session, exitstatus):
    """Leave /tmp clean: drop this run's DB (+ -wal/-shm) and study PDFs."""
    _cleanup_process_files()


atexit.register(_cleanup_process_files)


@pytest.fixture(autouse=True)
def _fresh_db():
    """Wipe this process's test database and studies folder before every test."""
    _cleanup_process_files()
    yield
