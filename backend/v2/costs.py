"""Explicit yen cash-flow arithmetic; no assumed market fees or nationality premium."""
from __future__ import annotations

from backend.v2.models import ValidationError

FIELDS = {
    "rent_yen", "mgmt_fee_yen", "monthly_extra_yen", "stay_months",
    "initial_payment_yen", "prepaid_rent_yen", "refundable_deposit_yen",
    "exit_fee_yen", "renewal_fee_yen", "discount_yen",
}


def calculate_costs(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("비용 입력은 JSON 객체여야 합니다.")
    if set(payload) - FIELDS:
        raise ValidationError("허용되지 않은 비용 항목입니다.")
    missing = FIELDS - set(payload)
    if missing:
        raise ValidationError("모든 비용 항목을 확인해 주세요: " + ", ".join(sorted(missing)))
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
            raise ValidationError(f"{key}: 확인된 0 이상의 정수 엔 금액이 필요합니다.")
    months = payload["stay_months"]
    if not 1 <= months <= 60:
        raise ValidationError("거주 기간은 1~60개월이어야 합니다.")
    if payload["rent_yen"] <= 0:
        raise ValidationError("월세는 0보다 커야 합니다.")
    base = payload["rent_yen"] + payload["mgmt_fee_yen"]
    all_in = base + payload["monthly_extra_yen"]
    initial = payload["initial_payment_yen"]
    prepaid = payload["prepaid_rent_yen"]
    deposit = payload["refundable_deposit_yen"]
    if prepaid + deposit > initial:
        raise ValidationError("선납 월비용과 환급성 보증금의 합계가 입주 청구 총액을 넘습니다.")
    if prepaid > all_in * months:
        raise ValidationError("선납 월비용이 선택한 거주 기간의 월비용 합계를 넘습니다.")
    nonrefundable = initial - prepaid - deposit
    gross = all_in * months + nonrefundable + payload["exit_fee_yen"] + payload["renewal_fee_yen"]
    if payload["discount_yen"] > gross:
        raise ValidationError("할인액이 거주 비용 합계를 넘습니다.")
    total = gross - payload["discount_yen"]
    return {
        "monthly_base_yen": base,
        "monthly_all_in_yen": all_in,
        "upfront_cash_yen": initial,
        "nonrefundable_initial_yen": nonrefundable,
        "stay_cost_yen": total,
        "deposit_at_risk_yen": deposit,
        "average_monthly_cost_yen": round(total / months, 2),
        "stay_months": months,
        "assumptions": [
            "환급성 보증금이 전액 반환되는 시나리오입니다. 반환되지 않으면 해당 금액만큼 비용이 늘어납니다.",
            "입주 청구액에 포함된 선납 월비용은 월별 비용과 중복 계산하지 않습니다.",
            "갱신·퇴거 비용과 할인은 선택 기간에 실제 적용되는 확인된 금액만 입력합니다.",
        ],
    }
