PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS patents (
    application_number TEXT PRIMARY KEY,      -- 정규화 13자리
    right_type   TEXT NOT NULL,
    title        TEXT NOT NULL,
    applicants   TEXT,                        -- JSON 배열
    application_date TEXT,
    status       TEXT,
    ipc_codes    TEXT,                        -- JSON 배열
    abstract     TEXT,
    open_number  TEXT, open_date TEXT,
    register_number TEXT, register_date TEXT,
    drawing_url  TEXT,
    source       TEXT NOT NULL,               -- kipris / sample
    fetched_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query_json   TEXT NOT NULL,
    query_hash   TEXT NOT NULL,
    source       TEXT NOT NULL,
    executed_at  TEXT NOT NULL,
    total_count  INTEGER,
    result_code  TEXT,
    status       TEXT NOT NULL,
    from_cache   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_hash ON search_runs(query_hash, executed_at);

CREATE TABLE IF NOT EXISTS run_records (
    run_id       INTEGER NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    application_number TEXT NOT NULL REFERENCES patents(application_number),
    rank         INTEGER NOT NULL,
    PRIMARY KEY (run_id, application_number)
);

CREATE TABLE IF NOT EXISTS favorites (
    application_number TEXT PRIMARY KEY REFERENCES patents(application_number),
    note         TEXT,
    added_at     TEXT NOT NULL,
    added_from_run INTEGER REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS response_cache (
    query_hash   TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_usage (
    month        TEXT PRIMARY KEY,            -- 'YYYY-MM'
    call_count   INTEGER NOT NULL DEFAULT 0
);
