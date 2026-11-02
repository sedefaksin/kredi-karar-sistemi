"""
BUYUK VERITABANI -- 3-5 sahte musteriden gercek bir SQLite veritabanina gecis.

OGRENME NOKTASI: Hackathon jurisine "3 tane hardcoded musterimiz var" demekle
"1000 kayitlik gercek bir veritabanindan sorgu yapiyoruz" demek arasinda
ciddi bir profesyonellik farki var -- ikincisi gercek bankacilik sistemine
baglanmaya hazir bir mimariyi gosterir. Bu script'i bir kez calistirip
data/bank.db dosyasini uretiyoruz; agent'lar artik Python dict'inden degil,
gercek SQL sorgulariyla veri okuyor.

Dagilim bilincli olarak gercekci kurgulandi:
  - Gelirler log-normal dagilima yakin (cok sayida orta gelir, az sayida
    yuksek gelir) -- gercek gelir dagilimlarina benzer sekilde.
  - Kredi gecmisi 0-180 ay arasi uniform.
  - Temerrut olasiligi dusuk (%8) -- gercekci bir portfoyde boyle olur.
"""

import sqlite3
import random
import os
import argparse
from faker import Faker

random.seed(42)  # tekrar uretilebilir sonuclar icin
fake = Faker("tr_TR")
Faker.seed(42)

DATA_DIR = os.environ.get("KREDI_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("KREDI_CUSTOMER_DB", os.path.join(DATA_DIR, "bank.db"))
N_CUSTOMERS = 1000

BRANCHES = ["Alsancak", "Karşıyaka", "Bornova", "Konak"]


def generate_customer(idx: int) -> dict:
    # Gelir: log-normal benzeri dagilim (coğunluk 15-45k, kuyruk 100k+)
    income = round(random.lognormvariate(mu=10.1, sigma=0.45), -2)
    income = max(12000, min(income, 250000))

    credit_history_months = random.randint(0, 180)

    # Borc/gelir orani genelde makul, bazen yuksek
    debt_ratio = random.betavariate(2, 5)  # 0-1 arasi, kucuk degerlere egilimli
    existing_debt = round(income * debt_ratio, -2)

    # Temerrut: %8 ihtimalle 1 veya 2
    past_defaults = 0
    if random.random() < 0.08:
        past_defaults = random.choice([1, 1, 1, 2])

    requested_amount = round(random.uniform(0.5, 8) * income, -2)
    requested_term_months = random.choice([6, 12, 18, 24, 36, 48, 60])

    return {
        "customer_id": f"CUST{idx:04d}",
        "name": fake.name(),
        "branch": random.choice(BRANCHES),
        "monthly_income": income,
        "existing_monthly_debt": existing_debt,
        "credit_history_months": credit_history_months,
        "past_defaults": past_defaults,
        # The sample deliberately leaves these decision-critical fields unknown.
        "requested_monthly_payment": None,
        "requested_monthly_payment_verified": None,
        "defaults_last_24_months": None,
        "eligibility_verified": None,
        "income_document_verified": None,
        "requested_amount": requested_amount,
        "requested_term_months": requested_term_months,
    }


def build_database(force: bool = False):
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    if os.path.exists(DB_PATH):
        if not force:
            raise FileExistsError(f"{DB_PATH} zaten var. Üzerine yazmak için --force kullanın.")
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            branch TEXT NOT NULL,
            monthly_income REAL NOT NULL,
            existing_monthly_debt REAL NOT NULL,
            credit_history_months INTEGER NOT NULL,
            past_defaults INTEGER NOT NULL,
            requested_monthly_payment REAL,
            requested_monthly_payment_verified INTEGER,
            defaults_last_24_months INTEGER,
            eligibility_verified INTEGER,
            income_document_verified INTEGER,
            requested_amount REAL NOT NULL,
            requested_term_months INTEGER NOT NULL
        )
        """
    )

    # Elle kurgulanan 4 "senaryo" musterisi -- demo'da hep ayni sonuclari
    # gormek icin sabit tutuluyor (dusuk / yuksek / orta risk / sinir durum).
    scripted = [
        {
            "customer_id": "CUST0001", "name": "Ayşe Kaya", "branch": "Alsancak",
            "monthly_income": 45000, "existing_monthly_debt": 8000,
            "credit_history_months": 36, "past_defaults": 0,
            "requested_monthly_payment": None, "defaults_last_24_months": None,
            "requested_monthly_payment_verified": None,
            "eligibility_verified": None, "income_document_verified": None,
            "requested_amount": 150000, "requested_term_months": 24,
        },
        {
            "customer_id": "CUST0002", "name": "Mehmet Demir", "branch": "Bornova",
            "monthly_income": 22000, "existing_monthly_debt": 12000,
            "credit_history_months": 8, "past_defaults": 1,
            "requested_monthly_payment": None, "defaults_last_24_months": None,
            "requested_monthly_payment_verified": None,
            "eligibility_verified": None, "income_document_verified": None,
            "requested_amount": 300000, "requested_term_months": 36,
        },
        {
            "customer_id": "CUST0003", "name": "Zeynep Şahin", "branch": "Konak",
            "monthly_income": 60000, "existing_monthly_debt": 5000,
            "credit_history_months": 72, "past_defaults": 0,
            "requested_monthly_payment": None, "defaults_last_24_months": None,
            "requested_monthly_payment_verified": None,
            "eligibility_verified": None, "income_document_verified": None,
            "requested_amount": 100000, "requested_term_months": 12,
        },
        {
            "customer_id": "CUST0004", "name": "Can Öztürk", "branch": "Karşıyaka",
            "monthly_income": 35000, "existing_monthly_debt": 5000,
            "credit_history_months": 18, "past_defaults": 0,
            "requested_monthly_payment": None, "defaults_last_24_months": None,
            "requested_monthly_payment_verified": None,
            "eligibility_verified": None, "income_document_verified": None,
            "requested_amount": 400000, "requested_term_months": 48,
        },
    ]

    rows = scripted + [generate_customer(i) for i in range(5, N_CUSTOMERS + 1)]

    cur.executemany(
        """
        INSERT INTO customers (
            customer_id, name, branch, monthly_income, existing_monthly_debt,
            credit_history_months, past_defaults, requested_monthly_payment,
            requested_monthly_payment_verified, defaults_last_24_months,
            eligibility_verified, income_document_verified,
            requested_amount, requested_term_months
        ) VALUES (
            :customer_id, :name, :branch, :monthly_income, :existing_monthly_debt,
            :credit_history_months, :past_defaults, :requested_monthly_payment,
            :requested_monthly_payment_verified, :defaults_last_24_months,
            :eligibility_verified, :income_document_verified,
            :requested_amount, :requested_term_months
        )
        """,
        rows,
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM customers")
    total = cur.fetchone()[0]
    conn.close()
    print(f"[OK] {total} müşteri data/bank.db içine yazıldı.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentetik müşteri veritabanını oluştur.")
    parser.add_argument("--force", action="store_true", help="Mevcut bank.db dosyasını silip yeniden üret.")
    build_database(force=parser.parse_args().force)
