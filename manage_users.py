"""Manage named application users without placing passwords on the command line."""
from __future__ import annotations

import argparse
from getpass import getpass

from data.security_db import create_user, list_users, reset_password, set_user_active, set_user_role


def main() -> int:
    parser = argparse.ArgumentParser(description="Kredi Karar Sistemi hesap yönetimi")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="Yeni hesap oluştur")
    add.add_argument("username")
    add.add_argument("--name", required=True, help="Ekranda gösterilecek ad soyad")
    add.add_argument("--role", required=True, choices=("admin", "analyst", "reviewer"))
    disable = sub.add_parser("disable", help="Hesabı ve açık oturumlarını kapat")
    disable.add_argument("username")
    enable = sub.add_parser("enable", help="Hesabı yeniden etkinleştir")
    enable.add_argument("username")
    role = sub.add_parser("set-role", help="Kullanıcı rolünü değiştir ve mevcut oturumlarını kapat")
    role.add_argument("username")
    role.add_argument("role", choices=("admin", "analyst", "reviewer"))
    password_reset = sub.add_parser("reset-password", help="Yeni parola belirle ve mevcut oturumları kapat")
    password_reset.add_argument("username")
    sub.add_parser("list", help="Hesapları ve rollerini listele")
    args = parser.parse_args()

    try:
        if args.command == "add":
            password = getpass("Parola (en az 12 karakter): ")
            confirmation = getpass("Parolayı tekrar girin: ")
            if password != confirmation:
                raise ValueError("Parolalar eşleşmiyor.")
            user_id = create_user(args.username, args.name, password, args.role)
            print(f"Hesap oluşturuldu: {args.username} ({args.role}, kullanıcı no {user_id})")
        elif args.command in {"disable", "enable"}:
            updated = set_user_active(args.username, args.command == "enable")
            if not updated:
                raise ValueError("Kullanıcı bulunamadı.")
            print(f"Hesap {'etkinleştirildi' if args.command == 'enable' else 'devre dışı bırakıldı'}: {args.username}")
        elif args.command == "set-role":
            if not set_user_role(args.username, args.role):
                raise ValueError("Kullanıcı bulunamadı.")
            print(f"Rol güncellendi ve açık oturumlar kapatıldı: {args.username} ({args.role})")
        elif args.command == "reset-password":
            password = getpass("Yeni parola (en az 12 karakter): ")
            confirmation = getpass("Yeni parolayı tekrar girin: ")
            if password != confirmation:
                raise ValueError("Parolalar eşleşmiyor.")
            if not reset_password(args.username, password):
                raise ValueError("Kullanıcı bulunamadı.")
            print(f"Parola güncellendi ve açık oturumlar kapatıldı: {args.username}")
        else:
            for user in list_users():
                status = "etkin" if user["active"] else "devre dışı"
                print(f"{user['username']}\t{user['display_name']}\t{user['role']}\t{status}")
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
