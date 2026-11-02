"""Decrypt one backup into a new directory; never overwrite a live database."""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from backup import AAD, CHUNK_SIZE, MAGIC, encryption_key


def restore(source: Path, output_dir: Path) -> Path:
    key = encryption_key()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / source.name.removesuffix(".enc")
    if destination.exists():
        raise FileExistsError(f"Hedef zaten var, üzerine yazılmadı: {destination}")
    temporary = destination.with_suffix(destination.suffix + ".restoring")
    if temporary.exists():
        raise FileExistsError(f"Geri yükleme geçici hedefi zaten var: {temporary}")
    try:
        with source.open("rb") as src:
            header = src.read(len(MAGIC) + 12)
            if len(header) != len(MAGIC) + 12 or not header.startswith(MAGIC):
                raise ValueError("Yedek dosya biçimi tanınmadı.")
            nonce = header[len(MAGIC):]
            src.seek(0, os.SEEK_END)
            total = src.tell()
            if total < len(header) + 16:
                raise ValueError("Yedek dosya eksik veya bozuk.")
            ciphertext_length = total - len(header) - 16
            src.seek(total - 16)
            tag = src.read(16)
            src.seek(len(header))
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(AAD)
            with temporary.open("xb") as dst:
                remaining = ciphertext_length
                while remaining:
                    block = src.read(min(CHUNK_SIZE, remaining))
                    if not block:
                        raise ValueError("Yedek dosya kesilmiş.")
                    remaining -= len(block)
                    dst.write(decryptor.update(block))
                dst.write(decryptor.finalize())
                dst.flush()
                os.fsync(dst.fileno())
        conn = sqlite3.connect(temporary)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            raise RuntimeError("Geri yüklenen veritabanında integrity_check başarısız.")
        temporary.replace(destination)
        return destination
    except (InvalidTag, OSError, ValueError, RuntimeError):
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Şifreli SQLite yedeğini yeni bir klasöre geri yükle")
    parser.add_argument("backup", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = restore(args.backup, args.output_dir)
    print(f"Geri yükleme tamamlandı ve SQLite kontrolü geçti: {result}")
    print("Bu dosya canlı veritabanının üzerine yazılmadı; dağıtıma almadan önce zincir ve iş akışını doğrulayın.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
