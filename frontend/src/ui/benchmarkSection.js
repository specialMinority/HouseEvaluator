/**
 * benchmarkSection.js — Benchmark card and compact benchmark section rendering.
 *
 * Exports: buildingStructureTextKo, renderBenchmarkCard, renderCompactBenchmarkSection
 */
import { formatYen, h, pick, safeString } from "../lib/utils.js";
import { benchmarkConfidenceTextKo } from "./riskTiers.js";

function confidencePill(conf) {
    const c = String(conf || "none");
    if (c === "high") return { cls: "pill pill--high", text: "벤치마크 신뢰도: 높음(high)" };
    if (c === "mid") return { cls: "pill pill--mid", text: "벤치마크 신뢰도: 보통(mid)" };
    if (c === "low") return { cls: "pill pill--low", text: "벤치마크 신뢰도: 낮음(low)" };
    return { cls: "pill pill--none", text: "벤치마크 신뢰도: 없음(none)" };
}

export function buildingStructureTextKo(value) {
    const v = String(value || "other");
    const map = {
        wood: "목조(木造)",
        light_steel: "경량철골(軽量鉄骨)",
        steel: "철골(鉄骨/S造)",
        rc: "철근콘크리트(RC)",
        src: "철골철근콘크리트(SRC)",
        other: "기타/모름",
    };
    return map[v] || v;
}

export function renderBenchmarkCard(response, benchmarkMeta, input) {
    const conf = pick(response, [
        "derived.benchmark_confidence",
        "benchmark_confidence",
        "benchmark.benchmark_confidence",
        "derived.benchmark_confidence",
    ]);
    const pill = confidencePill(conf);

    const value = pick(response, [
        "derived.benchmark_monthly_fixed_cost_yen",
        "benchmark_monthly_fixed_cost_yen",
        "benchmark.benchmark_monthly_fixed_cost_yen",
        "benchmark.benchmark_monthly_fixed_cost_yen_median",
    ]);
    const rawValue = pick(response, [
        "derived.benchmark_monthly_fixed_cost_yen_raw",
        "derived.benchmark_rent_yen_raw",
        "benchmark_monthly_fixed_cost_yen_raw",
    ]);
    const nSources = pick(response, [
        "derived.benchmark_n_sources",
        "benchmark_n_sources",
        "derived.benchmark_n_rows",
    ]);
    const matchedLevel = pick(response, ["derived.benchmark_matched_level", "benchmark_matched_level"]);
    const adjustments = pick(response, ["derived.benchmark_adjustments", "benchmark_adjustments"]);
    const mgmtFeeEstimate = pick(response, ["derived.benchmark_mgmt_fee_estimate_yen", "benchmark_mgmt_fee_estimate_yen"]);
    const inputStructure = input?.building_structure ? String(input.building_structure) : null;

    const isLiveMatched = /_(live|relaxed)$/.test(String(matchedLevel || ""));

    const hasAdjustment =
        !isLiveMatched &&
        rawValue !== undefined &&
        rawValue !== null &&
        value !== undefined &&
        value !== null &&
        Number(rawValue) !== Number(value);

    const sourceName = pick(response, ["benchmark.source_name", "benchmark_meta.source_name", "source_name"]);
    const sourceUpdatedAt = pick(response, ["benchmark.source_updated_at", "benchmark_meta.source_updated_at", "source_updated_at"]);
    const methodNotes = pick(response, ["benchmark.method_notes", "benchmark_meta.method_notes", "method_notes"]);

    const fallbackSources = benchmarkMeta?.sourceNames?.length ? benchmarkMeta.sourceNames.join(", ") : "없음";
    const fallbackUpdatedAt = benchmarkMeta?.latestSourceUpdatedAt || benchmarkMeta?.latestCollectedAt || null;
    const fallbackNotes = benchmarkMeta?.methodNotes?.length ? benchmarkMeta.methodNotes[0] : null;

    const notes = [];
    if (mgmtFeeEstimate !== undefined && mgmtFeeEstimate !== null && Number(mgmtFeeEstimate) > 0) {
        notes.push(`관리비 벤치마크가 없어, 월세의 일부를 관리비로 추정해 포함했습니다. (추정 관리비: ${formatYen(mgmtFeeEstimate)})`);
    }

    const live = pick(response, ["derived.live_benchmark"]);
    if (live && typeof live === "object") {
        const enabled = Boolean(live.enabled);
        const available = Boolean(live.available);
        const attempted = Boolean(live.attempted);
        const used = Boolean(live.used);
        const n = live.n_sources;
        const lvl = live.matched_level;
        const relax = live.relaxation_applied;
        const err = live.error;
        const attempts = Array.isArray(live.attempts) ? live.attempts : null;
        const providerNameUsed =
            safeString(live.provider_name) || (safeString(live.provider) ? safeString(live.provider).toUpperCase() : "실시간 비교");
        const providerLabel = used ? providerNameUsed : "실시간 비교";

        if (used) {
            const parts = [];
            if (typeof n === "number") parts.push(`N=${n}`);
            if (relax !== null && relax !== undefined) parts.push(`relax=${relax}`);
            if (lvl) parts.push(String(lvl));
            notes.push(`${providerLabel} 실시간 비교 벤치마크 사용${parts.length ? ` (${parts.join(", ")})` : ""}`);
        } else if (attempted) {
            notes.push(`${providerLabel}를 시도했지만 벤치마크로 사용하지 못했습니다${err ? `: ${String(err)}` : ""}`);
        } else if (!enabled) {
            notes.push("실시간 비교가 비활성화되어 있습니다 (SUUMO_LIVE=0).");
        } else if (!available) {
            notes.push("실시간 비교 모듈을 로드하지 못했습니다.");
        }

        if (attempted && !used) {
            const order = Array.isArray(live.provider_order) ? live.provider_order.filter(Boolean) : null;
            const envRaw = safeString(live.providers_env);
            const shown = order && order.length ? order.join(",") : envRaw;
            if (shown) notes.push(`${providerLabel} provider order: ${shown}`);
        }

        if (attempted && live.search_url) {
            notes.push(`${providerLabel} 검색 URL: ${String(live.search_url)}`);
        }

        if (attempted && live.filters && typeof live.filters === "object") {
            const f = live.filters;
            const parts = [];
            if (f.md_strategy) parts.push(`md전략 ${String(f.md_strategy)}`);
            if (f.area_min_sqm != null && f.area_max_sqm != null) parts.push(`면적 ${f.area_min_sqm}~${f.area_max_sqm}㎡`);
            if (f.walk_min_window != null && f.walk_max_window != null) parts.push(`도보 ${f.walk_min_window}~${f.walk_max_window}분`);
            if (f.age_max_years != null) parts.push(`연식 <=${f.age_max_years}년`);
            if (f.nearest_station_name) parts.push(`역 ${String(f.nearest_station_name)}`);
            if (f.bathroom_toilet_separate != null) parts.push(`욕실분리 ${f.bathroom_toilet_separate ? "O" : "X"}`);
            if (f.building_structure && String(f.building_structure) !== "other") {
                parts.push(`구조 ${buildingStructureTextKo(String(f.building_structure))}`);
            }
            if (f.orientation && String(f.orientation) !== "UNKNOWN") parts.push(`방위 ${String(f.orientation)}`);
            if (parts.length) notes.push(`${providerLabel} 추가 매칭 조건: ${parts.join(", ")}`);
        }

        if (attempted && attempts && attempts.length) {
            const isObj = (x) => x && typeof x === "object";

            // Show the last failure per provider (homes/suumo/...), plus the last few step attempts.
            const providerFails = [];
            const seen = new Set();
            for (let i = attempts.length - 1; i >= 0; i--) {
                const a = attempts[i];
                if (!isObj(a)) continue;
                if (!a.provider) continue;
                if ("step" in a) continue;
                const p = String(a.provider);
                if (seen.has(p)) continue;
                seen.add(p);
                providerFails.push(a);
            }
            providerFails.reverse();

            const stepAttempts = attempts.filter((a) => isObj(a) && ("step" in a));
            const tailSteps = stepAttempts.slice(Math.max(0, stepAttempts.length - 4));
            const tail = [...providerFails, ...tailSteps];
            const parts = tail.map((a) => {
                if (a?.provider && !("step" in a)) {
                    let s = `${String(a.provider)}: ${a?.error ? String(a.error) : "failed"}`;
                    if (a?.url) s += ` (url: ${String(a.url)})`;
                    return s;
                }
                const step = a?.step ?? "?";
                const strat = a?.md_strategy ?? (a?.page != null ? `page${a.page}` : "?");
                if (a?.error) return `step${step}/${strat}: ${String(a.error)}`;
                if (a?.fetched_n != null) {
                    let s = `step${step}/${strat}: fetched=${a.fetched_n}, matched=${a.matched_n}`;
                    if (a?.matched_total_n != null) s += `, total=${a.matched_total_n}`;
                    if (a?.layout_sample?.length) s += ` [layouts: ${a.layout_sample.join(",")}]`;
                    if (a?.coverage && typeof a.coverage === "object") {
                      const cov = a.coverage;
                      const parts = [];
                      if (cov.layout != null) parts.push(`layout=${cov.layout}`);
                      if (cov.area != null) parts.push(`area=${cov.area}`);
                      if (cov.walk != null) parts.push(`walk=${cov.walk}`);
                      if (cov.age != null) parts.push(`age=${cov.age}`);
                      if (cov.structure != null) parts.push(`struct=${cov.structure}`);
                      if (cov.bath != null) parts.push(`bath=${cov.bath}`);
                      if (cov.station != null) parts.push(`station=${cov.station}`);
                      if (parts.length) s += ` [cov: ${parts.join(",")}]`;
                    }
                    return s;
                }
                return `step${step}/${strat}`;
            });
            notes.push(`${providerLabel} 디버그(최근): ${parts.join(" | ")}`);
        }
    }
    if (String(conf || "none") === "none") {
        notes.push("해당 지역/조건에서 벤치마크 매칭이 실패했거나 표본이 부족할 수 있습니다.");
    } else if (String(conf || "") === "low") {
        notes.push("벤치마크 신뢰도가 낮습니다. 비교/해석에 주의하세요.");
    } else if (String(conf || "") === "mid" && String(matchedLevel || "") === "muni_level") {
        notes.push("시/구 매칭이더라도 표본이 적으면 신뢰도는 mid로 표시됩니다.");
    }
    if (
        !isLiveMatched &&
        inputStructure &&
        inputStructure !== "other" &&
        String(matchedLevel || "") !== "muni_structure_level" &&
        String(conf || "none") !== "none"
    ) {
        notes.push("구조별 벤치마크가 없어, 구조 미구분(전체) 벤치마크로 비교합니다.");
    }
    if (!isLiveMatched && inputStructure && String(matchedLevel || "") === "muni_structure_level") {
        notes.push(`구조별 벤치마크로 비교했습니다. (구조: ${buildingStructureTextKo(inputStructure)})`);
    }
    if (Number(nSources) === 1) notes.push("주의: 벤치마크가 단일 출처(표본 1개) 기반입니다.");
    if (hasAdjustment) {
        const adjFactors = [];
        if (adjustments?.area_factor !== undefined) adjFactors.push("면적");
        if (adjustments?.building_age_factor !== undefined) adjFactors.push("건물연식");
        if (adjustments?.station_walk_factor !== undefined) adjFactors.push("역거리");
        if (adjustments?.building_structure_factor !== undefined) adjFactors.push("건물구조");
        if (adjustments?.bathroom_toilet_separate_factor !== undefined) adjFactors.push("욕실분리");
        if (adjustments?.orientation_factor !== undefined) adjFactors.push("방위");
        const factorStr = adjFactors.join("/") || "복수 항목";
        notes.push(
            `벤치마크가 ${factorStr} 기준으로 보정되었습니다. (원본: ${formatYen(rawValue)} → 보정: ${formatYen(value)})`
        );
    }
    const note = notes.filter(Boolean).join("\n");

    return h("div", { class: "card" }, [
        h("div", { class: "actions", style: "justify-content: space-between; margin-top: 0;" }, [
            h("h3", { text: "벤치마크 근거" }),
            h("span", { class: pill.cls, text: pill.text }),
        ]),
        h("div", { class: "kv" }, [
            h("div", { class: "k", text: "벤치마크(월 고정비)" }),
            h("div", { class: "v mono", text: value === undefined || value === null ? "없음" : formatYen(value) }),
            hasAdjustment ? h("div", { class: "k", text: "원본(보정 전)" }) : null,
            hasAdjustment ? h("div", { class: "v mono", text: formatYen(rawValue) }) : null,
            h("div", { class: "k", text: "매칭 레벨" }),
            h("div", { class: "v mono", text: matchedLevel === undefined || matchedLevel === null ? "없음" : String(matchedLevel) }),
            h("div", { class: "k", text: "표본 수" }),
            h("div", { class: "v mono", text: nSources === undefined || nSources === null ? "없음" : String(nSources) }),
            h("div", { class: "k", text: "출처" }),
            h("div", { class: "v mono", text: safeString(sourceName) || fallbackSources || "없음" }),
            h("div", { class: "k", text: "업데이트" }),
            h("div", { class: "v mono", text: safeString(sourceUpdatedAt) || safeString(fallbackUpdatedAt) || "없음" }),
            h("div", { class: "k", text: "산출 방식" }),
            h("div", { class: "v", text: safeString(methodNotes) || safeString(fallbackNotes) || "없음" }),
        ]),
        note ? h("div", { class: "toast", text: note }) : null,
    ]);
}

export function renderCompactBenchmarkSection(response, benchmarkMeta, input) {
    const conf = pick(response, [
        "derived.benchmark_confidence",
        "benchmark_confidence",
        "benchmark.benchmark_confidence",
        "derived.benchmark_confidence",
    ]);
    const value = pick(response, [
        "derived.benchmark_monthly_fixed_cost_yen",
        "benchmark_monthly_fixed_cost_yen",
        "benchmark.benchmark_monthly_fixed_cost_yen",
        "benchmark.benchmark_monthly_fixed_cost_yen_median",
    ]);
    const nSources = pick(response, ["derived.benchmark_n_sources", "benchmark_n_sources", "derived.benchmark_n_rows"]);

    const prefMap = { tokyo: "도쿄", osaka: "오사카", kanagawa: "가나가와", saitama: "사이타마", chiba: "지바" };
    const locParts = [];
    if (input?.prefecture) locParts.push(prefMap[String(input.prefecture)] || String(input.prefecture));
    if (input?.municipality) locParts.push(String(input.municipality));
    else if (input?.nearest_station_name) locParts.push(`${input.nearest_station_name}역`);
    if (input?.layout_type) locParts.push(String(input.layout_type));
    const locStr = locParts.join(" ");

    const confText = benchmarkConfidenceTextKo(conf);
    const nText = nSources != null ? `N=${nSources}` : "";
    const valText = value != null ? formatYen(value) : "없음";
    const locLabel = locStr || "평가 기준";
    const summaryText = `${locLabel}: ${valText}${nText ? ` (${nText}, 신뢰도: ${confText})` : ` (신뢰도: ${confText})`}`;

    let expanded = false;
    const fullDetail = h("div", { id: "benchmark-details", style: "display:none; margin-top:12px;" }, [
        renderBenchmarkCard(response, benchmarkMeta, input),
    ]);

    const icon = h("span", { class: "benchmark-compact-icon", text: "▾" });
    const row = h("div", { class: "benchmark-compact-row" }, [
        h("span", { class: "benchmark-compact-text" }, [
            h("strong", { text: "벤치마크 근거" }),
            h("span", { text: ` — ${summaryText}` }),
        ]),
        icon,
    ]);

    function toggle() {
        expanded = !expanded;
        fullDetail.style.display = expanded ? "block" : "none";
        row.setAttribute("aria-expanded", expanded ? "true" : "false");
        icon.textContent = expanded ? "▴" : "▾";
    }
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });
    row.setAttribute("tabindex", "0");
    row.setAttribute("role", "button");
    row.setAttribute("aria-expanded", "false");
    row.setAttribute("aria-controls", "benchmark-details");

    return h("div", {}, [row, fullDetail]);
}
