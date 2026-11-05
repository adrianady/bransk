"""The frontend must not ship every view in the first download.

One 533 kB entry file carried React and all twelve views, so opening the app
also fetched the graph, the timeline and the chat. The entry is now split and
each view is a chunk of its own. A test holds the split in place: without one,
a plain `import` added back would silently undo it and nothing would notice.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DIST = FRONTEND / "dist"


def _node() -> str | None:
    return shutil.which("node")


@pytest.fixture(scope="module")
def built_dist():
    """A built dist/, or a skip when Node is not available in this shell."""
    if not _node():
        pytest.skip("node is not on PATH (it lives in nvm for an interactive shell)")
    if not (DIST / "index.html").is_file():
        pytest.skip("frontend/dist is not built")
    return DIST


def _entry_scripts(dist: Path) -> list[str]:
    html = (dist / "index.html").read_text(encoding="utf-8")
    out = []
    for part in html.split('src="')[1:]:
        out.append(part.split('"')[0])
    return [s for s in out if s.endswith(".js")]


def test_views_are_separate_chunks(built_dist):
    """Each lazy view must appear as its own file in dist/assets."""
    assets = {p.name for p in (built_dist / "assets").glob("*.js")}
    for view in ("GraphView", "Timeline", "StudiesView", "TheoriesView",
                 "ChatPanel", "EntryDetail", "EntryForm", "ReviewPanel",
                 "TodoList", "ThoughtsView", "AiStatusModal"):
        assert any(name.startswith(view) for name in assets), \
            f"{view} should be its own chunk, not bundled into the entry"


def test_the_entry_chunk_stays_small(built_dist):
    """The first download must stay well under the old 533 kB."""
    entry = _entry_scripts(built_dist)
    assert entry, "index.html must load one entry script"
    size = (built_dist / entry[0].lstrip("/")).stat().st_size
    assert size < 460_000, f"entry chunk is {size} bytes; the split regressed"


def test_no_unused_syntax_highlighter_dependency():
    """It was declared but imported nowhere, and pulled ~200 kB of grammars."""
    pkg = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "react-syntax-highlighter" not in deps
