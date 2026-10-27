-- events.db: owned by the (simulated) camera system.
-- The analytics application opens this file read-only.

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filename     TEXT    NOT NULL UNIQUE,
    camera_id    TEXT    NOT NULL,
    source_path  TEXT    NOT NULL,          -- relative to the data directory
    duration_s   REAL    NOT NULL,
    fps          REAL    NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    uploaded_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS camera_events (
    event_id           TEXT    PRIMARY KEY,  -- '<camera_id>#<NNN>'
    camera_id          TEXT    NOT NULL,
    event_no           INTEGER NOT NULL,
    source_id          INTEGER NOT NULL REFERENCES sources(id),
    source_offset_s    REAL    NOT NULL,     -- segment start inside the source video
    timestamp          TEXT    NOT NULL,     -- YYYY_MM_DD-hh_mm_ss
    timestamp_iso      TEXT    NOT NULL,     -- sortable
    duration_s         REAL    NOT NULL,
    fps                REAL    NOT NULL,
    frame_count        INTEGER NOT NULL,
    width              INTEGER NOT NULL,
    height             INTEGER NOT NULL,
    clip_path          TEXT    NOT NULL,     -- relative to the data directory
    trigger_frame_path TEXT    NOT NULL,
    trigger_offset_s   REAL    NOT NULL,
    created_at         TEXT    NOT NULL,
    UNIQUE (camera_id, event_no)
);

CREATE INDEX IF NOT EXISTS ix_camera_events_time ON camera_events (camera_id, timestamp_iso);
CREATE INDEX IF NOT EXISTS ix_camera_events_created ON camera_events (created_at);
