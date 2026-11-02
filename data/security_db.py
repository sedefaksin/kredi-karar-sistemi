"""Local authentication primitives: scrypt passwords and opaque DB-backed sessions."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("KREDI_DATA_DIR", Path(__file__).resolve().parent))
DB_PATH = Path(os.environ.get("KREDI_SECURITY_DB", DATA_DIR / "security.db"))
SESSION_IDLE_SECONDS = 30 * 60
SESSION_LIFETIME_SECONDS = 8 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 8
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
_DUMMY_SALT = bytes.fromhex("cb5f23b95a80075db4409f4d8269cc68")


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _init(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
      user_id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT NOT NULL UNIQUE COLLATE NOCASE,
      display_name TEXT NOT NULL,
      password_hash TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('admin','analyst','reviewer')),
      active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
      created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token_hash TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL REFERENCES users(user_id),
      csrf_hash TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      last_seen INTEGER NOT NULL,
      expires_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    CREATE TABLE IF NOT EXISTS login_attempts (
      attempt_key TEXT PRIMARY KEY,
      window_start INTEGER NOT NULL,
      failures INTEGER NOT NULL,
      locked_until INTEGER NOT NULL DEFAULT 0
    );
    """)


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=64)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def _valid_password(password: str) -> bool:
    return isinstance(password, str) and len(password) >= 12 and len(password) <= 256


def create_user(username: str, display_name: str, password: str, role: str) -> int:
    username = str(username).strip()
    display_name = str(display_name).strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{3,80}", username):
        raise ValueError("Kullanıcı adı 3–80 karakter olmalı; harf, sayı, nokta, @, _ veya - kullanılabilir.")
    if not display_name or len(display_name) > 120:
        raise ValueError("Ad soyad 1–120 karakter olmalı.")
    if role not in {"admin", "analyst", "reviewer"}:
        raise ValueError("Rol admin, analyst veya reviewer olmalı.")
    if not _valid_password(password):
        raise ValueError("Parola 12–256 karakter olmalı.")
    with _connect() as conn:
        _init(conn)
        try:
            cursor = conn.execute(
                "INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)",
                (username, display_name, _password_hash(password), role, int(time.time())),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Bu kullanıcı adı zaten kayıtlı.") from exc
        return int(cursor.lastrowid)


def list_users() -> list[dict]:
    with _connect() as conn:
        _init(conn)
        return [dict(row) for row in conn.execute(
            "SELECT user_id,username,display_name,role,active,created_at FROM users ORDER BY username"
        ).fetchall()]


def set_user_active(username: str, active: bool) -> bool:
    with _connect() as conn:
        _init(conn)
        user = conn.execute("SELECT user_id,role,active FROM users WHERE username=? COLLATE NOCASE",
                            (username.strip(),)).fetchone()
        if not user:
            return False
        if not active and user["role"] == "admin" and user["active"]:
            active_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if active_admins <= 1:
                raise ValueError("Son etkin admin hesabı kapatılamaz.")
        cursor = conn.execute("UPDATE users SET active=? WHERE username=? COLLATE NOCASE",
                              (int(active), username.strip()))
        if not active and cursor.rowcount:
            conn.execute("DELETE FROM sessions WHERE user_id=(SELECT user_id FROM users WHERE username=? COLLATE NOCASE)",
                         (username.strip(),))
        return cursor.rowcount == 1


def set_user_role(username: str, role: str) -> bool:
    if role not in {"admin", "analyst", "reviewer"}:
        raise ValueError("Rol admin, analyst veya reviewer olmalı.")
    with _connect() as conn:
        _init(conn)
        user = conn.execute("SELECT user_id,role,active FROM users WHERE username=? COLLATE NOCASE",
                            (username.strip(),)).fetchone()
        if not user:
            return False
        if user["role"] == "admin" and user["active"] and role != "admin":
            active_admins = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if active_admins <= 1:
                raise ValueError("Son etkin admin hesabı farklı role geçirilemez.")
        conn.execute("UPDATE users SET role=? WHERE user_id=?", (role, user["user_id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["user_id"],))
        return True


def reset_password(username: str, password: str) -> bool:
    if not _valid_password(password):
        raise ValueError("Parola 12–256 karakter olmalı.")
    with _connect() as conn:
        _init(conn)
        user = conn.execute("SELECT user_id FROM users WHERE username=? COLLATE NOCASE",
                            (username.strip(),)).fetchone()
        if not user:
            return False
        conn.execute("UPDATE users SET password_hash=? WHERE user_id=?",
                     (_password_hash(password), user["user_id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["user_id"],))
        return True


def _verify(encoded: str | None, password: str) -> bool:
    if not encoded:
        hashlib.scrypt(password.encode("utf-8"), salt=_DUMMY_SALT, n=SCRYPT_N,
                       r=SCRYPT_R, p=SCRYPT_P, dklen=64)
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if (algorithm != "scrypt" or int(n) != SCRYPT_N or int(r) != SCRYPT_R or int(p) != SCRYPT_P
                or len(bytes.fromhex(salt_hex)) != 16 or len(bytes.fromhex(digest_hex)) != 64):
            return False
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=64)
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (AttributeError, ValueError, TypeError, MemoryError):
        return False


def authenticate(username: str, password: str, client_key: str) -> dict | None:
    """Apply a persistent lockout per username/IP pair and return a safe user record."""
    now = int(time.time())
    normalized = str(username).strip().casefold()[:80]
    attempt_key = hashlib.sha256(f"{normalized}\0{client_key}".encode("utf-8")).hexdigest()
    with _connect() as conn:
        _init(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM login_attempts WHERE window_start < ?", (now - 24 * 60 * 60,))
        row = conn.execute("SELECT * FROM login_attempts WHERE attempt_key=?", (attempt_key,)).fetchone()
        if row and row["locked_until"] > now:
            conn.commit()
            _verify(None, str(password))
            return None
        user = conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1",
                            (normalized,)).fetchone()
        valid = _verify(user["password_hash"] if user else None, str(password))
        if user and valid:
            conn.execute("DELETE FROM login_attempts WHERE attempt_key=?", (attempt_key,))
            conn.commit()
            return {"user_id": user["user_id"], "username": user["username"],
                    "display_name": user["display_name"], "role": user["role"]}
        if row and now - row["window_start"] < LOGIN_WINDOW_SECONDS:
            failures = row["failures"] + 1
            window_start = row["window_start"]
        else:
            failures, window_start = 1, now
        locked_until = now + LOGIN_WINDOW_SECONDS if failures >= LOGIN_MAX_FAILURES else 0
        conn.execute("INSERT INTO login_attempts(attempt_key,window_start,failures,locked_until) VALUES(?,?,?,?) "
                     "ON CONFLICT(attempt_key) DO UPDATE SET window_start=excluded.window_start, "
                     "failures=excluded.failures, locked_until=excluded.locked_until",
                     (attempt_key, window_start, failures, locked_until))
        conn.commit()
    return None


def create_session(user_id: int) -> tuple[str, str]:
    now = int(time.time())
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    csrf_hash = hashlib.sha256(csrf.encode()).hexdigest()
    with _connect() as conn:
        _init(conn)
        conn.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?",
                     (now, now - SESSION_IDLE_SECONDS))
        conn.execute("INSERT INTO sessions(token_hash,user_id,csrf_hash,created_at,last_seen,expires_at) VALUES(?,?,?,?,?,?)",
                     (token_hash, user_id, csrf_hash, now, now, now + SESSION_LIFETIME_SECONDS))
    return token, csrf


def get_session(token: str | None, *, touch: bool = True) -> dict | None:
    if not token or len(token) > 128:
        return None
    now = int(time.time())
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        _init(conn)
        row = conn.execute("""SELECT s.*,u.username,u.display_name,u.role,u.active
          FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE s.token_hash=?""", (digest,)).fetchone()
        if not row or not row["active"] or row["expires_at"] <= now or row["last_seen"] < now - SESSION_IDLE_SECONDS:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
            return None
        if touch:
            conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, digest))
        return {"user_id": row["user_id"], "username": row["username"],
                "display_name": row["display_name"], "role": row["role"],
                "csrf_hash": row["csrf_hash"], "token_hash": digest}


def rotate_csrf(session: dict) -> str:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        _init(conn)
        cursor = conn.execute("UPDATE sessions SET csrf_hash=? WHERE token_hash=?",
                              (digest, session["token_hash"]))
        if cursor.rowcount != 1:
            raise RuntimeError("Session expired")
    return token


def csrf_is_valid(session: dict, candidate: str | None) -> bool:
    if not candidate or len(candidate) > 128:
        return False
    return hmac.compare_digest(session["csrf_hash"], hashlib.sha256(candidate.encode()).hexdigest())


def delete_session(token: str | None) -> None:
    if not token:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        _init(conn)
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))


def user_count() -> int:
    with _connect() as conn:
        _init(conn)
        return int(conn.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0])
