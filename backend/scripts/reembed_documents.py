"""
Re-embed PDF chunks whose stored vectors do not match the active backend.

The `documents.embedding` column holds vectors from whichever embedding model
indexed the PDFs first. When the active model changes (this machine went from
Ollama nomic-embed-text, 768 dims, to llama.cpp bge-m3, 1024 dims) every old
vector becomes incomparable: `search_documents` drops all of them and returns
[] without an error. This script recomputes the vectors with the backend that
answers now and writes only the embedding column.

Usage (from backend/):
    .venv/bin/python scripts/reembed_documents.py               # dry run
    .venv/bin/python scripts/reembed_documents.py --limit 10
    .venv/bin/python scripts/reembed_documents.py --entry-id 42
    .venv/bin/python scripts/reembed_documents.py --apply       # write + backup

The dry run sends no embedding request and writes nothing: it reports the
stored dimensions, the active dimension and how many chunks would be redone.
No app server is needed; the script talks to the embedding backend through
`app.embeddings`. With --apply only `documents.embedding` changes — content,
entry ids and chunk order are left untouched.
"""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.db import DB_PATH, get_db  # noqa: E402
from app.embeddings import embed, embedding_backend, embedding_dimension  # noqa: E402


def _dim(raw: str | None) -> int:
    """Dimension of a stored vector; 0 for blank, NULL or unparseable JSON."""
    try:
        emb = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return 0
    return len(emb) if isinstance(emb, list) and emb else 0


def _histogram(rows: list[sqlite3.Row]) -> dict[int, int]:
    """Count stored chunks per vector dimension."""
    hist: dict[int, int] = {}
    for row in rows:
        dim = _dim(row["embedding"])
        hist[dim] = hist.get(dim, 0) + 1
    return dict(sorted(hist.items()))


def _format_dims(hist: dict[int, int]) -> str:
    return ", ".join(f"{dim}d: {count}" for dim, count in sorted(hist.items())) or "niciun vector"


def _predict_after(before: dict[int, int], pending: list[sqlite3.Row], active_dim: int) -> dict[int, int]:
    """Histogram the considered rows would have once every pending chunk is redone."""
    after = dict(before)
    for row in pending:
        old_dim = _dim(row["embedding"])
        if old_dim in after:
            after[old_dim] -= 1
            if after[old_dim] <= 0:
                del after[old_dim]
        after[active_dim] = after.get(active_dim, 0) + 1
    return dict(sorted(after.items()))


def _load_rows(conn: sqlite3.Connection, entry_id: int) -> list[sqlite3.Row]:
    sql = "SELECT id, entry_id, content, embedding FROM documents"
    params: list[int] = []
    if entry_id:
        sql += " WHERE entry_id = ?"
        params.append(entry_id)
    sql += " ORDER BY id"
    return conn.execute(sql, params).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--apply", action="store_true", help="scrie vectorii noi (face backup înainte)")
    ap.add_argument("--dry-run", action="store_true", help="doar raportează (implicit dacă lipsește --apply)")
    ap.add_argument("--limit", type=int, default=0, help="re-embed la cel mult N chunk-uri (0 = toate)")
    ap.add_argument("--entry-id", type=int, default=0, help="doar chunk-urile acestei intrări")
    ap.add_argument("--batch", type=int, default=32, help="chunk-uri per cerere de embedding (implicit 32)")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        ap.error("--dry-run și --apply se exclud")
    if args.batch < 1:
        ap.error("--batch trebuie să fie cel puțin 1")

    active_dim = embedding_dimension()
    if active_dim is None:
        print(
            "Niciun backend de embeddings nu răspunde: dimensiunea activă e necunoscută.\n"
            "Pornește llama.cpp (sau Ollama cu un model de embedding) și rulează din nou."
        )
        return 2
    print(f"Backend: {embedding_backend()} — dimensiune activă {active_dim}d")

    conn = get_db()
    try:
        rows = _load_rows(conn, args.entry_id)
        if not rows:
            scope = f" pentru intrarea {args.entry_id}" if args.entry_id else ""
            print(f"Niciun chunk indexat{scope} — nimic de făcut.")
            return 0

        before = _histogram(rows)
        pending = [row for row in rows if _dim(row["embedding"]) != active_dim]
        over_limit = 0
        if args.limit and len(pending) > args.limit:
            over_limit = len(pending) - args.limit
            pending = pending[: args.limit]
        already_ok = len(rows) - len(pending) - over_limit

        print(f"Chunk-uri scanate: {len(rows)} (dimensiuni: {_format_dims(before)})")
        if not pending:
            print("Toate vectorii au deja dimensiunea activă — nimic de re-embedat.")
            return 0
        print(
            f"De re-embedat: {len(pending)}"
            f" (sărite: {already_ok} deja corecte"
            + (f", {over_limit} peste --limit" if over_limit else "")
            + ")"
        )
        predicted = _predict_after(before, pending, active_dim)

        if not args.apply:
            print(f"Dimensiuni după (estimat): {_format_dims(predicted)}")
            print("\nProbă (dry run). Nu s-a scris nimic; scrie cu --apply.")
            return 0

        # A full checkpoint folds the WAL back into the main file so the copy
        # below is a usable backup, not a main file missing its newest rows.
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.OperationalError:
            pass
        backup = DB_PATH.with_name(
            f"{DB_PATH.name}.bak-reembed-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(DB_PATH, backup)
        print(f"Backup: {backup}")

        processed = 0
        failed = 0
        for start in range(0, len(pending), args.batch):
            batch = pending[start : start + args.batch]
            try:
                vectors = embed([row["content"] for row in batch])
            except Exception as exc:  # one bad batch must not lose the run
                vectors = None
                print(f"  lot {start + 1}-{start + len(batch)}: eroare ({exc})", flush=True)
            if vectors is None:
                failed += len(batch)
                print(
                    f"  lot {start + 1}-{start + len(batch)}: backendul nu a răspuns — "
                    f"{len(batch)} eșuate",
                    flush=True,
                )
                continue
            written = 0
            with conn:
                for row, vec in zip(batch, vectors):
                    if not isinstance(vec, list) or len(vec) != active_dim:
                        failed += 1
                        continue
                    conn.execute(
                        "UPDATE documents SET embedding = ? WHERE id = ?",
                        [json.dumps(vec), row["id"]],
                    )
                    written += 1
            failed += max(0, len(batch) - len(vectors))
            processed += written
            print(
                f"  lot {start + 1}-{start + len(batch)}: {written} scrise",
                flush=True,
            )

        skipped = already_ok + over_limit
        try:
            after = _histogram(_load_rows(conn, args.entry_id))
        except sqlite3.Error:
            after = predicted
        print(f"\nProcesate: {processed} / Sărite: {skipped} / Eșuate: {failed}")
        print(f"Dimensiuni: {_format_dims(before)} → {_format_dims(after)}")
        return 0 if failed == 0 else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
