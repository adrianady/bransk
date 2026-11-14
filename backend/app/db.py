"""
Bransk — SQLite database setup with FTS5 full-text search.
"""
import sqlite3
import os
import re
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


def _migrate_project_table(conn: sqlite3.Connection) -> None:
    """Rebuild an old single-row `project` table without its CHECK constraint.

    Older databases declared ``id INTEGER PRIMARY KEY CHECK (id = 1)``, which
    SQLite cannot ALTER away. Multi-project support needs ordinary auto-
    incrementing rows, so the table is rebuilt with the exact columns it has
    now (including those added by later migrations) and its row copied over.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'project'"
    ).fetchone()
    old_sql = (row["sql"] if row else "") or ""
    check = r"PRIMARY\s+KEY\s+CHECK\s*\(\s*id\s*=\s*1\s*\)"
    if not re.search(check, old_sql, re.IGNORECASE):
        return

    new_sql = re.sub(check, "PRIMARY KEY AUTOINCREMENT", old_sql, count=1, flags=re.IGNORECASE)
    new_sql = re.sub(
        r"^CREATE\s+TABLE\s+(?:\"|`|\[)?project(?:\"|`|\])?",
        "CREATE TABLE project__migration",
        new_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(project)").fetchall()]
    quoted = ", ".join(f'"{column}"' for column in columns)

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(new_sql)
        conn.execute(
            f"INSERT INTO project__migration ({quoted}) SELECT {quoted} FROM project"
        )
        conn.execute("DROP TABLE project")
        conn.execute("ALTER TABLE project__migration RENAME TO project")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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

            -- Active multi-step chat task (research, batch download, screening,
            -- thesis drafting). One row is 'active' at a time; the chat prompt
            -- reads it every turn, so the assistant remembers what workflow it
            -- was in the middle of even after a restart or history trimming.
            CREATE TABLE IF NOT EXISTS chat_tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                kind          TEXT NOT NULL DEFAULT 'task',
                goal          TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'active'
                              CHECK(status IN ('active','done','failed','cancelled')),
                details       TEXT NOT NULL DEFAULT '',
                progress_json TEXT NOT NULL DEFAULT '{}',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_chat_tasks_status ON chat_tasks(status, updated_at DESC);

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

            -- One optional shared browser account for remote-device access.
            -- Passwords are stored only as salted scrypt hashes; session IDs are
            -- stored only as SHA-256 digests so a DB read cannot replay them.
            CREATE TABLE IF NOT EXISTS auth_account (
                id            INTEGER PRIMARY KEY CHECK(id = 1),
                username      TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    INTEGER NOT NULL,
                updated_at    INTEGER
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_hash TEXT PRIMARY KEY,
                username     TEXT NOT NULL,
                created_at   INTEGER NOT NULL,
                expires_at   INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
            CREATE TABLE IF NOT EXISTS auth_login_attempts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_key       TEXT NOT NULL,
                attempted_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_peer_time
                ON auth_login_attempts(ip_key, attempted_at);
            CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_time
                ON auth_login_attempts(attempted_at);

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
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
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

        # ── Migration: more than one project per database ──
        # SQLite cannot drop the old CHECK (id = 1); rebuild the table in place.
        _migrate_project_table(conn)

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

        # ── Migration: PDF highlights (2026-11) ──
        # Visual anchors made in the native reader: a saved text selection
        # with its page. The chat and study QA read them as user evidence,
        # and the thesis map can link them like document fragments.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pdf_highlights (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                page_number INTEGER,
                quote       TEXT NOT NULL DEFAULT '',
                note        TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_highlights_entry ON pdf_highlights(entry_id)"
        )
        for col, ddl in [
            ("start_offset", "INTEGER DEFAULT NULL"),
            ("end_offset", "INTEGER DEFAULT NULL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE pdf_highlights ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: canonical page texts (slice 1, 2026-11) ──
        # page_texts.text for (entry_id, page_number) is the single source of
        # truth for anchoring; character offsets are defined relative to it.
        # `quote` in pdf_highlights stays display/verification only.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS page_texts (
                entry_id    INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                page_number INTEGER NOT NULL,
                text        TEXT NOT NULL DEFAULT '',
                source      TEXT NOT NULL DEFAULT 'native',
                status      TEXT NOT NULL DEFAULT 'native',
                char_count  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (entry_id, page_number)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_page_texts_entry ON page_texts(entry_id)"
        )
        # The number printed on the page (may differ from the PDF page index).
        # Empty means "unknown" — never guessed from the index.
        for col, ddl in [("printed_page", "TEXT DEFAULT ''")]:
            try:
                conn.execute(f"ALTER TABLE page_texts ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: checked statements from study notes (2026-11) ──
        # Every statement the researcher writes is checked against the source
        # document before it is kept: verbatim quote + printed page + status,
        # plus a correction row when the note misread the text. One row per
        # statement; re-checking a note replaces its rows.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS note_checks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id      INTEGER NOT NULL REFERENCES study_notes(id) ON DELETE CASCADE,
                entry_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                statement    TEXT NOT NULL,
                status       TEXT NOT NULL CHECK (status IN
                             ('verificat','verificat_cu_nuanta','incomplet','subestimat',
                              'corectat','depasit','neverificat')),
                page_pdf     INTEGER,
                page_printed TEXT DEFAULT '',
                quote        TEXT DEFAULT '',
                correction   TEXT DEFAULT '',
                method       TEXT NOT NULL DEFAULT 'exact' CHECK (method IN ('exact','llm','manual')),
                confidence   REAL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_checks_note ON note_checks(note_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_note_checks_entry ON note_checks(entry_id, status)"
        )

        # ── Migration: PRISMA screening decisions (2026-11) ──
        # The research pipeline produced protocol + PrismaFlow, but nobody
        # could record the actual include/exclude judgement per study. One
        # decision per study keeps the flow auditable.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS screening_decisions (
                entry_id   INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
                decision   TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'maybe')),
                reason     TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS questionnaires (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id        INTEGER DEFAULT 1,
                title             TEXT NOT NULL DEFAULT '',
                description       TEXT NOT NULL DEFAULT '',
                construct         TEXT NOT NULL DEFAULT '',
                target_population TEXT NOT NULL DEFAULT '',
                reliability_notes TEXT NOT NULL DEFAULT '',
                items_json        TEXT NOT NULL DEFAULT '[]',
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # ── Migration: PDF identity + reading progress + ISBN (2026-11) ──
        # pdf_hash lets duplicate detection match identical files even when
        # titles/DOIs differ; read_status tracks the reading workflow;
        # isbn covers books (DOI is for papers).
        for col, ddl in [
            ("pdf_hash", "TEXT DEFAULT ''"),
            ("read_status", "TEXT DEFAULT 'unread'"),
            ("isbn", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE entries ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # ── Migration: AI-extracted concepts & supporting evidence (2026-11) ──
        # Concepts are factors/subfactors/constructs/mechanisms/outcomes mined
        # from highlights, notes or a text selection; evidence pins each
        # concept to the study entry + quote it came from.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extracted_concepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                name TEXT NOT NULL,
                concept_type TEXT DEFAULT 'factor',
                description TEXT DEFAULT '',
                parent_concept_id INTEGER REFERENCES extracted_concepts(id) ON DELETE SET NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS concept_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                concept_id INTEGER REFERENCES extracted_concepts(id) ON DELETE CASCADE,
                entry_id INTEGER REFERENCES entries(id) ON DELETE CASCADE,
                quote TEXT NOT NULL,
                page_number INTEGER,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_concepts_project ON extracted_concepts(project_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_concept_evidence_concept ON concept_evidence(concept_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_concept_evidence_entry ON concept_evidence(entry_id)"
        )

        # ── Migration: claim hierarchy + stance edges (slice 5, 2026-11) ──
        # `theories` already holds an arbitrary tree (parent_id); claims are
        # leaves of that tree, marked by node_type instead of a new table.
        for col, ddl in [
            ("node_type", "TEXT DEFAULT 'theory'"),
            ("statement", "TEXT DEFAULT ''"),
            ("test_variable", "TEXT DEFAULT ''"),
            ("test_condition", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE theories ADD COLUMN {col} {ddl}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_stance (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id          INTEGER NOT NULL REFERENCES theories(id) ON DELETE CASCADE,
                entry_id          INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                direction         TEXT NOT NULL
                                  CHECK (direction IN ('supports','contradicts','mixed','neutral')),
                aspect            TEXT DEFAULT '',
                highlight_id      INTEGER REFERENCES pdf_highlights(id) ON DELETE SET NULL,
                evidence_page     INTEGER,
                evidence_start    INTEGER,
                evidence_end      INTEGER,
                evidence_quote    TEXT NOT NULL,
                reasoning         TEXT DEFAULT '',
                confidence        REAL,
                status            TEXT NOT NULL DEFAULT 'proposed'
                                  CHECK (status IN ('proposed','accepted','rejected','insufficient')),
                proposed_by       TEXT NOT NULL DEFAULT 'ai',
                provider          TEXT DEFAULT '',
                model             TEXT DEFAULT '',
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                decided_at        TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_claim_stance_claim ON claim_stance(claim_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_claim_stance_entry ON claim_stance(entry_id, status)"
        )

        # ── Migration: verified note dossiers (2026-11) ──
        # A dossier pins one project's researcher notes on one study entry to
        # verbatim quotes from the canonical page text. A claim may only carry
        # status `verificat` with a quote that normalizes to a substring of the
        # declared (entry_id, page_pdf) page text. Codes A1..An are stable and
        # never reused within a dossier.
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS note_dossiers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','partial','verificat')),
                title TEXT NOT NULL DEFAULT '',
                source_sheet_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                code_seq INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (project_id, entry_id)
            );
            CREATE TABLE IF NOT EXISTS note_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dossier_id INTEGER NOT NULL REFERENCES note_dossiers(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                raw_note TEXT NOT NULL DEFAULT '',
                statement TEXT NOT NULL DEFAULT '',
                quote TEXT NOT NULL DEFAULT '',
                page_pdf INTEGER,
                page_printed TEXT NOT NULL DEFAULT '',
                engine TEXT NOT NULL DEFAULT '',
                visual_check TEXT NOT NULL DEFAULT ''
                    CHECK (visual_check IN ('','da','nu','necesar')),
                vision_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'neverificat'
                    CHECK (status IN ('verificat','verificat_cu_nuanta','incomplet',
                                      'subestimat','corectat','depasit','neverificat')),
                correction TEXT NOT NULL DEFAULT '',
                method TEXT NOT NULL DEFAULT 'exact'
                    CHECK (method IN ('exact','llm','manual')),
                coverage REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                verified_at TEXT,
                UNIQUE (dossier_id, code)
            );
            CREATE TABLE IF NOT EXISTS note_sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dossier_id INTEGER NOT NULL REFERENCES note_dossiers(id) ON DELETE CASCADE,
                kind TEXT NOT NULL
                    CHECK (kind IN ('fisa_sursei','ce_spune','continut','teza','corectii',
                                    'lipsea','glosar','formulari','de_verificat')),
                title TEXT NOT NULL DEFAULT '',
                position INTEGER NOT NULL DEFAULT 0,
                body_md TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (dossier_id, kind)
            );
            CREATE TABLE IF NOT EXISTS note_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dossier_id INTEGER NOT NULL REFERENCES note_dossiers(id) ON DELETE CASCADE,
                from_code TEXT NOT NULL,
                to_code TEXT NOT NULL,
                motive TEXT NOT NULL,
                page TEXT NOT NULL DEFAULT '',
                claim_code TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                CHECK (length(trim(motive)) > 0)
            );
            CREATE TABLE IF NOT EXISTS note_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dossier_id INTEGER NOT NULL REFERENCES note_dossiers(id) ON DELETE CASCADE,
                summary TEXT NOT NULL,
                changed_json TEXT NOT NULL DEFAULT '[]',
                open_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_note_claims_dossier_status
                ON note_claims(dossier_id, status);
            CREATE INDEX IF NOT EXISTS idx_note_edges_dossier ON note_edges(dossier_id);
            CREATE INDEX IF NOT EXISTS idx_note_dossiers_project ON note_dossiers(project_id);
            CREATE TABLE IF NOT EXISTS note_concepts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE (project_id, code)
            );
            CREATE TABLE IF NOT EXISTS note_concept_seq (
                project_id INTEGER PRIMARY KEY,
                seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS note_concept_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                concept_id INTEGER NOT NULL REFERENCES note_concepts(id) ON DELETE CASCADE,
                dossier_id INTEGER NOT NULL REFERENCES note_dossiers(id) ON DELETE CASCADE,
                claim_code TEXT NOT NULL,
                note TEXT NOT NULL,
                page TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                CHECK (length(trim(note)) > 0),
                UNIQUE (concept_id, dossier_id, claim_code)
            );
            CREATE INDEX IF NOT EXISTS idx_note_concepts_project
                ON note_concepts(project_id);
            CREATE INDEX IF NOT EXISTS idx_note_concept_links_concept
                ON note_concept_links(concept_id);
            CREATE INDEX IF NOT EXISTS idx_note_concept_links_dossier
                ON note_concept_links(dossier_id);
        """)
        # Live databases created before `code_seq` need the monotonic counter
        # for stable claim codes; fresh tables already carry it above.
        try:
            conn.execute(
                "ALTER TABLE note_dossiers"
                " ADD COLUMN code_seq INTEGER NOT NULL DEFAULT 0"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        # Live databases created before visual confirmation need the audit
        # sidecar for REQ-NOTE-10; fresh tables already carry it above.
        try:
            conn.execute(
                "ALTER TABLE note_claims"
                " ADD COLUMN vision_json TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.commit()
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
