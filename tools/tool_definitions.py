"""Deterministic risk, policy and portfolio operations.

Gemini may explain these results, but it cannot calculate or change them.
Unknown decision-critical inputs fail closed and require a human review.
"""
from __future__ import annotations

import math

from data.customer_db import get_customer_by_id, search_customers as _search_customers_db


POLICY_TEXT = {
    "risk": ("03-otomatik-onay-ve-insan-devri.md", "Madde 1 — Otomatik Onay Koşulları",
             "Onay önerisi için risk skoru 25'in altında olmalıdır; nihai kararı uzman verir."),
    "debt": ("01-kredi-basvuru-kosullari.md", "Madde 1 — Borç/Gelir Oranı Limiti",
             "Toplam aylık borç ödemeleri, yeni kredi taksiti dahil, net gelirin %50'sini aşamaz."),
    "history": ("03-otomatik-onay-ve-insan-devri.md", "Madde 1 — Otomatik Onay Koşulları",
                "Otomatik onay için kredi geçmişi en az 12 ay olmalıdır."),
    "defaults": ("03-otomatik-onay-ve-insan-devri.md", "Madde 2 — Temerrüt Geçmişi ve İnsan Devri",
                 "Son 24 ayda temerrüt kaydı bulunan başvurular insan incelemesine yönlendirilir; sistem otomatik onay veya nihai ret veremez."),
    "income_cap": ("01-kredi-basvuru-kosullari.md", "Madde 2 — Kredi Geçmişi Şartı",
                   "12 aydan kısa geçmişte tutar aylık gelirin 10 katını; 12 ay ve üzeri geçmişte 20 katını aşamaz."),
    "term": ("02-faiz-ve-vade-tablosu.md", "Madde 2 — Azami Vade",
             "Tutar aylık gelirin 5 katını aşarsa azami vade 36 aydır; diğer başvurularda 60 aydır."),
    "collateral": ("04-risk-ve-teminat.md", "Madde 2 — Teminat Gereksinimi",
                   "Tutar aylık gelirin 6 katını aşarsa kredi tutarının en az %20'si kadar teminat gerekir."),
    "eligibility": ("01-kredi-basvuru-kosullari.md", "Madde 3–4 — Başvuru ve Uygunluk",
                    "Kimlik, gelir belgesi, yaş ve vatandaşlık/oturma izni uygunluğu doğrulanmalıdır."),
}


def _finite_nonnegative(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} sayısal ve sıfırdan küçük olmayan bir değer olmalı.")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} geçerli ve sıfırdan küçük olmayan bir değer olmalı.")
    return value


def get_customer_data(customer_id: str) -> dict:
    customer = get_customer_by_id(customer_id)
    return customer if customer is not None else {"error": f"{customer_id} bulunamadı"}


def calculate_risk_score(
    monthly_income: float,
    existing_monthly_debt: float,
    credit_history_months: float,
    past_defaults: float,
    requested_amount: float,
    requested_monthly_payment: float | None = None,
) -> dict:
    monthly_income = _finite_nonnegative("Aylık gelir", monthly_income)
    existing_monthly_debt = _finite_nonnegative("Mevcut aylık borç", existing_monthly_debt)
    credit_history_months = _finite_nonnegative("Kredi geçmişi", credit_history_months)
    past_defaults = _finite_nonnegative("Temerrüt sayısı", past_defaults)
    requested_amount = _finite_nonnegative("Talep edilen tutar", requested_amount)
    if monthly_income <= 0:
        raise ValueError("Aylık gelir sıfırdan büyük olmalı.")

    payment_known = requested_monthly_payment is not None
    payment = _finite_nonnegative("Yeni kredi aylık taksiti", requested_monthly_payment) if payment_known else 0
    debt_to_income = (existing_monthly_debt + payment) / monthly_income
    loan_to_annual_income = requested_amount / (monthly_income * 12)
    score = (
        min(debt_to_income * 100, 40)
        + min(loan_to_annual_income * 20, 30)
        + past_defaults * 20
        - min(credit_history_months / 12, 5) * 2
    )
    score = max(0, min(100, round(score, 1)))
    return {
        "risk_score": score,
        "debt_to_income_ratio": round(debt_to_income, 4),
        "debt_to_income_complete": payment_known,
        "loan_to_income_ratio": round(requested_amount / monthly_income, 4),
        "risk_level": "yüksek" if score >= 50 else "orta" if score >= 25 else "düşük",
    }


def check_policy_compliance(
    risk_score: float,
    debt_to_income_ratio: float,
    credit_history_months: float,
    past_defaults: float,
    monthly_income: float = 0,
    requested_amount: float = 0,
    collateral_amount: float = 0,
    requested_term_months: int | None = None,
    requested_monthly_payment: float | None = None,
    defaults_last_24_months: int | None = None,
    eligibility_verified: bool | None = None,
    income_document_verified: bool | None = None,
) -> dict:
    """Return a conservative, explainable policy recommendation, never a final credit decision."""
    risk_score = _finite_nonnegative("Risk skoru", risk_score)
    debt_to_income_ratio = _finite_nonnegative("Borç/gelir oranı", debt_to_income_ratio)
    credit_history_months = _finite_nonnegative("Kredi geçmişi", credit_history_months)
    past_defaults = _finite_nonnegative("Temerrüt sayısı", past_defaults)
    monthly_income = _finite_nonnegative("Aylık gelir", monthly_income)
    requested_amount = _finite_nonnegative("Talep edilen tutar", requested_amount)
    collateral_amount = _finite_nonnegative("Beyan edilen teminat", collateral_amount)
    if monthly_income <= 0:
        raise ValueError("Aylık gelir sıfırdan büyük olmalı.")
    if defaults_last_24_months is not None:
        if isinstance(defaults_last_24_months, bool) or not isinstance(defaults_last_24_months, int) or defaults_last_24_months < 0:
            raise ValueError("Son 24 ay temerrüt sayısı sıfır veya pozitif tam sayı olmalı.")
    if requested_term_months is not None:
        if isinstance(requested_term_months, bool) or not isinstance(requested_term_months, int) or requested_term_months <= 0:
            raise ValueError("Talep edilen vade pozitif tam sayı olmalı.")

    eligibility_ok = eligibility_verified is True or (type(eligibility_verified) is int and eligibility_verified == 1)
    eligibility_failed = eligibility_verified is False or (type(eligibility_verified) is int and eligibility_verified == 0)
    income_document_ok = income_document_verified is True or (type(income_document_verified) is int and income_document_verified == 1)
    income_document_failed = income_document_verified is False or (type(income_document_verified) is int and income_document_verified == 0)

    income_multiple = requested_amount / monthly_income
    income_cap_multiple = 10 if credit_history_months < 12 else 20
    collateral_required = requested_amount > monthly_income * 6
    required_collateral = requested_amount * 0.20 if collateral_required else 0

    def check(key: str, label: str, status: str, detail: str) -> dict:
        filename, section, text = POLICY_TEXT[key]
        return {"key": key, "label": label, "status": status, "detail": detail,
                "source_file": filename, "source_section": section, "source_text": text}

    checks = [
        check("risk", "Risk skoru 25'in altında", "pass" if risk_score < 25 else "fail", f"{risk_score:.1f}/100"),
        check("debt", "Yeni taksit dahil toplam borç/gelir en fazla %50", "unknown" if requested_monthly_payment is None else ("pass" if debt_to_income_ratio <= .5 else "fail"),
              "Yeni kredi taksiti verisi yok" if requested_monthly_payment is None else f"%{debt_to_income_ratio * 100:.1f}"),
        check("history", "Kredi geçmişi en az 12 ay", "pass" if credit_history_months >= 12 else "fail", f"{credit_history_months:g} ay"),
        check("defaults", "Son 24 ayda temerrüt kaydı yok", "pass" if (defaults_last_24_months == 0 or (defaults_last_24_months is None and past_defaults == 0)) else ("fail" if defaults_last_24_months and defaults_last_24_months > 0 else "unknown"),
              "Kayıt yok" if (defaults_last_24_months == 0 or (defaults_last_24_months is None and past_defaults == 0)) else (f"{defaults_last_24_months} kayıt" if defaults_last_24_months is not None else "Tarih alanı bulunmuyor")),
        check("income_cap", f"Talep tutarı gelir tavanı içinde ({income_cap_multiple}×)", "pass" if income_multiple <= income_cap_multiple else "fail", f"{income_multiple:.2f}×"),
        check("term", "Vade sınırı uygun", "unknown" if requested_term_months is None else ("fail" if (income_multiple > 5 and requested_term_months > 36) or requested_term_months > 60 else "pass"),
              "Vade verisi yok" if requested_term_months is None else f"{requested_term_months} ay"),
        check("eligibility", "Kimlik/yaş/ikamet uygunluğu doğrulandı", "pass" if eligibility_ok else "fail" if eligibility_failed else "unknown",
              "Doğrulandı" if eligibility_ok else "Uygunluk doğrulanmadı" if eligibility_failed else "Veri tabanında doğrulama kaydı yok"),
        check("eligibility", "Gelir belgesi doğrulandı", "pass" if income_document_ok else "fail" if income_document_failed else "unknown",
              "Doğrulandı" if income_document_ok else "Belge doğrulaması başarısız" if income_document_failed else "Veri tabanında doğrulama kaydı yok"),
    ]
    if collateral_required:
        declared_status = "unknown" if collateral_amount >= required_collateral else "fail"
        checks.append(check("collateral", "Gerekli teminat mevcut ve doğrulanmış", declared_status,
                            f"Gerekli {required_collateral:,.0f} ₺ · beyan {collateral_amount:,.0f} ₺; uzman doğrulaması gerekli"))
    else:
        checks.append(check("collateral", "Teminat eşiği aşılmadı", "pass", "Gerekli değil"))

    reasons = [f"{item['label']}: {item['detail']}" for item in checks if item["status"] in {"fail", "unknown"}]
    recent_defaults_known = defaults_last_24_months is not None or past_defaults == 0
    recent_defaults = (defaults_last_24_months or 0) if defaults_last_24_months is not None else 0
    if risk_score > 75 and recent_defaults_known and recent_defaults > 0:
        recommendation = "RET ÖNERİSİ"
    elif all(item["status"] == "pass" for item in checks):
        recommendation = "ONAY ÖNERİSİ"
    else:
        recommendation = "UZMAN İNCELEMESİ"

    references = []
    seen = set()
    for item in checks:
        ref = (item["source_file"], item["source_section"], item["source_text"])
        if ref not in seen:
            references.append({"source_file": ref[0], "section": ref[1], "text": ref[2]})
            seen.add(ref)
    adverse = next((item for item in checks if item["status"] in {"fail", "unknown"}), checks[0])
    return {
        "eligible_for_auto_approval": all(item["status"] == "pass" for item in checks),
        "review_required": True,
        "recommended_decision": recommendation,
        "reasons_against": reasons,
        "checks": checks,
        "income_multiple": round(income_multiple, 2),
        "income_cap_multiple": income_cap_multiple,
        "collateral_required": collateral_required,
        "required_collateral": round(required_collateral, 2),
        "collateral_amount": round(collateral_amount, 2),
        "collateral_is_verified": False,
        "cited_policy_section": adverse["source_section"],
        "cited_policy_text": adverse["source_text"],
        "cited_source_file": adverse["source_file"],
        "policy_references": references,
    }


def search_customers(
    branch: str | None = None,
    min_income: float | None = None,
    max_income: float | None = None,
    has_defaults: bool | None = None,
    limit: int = 1000,
) -> dict:
    results = _search_customers_db(
        branch=branch, min_income=min_income, max_income=max_income,
        has_defaults=has_defaults, limit=max(1, min(int(limit), 1000)),
    )
    return {"count": len(results), "customers": results}
