/**
 * riskTiers.js — Risk classification and risk flag rendering.
 *
 * Exports: stationWalkRiskTier, initialMultipleRiskTier, rentOverBenchmarkRiskTier,
 *          benchmarkConfidenceTextKo, RISK_FLAG_KO, renderRiskFlags, renderRiskChips
 */
import { h, isObject, safeString } from "../lib/utils.js";

function toNumberOrNull(value) {
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? n : null;
}

function severityTextKo(sev) {
    const s = String(sev || "");
    if (s === "high") return "높음";
    if (s === "mid") return "중간";
    if (s === "low") return "낮음";
    return s || "없음";
}

export function stationWalkRiskTier(walkMin) {
    const m = toNumberOrNull(walkMin);
    if (m === null) return null;
    if (m >= 45) {
        return {
            severity: "high",
            summary: `역까지 도보 ${m}분은 너무 멀어요. 통근/일상 이동이 매우 힘들 수 있어요.`,
            desc: `역까지 도보 ${m}분으로 매우 멉니다. 통근·귀가·외출이 매우 힘들 수 있어요.`,
        };
    }
    if (m >= 30) {
        return {
            severity: "mid",
            summary: `역까지 도보 ${m}분은 많이 멀어요. 이동 시간이 꽤 길어질 수 있어요.`,
            desc: `역까지 도보 ${m}분이면 통근/외출이 꽤 불편할 수 있어요.`,
        };
    }
    if (m >= 20) {
        return {
            severity: "low",
            summary: `역까지 도보 ${m}분이면 꽤 멀게 느껴질 수 있어요.`,
            desc: `역까지 도보 ${m}분이면 비/짐 있을 때 불편할 수 있어요.`,
        };
    }
    return null;
}

export function initialMultipleRiskTier(imAssessment) {
    const a = String(imAssessment || "");
    if (!a) return null;
    if (a.startsWith("높음")) {
        return {
            severity: "high",
            title: "초기비용이 시세보다 높음",
            desc: `초기비용(IM)이 '${a}' 수준이에요. (예: 레이킹/중개수수료/보증회사 비용 등) 세부 항목을 확인하고 협상 여지를 점검해 보세요.`,
        };
    }
    if (a.startsWith("다소 높음")) {
        return {
            severity: "mid",
            title: "초기비용이 시세보다 다소 높음",
            desc: `초기비용(IM)이 '${a}' 수준이에요. 동일 조건의 다른 매물과 비교해보고, 포함 내역(레이킹/중개수수료 등)을 확인해 보세요.`,
        };
    }
    return null;
}

export function rentOverBenchmarkRiskTier(rentDeltaRatio, benchmarkConfidence) {
    const conf = String(benchmarkConfidence || "none");
    if (conf === "none" || conf === "low") return null;

    const d = toNumberOrNull(rentDeltaRatio);
    if (d === null || d <= 0) return null;

    const pct = Math.round(d * 100);
    if (d >= 0.5) {
        return {
            severity: "high",
            title: "월세가 시세보다 훨씬 비쌈",
            desc: `비슷한 조건의 시세보다 약 ${pct}% 비싸요. 같은 예산이면 다른 매물도 함께 보는 걸 추천해요.`,
        };
    }
    if (d >= 0.25) {
        return {
            severity: "mid",
            title: "월세가 시세보다 비쌈",
            desc: `비슷한 조건의 시세보다 약 ${pct}% 비싼 편이에요.`,
        };
    }
    if (d >= 0.1) {
        return {
            severity: "low",
            title: "월세가 시세보다 조금 비쌈",
            desc: `비슷한 조건의 시세보다 약 ${pct}% 비싼 편이에요.`,
        };
    }
    return null;
}

export function benchmarkConfidenceTextKo(conf) {
    const c = String(conf || "none");
    if (c === "high") return "높음";
    if (c === "mid") return "보통";
    if (c === "low") return "낮음";
    return "없음";
}

export const RISK_FLAG_KO = {
    HIGH_INITIAL_MULTIPLE: {
        title: "초기비용 부담 큼(IM)",
        desc: "초기비용 배수(IM)가 높아 초기 부담이 큽니다.",
    },
    FAR_FROM_STATION: {
        title: "역에서 멂",
        desc: "도보 시간이 길어 통근/귀가가 불편할 수 있습니다.",
    },
    VERY_SMALL_AREA: {
        title: "면적 매우 작음",
        desc: "체감이 좁아 생활이 답답할 수 있습니다.",
    },
    OLD_BUILDING: {
        title: "노후 건물",
        desc: "설비 노후/수리 이슈 가능성에 주의하세요.",
    },
    NORTH_FACING: {
        title: "북향",
        desc: "채광이 불리할 수 있습니다.",
    },
    BATH_NOT_SEPARATE: {
        title: "욕실/화장실 분리 아님",
        desc: "선호도에 따라 감점될 수 있습니다.",
    },
    HIGH_OTHER_FEES: {
        title: "기타 초기비용 큼",
        desc: "기타 비용 항목을 항목별로 확인하세요.",
    },
    LONG_CONTRACT_TERM: {
        title: "계약기간 김",
        desc: "이사 계획이 있다면 계약기간이 리스크가 될 수 있습니다.",
    },
    HIGH_EARLY_TERMINATION_PENALTY: {
        title: "중도해지 위약금 큼",
        desc: "위약금이 높아 유연성이 떨어질 수 있습니다.",
    },
};

export function renderRiskFlags(flags, explanations, context) {
    const arr = Array.isArray(flags) ? flags : [];

    const walkTier = stationWalkRiskTier(context?.stationWalkMin);
    const rentTier = rentOverBenchmarkRiskTier(context?.rentDeltaRatio, context?.benchmarkConfidence);
    const imTier = initialMultipleRiskTier(context?.imAssessment);
    const extraCards = [rentTier, imTier].filter(Boolean);

    if (arr.length === 0 && extraCards.length === 0) return h("div", { class: "hint", text: "리스크 플래그 없음" });

    const items = [];
    for (const c of extraCards) {
        items.push(
            h("div", { class: "card" }, [
                h("h3", { text: safeString(c.title) || "주의" }),
                c.severity ? h("div", { class: "hint", text: `위험도: ${severityTextKo(c.severity)}` }) : null,
                c.desc ? h("div", { class: "hint", text: safeString(c.desc) }) : null,
            ])
        );
    }
    for (const f of arr) {
        const id = typeof f === "string" ? f : isObject(f) ? safeString(f.risk_flag_id || f.id) : safeString(f);
        const baseSev = isObject(f) ? safeString(f.severity) : "";
        const expl = isObject(explanations) && id ? safeString(explanations[id]) : "";
        const info = id ? RISK_FLAG_KO[id] : null;
        const title = info?.title || id || "알 수 없는 플래그";

        let sev = baseSev;
        let desc = expl || info?.desc || "";
        if (id === "FAR_FROM_STATION" && walkTier) {
            sev = walkTier.severity;
            desc = walkTier.desc;
        }

        items.push(
            h("div", { class: "card" }, [
                h("h3", { text: title }),
                id ? h("div", { class: "hint mono", text: id }) : null,
                sev ? h("div", { class: "hint", text: `위험도: ${severityTextKo(sev)}` }) : null,
                desc ? h("div", { class: "hint", text: desc }) : null,
            ])
        );
    }
    return h("div", { class: "cards" }, items);
}

export function renderRiskChips(flags, explanations, context) {
    const arr = Array.isArray(flags) ? flags : [];
    const walkTier = stationWalkRiskTier(context?.stationWalkMin);
    const rentTier = rentOverBenchmarkRiskTier(context?.rentDeltaRatio, context?.benchmarkConfidence);
    const imTier = initialMultipleRiskTier(context?.imAssessment);
    const chips = [];

    for (const tier of [rentTier, imTier]) {
        if (!tier) continue;
        const cls = tier.severity === "high" ? "risk-chip risk-chip--high" : tier.severity === "low" ? "risk-chip risk-chip--low" : "risk-chip";
        const icon = tier.severity === "high" ? "⚠" : "△";
        chips.push(h("span", { class: cls, title: tier.desc || "", text: `${icon} ${tier.title || ""}` }));
    }

    for (const f of arr) {
        const id = typeof f === "string" ? f : isObject(f) ? safeString(f.risk_flag_id || f.id) : safeString(f);
        let sev = isObject(f) ? safeString(f.severity) : "mid";
        if (id === "FAR_FROM_STATION" && walkTier) sev = walkTier.severity;
        const info = id ? RISK_FLAG_KO[id] : null;
        const title = info?.title || id || "플래그";
        const desc = (isObject(explanations) && id ? safeString(explanations[id]) : "") || info?.desc || "";
        const cls = sev === "high" ? "risk-chip risk-chip--high" : sev === "low" ? "risk-chip risk-chip--low" : "risk-chip";
        const icon = sev === "high" ? "⚠" : "△";
        chips.push(h("span", { class: cls, title: desc, text: `${icon} ${title}` }));
    }

    if (chips.length === 0) return h("span", { class: "hint", text: "리스크 플래그 없음" });
    return h("div", { class: "risk-chips" }, chips);
}
