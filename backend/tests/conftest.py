"""Shared pytest setup: hermetic DB + offline mock LLM, backend on sys.path."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["LLM_PROVIDER"] = "mock"
os.environ["CHRONOKG_DB_PATH"] = os.path.join(tempfile.gettempdir(), "chronokg_pytest.db")


@pytest.fixture(autouse=True)
def _fresh_db():
    """Wipe the test database before every test."""
    path = os.environ["CHRONOKG_DB_PATH"]
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass
    yield
