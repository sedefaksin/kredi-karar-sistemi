"""
Gercek veritabani erisim katmani (mock_customers.py'nin yerini alir).

OGRENME NOKTASI: Artik "get_customer_by_id" bir Python dict'ine degil,
gercek bir SQL sorgusuna gidiyor. Boylece:
  1) Veri hacmi (1000 musteri) koddan bagimsiz buyuyebilir
  2) search_customers gibi esnek sorgular yazilabilir (ornegin "riski
     yuksek son 30 basvuruyu getir" gibi bir agent araci eklemek istersen
     buraya bir SQL WHERE kosulu eklemen yeterli)
  3) Gercek bankacilik sistemine gecerken sadece bu dosyayi degistirmen
     yeterli -- ustteki agent/tool kodu HIC degismez. Bu "repository
     pattern" dedigimiz, veri kaynagini is mantigindan izole eden bir
     yazilim tasarim deseni.
"""

import sqlite3
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("KREDI_DATA_DIR", Path(__file__).resolve().parent))
DB_PATH = Path(os.environ.get("KREDI_CUSTOMER_DB", DATA_DIR / "bank.db"))


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row  # satirlara dict gibi erisim (row["name"])
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def get_customer_by_id(customer_id: str) -> dict | None:
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def search_customers(
    branch: str | None = None,
    min_income: float | None = None,
    max_income: float | None = None,
    has_defaults: bool | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Esnek arama araci -- orn. 'Karsiyaka subesindeki temerrutlu musterileri
    getir' gibi bir agent gorevini destekler. Buyuk veritabaninin gercek
    degerini gosteren arac budur: 3-5 sabit musteride bu aracin bir anlami
    olmazdi.
    """
    query = "SELECT * FROM customers WHERE 1=1"
    params = []

    if branch:
        query += " AND branch = ?"
        params.append(branch)
    if min_income is not None:
        query += " AND monthly_income >= ?"
        params.append(min_income)
    if max_income is not None:
        query += " AND monthly_income <= ?"
        params.append(max_income)
    if has_defaults is not None:
        query += " AND past_defaults " + (">" if has_defaults else "=") + " 0"

    query += " LIMIT ?"
    params.append(limit)

    conn = _connect()
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_customers() -> int:
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    conn.close()
    return total


def list_branches() -> list[str]:
    conn = _connect()
    rows = conn.execute("SELECT DISTINCT branch FROM customers ORDER BY branch").fetchall()
    conn.close()
    return [r["branch"] for r in rows]


def list_customer_ids(limit: int = 1000) -> list[str]:
    conn = _connect()
    rows = conn.execute(
        "SELECT customer_id, name FROM customers ORDER BY customer_id LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [(r["customer_id"], r["name"]) for r in rows]
