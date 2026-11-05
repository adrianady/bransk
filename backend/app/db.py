"""
Bransk — SQLite database setup with FTS5 full-text search.
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
    # Avoid "database is locked" failures when the LLM action parser or the
    # background scheduler writes concurrently with other requests.
    conn.execute("PRAGMA busy_timeout=5000")
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
                pdf_path    TEXT DEFAULT '',   -- local file name in the studies folder
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

            -- AI call observability (provider, latency, failures)
            CREATE TABLE IF NOT EXISTS ai_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                provider    TEXT DEFAULT '',
                model       TEXT DEFAULT '',
                endpoint    TEXT DEFAULT '',
                ok          INTEGER DEFAULT 1,
                error       TEXT DEFAULT '',
                latency_ms  INTEGER DEFAULT 0,
                prompt_chars INTEGER DEFAULT 0,
                reply_chars INTEGER DEFAULT 0,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- PDF full-text chunks for semantic retrieval (RAG over papers)
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                chunk_index INTEGER DEFAULT 0,
                page_number INTEGER DEFAULT NULL, -- 1-based PDF page; NULL for legacy/unverifiable chunks
                content     TEXT NOT NULL,
                embedding   TEXT DEFAULT '',  -- JSON float array
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_documents_entry ON documents(entry_id);

            -- User personal notes per study / PDF (with course context and page refs)
            CREATE TABLE IF NOT EXISTS study_notes (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id       INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                course_context TEXT DEFAULT '',  -- e.g. "Cursul de Neuroștiințe", "Seminarul 4"
                content        TEXT NOT NULL,    -- user note text
                page_number    INTEGER DEFAULT NULL, -- page reference in PDF
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_study_notes_entry ON study_notes(entry_id);

            -- Provenance for notes written from a PDF page or saved AI answer.
            -- A note may cite multiple retrieved chunks; legacy notes remain valid
            -- and simply have no rows here.
            CREATE TABLE IF NOT EXISTS study_note_sources (
                note_id     INTEGER NOT NULL REFERENCES study_notes(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (note_id, document_id)
            );
            CREATE INDEX IF NOT EXISTS idx_note_sources_document ON study_note_sources(document_id);
        """)

        # ── Thesis project: the single "centre" of the app ──
        # One row holds the licence/dissertation the user is writing: its
        # title (editable at any time), the paper type, and the outline with
        # the text written into each section (structure_json).
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS project (
                id             INTEGER PRIMARY KEY CHECK (id = 1),
                title          TEXT NOT NULL DEFAULT '',
                work_type      TEXT NOT NULL DEFAULT 'licenta',
                field          TEXT NOT NULL DEFAULT '',
                description    TEXT NOT NULL DEFAULT '',
                research_q     TEXT NOT NULL DEFAULT '',
                methodology    TEXT NOT NULL DEFAULT '',
                deadline       TEXT DEFAULT '',
                structure_json TEXT NOT NULL DEFAULT '[]',
                plan_json      TEXT NOT NULL DEFAULT '{}',
                analysis_json  TEXT NOT NULL DEFAULT '',
                analysis_at    TEXT NOT NULL DEFAULT '',
                analysis_key   TEXT NOT NULL DEFAULT '',
                feedback_json  TEXT NOT NULL DEFAULT '',
                feedback_at    TEXT NOT NULL DEFAULT '',
                feedback_key   TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Brainstorming notes for the thesis: hunches, links, possible
            -- variables, things to read. Kept apart from entries because they
            -- are half-formed by nature and must not pollute the timeline.
            CREATE TABLE IF NOT EXISTS project_ideas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                text        TEXT NOT NULL,
                category    TEXT NOT NULL DEFAULT 'general',
                section_id  TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_ideas_category ON project_ideas(category);

            -- Small key/value settings that the user changes from the UI.
            -- The chosen model lives here, not in .env: .env is a file for
            -- secrets and defaults, a model choice is a preference the app
            -- must be able to change without a restart.
            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            -- Thesis claims are anchored to a section ID from structure_json.
            -- Evidence connects a claim to a real study and optionally to the
            -- exact PDF fragment and/or a saved note that supports it.
            CREATE TABLE IF NOT EXISTS project_claims (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                section_id  TEXT NOT NULL,
                text        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_project_claims_section ON project_claims(section_id);

            CREATE TABLE IF NOT EXISTS project_claim_evidence (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id    INTEGER NOT NULL REFERENCES project_claims(id) ON DELETE CASCADE,
                entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                note_id     INTEGER REFERENCES study_notes(id) ON DELETE SET NULL,
                relation    TEXT NOT NULL DEFAULT 'supports'
                            CHECK(relation IN ('supports','contradicts','contextualizes')),
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON project_claim_evidence(claim_id);
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_entry ON project_claim_evidence(entry_id);
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

        # ── Indexes on the hot read paths ──
        # /studies and /timeline filter on `archived` and order by date, and the
        # timeline counts relationships in both directions once per row. Without
        # these, SQLite scans entries and builds a temp B-tree for every list.
        # Idempotent so a database created before them gains them on next boot.
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_entries_archived_date ON entries(archived, entry_date DESC);
            CREATE INDEX IF NOT EXISTS idx_entries_updated ON entries(archived, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_rel_from ON relationships(from_id);
            CREATE INDEX IF NOT EXISTS idx_rel_to ON relationships(to_id);
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
            ("pdf_path", "TEXT DEFAULT ''"),
        ]
        for col, ddl in _add_column:
            try:
                conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: AI observability token counters (added later) ──
        for col, ddl in [
            ("prompt_tokens", "INTEGER DEFAULT 0"),
            ("completion_tokens", "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE ai_events ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: the research plan, the analysis and feedback caches ──
        # plan_json holds participants, instruments, hypotheses, ethics and the
        # schedule — the things a thesis needs that are not the outline.
        # analysis_*/feedback_* keep the last generated text, keyed by a
        # fingerprint of the inputs, so opening a tab no longer pays for an LLM
        # call and only an explicit POST does.
        for col, ddl in [
            ("plan_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("analysis_json", "TEXT NOT NULL DEFAULT ''"),
            ("analysis_at", "TEXT NOT NULL DEFAULT ''"),
            ("analysis_key", "TEXT NOT NULL DEFAULT ''"),
            ("feedback_json", "TEXT NOT NULL DEFAULT ''"),
            ("feedback_at", "TEXT NOT NULL DEFAULT ''"),
            ("feedback_key", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE project ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: real bibliographic identifiers ──
        # The research pipeline produced plausible-but-wrong source URLs; a DOI
        # resolved against Crossref is the only trustworthy identifier, so it
        # lives in its own column with a confidence note beside it.
        for col, ddl in [
            ("doi", "TEXT DEFAULT ''"),
            ("doi_source", "TEXT DEFAULT ''"),
            ("doi_checked_at", "TEXT DEFAULT ''"),
            ("bib_title", "TEXT DEFAULT ''"),
            ("bib_authors", "TEXT DEFAULT ''"),
            ("bib_year", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: page-level PDF provenance ──
        # Old chunks intentionally stay NULL: chunk order is not a trustworthy
        # substitute for the source PDF page.
        try:
            conn.execute("ALTER TABLE documents ADD COLUMN page_number INTEGER DEFAULT NULL")
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
