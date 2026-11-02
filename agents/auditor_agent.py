"""Fail-closed audit of report fields against deterministic evidence."""
from __future__ import annotations

import re
import unicodedata


def _norm(value: str) -> str:
    value = value.replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", value).strip().upper()


def audit_report(evidence: dict, report: str) -> list[str]:
    issues = []
    fields = {}
    for line in report.splitlines():
        label, separator, value = line.partition(":")
        if separator:
            fields[_norm(label)] = value.strip()
    required = ("MUSTERI", "RISK SKORU", "OTOMATIK ONAYA UYGUN MU", "GEREKCE", "GEMINI ACIKLAMASI", "DAYANILAN BELGE", "NIHAI ONERI", "SON KARAR")
    for label in required:
        if not fields.get(label):
            issues.append(f"{label} alanı eksik")

    customer = evidence["get_customer_data"]
    risk = evidence["calculate_risk_score"]
    policy = evidence["check_policy_compliance"]
    if customer["customer_id"] not in fields.get("MUSTERI", ""):
        issues.append("Müşteri kimliği raporda kanıtla eşleşmiyor")
    risk_match = re.search(r"([0-9]+(?:[,.][0-9]+)?)", fields.get("RISK SKORU", ""))
    if not risk_match or abs(float(risk_match.group(1).replace(",", ".")) - float(risk["risk_score"])) > 0.05:
        issues.append("Risk skoru raporda kanıtla eşleşmiyor")
    if _norm(fields.get("NIHAI ONERI", "")) != _norm(policy["recommended_decision"]):
        issues.append("Sistem önerisi kanıttaki politika sonucuyla eşleşmiyor")
    expected_eligibility = "KONTROL EDILEN TUM KOSULLER SAGLANDI" if policy["eligible_for_auto_approval"] else "OTOMATIK ONAY KOSULLARI SAGLANMADI VEYA DOGRULANAMAYAN VERI VAR"
    if _norm(fields.get("OTOMATIK ONAYA UYGUN MU", "")) != expected_eligibility:
        issues.append("Uygunluk ifadesi deterministik politika sonucuyla eşleşmiyor")
    expected_reason = ("İnsan incelemesi gerekli: " + "; ".join(policy["reasons_against"])) if policy["reasons_against"] else "Kontrol edilen ölçütler uygun. Nihai karar yine yetkili uzmanın incelemesine bağlıdır."
    if _norm(fields.get("GEREKCE", "")) != _norm(expected_reason):
        issues.append("Gerekçe deterministik politika bulgularıyla eşleşmiyor")
    if "UZMAN" not in _norm(fields.get("SON KARAR", "")):
        issues.append("Nihai insan incelemesi şartı raporda bulunmuyor")
    for reference in policy["policy_references"]:
        expected = reference["source_file"]
        if expected not in fields.get("DAYANILAN BELGE", ""):
            issues.append(f"Politika kaynağı raporda yok: {expected}")
    if not policy.get("review_required"):
        issues.append("İnsan inceleme kilidi etkin değil")
    return issues
