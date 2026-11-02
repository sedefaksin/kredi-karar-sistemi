"""Create SQLite-consistent, authenticated AES-256-GCM database backups."""
from __future__ import annotations

import argparse
import base64
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from data import audit_db, customer_db, security_db

MAGIC = b"KKSENC1"
AAD = b"KrediKararSistemi encrypted backup v1"
CHUNK_SIZE = 1024 * 1024


def encryption_key() -> bytes:
    encoded = os.environ.get("KREDI_BACKUP_ENCRYPTION_KEY", "")
    if not encoded:
        raise SystemExit("KREDI_BACKUP_ENCRYPTION_KEY ayarlayın; komut satırına gerçek anahtar yazmayın.")
    try:
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SystemExit("Yedek anahtarı geçerli URL-safe Base64 olmalı.") from exc
    if len(key) != 32:
        raise SystemExit("Yedek anahtarı çözüldüğünde tam 32 bayt olmalı.")
    return key


def _integrity_check(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if result != "ok":
        raise RuntimeError(f"SQLite integrity_check başarısız: {path.name}")


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    src = sqlite3.connect(source, timeout=30)
    dest = sqlite3.connect(destination)
    try:
        src.backup(dest, pages=256, sleep=0.05)
        dest.commit()
    finally:
        dest.close()
        src.close()
    _integrity_check(destination)


def _encrypt(source: Path, destination: Path, key: bytes) -> None:
    import secrets
    nonce = secrets.token_bytes(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(AAD)
    created = False
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            dst.write(MAGIC + nonce)
            while block := src.read(CHUNK_SIZE):
                dst.write(encryptor.update(block))
            dst.write(encryptor.finalize())
            dst.write(encryptor.tag)
            dst.flush()
            os.fsync(dst.fileno())
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def create_backups(output_dir: Path) -> list[Path]:
    key = encryption_key()
    chain = audit_db.verify_integrity()
    if not chain["valid"]:
        raise SystemExit("Denetim zinciri doğrulanamadı; yedekleme durduruldu.")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    databases = {
        "customers": Path(customer_db.DB_PATH),
        "audit": Path(audit_db.DB_PATH),
        "security": Path(security_db.DB_PATH),
    }
    results = []
    for name, source in databases.items():
        if not source.exists():
            continue
        fd, temporary_name = tempfile.mkstemp(prefix=f".{name}-", suffix=".sqlite", dir=output_dir)
        os.close(fd)
        temporary = Path(temporary_name)
        destination = output_dir / f"{name}-{timestamp}.sqlite.enc"
        try:
            _sqlite_snapshot(source, temporary)
            _encrypt(temporary, destination, key)
            results.append(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Şifreli Kredi Karar Sistemi veritabanı yedeği oluştur")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(os.environ.get("KREDI_BACKUP_DIR", Path(__file__).resolve().parent / "backups")))
    args = parser.parse_args()
    for path in create_backups(args.output_dir):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
