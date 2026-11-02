"""Local web app: same-origin JSON API and static frontend, standard library only."""
from __future__ import annotations

import json
import ipaddress
import math
import os
import re
import sys
import threading
import time
import unicodedata
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
sys.path.insert(0, str(ROOT))

from agents.auditor_agent import audit_report
from agents.data_gathering_agent import gather_evidence
from agents.report_agent import write_report
from data.audit_db import create_assessment, list_decisions, resolve_assessment, verify_integrity
from data.customer_db import count_customers, get_customer_by_id, list_branches, list_customer_ids
from data import security_db
from llm_client import MODEL_NAME, PROVIDER, has_llm_configured
from tools.tool_definitions import calculate_risk_score, search_customers

MAX_BODY_BYTES = 1_000_000
SESSION_COOKIE = "kredi_session"
APP_ENV = os.environ.get("KREDI_ENV", "development").strip().lower()
PUBLIC_ORIGIN = os.environ.get("KREDI_PUBLIC_ORIGIN", "").rstrip("/")
RATE_LOCK = threading.Lock()
RATE_BUCKETS: dict[str, deque] = defaultdict(deque)


def _rate_allowed(key: str, maximum: int, window_seconds: int) -> bool:
    now = time.monotonic()
    with RATE_LOCK:
        bucket = RATE_BUCKETS[key]
        while bucket and now - bucket[0] >= window_seconds:
            bucket.popleft()
        if len(bucket) >= maximum:
            return False
        bucket.append(now)
        return True


def _safe_user(session: dict) -> dict:
    return {key: session[key] for key in ("user_id", "username", "display_name", "role")}

def _normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return re.sub(r"\s+", " ", "".join(ch for ch in decomposed if not unicodedata.combining(ch)))


def _portfolio_filters(question: str) -> dict:
    q = _normalise(question)
    branches = list_branches()
    branch = next((name for name in branches if _normalise(name) in q), None)
    if any(term in q for term in ("temerrutsuz", "temerrudu olmayan", "temerrut kaydi olmayan")):
        has_defaults = False
    elif any(term in q for term in ("temerrutlu", "temerrut kaydi", "gecikmis borc")):
        has_defaults = True
    else:
        has_defaults = None

    amounts = re.findall(r"\d[\d.,]*", q)
    parsed = []
    for raw in amounts:
        try:
            parsed.append(float(raw.replace(".", "").replace(",", ".")))
        except ValueError:
            continue
    min_income = max_income = None
    if parsed and any(word in q for word in ("ustunde", "fazla", "en az")):
        min_income = parsed[0]
    elif parsed and any(word in q for word in ("altinda", "kadar", "en cok")):
        max_income = parsed[0]
    return {"branch": branch, "has_defaults": has_defaults, "min_income": min_income, "max_income": max_income}


def _evaluate(customer_id: str, demo: bool, collateral_amount: float) -> dict:
    if not demo and not has_llm_configured():
        return {"error": "Gemini API anahtarı ayarlı değil. Canlı açıklama için GEMINI_API_KEY tanımlayın veya örnek veri modunu kullanın."}
    evidence = gather_evidence(customer_id, collateral_amount=collateral_amount)
    if "error" in evidence:
        return {"error": evidence["error"]}
    mode = "demo" if demo else "gemini"
    warning = None
    try:
        report = write_report(evidence, use_gemini=not demo)
    except Exception as exc:
        if not demo:
            mode = "gemini_fallback"
            warning = f"Gemini açıklaması alınamadı; karar alanları deterministik rapordan oluşturuldu ({type(exc).__name__})."
        report = write_report(evidence, use_gemini=False)
    issues = audit_report(evidence, report)
    if issues:
        # Fail closed: never show an unaudited model-authored report as verified.
        report = write_report(evidence, use_gemini=False)
        issues = audit_report(evidence, report)
    if issues:
        return {"error": "Rapor doğrulaması başarısız oldu; sonuç uzman tarafından ayrıca incelenmeli.", "audit_issues": issues}
    decision_id = create_assessment(
        customer_id=customer_id, mode=mode, evidence=evidence, report_consistent=True,
    )
    evidence["decision_id"] = decision_id
    return {"evidence": evidence, "report": report, "warning": warning,
            "audit_issues": [], "decision_id": decision_id, "mode": mode}


def _portfolio(question: str) -> dict:
    filters = _portfolio_filters(question)
    result = search_customers(**filters, limit=1000)
    customers = result["customers"]
    if not customers:
        return {"filters": filters, "count": 0, "customers": [], "metrics": None}
    scored = [
        calculate_risk_score(
            monthly_income=c["monthly_income"],
            existing_monthly_debt=c["existing_monthly_debt"],
            credit_history_months=c["credit_history_months"],
            past_defaults=c["past_defaults"],
            requested_amount=c["requested_amount"],
            requested_monthly_payment=(c.get("requested_monthly_payment")
                if c.get("requested_monthly_payment_verified") in (True, 1) else None),
        )
        for c in customers
    ]
    scores = [item["risk_score"] for item in scored]
    complete_dti_count = sum(item["debt_to_income_complete"] for item in scored)
    defaults = sum(c["past_defaults"] > 0 for c in customers)
    summary_rows = []
    for customer, score in zip(customers, scores):
        summary_rows.append({
            "customer_id": customer["customer_id"], "name": customer["name"], "branch": customer["branch"],
            "monthly_income": customer["monthly_income"], "past_defaults": customer["past_defaults"], "risk_score": score,
        })
    return {
        "filters": filters,
        "count": len(customers),
        "customers": summary_rows,
        "metrics": {
            "average_risk": round(sum(scores) / len(scores), 1),
            "defaulted_customers": defaults,
            "default_rate": round(defaults / len(customers) * 100, 1),
            "average_income": round(sum(c["monthly_income"] for c in customers) / len(customers)),
            "risk_data_complete_count": complete_dti_count,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "KrediKararSistemi"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging client addresses, query strings, headers, or request bodies.
        safe_message = re.sub(r"https?://[^\s]+", "[url]", fmt % args)
        print(f"[{self.log_date_time_string()}] {safe_message}")

    def _headers(self, content_type="application/json; charset=utf-8", status=200, length=None,
                 extra_headers: list[tuple[str, str]] | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if APP_ENV == "production":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, data, status=200, extra_headers: list[tuple[str, str]] | None = None):
        raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._headers(status=status, length=len(raw), extra_headers=extra_headers)
        self.wfile.write(raw)

    def _cookie_token(self) -> str | None:
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
            return jar[SESSION_COOKIE].value if SESSION_COOKIE in jar else None
        except Exception:
            return None

    def _session(self) -> dict | None:
        return security_db.get_session(self._cookie_token())

    def _client_ip(self) -> str:
        remote = self.client_address[0] if self.client_address else "unknown"
        trusted = {item.strip() for item in os.environ.get("KREDI_TRUSTED_PROXY_IPS", "").split(",") if item.strip()}
        if remote not in trusted:
            return remote
        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            return remote

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return False
        parsed = urlparse(origin)
        if APP_ENV == "production":
            return origin.rstrip("/") == PUBLIC_ORIGIN
        host = self.headers.get("Host", "")
        return parsed.scheme == "http" and parsed.netloc.lower() == host.lower()

    def _authorize(self, allowed_roles: set[str]) -> dict | None:
        session = self._session()
        if not session:
            self._json({"error": "Oturum açmanız gerekiyor.", "code": "AUTH_REQUIRED"}, 401)
            return None
        if session["role"] not in allowed_roles:
            self._json({"error": "Bu işlem için yetkiniz yok.", "code": "FORBIDDEN"}, 403)
            return None
        return session

    def _csrf_valid(self, session: dict) -> bool:
        return self._origin_allowed() and security_db.csrf_is_valid(
            session, self.headers.get("X-CSRF-Token"))

    def _body(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("İçerik türü application/json olmalı.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("İstek gövdesi okunamadı.")
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("İstek gövdesi izin verilen boyutu aşıyor.")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Geçerli bir JSON isteği gönderin.")
        if not isinstance(value, dict):
            raise ValueError("İstek gövdesi JSON nesnesi olmalı.")
        return value

    def do_GET(self):
        try:
            self._handle_get()
        except Exception as exc:
            print(f"API error at GET {urlparse(self.path).path}: {type(exc).__name__}")
            try:
                self._json({"error": "İstek işlenemedi. Günlük kaydını kontrol edin."}, 500)
            except OSError:
                pass

    def _handle_get(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            try:
                count_customers()
                if security_db.user_count() == 0:
                    raise RuntimeError("No active users")
                return self._json({"status": "ok"})
            except Exception:
                return self._json({"status": "not_ready"}, 503)
        if path == "/api/session":
            session = self._session()
            if not session:
                return self._json({"authenticated": False})
            try:
                csrf = security_db.rotate_csrf(session)
            except RuntimeError:
                return self._json({"authenticated": False})
            return self._json({"authenticated": True, "user": _safe_user(session), "csrf_token": csrf})
        if path in {"/api/audit", "/api/audit/integrity"}:
            session = self._authorize({"reviewer", "admin"})
            if not session:
                return
            if path == "/api/audit/integrity":
                return self._json(verify_integrity())
            return self._json(list_decisions())
        if path in {"/api/meta", "/api/customers"}:
            session = self._authorize({"analyst", "reviewer", "admin"})
            if not session:
                return
        if path == "/api/meta":
            return self._json({
                "customer_count": count_customers(), "branches": list_branches(),
                "provider": PROVIDER, "model": MODEL_NAME,
                "llm_configured": has_llm_configured(),
            })
        if path == "/api/customers":
            return self._json([{"customer_id": cid, "name": name} for cid, name in list_customer_ids()])
        assets = {"/": (WEB_DIR / "index.html", "text/html; charset=utf-8"),
                  "/styles.css": (WEB_DIR / "styles.css", "text/css; charset=utf-8"),
                  "/app.js": (WEB_DIR / "app.js", "text/javascript; charset=utf-8"),
                  "/favicon.svg": (WEB_DIR / "favicon.svg", "image/svg+xml")}
        if path not in assets:
            return self._json({"error": "Kaynak bulunamadı."}, 404)
        file_path, mime = assets[path]
        try:
            payload = file_path.read_bytes()
        except OSError:
            return self._json({"error": "Arayüz dosyaları bulunamadı."}, 500)
        self._headers(mime, length=len(payload))
        self.wfile.write(payload)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if not self._origin_allowed():
                return self._json({"error": "İstek kaynağı doğrulanamadı."}, 403)
            body = self._body()
            if path == "/api/login":
                client_key = self._client_ip()
                if not _rate_allowed(f"login:{client_key}", 20, 60):
                    return self._json({"error": "Çok fazla giriş denemesi. Bir dakika sonra tekrar deneyin."}, 429)
                username, password = body.get("username"), body.get("password")
                if not isinstance(username, str) or not isinstance(password, str):
                    return self._json({"error": "Kullanıcı adı ve parola gerekli."}, 400)
                if len(username) > 80 or len(password) > 256:
                    return self._json({"error": "Kullanıcı adı veya parola izin verilen uzunluğu aşıyor."}, 400)
                user = security_db.authenticate(username, password, client_key)
                if not user:
                    return self._json({"error": "Kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli."}, 401)
                token, csrf = security_db.create_session(user["user_id"])
                cookie = (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; "
                          f"Max-Age={security_db.SESSION_LIFETIME_SECONDS}")
                if APP_ENV == "production":
                    cookie += "; Secure"
                return self._json({"authenticated": True, "user": user, "csrf_token": csrf},
                                  extra_headers=[("Set-Cookie", cookie)])

            session = self._authorize({"admin", "analyst", "reviewer"})
            if not session:
                return
            if not self._csrf_valid(session):
                return self._json({"error": "İstek doğrulaması başarısız. Sayfayı yenileyip tekrar deneyin."}, 403)
            if path == "/api/logout":
                security_db.delete_session(self._cookie_token())
                cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                if APP_ENV == "production":
                    cookie += "; Secure"
                return self._json({"logged_out": True}, extra_headers=[("Set-Cookie", cookie)])
            if path == "/api/audit" and session["role"] not in {"reviewer", "admin"}:
                return self._json({"error": "Uzman kararları için reviewer veya admin rolü gerekir."}, 403)
            if path in {"/api/evaluate", "/api/portfolio"} and session["role"] not in {"analyst", "reviewer", "admin"}:
                return self._json({"error": "Bu işlem için yetkiniz yok."}, 403)
            if path == "/api/evaluate":
                if not _rate_allowed(f"evaluate:{session['user_id']}", 30, 60):
                    return self._json({"error": "Değerlendirme hız sınırına ulaşıldı. Bir dakika sonra tekrar deneyin."}, 429)
                if not isinstance(body.get("demo", True), bool):
                    return self._json({"error": "demo alanı true veya false olmalı."}, 400)
                cid = str(body.get("customer_id", ""))
                if not cid or not get_customer_by_id(cid):
                    return self._json({"error": "Müşteri kaydı bulunamadı."}, 404)
                collateral = body.get("collateral_amount", 0)
                if isinstance(collateral, bool) or not isinstance(collateral, (int, float)) or not math.isfinite(collateral) or collateral < 0 or collateral > 1_000_000_000_000:
                    return self._json({"error": "Teminat tutarı 0 ile 1.000.000.000.000 arasında olmalı."}, 400)
                result = _evaluate(cid, body.get("demo", True), float(collateral))
                if result.get("error"):
                    return self._json(result, 422)
                return self._json(result)
            if path == "/api/portfolio":
                if not _rate_allowed(f"portfolio:{session['user_id']}", 60, 60):
                    return self._json({"error": "Portföy sorgu hız sınırına ulaşıldı. Bir dakika sonra tekrar deneyin."}, 429)
                question = str(body.get("question", "")).strip()
                if not question or len(question) > 500:
                    return self._json({"error": "1–500 karakter arasında bir portföy sorusu yazın."}, 400)
                return self._json(_portfolio(question))
            if path == "/api/audit":
                decision_id = str(body.get("decision_id", ""))
                decision = str(body.get("decision", ""))
                note = str(body.get("note", "")).strip()
                if decision not in {"ONAYLANDI (insan)", "REDDEDİLDİ (insan)"}:
                    return self._json({"error": "Geçerli bir insan kararı seçin."}, 400)
                if len(note) > 1000:
                    return self._json({"error": "İnceleme notu en fazla 1000 karakter olabilir."}, 400)
                if not decision_id or len(decision_id) > 64:
                    return self._json({"error": "Geçerli bir değerlendirme kimliği gönderin."}, 400)
                saved = resolve_assessment(
                    decision_id, decision, session["display_name"], note,
                    reviewer_user_id=session["user_id"], reviewer_username=session["username"],
                )
                if not saved:
                    return self._json({"error": "Değerlendirme bulunamadı veya daha önce sonuçlandırılmış."}, 409)
                return self._json({"saved": True, "decision_id": decision_id}, 200)
            return self._json({"error": "API uç noktası bulunamadı."}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            print(f"API error at {path}: {type(exc).__name__}")
            return self._json({"error": "İstek işlenemedi. Günlük kaydını kontrol edin."}, 500)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Bound concurrent request threads so bursts cannot spawn unbounded workers."""
    max_workers = 64

    def __init__(self, server_address, request_handler):
        self._worker_slots = threading.BoundedSemaphore(self.max_workers)
        super().__init__(server_address, request_handler)

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


def main():
    if APP_ENV not in {"development", "production"}:
        raise SystemExit("KREDI_ENV development veya production olmalı.")
    if APP_ENV == "production":
        parsed_origin = urlparse(PUBLIC_ORIGIN)
        if parsed_origin.scheme != "https" or not parsed_origin.netloc or parsed_origin.path not in {"", "/"} or parsed_origin.query or parsed_origin.username:
            raise SystemExit("Production için KREDI_PUBLIC_ORIGIN geçerli bir https:// adresi olmalı.")
        if len(os.environ.get("KREDI_AUDIT_HMAC_KEY", "")) < 32:
            raise SystemExit("Production için en az 32 karakterlik KREDI_AUDIT_HMAC_KEY ayarlayın.")
    host = os.environ.get("KREDI_HOST", "127.0.0.1")
    port = int(os.environ.get("KREDI_PORT", "8000"))
    if not 1 <= port <= 65535:
        raise SystemExit("KREDI_PORT 1 ile 65535 arasında olmalı.")
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Güvenlik için sunucu varsayılan olarak yalnızca bu bilgisayara açılır.")
    if security_db.user_count() == 0:
        raise SystemExit("Henüz kullanıcı yok. Önce `python manage_users.py add admin --name \"Yönetici\" --role admin` komutuyla ilk hesabı oluşturun.")
    server = BoundedThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.request_queue_size = 64
    print(f"Kredi Karar Sistemi hazır: http://{host}:{port} ({APP_ENV})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSunucu kapatılıyor…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
