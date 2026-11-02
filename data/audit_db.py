"""Transactional, append-oriented local decision audit storage."""
from __future__ import annotations

import json
import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("KREDI_DATA_DIR", Path(__file__).resolve().parent))
DB_PATH = Path(os.environ.get("KREDI_AUDIT_DB", DATA_DIR / "audit.db"))


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def _session():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        customer_id TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('demo', 'gemini', 'gemini_fallback')),
        risk_score REAL NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
        recommendation TEXT NOT NULL,
        reasons_json TEXT NOT NULL,
        checks_json TEXT NOT NULL,
        report_consistent INTEGER NOT NULL CHECK (report_consistent IN (0, 1)),
        final_decision TEXT,
        reviewer TEXT,
        reviewer_user_id INTEGER,
        reviewer_username TEXT,
        note TEXT,
        resolved_at TEXT
    )""")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(decisions)").fetchall()}
    if "reviewer_user_id" not in columns:
        conn.execute("ALTER TABLE decisions ADD COLUMN reviewer_user_id INTEGER")
    if "reviewer_username" not in columns:
        conn.execute("ALTER TABLE decisions ADD COLUMN reviewer_username TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_events (
        event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        decision_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        previous_hash TEXT NOT NULL,
        event_hash TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_chain_meta (
        meta_key TEXT PRIMARY KEY,
        meta_value TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_decision ON audit_events(decision_id)")
    conn.executescript("""
    CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
      BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
      BEGIN SELECT RAISE(ABORT, 'audit events are immutable'); END;
    """)
    conn.commit()
    # Baseline old local logs exactly once; missing events after initialization are corruption.
    conn.execute("BEGIN IMMEDIATE")
    initialized = conn.execute(
        "INSERT OR IGNORE INTO audit_chain_meta(meta_key,meta_value) VALUES('baseline','complete')"
    ).rowcount == 1
    if initialized:
        legacy = conn.execute("SELECT * FROM decisions ORDER BY created_at, decision_id").fetchall()
        for row in legacy:
            _append_event(conn, row["decision_id"], "assessment.legacy_baseline", _snapshot(row))
    conn.commit()


def _signing_key() -> bytes:
    configured = os.environ.get("KREDI_AUDIT_HMAC_KEY")
    if configured:
        return configured.encode("utf-8")
    if os.environ.get("KREDI_ENV", "development").lower() == "production":
        raise RuntimeError("Production audit signing key is missing")
    key_path = DB_PATH.with_suffix(".hmac-key")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return key_path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        try:
            with key_path.open("xb") as handle:
                handle.write(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass
            return key
        except FileExistsError:
            return key_path.read_bytes()


def _snapshot(row) -> dict:
    return {
        "decision_id": row["decision_id"], "created_at": row["created_at"],
        "customer_id": row["customer_id"], "mode": row["mode"],
        "risk_score": row["risk_score"], "recommendation": row["recommendation"],
        "reasons": json.loads(row["reasons_json"]), "checks": json.loads(row["checks_json"]),
        "report_consistent": int(row["report_consistent"]),
        "final_decision": row["final_decision"], "reviewer": row["reviewer"],
        "reviewer_user_id": row["reviewer_user_id"], "reviewer_username": row["reviewer_username"],
        "note": row["note"], "resolved_at": row["resolved_at"],
    }


def _append_event(conn, decision_id: str, event_type: str, payload: dict) -> None:
    last = conn.execute("SELECT event_hash FROM audit_events ORDER BY event_seq DESC LIMIT 1").fetchone()
    previous = last["event_hash"] if last else "0" * 64
    event_id = str(uuid.uuid4())
    occurred_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signed = json.dumps({"event_id": event_id, "decision_id": decision_id,
                         "event_type": event_type, "occurred_at": occurred_at,
                         "payload": json.loads(payload_json), "previous_hash": previous},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    event_hash = hmac.new(_signing_key(), signed, hashlib.sha256).hexdigest()
    conn.execute("""INSERT INTO audit_events
      (event_id,decision_id,event_type,occurred_at,payload_json,previous_hash,event_hash)
      VALUES(?,?,?,?,?,?,?)""",
      (event_id, decision_id, event_type, occurred_at, payload_json, previous, event_hash))


def _verify(conn) -> dict:
    key = _signing_key()
    rows = conn.execute("SELECT * FROM audit_events ORDER BY event_seq").fetchall()
    previous = "0" * 64
    last_state = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
            signed = json.dumps({"event_id": row["event_id"], "decision_id": row["decision_id"],
                                 "event_type": row["event_type"], "occurred_at": row["occurred_at"],
                                 "payload": payload, "previous_hash": previous},
                                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected = hmac.new(key, signed, hashlib.sha256).hexdigest()
        except (ValueError, TypeError, KeyError):
            return {"valid": False, "event_count": len(rows)}
        if row["previous_hash"] != previous or not hmac.compare_digest(row["event_hash"], expected):
            return {"valid": False, "event_count": len(rows)}
        previous = row["event_hash"]
        last_state[row["decision_id"]] = payload
    decisions = conn.execute("SELECT * FROM decisions").fetchall()
    if len(last_state) != len(decisions):
        return {"valid": False, "event_count": len(rows)}
    for decision in decisions:
        snapshot = last_state.get(decision["decision_id"])
        if snapshot is None or snapshot != _snapshot(decision):
            return {"valid": False, "event_count": len(rows)}
    return {"valid": True, "event_count": len(rows), "head": previous}


def verify_integrity() -> dict:
    with _session() as conn:
        _init(conn)
        return _verify(conn)


def create_assessment(customer_id: str, mode: str, evidence: dict, report_consistent: bool) -> str:
    decision_id = str(uuid.uuid4())
    policy = evidence["check_policy_compliance"]
    risk = evidence["calculate_risk_score"]["risk_score"]
    with _session() as conn:
        _init(conn)
        if not _verify(conn)["valid"]:
            raise RuntimeError("Audit log integrity check failed")
        conn.execute(
            """INSERT INTO decisions
            (decision_id, created_at, customer_id, mode, risk_score, recommendation,
             reasons_json, checks_json, report_consistent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision_id, datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
             customer_id, mode, risk, policy["recommended_decision"],
             json.dumps(policy["reasons_against"], ensure_ascii=False),
             json.dumps(policy["checks"], ensure_ascii=False), int(report_consistent)),
        )
        row = conn.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        _append_event(conn, decision_id, "assessment.created", _snapshot(row))
        conn.commit()
    return decision_id


def resolve_assessment(decision_id: str, decision: str, reviewer: str, note: str,
                       reviewer_user_id: int | None = None,
                       reviewer_username: str | None = None) -> bool:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    with _session() as conn:
        _init(conn)
        if not _verify(conn)["valid"]:
            raise RuntimeError("Audit log integrity check failed")
        cursor = conn.execute(
            """UPDATE decisions SET final_decision=?, reviewer=?, reviewer_user_id=?,
            reviewer_username=?, note=?, resolved_at=?
            WHERE decision_id=? AND final_decision IS NULL""",
            (decision, reviewer, reviewer_user_id, reviewer_username, note, now, decision_id),
        )
        if cursor.rowcount == 1:
            row = conn.execute("SELECT * FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
            _append_event(conn, decision_id, "assessment.resolved", _snapshot(row))
        conn.commit()
        return cursor.rowcount == 1


def list_decisions(limit: int = 500) -> list[dict]:
    with _session() as conn:
        _init(conn)
        if not _verify(conn)["valid"]:
            raise RuntimeError("Audit log integrity check failed")
        rows = conn.execute("SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["reasons"] = json.loads(item.pop("reasons_json"))
        item["checks"] = json.loads(item.pop("checks_json"))
        item["report_consistent"] = bool(item["report_consistent"])
        result.append(item)
    return result
