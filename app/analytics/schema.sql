-- analytics.db: everything the analytics application writes.
-- Camera events are referenced by event_id ('<camera_id>#<NNN>') from events.db.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,              -- JSON
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conditions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id          TEXT    NOT NULL,
    enabled            INTEGER NOT NULL DEFAULT 1,
    deleted            INTEGER NOT NULL DEFAULT 0,
    current_version_id INTEGER,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conditions_camera ON conditions (camera_id);

CREATE TABLE IF NOT EXISTS condition_versions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id      INTEGER NOT NULL REFERENCES conditions(id),
    version           INTEGER NOT NULL,
    prompt            TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    scope             TEXT    NOT NULL CHECK (scope IN ('frame', 'event', 'history')),
    focus_questions   TEXT    NOT NULL,     -- JSON [{id, question}]
    decision_criteria TEXT    NOT NULL,
    scope_overridden  INTEGER NOT NULL DEFAULT 0,
    suggestion        TEXT,                 -- JSON: the LLM's original proposal
    llm_call_id       INTEGER,
    created_at        TEXT    NOT NULL,
    UNIQUE (condition_id, version)
);

CREATE TABLE IF NOT EXISTS frame_selections (
    event_id         TEXT    PRIMARY KEY,
    camera_id        TEXT    NOT NULL,
    event_no         INTEGER NOT NULL,
    origin           TEXT    NOT NULL,      -- import | auto
    mode             TEXT    NOT NULL,
    threshold        REAL    NOT NULL,
    threshold_source TEXT    NOT NULL,      -- auto | fixed | manual
    manual           INTEGER NOT NULL,
    frames           TEXT    NOT NULL,      -- JSON [{index, t, score, kinds}]
    signals          TEXT    NOT NULL,      -- JSON {t, motion, ref}
    stats            TEXT    NOT NULL,      -- JSON
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_id        TEXT    NOT NULL,
    status           TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    first_event_no   INTEGER NOT NULL,
    last_event_no    INTEGER NOT NULL,
    start_iso        TEXT    NOT NULL,      -- timestamp of the first event
    last_event_iso   TEXT    NOT NULL,      -- timestamp of the last event
    end_iso          TEXT    NOT NULL,      -- last event + its duration
    event_count      INTEGER NOT NULL,
    last_activity_at TEXT    NOT NULL,      -- wall clock, for the idle close
    closed_at        TEXT,
    close_reason     TEXT,
    summary          TEXT,
    summary_llm_call_id INTEGER
);
CREATE INDEX IF NOT EXISTS ix_episodes_camera ON episodes (camera_id, start_iso);

CREATE TABLE IF NOT EXISTS event_queue (
    event_id      TEXT    PRIMARY KEY,
    camera_id     TEXT    NOT NULL,
    event_no      INTEGER NOT NULL,
    timestamp_iso TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK (status IN ('WAITING', 'QUEUED', 'PROCESSING', 'DONE', 'ERROR')),
    episode_id    INTEGER REFERENCES episodes(id),
    error         TEXT,
    discovered_at TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_queue_status ON event_queue (status, discovered_at, camera_id, event_no);

CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    purpose     TEXT    NOT NULL,           -- condition | observation | decision | closing | summary | repair
    model       TEXT    NOT NULL,
    think       INTEGER NOT NULL,
    system      TEXT    NOT NULL,
    prompt      TEXT    NOT NULL,
    images      TEXT    NOT NULL,           -- JSON [{label, width, height, bytes}] (no image data)
    options     TEXT    NOT NULL,           -- JSON
    response    TEXT,
    thinking    TEXT,
    parsed      TEXT,                       -- JSON
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 1,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms INTEGER,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT    NOT NULL,
    camera_id        TEXT    NOT NULL,
    event_no         INTEGER NOT NULL,
    frames_hash      TEXT    NOT NULL,
    focus_hash       TEXT    NOT NULL,
    covered_versions TEXT    NOT NULL,      -- JSON [condition_version_id]
    frames           TEXT    NOT NULL,      -- JSON [{n, t, kinds}]
    description      TEXT    NOT NULL,
    focus_answers    TEXT    NOT NULL,      -- JSON [{id, answer, evidence, frame}]
    best_frame       INTEGER,
    llm_call_id      INTEGER,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_observations_event ON observations (event_id, frames_hash);

CREATE TABLE IF NOT EXISTS runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                 TEXT    NOT NULL,   -- live | rerun_event | rerun_episode | backfill
    condition_id         INTEGER NOT NULL,
    condition_version_id INTEGER NOT NULL,
    camera_id            TEXT    NOT NULL,
    episode_id           INTEGER NOT NULL,
    status               TEXT    NOT NULL CHECK (status IN ('RUNNING', 'DONE', 'ERROR')),
    error                TEXT,
    started_at           TEXT    NOT NULL,
    finished_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_runs_live ON runs (kind, condition_id, episode_id);

CREATE TABLE IF NOT EXISTS evaluations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               INTEGER NOT NULL REFERENCES runs(id),
    condition_id         INTEGER NOT NULL,
    condition_version_id INTEGER NOT NULL,
    camera_id            TEXT    NOT NULL,
    episode_id           INTEGER NOT NULL,
    event_id             TEXT    NOT NULL,
    event_no             INTEGER NOT NULL,
    is_closing           INTEGER NOT NULL DEFAULT 0,
    decision             TEXT    NOT NULL CHECK (decision IN ('YES', 'NO', 'UNCERTAIN', 'PENDING', 'SKIPPED', 'ERROR')),
    confidence           REAL,
    reasoning            TEXT,
    focus_results        TEXT,              -- JSON [{id, question, status, evidence, event_no}]
    key_event_no         INTEGER,
    key_frame            INTEGER,           -- frame number within the key event's observation
    observation_id       INTEGER,
    llm_call_id          INTEGER,
    superseded           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_eval_current ON evaluations (condition_id, episode_id, superseded);
CREATE INDEX IF NOT EXISTS ix_eval_event ON evaluations (event_id);

CREATE TABLE IF NOT EXISTS alerts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                 TEXT    NOT NULL CHECK (kind IN ('DETECTED', 'NEEDS_REVIEW')),
    status               TEXT    NOT NULL CHECK (status IN ('ACTIVE', 'NEEDS_REVIEW', 'CONFIRMED', 'DISMISSED', 'SUPERSEDED')),
    condition_id         INTEGER NOT NULL,
    condition_version_id INTEGER NOT NULL,
    camera_id            TEXT    NOT NULL,
    episode_id           INTEGER NOT NULL,
    run_id               INTEGER,
    evaluation_id        INTEGER NOT NULL,
    prompt               TEXT    NOT NULL,
    title                TEXT    NOT NULL,
    scope                TEXT    NOT NULL,
    decision             TEXT    NOT NULL,
    confidence           REAL,
    reasoning            TEXT,
    focus_results        TEXT,              -- JSON
    event_ids            TEXT    NOT NULL,  -- JSON [event_id]
    key_event_id         TEXT,
    key_frame_t          REAL,
    key_frame_path       TEXT,              -- relative to the data directory
    episode_start_iso    TEXT,
    episode_end_iso      TEXT,
    episode_summary      TEXT,
    review_note          TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alerts_current ON alerts (condition_id, episode_id, status);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT    NOT NULL,           -- rerun_event | rerun_episode | backfill | analyze_camera
    payload     TEXT    NOT NULL,           -- JSON
    status      TEXT    NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'DONE', 'ERROR')),
    error       TEXT,
    created_at  TEXT    NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    stage     TEXT    NOT NULL,
    camera_id TEXT,
    event_id  TEXT,
    message   TEXT    NOT NULL,
    data      TEXT
);
