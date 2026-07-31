"""
ChronoKG — SQLite database setup with FTS5 full-text search.
"""
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = Path(os.getenv("CHRONOKG_DB_PATH", str(DB_DIR / "chronokg.db")))


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory and WAL mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize database schema and FTS5 tables."""
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                entry_date  TEXT,          -- ISO date when the event/info happened
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                tags        TEXT DEFAULT '',   -- comma-separated
                source      TEXT DEFAULT '',   -- where info came from
                importance  INTEGER DEFAULT 1 CHECK(importance BETWEEN 1 AND 5),
                study_type  TEXT DEFAULT '',   -- 'paper' | 'book' | 'review' | 'study' ... ('' = regular entry)
                authors     TEXT DEFAULT '',   -- authors for study entries
                quality     INTEGER DEFAULT NULL, -- qualitative appraisal 1-5 (studies only)
                url_validated INTEGER DEFAULT 0, -- source URL reachability check
                design      TEXT DEFAULT '',   -- study design (RCT, cohort, ...)
                effect_direction TEXT DEFAULT '', -- positive | negative | mixed | null
                archived    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS theories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                parent_id   INTEGER REFERENCES theories(id) ON DELETE SET NULL,
                status      TEXT DEFAULT 'emerging'
                            CHECK(status IN ('supported','contested','refuted','emerging')),
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS theory_evidence (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                theory_id   INTEGER NOT NULL REFERENCES theories(id) ON DELETE CASCADE,
                entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                stance      TEXT NOT NULL CHECK(stance IN ('pro','contra')),
                note        TEXT DEFAULT '',
                weight      INTEGER DEFAULT 3 CHECK(weight BETWEEN 1 AND 5),
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(theory_id, entry_id, stance)
            );

            CREATE TABLE IF NOT EXISTS relationships (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                to_id       INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                label       TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(from_id, to_id, label)
            );

            CREATE TABLE IF NOT EXISTS tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                color       TEXT DEFAULT '#6366f1'
            );

            CREATE TABLE IF NOT EXISTS spaced_repetition (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
                ease_factor REAL DEFAULT 2.5,
                interval    INTEGER DEFAULT 0,
                repetitions INTEGER DEFAULT 0,
                next_review TEXT,
                quiz_enabled INTEGER DEFAULT 1,
                question_count INTEGER DEFAULT 3
            );

            CREATE TABLE IF NOT EXISTS review_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id     INTEGER NOT NULL REFERENCES spaced_repetition(id) ON DELETE CASCADE,
                quality     INTEGER NOT NULL CHECK(quality BETWEEN 0 AND 5),
                reviewed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS todos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL UNIQUE REFERENCES entries(id) ON DELETE CASCADE,
                due_date    TEXT,
                completed   INTEGER DEFAULT 0,
                priority    TEXT DEFAULT 'normal' CHECK(priority IN ('critical','important','normal')),
                effort      TEXT DEFAULT 'm' CHECK(effort IN ('s','m','l','xl'))
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id        INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                title           TEXT DEFAULT '',
                trigger_at      TEXT NOT NULL,
                reminder_type   TEXT DEFAULT 'once',
                recurrence      TEXT,
                notified        INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS thoughts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                description     TEXT DEFAULT '',
                tag_ids         TEXT DEFAULT '',  -- comma-separated tag IDs
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Chat history persistence
            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content     TEXT NOT NULL,
                provider    TEXT DEFAULT '',
                model       TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Create FTS5 virtual table for full-text search
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                title, content, tags,
                content='entries',
                content_rowid='id'
            );

            -- Triggers to keep FTS index in sync
            CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
                INSERT INTO entries_fts(rowid, title, content, tags)
                VALUES (new.id, new.title, new.content, new.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts, rowid, title, content, tags)
                VALUES ('delete', old.id, old.title, old.content, old.tags);
            END;

            CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
                INSERT INTO entries_fts(entries_fts, rowid, title, content, tags)
                VALUES ('delete', old.id, old.title, old.content, old.tags);
                INSERT INTO entries_fts(rowid, title, content, tags)
                VALUES (new.id, new.title, new.content, new.tags);
            END;
        """)

        conn.commit()

        # ── Migration: study metadata columns (added later, keep old DBs working) ──
        _add_column = [
            ("study_type", "TEXT DEFAULT ''"),
            ("authors", "TEXT DEFAULT ''"),
            ("quality", "INTEGER DEFAULT NULL"),
            ("url_validated", "INTEGER DEFAULT 0"),
            ("design", "TEXT DEFAULT ''"),
            ("effect_direction", "TEXT DEFAULT ''"),
        ]
        for col, ddl in _add_column:
            try:
                conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
    finally:
        conn.close()


def rebuild_fts():
    """Rebuild the FTS index."""
    conn = get_db()
    try:
        conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()
