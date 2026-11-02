"""Create a traceable report; Gemini may explain, but cannot set decision fields."""
from __future__ import annotations

import json

from llm_client import MODEL_NAME, get_client


def _fallback_explanation(policy: dict) -> str:
    if policy["reasons_against"]:
        return "İnsan incelemesi gerekli: " + "; ".join(policy["reasons_against"])
    return "Kontrol edilen ölçütler uygun. Nihai karar yine yetkili uzmanın incelemesine bağlıdır."


def _gemini_explanation(evidence: dict, client=None) -> str:
    policy = evidence["check_policy_compliance"]
    # No names, customer IDs, amounts, or raw application records are sent to Gemini.
    summary = {
        "risk_level": evidence["calculate_risk_score"]["risk_level"],
        "recommendation": policy["recommended_decision"],
        "policy_findings": [
            {"criterion": item["label"], "status": item["status"]}
            for item in policy["checks"]
        ],
    }
    client = client or get_client()
    from google.genai import types

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents="Aşağıdaki yapılandırılmış bulguları, yeni olgu eklemeden Türkçe ve kısa biçimde açıkla. Nihai karar verme; yalnızca sistem önerisinin açıklamasını yaz.\n" + json.dumps(summary, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction="Sen bir kredi uzmanına yardımcı olan rapor yazım asistanısın. Kanıt dışı bilgi, varsayım, müşteri kimliği ve yeni sayısal değer üretme. En fazla 3 kısa cümle yaz.",
            temperature=0.1,
            max_output_tokens=250,
        ),
    )
    text = " ".join((response.text or "").split())
    if not text or len(text) > 1200:
        raise RuntimeError("Gemini geçerli uzunlukta açıklama üretmedi.")
    return text


def write_report(evidence: dict, use_gemini: bool = False, client=None) -> str:
    customer = evidence["get_customer_data"]
    risk = evidence["calculate_risk_score"]
    policy = evidence["check_policy_compliance"]
    explanation = _gemini_explanation(evidence, client) if use_gemini else "Gemini örnek veri modunda kullanılmadı."
    rationale = _fallback_explanation(policy)
    refs = policy["policy_references"]
    citations = "; ".join(f"[kaynak: {ref['source_file']} — {ref['section']}]" for ref in refs)
    eligible = "Kontrol edilen tüm koşullar sağlandı" if policy["eligible_for_auto_approval"] else "Otomatik onay koşulları sağlanmadı veya doğrulanamayan veri var"
    return (
        f"MUSTERI: {customer['name']} (ID: {customer['customer_id']})\n"
        f"RISK SKORU: {risk['risk_score']} ({risk['risk_level']})\n"
        f"OTOMATIK ONAYA UYGUN MU: {eligible}\n"
        f"GEREKCE: {rationale}\n"
        f"GEMINI ACIKLAMASI: {explanation}\n"
        f"DAYANILAN BELGE: {citations}\n"
        f"NIHAI ONERI: {policy['recommended_decision']}\n"
        "SON KARAR: Yetkili uzman incelemesi ve onayı gerekir."
    )
