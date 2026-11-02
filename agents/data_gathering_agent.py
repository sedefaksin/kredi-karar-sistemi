"""Deterministically assemble customer, risk and policy evidence."""
from __future__ import annotations

from tools.tool_definitions import (
    calculate_risk_score,
    check_policy_compliance,
    get_customer_data,
)


def gather_evidence(customer_id: str, collateral_amount: float = 0) -> dict:
    customer = get_customer_data(customer_id)
    if "error" in customer:
        return {"error": customer["error"]}
    payment_is_verified = customer.get("requested_monthly_payment_verified") in (True, 1)
    monthly_payment = customer.get("requested_monthly_payment") if payment_is_verified else None
    risk = calculate_risk_score(
        monthly_income=customer["monthly_income"],
        existing_monthly_debt=customer["existing_monthly_debt"],
        credit_history_months=customer["credit_history_months"],
        past_defaults=customer["past_defaults"],
        requested_amount=customer["requested_amount"],
        requested_monthly_payment=monthly_payment,
    )
    policy = check_policy_compliance(
        risk_score=risk["risk_score"],
        debt_to_income_ratio=risk["debt_to_income_ratio"],
        credit_history_months=customer["credit_history_months"],
        past_defaults=customer["past_defaults"],
        monthly_income=customer["monthly_income"],
        requested_amount=customer["requested_amount"],
        collateral_amount=collateral_amount,
        requested_term_months=customer.get("requested_term_months"),
        requested_monthly_payment=monthly_payment,
        defaults_last_24_months=customer.get("defaults_last_24_months"),
        eligibility_verified=customer.get("eligibility_verified"),
        income_document_verified=customer.get("income_document_verified"),
    )
    return {
        "get_customer_data": customer,
        "calculate_risk_score": risk,
        "check_policy_compliance": policy,
        "customer_id": customer_id,
    }
