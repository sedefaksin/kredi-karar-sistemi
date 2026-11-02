"""Offline integration tests for deterministic decisions and the local HTTP flow."""
from __future__ import annotations

import json
import sqlite3
import base64
import threading
import http.cookiejar
from types import SimpleNamespace
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agents.auditor_agent import audit_report
from agents.data_gathering_agent import gather_evidence
from agents.report_agent import write_report
from app import Handler
from data import audit_db
from data import security_db
from backup import create_backups
from restore_backup import restore

_API_CLIENTS = {}
@pytest.fixture
def local_api(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_db, "DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(security_db, "DB_PATH", tmp_path / "security.db")
    security_db.create_user("reviewer", "Test Uzmanı", "strong-test-password-123", "reviewer")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.request.Request(base + "/api/login",
        data=json.dumps({"username": "reviewer", "password": "strong-test-password-123"}).encode(),
        method="POST", headers={"Content-Type": "application/json", "Origin": base})
    with opener.open(login, timeout=5) as response:
        csrf = json.loads(response.read().decode())["csrf_token"]
    _API_CLIENTS[base] = {"opener": opener, "csrf": csrf}
    yield base
    _API_CLIENTS.pop(base, None)
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request_json(url, method="GET", payload=None):
    base = url.split("/api/", 1)[0]
    client = _API_CLIENTS.get(base)
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if client:
        headers["Origin"] = base
        if method.upper() != "GET": headers["X-CSRF-Token"] = client["csrf"]
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        opener = client["opener"] if client else urllib.request.build_opener()
        with opener.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_policy_is_deterministic_and_missing_data_fails_closed():
    evidence = gather_evidence("CUST0001")
    policy = evidence["check_policy_compliance"]
    assert evidence["calculate_risk_score"]["risk_score"] == 17.3
    assert policy["review_required"] is True
    assert policy["recommended_decision"] == "UZMAN İNCELEMESİ"
    assert any(check["status"] == "unknown" for check in policy["checks"])
    assert policy["eligible_for_auto_approval"] is False


def test_report_auditor_rejects_mutated_evidence_fields():
    evidence = gather_evidence("CUST0001")
    report = write_report(evidence)
    assert audit_report(evidence, report) == []
    tampered = report.replace("RISK SKORU: 17.3", "RISK SKORU: 1.0")
    assert any("Risk skoru" in issue for issue in audit_report(evidence, tampered))


def test_gemini_only_receives_minimal_nonidentifying_explanation_context():
    evidence = gather_evidence("CUST0001")
    class FakeModels:
        contents = ""
        def generate_content(self, **kwargs):
            self.contents = kwargs["contents"]
            return SimpleNamespace(text="Veri eksikleri nedeniyle uzman incelemesi gerekir.")
    fake_client = SimpleNamespace(models=FakeModels())
    report = write_report(evidence, use_gemini=True, client=fake_client)
    assert audit_report(evidence, report) == []
    assert "Ayşe Kaya" not in fake_client.models.contents
    assert "CUST0001" not in fake_client.models.contents
    assert "150000" not in fake_client.models.contents


def test_http_evaluation_persists_and_resolves_same_server_record(local_api):
    status, result = request_json(local_api + "/api/evaluate", "POST", {
        "customer_id": "CUST0001", "demo": True, "collateral_amount": 0,
    })
    assert status == 200
    assert result["mode"] == "demo"
    assert result["evidence"]["decision_id"] == result["decision_id"]
    assert result["audit_issues"] == []

    status, rows = request_json(local_api + "/api/audit")
    assert status == 200 and len(rows) == 1
    assert rows[0]["decision_id"] == result["decision_id"]
    assert rows[0]["recommendation"] == "UZMAN İNCELEMESİ"
    assert rows[0]["final_decision"] is None

    status, saved = request_json(local_api + "/api/audit", "POST", {
        "decision_id": result["decision_id"], "decision": "ONAYLANDI (insan)",
        "reviewer": "Test Uzmanı", "note": "Test inceleme notu",
    })
    assert status == 200 and saved["saved"] is True

    status, rows = request_json(local_api + "/api/audit")
    assert status == 200 and rows[0]["final_decision"] == "ONAYLANDI (insan)"
    assert rows[0]["reviewer_username"] == "reviewer"
    status, _ = request_json(local_api + "/api/audit", "POST", {
        "decision_id": result["decision_id"], "decision": "REDDEDİLDİ (insan)",
        "reviewer": "Test Uzmanı", "note": "İkinci kez yazılmamalı",
    })
    assert status == 409
    status, integrity = request_json(local_api + "/api/audit/integrity")
    assert status == 200 and integrity["valid"] is True


def test_live_mode_requires_gemini_key_and_demo_flag_is_typed(local_api, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    status, result = request_json(local_api + "/api/evaluate", "POST", {
        "customer_id": "CUST0001", "demo": False, "collateral_amount": 0,
    })
    assert status == 422 and "GEMINI_API_KEY" in result["error"]
    status, result = request_json(local_api + "/api/evaluate", "POST", {
        "customer_id": "CUST0001", "demo": "false", "collateral_amount": 0,
    })
    assert status == 400 and "true veya false" in result["error"]


def test_portfolio_results_mark_incomplete_risk_inputs(local_api):
    status, result = request_json(local_api + "/api/portfolio", "POST", {
        "question": "Karşıyaka'daki temerrütlü müşterilerin ortalama riski nedir?",
    })
    assert status == 200 and result["count"] > 0
    assert result["filters"]["branch"] == "Karşıyaka"
    assert result["filters"]["has_defaults"] is True
    assert result["metrics"]["risk_data_complete_count"] == 0


def test_anonymous_access_csrf_and_role_boundaries(local_api, tmp_path):
    anonymous = urllib.request.build_opener()
    request = urllib.request.Request(local_api + "/api/customers")
    with pytest.raises(urllib.error.HTTPError) as unauth:
        anonymous.open(request, timeout=5)
    assert unauth.value.code == 401

    reviewer_client = _API_CLIENTS[local_api]
    no_csrf = urllib.request.Request(local_api + "/api/portfolio",
        data=json.dumps({"question": "Karşıyaka müşterileri"}).encode(), method="POST",
        headers={"Content-Type": "application/json", "Origin": local_api})
    with pytest.raises(urllib.error.HTTPError) as csrf_error:
        reviewer_client["opener"].open(no_csrf, timeout=5)
    assert csrf_error.value.code == 403

    security_db.create_user("analyst1", "Analist Kullanıcı", "another-strong-password-456", "analyst")
    analyst = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    login = urllib.request.Request(local_api + "/api/login",
        data=json.dumps({"username": "analyst1", "password": "another-strong-password-456"}).encode(),
        method="POST", headers={"Content-Type": "application/json", "Origin": local_api})
    with analyst.open(login, timeout=5) as response:
        csrf = json.loads(response.read().decode())["csrf_token"]
    audit_request = urllib.request.Request(local_api + "/api/audit")
    with pytest.raises(urllib.error.HTTPError) as role_error:
        analyst.open(audit_request, timeout=5)
    assert role_error.value.code == 403
    assert csrf


def test_audit_chain_detects_database_row_tampering(local_api):
    status, result = request_json(local_api + "/api/evaluate", "POST", {
        "customer_id": "CUST0001", "demo": True, "collateral_amount": 0,
    })
    assert status == 200
    with sqlite3.connect(audit_db.DB_PATH) as conn:
        conn.execute("UPDATE decisions SET risk_score=1 WHERE decision_id=?", (result["decision_id"],))
    assert audit_db.verify_integrity()["valid"] is False


def test_backup_is_encrypted_and_restore_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_db, "DB_PATH", tmp_path / "audit.db")
    monkeypatch.setattr(security_db, "DB_PATH", tmp_path / "security.db")
    security_db.create_user("backupuser", "Yedek Test", "backup-test-password-123", "admin")
    monkeypatch.setenv("KREDI_BACKUP_ENCRYPTION_KEY",
                       base64.urlsafe_b64encode(b"b" * 32).decode("ascii"))
    paths = create_backups(tmp_path / "backups")
    assert len(paths) == 3
    assert all(path.read_bytes().startswith(b"KKSENC1") for path in paths)
    restored = restore(paths[0], tmp_path / "restored")
    assert restored.exists()
    damaged = tmp_path / "damaged.sqlite.enc"
    data = bytearray(paths[1].read_bytes())
    data[-1] ^= 0x01
    damaged.write_bytes(data)
    with pytest.raises(Exception):
        restore(damaged, tmp_path / "damaged-restore")
    assert not list((tmp_path / "damaged-restore").glob("*.sqlite"))


def test_user_management_protects_last_admin_and_revokes_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(security_db, "DB_PATH", tmp_path / "security.db")
    admin_id = security_db.create_user("rootadmin", "Ana Yönetici", "first-admin-password-123", "admin")
    token, _ = security_db.create_session(admin_id)
    with pytest.raises(ValueError, match="Son etkin admin"):
        security_db.set_user_active("rootadmin", False)
    with pytest.raises(ValueError, match="Son etkin admin"):
        security_db.set_user_role("rootadmin", "reviewer")
    assert security_db.get_session(token) is not None
    assert security_db.reset_password("rootadmin", "replacement-password-456")
    assert security_db.get_session(token) is None
    assert security_db.authenticate("rootadmin", "replacement-password-456", "127.0.0.1") is not None
    assert security_db.authenticate("rootadmin", "first-admin-password-123", "127.0.0.1") is None
