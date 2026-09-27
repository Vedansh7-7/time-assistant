"""SQLite schema, versioned with PRAGMA user_version.

Append new migrations to MIGRATIONS; never edit an applied one. Every
important table has created_at/updated_at so audit history can be added later.
Datetimes are stored as ISO-8601 UTC strings ("2026-09-29T12:30:00+00:00").
"""

MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE people (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        aliases TEXT NOT NULL DEFAULT '',          -- comma-separated alternative names
        relationship TEXT,
        importance TEXT,
        communication_style TEXT,
        preferences TEXT,
        custom_instructions TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE tasks (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        deadline TEXT NOT NULL,
        estimated_duration_min INTEGER NOT NULL CHECK (estimated_duration_min > 0),
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','dropped')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE commitments (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'meeting',
        start TEXT NOT NULL,
        end TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('tentative','confirmed','completed','cancelled','needs_reschedule')),
        authority_level INTEGER NOT NULL CHECK (authority_level BETWEEN 1 AND 5),
        location TEXT,
        notes TEXT,
        buffer_before_min INTEGER NOT NULL DEFAULT 0,
        buffer_after_min INTEGER NOT NULL DEFAULT 0,
        recurrence TEXT,                           -- JSON rule or NULL
        exdates TEXT NOT NULL DEFAULT '[]',        -- JSON list of UTC occurrence starts
        reminders TEXT,                            -- JSON list, NULL = defaults
        task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
        displaced_by INTEGER REFERENCES commitments(id),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_commitments_time ON commitments(start, end);
    CREATE INDEX ix_commitments_status ON commitments(status);

    CREATE TABLE commitment_people (
        commitment_id INTEGER NOT NULL REFERENCES commitments(id) ON DELETE CASCADE,
        person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        PRIMARY KEY (commitment_id, person_id)
    );

    CREATE TABLE availability_rules (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('block','avoid','prefer')),
        weekdays TEXT NOT NULL DEFAULT '',          -- comma-separated 0..6, empty = every day
        start_minute INTEGER NOT NULL CHECK (start_minute BETWEEN 0 AND 1440),
        end_minute INTEGER NOT NULL CHECK (end_minute BETWEEN 0 AND 1440),
        applies_to TEXT,
        label TEXT NOT NULL DEFAULT '',
        weight INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (end_minute > start_minute)
    );

    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL                         -- JSON
    );

    -- Reminders fired (dedupe log). Reminder *specs* live on commitments.
    CREATE TABLE reminder_log (
        occurrence_key TEXT NOT NULL,
        fire_at TEXT NOT NULL,
        commitment_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        fired_at TEXT NOT NULL,
        dismissed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (occurrence_key, fire_at)
    );

    -- Planned actions awaiting user confirmation (guardrail layer).
    CREATE TABLE pending_actions (
        token TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        channel TEXT NOT NULL,
        payload TEXT NOT NULL,                      -- JSON request to re-plan at execution
        fingerprint TEXT NOT NULL,
        confirmation TEXT NOT NULL,                 -- confirm | typed
        typed_phrase TEXT,
        summary TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','executed','rejected','expired','superseded')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        resolved_at TEXT
    );

    CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY,
        conversation TEXT NOT NULL DEFAULT 'default',
        role TEXT NOT NULL,                         -- user | assistant | tool | system
        content TEXT NOT NULL,                      -- JSON message
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_chat_conv ON chat_messages(conversation, id);
    """,
    # 2 — public booking requests + email outbox
    """
    CREATE TABLE public_requests (
        id INTEGER PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,                   -- secret token for the requester's status link
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        motive TEXT NOT NULL,
        duration_min INTEGER NOT NULL,
        start TEXT NOT NULL,
        end TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'requested'
            CHECK (status IN ('requested','confirmed','waitlisted','declined','withdrawn')),
        reason TEXT,                                -- decline reason (shared only if share_reason)
        share_reason INTEGER NOT NULL DEFAULT 0,
        commitment_id INTEGER REFERENCES commitments(id) ON DELETE SET NULL,
        client_hash TEXT,                           -- salted hash of the client IP (rate limiting)
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_public_requests_status ON public_requests(status, start);

    CREATE TABLE outbox (
        id INTEGER PRIMARY KEY,
        to_addr TEXT NOT NULL,
        subject TEXT NOT NULL,
        html TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT
    );
    """,
]
