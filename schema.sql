-- SQLite schema for a learning simulation. All identities and data are fictional.
PRAGMA foreign_keys = ON;

CREATE TABLE actors (
    actor_id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('requester', 'steward', 'approver'))
);
INSERT INTO actors VALUES
    ('alice', 'requester'), ('bob', 'steward'),
    ('carol', 'approver'), ('diana', 'approver');

CREATE TABLE source_records (
    source_system TEXT NOT NULL,
    source_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (source_system, source_id)
);

CREATE TABLE suppliers (
    supplier_id TEXT PRIMARY KEY,
    supplier_name TEXT NOT NULL,
    country TEXT NOT NULL,
    tax_id TEXT NOT NULL,
    payment_terms TEXT NOT NULL,
    bank_account TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    UNIQUE (country, tax_id)
);

CREATE TABLE source_mapping (
    source_system TEXT NOT NULL,
    source_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL REFERENCES suppliers,
    reviewed_by TEXT NOT NULL REFERENCES actors,
    decision_note TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    PRIMARY KEY (source_system, source_id),
    FOREIGN KEY (source_system, source_id) REFERENCES source_records
);

CREATE TABLE requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('CREATE', 'UPDATE')),
    supplier_id TEXT NOT NULL,
    requester TEXT NOT NULL REFERENCES actors,
    reason TEXT NOT NULL,
    verification_ref TEXT NOT NULL,
    payload TEXT NOT NULL,
    before_payload TEXT NOT NULL,
    expected_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('SUBMITTED', 'VALIDATION_FAILED', 'VALIDATED', 'APPROVED', 'REJECTED', 'APPLIED')),
    validator TEXT REFERENCES actors,
    approver TEXT REFERENCES actors,
    decision_note TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    applied_at TEXT,
    CHECK (approver IS NULL OR approver != requester),
    CHECK (approver IS NULL OR approver != validator)
);

CREATE TABLE validation_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests,
    rule_id TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    detail TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    UNIQUE (request_id, rule_id)
);

CREATE TABLE erp_snapshot (
    supplier_id TEXT PRIMARY KEY REFERENCES suppliers,
    payload TEXT NOT NULL,
    version INTEGER NOT NULL,
    synced_at TEXT NOT NULL
);

CREATE TABLE sync_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id TEXT NOT NULL REFERENCES suppliers,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    detail TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);

CREATE TABLE issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES requests,
    rule_id TEXT NOT NULL,
    owner TEXT NOT NULL REFERENCES actors,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'INVESTIGATING', 'REMEDIATED', 'CLOSED')),
    root_cause TEXT,
    remediation TEXT,
    replacement_request_id INTEGER REFERENCES requests,
    retested_by TEXT REFERENCES actors,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    CHECK (retested_by IS NULL OR retested_by != owner)
);

CREATE TABLE audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    actor TEXT NOT NULL REFERENCES actors,
    action TEXT NOT NULL,
    object_ref TEXT NOT NULL,
    detail TEXT NOT NULL
);
-- These guard against accidental SQL updates, not a database owner's tampering.
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'Audit events are append-only'); END;

-- A submitted proposal is immutable. Corrections require a new request.
CREATE TRIGGER request_no_rewrite
BEFORE UPDATE OF kind, supplier_id, requester, reason, verification_ref,
                 payload, before_payload, expected_version ON requests
BEGIN SELECT RAISE(ABORT, 'Create a new request to revise a proposal'); END;
