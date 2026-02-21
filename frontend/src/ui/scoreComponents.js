/**
 * scoreComponents.js — Score card, gauge, and description builder components.
 *
 * Exports: renderScoreCard, renderBullets, renderDonutGauge,
 *          renderComponentScoreCard, buildContextLabel,
 *          makeLocationDesc, makeConditionDesc, makeCostDesc,
 *          orientationTextKo
 */
import { formatCompactNumber, h, safeString } from "../lib/utils.js";

function toNumberOrNull(value) {
    const n = typeof value === "number" ? value : Number(value);
    return Number.isFinite(n) ? n : null;
}

export function orientationTextKo(value) {
    const v = String(value || "UNKNOWN");
    const map = {
        N: "북쪽", NE: "북동쪽", E: "동쪽", SE: "남동쪽",
        S: "남쪽", SW: "남서쪽", W: "서쪽", NW: "북서쪽",
        UNKNOWN: "모름",
    };
    return map[v] || v;
}

export function renderScoreCard(title, score, grade) {
    return h("div", { class: "card" }, [
        h("h3", { text: title }),
        h("div", { class: "kv" }, [
            h("div", { class: "k", text: "등급" }),
            h("div", { class: "v mono", text: safeString(grade) || "없음" }),
            h("div", { class: "k", text: "점수" }),
            h("div", { class: "v mono", text: score === undefined ? "없음" : formatCompactNumber(score) }),
        ]),
    ]);
}

export function renderBullets(items) {
    const arr = (Array.isArray(items) ? items : []).filter((x) => typeof x === "string" && x.trim().length > 0);
    if (arr.length === 0) return h("div", { class: "hint", text: "없음" });
    return h("ul", { class: "list" }, arr.map((t) => h("li", { text: t })));
}

export function renderDonutGauge(score, grade, contextLabel) {
    const pct = Math.max(0, Math.min(100, Number(score) || 0));
    const r = 54;
    const size = 148;
    const cx = size / 2;
    const cy = size / 2;
    const circ = 2 * Math.PI * r;
    const filled = circ * (pct / 100);
    const gap = circ - filled;
    const gradeColors = { A: "#34c759", B: "#0071e3", C: "#ff9f0a", D: "#ff3b30" };
    const fillColor = gradeColors[String(grade || "B").toUpperCase()] || "#0071e3";

    const svgWrap = h("div", { class: "gauge-svg-wrap" });
    svgWrap.innerHTML =
        `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">` +
        `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#e5e5ea" stroke-width="13"/>` +
        `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${fillColor}" stroke-width="13"` +
        ` stroke-dasharray="${filled.toFixed(2)} ${gap.toFixed(2)}"` +
        ` stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>` +
        `</svg>`;

    const scoreText = score !== undefined && score !== null ? String(Math.round(Number(score))) : "--";
    return h("div", { class: "gauge-section" }, [
        h("div", { class: "gauge-container" }, [
            svgWrap,
            h("div", { class: "gauge-inner" }, [
                h("span", { class: "gauge-label-text", text: "종합 점수" }),
                h("span", { class: "gauge-score-num", text: scoreText }),
                h("span", { class: "gauge-grade-text", text: `/ ${String(grade || "?")}등급` }),
            ]),
        ]),
        contextLabel ? h("div", { class: "gauge-context-label", text: contextLabel }) : null,
    ]);
}

export function renderComponentScoreCard(title, score, grade, icon, desc) {
    const pct = Math.max(0, Math.min(100, Number(score) || 0));
    const gl = String(grade || "").toUpperCase();
    const gradeCls = "grade-badge" + (["A", "B", "C", "D"].includes(gl) ? ` grade-badge--${gl.toLowerCase()}` : " grade-badge--d");
    const fill = h("div", { class: "score-bar__fill" });
    fill.style.width = `${pct}%`;
    const scoreText = score !== undefined && score !== null ? String(Math.round(Number(score))) : "--";
    return h("div", { class: "score-card" }, [
        h("div", { class: "score-card__header" }, [
            h("span", { class: "score-card__name", text: title }),
            h("span", { class: "score-card__num", text: scoreText }),
            h("span", { class: gradeCls, text: gl || "?" }),
        ]),
        h("div", { class: "score-bar" }, [fill]),
        h("div", { class: "score-card__footer" }, [
            icon ? h("span", { class: "score-card__icon", text: icon }) : null,
            h("span", { class: "score-card__hint", text: desc || "" }),
        ]),
    ]);
}

export function buildContextLabel(input) {
    const prefMap = { tokyo: "도쿄", osaka: "오사카", kanagawa: "가나가와", saitama: "사이타마", chiba: "지바" };
    const parts = [];
    if (input?.prefecture) parts.push(prefMap[String(input.prefecture)] || String(input.prefecture));
    if (input?.municipality) parts.push(String(input.municipality));
    else if (input?.nearest_station_name) parts.push(`${input.nearest_station_name}역`);
    if (input?.layout_type) parts.push(String(input.layout_type));
    return parts.length ? `평가 기준: ${parts.join(" ")}` : "평가 기준";
}

export function makeLocationDesc(input, grade) {
    const walk = toNumberOrNull(input?.station_walk_min);
    const parts = [];
    if (walk !== null) parts.push(`역 도보 ${walk}분`);
    if (grade === "A") parts.push("입지 우수");
    else if (grade === "B") parts.push("입지 양호");
    else if (grade === "C") parts.push("입지 보통");
    else if (grade === "D") parts.push("입지 불편");
    return parts.join(", ");
}

export function makeConditionDesc(input, ageYears) {
    const age = toNumberOrNull(ageYears);
    const parts = [];
    if (age !== null) parts.push(`준공 ${age}년`);
    if (input?.orientation && input.orientation !== "UNKNOWN") {
        parts.push(`채광 ${orientationTextKo(input.orientation)}`);
    }
    return parts.join(", ");
}

export function makeCostDesc(rentDelta, benchmarkConf, imAssessment) {
    const parts = [];
    const d = toNumberOrNull(rentDelta);
    const conf = String(benchmarkConf || "none");
    if (conf !== "none" && d !== null) {
        const absPct = Math.abs(Math.round(d * 100));
        if (d <= -0.1) parts.push(`시세보다 ${absPct}% 저렴`);
        else if (d >= 0.1) parts.push(`시세보다 ${absPct}% 비쌈`);
        else parts.push("시세 수준");
    }
    if (imAssessment) parts.push(String(imAssessment).split("(")[0].trim());
    return parts.join(", ");
}
