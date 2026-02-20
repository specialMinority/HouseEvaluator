import { formatCompactNumber, formatYen, h, isObject, parseQuery, pick, safeString } from "../lib/utils.js";

function confidencePill(conf) {
  const c = String(conf || "none");
  if (c === "high") return { cls: "pill pill--high", text: "벤치마크 신뢰도: 높음(high)" };
  if (c === "mid") return { cls: "pill pill--mid", text: "벤치마크 신뢰도: 보통(mid)" };
  if (c === "low") return { cls: "pill pill--low", text: "벤치마크 신뢰도: 낮음(low)" };
  return { cls: "pill pill--none", text: "벤치마크 신뢰도: 없음(none)" };
}

function normalizeList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  return [];
}

function toNumberOrNull(value) {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function stationWalkRiskTier(walkMin) {
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

function initialMultipleRiskTier(imAssessment) {
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
  if (a.startsWith("높음")) {
    return {
      severity: "high",
      title: "초기비용이 시세보다 높음",
      desc: `초기비용 배수(IM)가 ${a}. 礼金/중개수수료/열쇠교체 등 협상 여지를 꼭 확인하세요.`,
    };
  }
  if (a.startsWith("다소 높음")) {
    return {
      severity: "mid",
      title: "초기비용이 시세보다 다소 높음",
      desc: `초기비용 배수(IM)가 ${a}. 협상으로 줄일 수 있는 항목이 있는지 확인해 보세요.`,
    };
  }
  return null;
}

function rentOverBenchmarkRiskTier(rentDeltaRatio, benchmarkConfidence) {
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

function benchmarkConfidenceTextKo(conf) {
  const c = String(conf || "none");
  if (c === "high") return "높음";
  if (c === "mid") return "보통";
  if (c === "low") return "낮음";
  return "없음";
}

function orientationTextKo(value) {
  const v = String(value || "UNKNOWN");
  const map = {
    N: "북쪽",
    NE: "북동쪽",
    E: "동쪽",
    SE: "남동쪽",
    S: "남쪽",
    SW: "남서쪽",
    W: "서쪽",
    NW: "북서쪽",
    UNKNOWN: "모름",
  };
  return map[v] || v;
}

function buildingStructureTextKo(value) {
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

function severityTextKo(sev) {
  const s = String(sev || "");
  if (s === "high") return "높음";
  if (s === "mid") return "중간";
  if (s === "low") return "낮음";
  return s || "없음";
}

const RISK_FLAG_KO = {
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

function renderScoreCard(title, score, grade) {
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

function renderBullets(items) {
  const arr = normalizeList(items).filter((x) => typeof x === "string" && x.trim().length > 0);
  if (arr.length === 0) return h("div", { class: "hint", text: "없음" });
  return h("ul", { class: "list" }, arr.map((t) => h("li", { text: t })));
}

function renderRiskFlags(flags, explanations, context) {
  const arr = normalizeList(flags);

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

function deriveBreakdownMode(input) {
  const keys = ["reikin_yen", "brokerage_fee_yen", "key_change_yen", "cleaning_fee_yen"];
  return keys.some((k) => input[k] !== undefined && input[k] !== null) ? "breakdown" : "total";
}

function computeWhatIfInput(input, mode, params) {
  const out = { ...input };

  if (mode === "total") {
    const pct = Number(params.reductionPct || 0);
    const total = Number(input.initial_cost_total_yen);
    if (Number.isFinite(total)) {
      out.initial_cost_total_yen = Math.max(0, Math.round(total * (1 - pct / 100)));
    }
    return out;
  }

  const keys = ["reikin_yen", "brokerage_fee_yen", "key_change_yen", "cleaning_fee_yen"];
  let reduction = 0;
  for (const k of keys) {
    const base = Number(input[k]);
    if (!Number.isFinite(base)) continue;
    const action = params[k] || "no_change";
    if (action === "half") {
      const delta = Math.round(base * 0.5);
      out[k] = base - delta;
      reduction += delta;
    } else if (action === "waive") {
      out[k] = 0;
      reduction += base;
    }
  }

  const total = Number(input.initial_cost_total_yen);
  if (Number.isFinite(total)) out.initial_cost_total_yen = Math.max(0, total - reduction);
  return out;
}

function renderBenchmarkCard(response, benchmarkMeta, input) {
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
  const inputStructure = input?.building_structure ? String(input.building_structure) : null;

  const hasAdjustment =
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
  if (String(conf || "none") === "none") {
    notes.push("해당 지역/조건에서 벤치마크 매칭이 실패했거나 표본이 부족할 수 있습니다.");
  } else if (String(conf || "") === "low") {
    notes.push("벤치마크 신뢰도가 낮습니다. 비교/해석에 주의하세요.");
  } else if (String(conf || "") === "mid" && String(matchedLevel || "") === "muni_level") {
    notes.push("시/구 매칭이더라도 표본이 적으면 신뢰도는 mid로 표시됩니다.");
  }
  if (
    inputStructure &&
    inputStructure !== "other" &&
    String(matchedLevel || "") !== "muni_structure_level" &&
    String(conf || "none") !== "none"
  ) {
    notes.push("구조별 벤치마크가 없어, 구조 미구분(전체) 벤치마크로 비교합니다.");
  }
  if (inputStructure && String(matchedLevel || "") === "muni_structure_level") {
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

// ── New graph-style components ───────────────────────────────────────────────

function renderDonutGauge(score, grade, contextLabel) {
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

function renderComponentScoreCard(title, score, grade, icon, desc) {
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

function buildContextLabel(input) {
  const prefMap = { tokyo: "도쿄", osaka: "오사카", kanagawa: "가나가와", saitama: "사이타마", chiba: "지바" };
  const parts = [];
  if (input?.prefecture) parts.push(prefMap[String(input.prefecture)] || String(input.prefecture));
  if (input?.municipality) parts.push(String(input.municipality));
  else if (input?.nearest_station_name) parts.push(`${input.nearest_station_name}역`);
  if (input?.layout_type) parts.push(String(input.layout_type));
  return parts.length ? `평가 기준: ${parts.join(" ")}` : "평가 기준";
}

function makeLocationDesc(input, grade) {
  const walk = toNumberOrNull(input?.station_walk_min);
  const parts = [];
  if (walk !== null) parts.push(`역 도보 ${walk}분`);
  if (grade === "A") parts.push("입지 우수");
  else if (grade === "B") parts.push("입지 양호");
  else if (grade === "C") parts.push("입지 보통");
  else if (grade === "D") parts.push("입지 불편");
  return parts.join(", ");
}

function makeConditionDesc(input, ageYears) {
  const age = toNumberOrNull(ageYears);
  const parts = [];
  if (age !== null) parts.push(`준공 ${age}년`);
  if (input?.orientation && input.orientation !== "UNKNOWN") {
    parts.push(`채광 ${orientationTextKo(input.orientation)}`);
  }
  return parts.join(", ");
}

function makeCostDesc(rentDelta, benchmarkConf, imAssessment) {
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

function renderRiskChips(flags, explanations, context) {
  const arr = normalizeList(flags);
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

function renderCompactBenchmarkSection(response, benchmarkMeta, input) {
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

export function renderResultView({
  spec,
  response,
  input,
  benchmarkMeta,
  loading,
  onBack,
  onWhatIf,
}) {
  const q = parseQuery();
  const showDebug = q.debug === "1";

  const summary = pick(response, ["report.summary_ko", "summary_ko", "report.summary"]);
  const riskFlags = pick(response, ["report.risk_flags", "risk_flags"]);
  const riskExpl = pick(response, ["report.risk_flag_explanations_ko", "risk_flag_explanations_ko"]);

  const negKo = pick(response, ["report.negotiation_suggestions.ko", "negotiation_suggestions.ko"]);
  const negJa = pick(response, ["report.negotiation_suggestions.ja", "negotiation_suggestions.ja"]);
  const altJa = pick(response, ["report.alternative_search_queries_ja", "alternative_search_queries_ja"]);
  const derivedMonthly = pick(response, ["derived.monthly_fixed_cost_yen", "monthly_fixed_cost_yen"]);
  const derivedAgeYears = pick(response, ["derived.building_age_years", "building_age_years"]);
  const derivedInitialMultiple = pick(response, ["derived.initial_multiple", "initial_multiple"]);
  const derivedBenchmark = pick(response, ["derived.benchmark_monthly_fixed_cost_yen", "benchmark_monthly_fixed_cost_yen"]);
  const derivedBenchmarkConf = pick(response, [
    "derived.benchmark_confidence",
    "benchmark_confidence",
    "benchmark.benchmark_confidence",
    "derived.benchmark_confidence",
  ]);
  const derivedRentDelta = pick(response, ["derived.rent_delta_ratio", "rent_delta_ratio"]);
  const derivedImAssessment = pick(response, ["derived.im_assessment"]);
  const derivedImAssessmentForeigner = pick(response, ["derived.im_assessment_foreigner"]);
  const derivedImMarketAvg = pick(response, ["derived.initial_multiple_market_avg"]);

  const scores = {
    overall: pick(response, ["scoring.overall_score", "overall_score"]),
    location: pick(response, ["scoring.location_score", "location_score"]),
    condition: pick(response, ["scoring.condition_score", "condition_score"]),
    cost: pick(response, ["scoring.cost_score", "cost_score"]),
  };
  const grades = {
    overall: pick(response, ["grades.overall_grade", "overall_grade"]),
    location: pick(response, ["grades.location_grade", "location_grade"]),
    condition: pick(response, ["grades.condition_grade", "condition_grade"]),
    cost: pick(response, ["grades.cost_grade", "cost_grade"]),
  };

  function makeFriendlySummary() {
    const loc = toNumberOrNull(scores.location);
    const cond = toNumberOrNull(scores.condition);
    const cost = toNumberOrNull(scores.cost);
    const monthly = toNumberOrNull(derivedMonthly);
    const walk = toNumberOrNull(input?.station_walk_min);
    const initialTotal = toNumberOrNull(input?.initial_cost_total_yen);
    const initialMultiple = toNumberOrNull(derivedInitialMultiple);
    const rentDelta = toNumberOrNull(derivedRentDelta);
    const confText = benchmarkConfidenceTextKo(derivedBenchmarkConf);
    const out = [];

    if (loc !== null && cond !== null && cost !== null) {
      if (cond - cost >= 20) out.push("집 상태는 괜찮지만, 비용이 아쉬운 편이에요.");
      else if (loc - cond >= 20) out.push("교통/입지는 괜찮지만, 집 상태는 타협이 필요해요.");
      else if (cost - loc >= 20) out.push("가격은 괜찮지만, 이동/입지는 불편할 수 있어요.");
      else out.push("입지·집 상태·비용이 비교적 균형 잡힌 편이에요.");
    }

    if (monthly !== null) out.push(`매달 내는 돈(월세+관리비)은 ${formatYen(monthly)}예요.`);

    const walkTier = stationWalkRiskTier(walk);
    if (walkTier) out.push(walkTier.summary);

    if (initialTotal !== null && initialMultiple !== null) {
      const imAvg = toNumberOrNull(derivedImMarketAvg);
      const imLevelText = derivedImAssessment ? ` (수준: ${derivedImAssessment})` : "";
      const imForeignText =
        derivedImAssessmentForeigner && derivedImAssessmentForeigner !== derivedImAssessment
          ? ` / 외국인 기준: ${derivedImAssessmentForeigner}`
          : "";
      const avgHint = imAvg !== null ? ` [이 지역 시장 평균: 약 ${formatCompactNumber(imAvg)}개월치]` : "";
      out.push(
        `처음에 내야 하는 돈은 ${formatYen(initialTotal)}이고, 이는 매달 내는 돈의 약 ${formatCompactNumber(initialMultiple)}개월치예요.${imLevelText}${imForeignText}${avgHint}`
      );
    }

    if (String(derivedBenchmarkConf || "none") === "none") {
      out.push("이 지역은 시세 비교 데이터가 부족해서, 가격 비교는 정확하지 않을 수 있어요.");
    } else if (rentDelta !== null) {
      const abs = Math.abs(rentDelta);
      const pct = Math.round(abs * 100);
      if (pct <= 1) out.push(`비슷한 집 시세와 비교하면, 월세는 비슷한 편이에요. (신뢰도: ${confText})`);
      else if (rentDelta > 0) {
        if (rentDelta >= 0.5) out.push(`비슷한 집 시세와 비교하면, 월세가 많이 비싼 편이에요. (약 ${pct}%, 신뢰도: ${confText})`);
        else if (rentDelta >= 0.25) out.push(`비슷한 집 시세와 비교하면, 월세가 꽤 비싼 편이에요. (약 ${pct}%, 신뢰도: ${confText})`);
        else out.push(`비슷한 집 시세와 비교하면, 월세는 약 ${pct}% 비싼 편이에요. (신뢰도: ${confText})`);
      } else {
        out.push(`비슷한 집 시세와 비교하면, 월세는 약 ${pct}% 저렴한 편이에요. (신뢰도: ${confText})`);
      }
    }

    return out;
  }

  function makeFriendlyEvidenceBullets() {
    const bullets = [];

    const walk = toNumberOrNull(input?.station_walk_min);
    if (walk !== null) bullets.push(`역까지 걸어서 ${walk}분`);

    const area = toNumberOrNull(input?.area_sqm);
    if (area !== null) bullets.push(`방 크기(전용면적): ${formatCompactNumber(area)}㎡`);

    const age = toNumberOrNull(derivedAgeYears);
    if (age !== null) bullets.push(`건물 나이: ${formatCompactNumber(age)}년`);

    if (input?.building_structure) bullets.push(`건물 구조: ${buildingStructureTextKo(input.building_structure)}`);

    if (input?.orientation) bullets.push(`방향: ${orientationTextKo(input.orientation)}`);

    if (typeof input?.bathroom_toilet_separate === "boolean") {
      bullets.push(`욕실/화장실: ${input.bathroom_toilet_separate ? "분리" : "일체형"}`);
    }

    const monthly = toNumberOrNull(derivedMonthly);
    if (monthly !== null) bullets.push(`매달 내는 돈(월세+관리비): ${formatYen(monthly)}`);

    const initialTotal = toNumberOrNull(input?.initial_cost_total_yen);
    const initialMultiple = toNumberOrNull(derivedInitialMultiple);
    if (initialTotal !== null && initialMultiple !== null) {
      bullets.push(`처음에 내야 하는 돈(초기비용 합계): ${formatYen(initialTotal)} (약 ${formatCompactNumber(initialMultiple)}개월치)`);
    }
    const imAvg = toNumberOrNull(derivedImMarketAvg);
    if (derivedImAssessment) {
      let imBullet = `초기비용 수준(시장 비교): ${derivedImAssessment}`;
      if (imAvg !== null) imBullet += ` — 이 지역 평균 약 ${formatCompactNumber(imAvg)}개월치 기준`;
      if (derivedImAssessmentForeigner && derivedImAssessmentForeigner !== derivedImAssessment) {
        imBullet += ` / 외국인 기준(+1개월 완화): ${derivedImAssessmentForeigner}`;
      } else if (derivedImAssessmentForeigner) {
        imBullet += ` / 외국인 기준(+1개월 완화): 동일`;
      }
      bullets.push(imBullet);
    }

    const bench = toNumberOrNull(derivedBenchmark);
    const rentDelta = toNumberOrNull(derivedRentDelta);
    const conf = String(derivedBenchmarkConf || "none");
    if (conf !== "none" && bench !== null && rentDelta !== null) {
      const pct = Math.round(Math.abs(rentDelta) * 100);
      bullets.push(`시세(월세 기준): ${formatYen(bench)} · 지금 월세는 시세보다 약 ${pct}% ${rentDelta < 0 ? "저렴" : "비쌈"} (신뢰도: ${benchmarkConfidenceTextKo(conf)})`);
    }

    return bullets;
  }

  // What-if UI (calls /api/evaluate with modified inputs)
  const mode = deriveBreakdownMode(input || {});
  const whatIfState = { reductionPct: 10, reikin_yen: "no_change", brokerage_fee_yen: "no_change", key_change_yen: "no_change", cleaning_fee_yen: "no_change" };

  const whatIfCard = h("div", { class: "card" }, [
    h("h3", { text: "초기비용 줄여보기" }),
    h("div", {
      class: "hint",
      text:
        mode === "total"
          ? "초기비용 합계를 몇 % 줄였다고 가정하고 다시 계산합니다."
          : "礼金/중개수수료/열쇠교체비/청소비를 줄이거나 0원으로 했다고 가정하고 다시 계산합니다.",
    }),
  ]);

  const whatIfControls = [];
  if (mode === "total") {
    const slider = h("input", {
      type: "range",
      min: "0",
      max: "30",
      step: "1",
      value: String(whatIfState.reductionPct),
      onInput: (e) => {
        whatIfState.reductionPct = Number(e.target.value);
        pctText.textContent = `${whatIfState.reductionPct}%`;
      },
    });
    const pctText = h("span", { class: "mono", text: `${whatIfState.reductionPct}%` });
    whatIfControls.push(
      h("div", {}, [
        h("div", { class: "hint", text: "초기비용 합계 감소" }),
        h("div", { class: "actions", style: "justify-content: space-between; margin-top: 0;" }, [pctText, slider]),
      ])
    );
  } else {
    const rows = [
      { key: "reikin_yen", label: "礼金 (reikin_yen)" },
      { key: "brokerage_fee_yen", label: "중개수수료 (brokerage_fee_yen)" },
      { key: "key_change_yen", label: "열쇠교체비 (key_change_yen)" },
      { key: "cleaning_fee_yen", label: "청소비 (cleaning_fee_yen)" },
    ];
    for (const r of rows) {
      const sel = h(
        "select",
        {
          onChange: (e) => {
            whatIfState[r.key] = e.target.value;
          },
        },
        [
          h("option", { value: "no_change", text: "변경 없음" }),
          h("option", { value: "half", text: "50% 감소" }),
          h("option", { value: "waive", text: "면제(0)" }),
        ]
      );
      whatIfControls.push(
        h("div", { class: "field" }, [
          h("label", { text: r.label }),
          sel,
          h("div", { class: "hint", text: `현재: ${formatYen(input?.[r.key])}` }),
        ])
      );
    }
  }

  const applyBtn = h("button", {
    type: "button",
    class: "btn",
    disabled: loading,
    text: loading ? "재평가 중…" : "재평가하기",
    onClick: () => {
      const next = computeWhatIfInput(input || {}, mode, whatIfState);
      onWhatIf(next);
    },
  });

  whatIfCard.appendChild(h("div", { class: "row row--2" }, whatIfControls));
  whatIfCard.appendChild(h("div", { class: "actions" }, [applyBtn]));

  const rawDetails = showDebug
    ? h("details", {}, [
        h("summary", { class: "pill", text: "원본 응답(디버그)" }),
        h("pre", { class: "card mono", text: JSON.stringify(response, null, 2) }),
      ])
    : null;

  const contextLabel = buildContextLabel(input);
  const hero = h("div", { class: "result-hero" }, [
    renderDonutGauge(scores.overall, grades.overall, contextLabel),
    h("div", { class: "score-grid" }, [
      renderComponentScoreCard(
        "입지/교통",
        scores.location,
        grades.location,
        "🚉",
        makeLocationDesc(input, String(grades.location || "").toUpperCase())
      ),
      renderComponentScoreCard(
        "집 컨디션",
        scores.condition,
        grades.condition,
        "🏠",
        makeConditionDesc(input, derivedAgeYears)
      ),
      renderComponentScoreCard("비용", scores.cost, grades.cost, "💴", makeCostDesc(derivedRentDelta, derivedBenchmarkConf, derivedImAssessment)),
    ]),
  ]);

  const summaryCard = h("div", { class: "card" }, [
    h("h3", { text: "요약" }),
    (() => {
      const lines = makeFriendlySummary();
      return lines.length ? h("ul", { class: "list" }, lines.map((t) => h("li", { text: t }))) : h("div", { class: "hint", text: "없음" });
    })(),
    typeof summary === "string" && summary.trim().length ? h("div", { class: "toast", text: summary.trim() }) : null,
  ]);

  const riskContext = {
    stationWalkMin: input?.station_walk_min,
    rentDeltaRatio: derivedRentDelta,
    benchmarkConfidence: derivedBenchmarkConf,
    imAssessment: derivedImAssessment,
  };
  const riskCard = h("div", { class: "card" }, [
    h("h3", { text: "리스크" }),
    renderRiskChips(riskFlags, riskExpl, riskContext),
    h("details", { open: false }, [
      h("summary", { class: "pill", text: "자세히 보기" }),
      h("div", { class: "divider" }),
      renderRiskFlags(riskFlags, riskExpl, riskContext),
    ]),
  ]);

  const criteriaCard = h("div", { class: "card" }, [
    h("h3", { text: "평가기준" }),
    h("ul", { class: "list" }, [
      h("li", { text: "총점: 입지/교통 35% + 집 컨디션 25% + 비용 40%" }),
      h("li", { text: "입지/교통: 역까지 도보 시간(분) 중심" }),
      h("li", { text: "집 컨디션: 면적, 연식, 구조, 방향, 욕실/화장실 분리 여부" }),
      h("li", { text: "비용: 시세 대비 월세(월세+관리비) + 초기비용(IM). 초기비용이 시장 평균보다 높을수록 비용 점수가 내려가요." }),
    ]),
    h("div", { class: "hint", text: "시세 비교 데이터가 부족하면(신뢰도 없음) 비용 평가는 중립적으로 나올 수 있어요." }),
    h("div", { class: "hint", text: "등급 기준: A(85+) / B(70+) / C(55+) / D(0+)" }),
  ]);

  const evidenceCard = h("div", { class: "card" }, [h("h3", { text: "근거" }), renderBullets(makeFriendlyEvidenceBullets())]);

  const negotiationCard = h("div", { class: "card" }, [
    h("h3", { text: "협상/대안" }),
    h("div", { class: "divider" }),
    h("div", { class: "hint", text: "협상 제안" }),
    renderBullets(negKo),
    normalizeList(negJa).length || normalizeList(altJa).length
      ? h("details", { open: false }, [
          h("summary", { class: "pill", text: "일본어 문구/검색 쿼리(복사용)" }),
          h("div", { class: "divider" }),
          h("div", { class: "hint", text: "협상 문구(일본어)" }),
          renderBullets(negJa),
          h("div", { class: "divider" }),
          h("div", { class: "hint", text: "대안 검색 쿼리(일본어)" }),
          renderBullets(altJa),
        ])
      : null,
  ]);

  const bottomRow = h("div", { class: "result-bottom-row" }, [negotiationCard, whatIfCard]);
  const benchmarkCompact = renderCompactBenchmarkSection(response, benchmarkMeta, input);

  return h("div", { class: "panel" }, [
    h("div", { class: "panel__header" }, [
      h("div", {}, [h("h2", { class: "panel__title", text: "결과" })]),
      h("div", { class: "actions", style: "margin-top: 0;" }, [
        h("button", { type: "button", class: "btn btn--ghost", onClick: onBack, text: "다시 입력" }),
      ]),
    ]),
    h("div", { class: "panel__body" }, [
      hero,
      h("div", { class: "cards" }, [
        summaryCard,
        riskCard,
        bottomRow,
        benchmarkCompact,
        evidenceCard,
        criteriaCard,
        rawDetails,
      ]),
    ]),
  ]);
}
