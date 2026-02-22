import { formatCompactNumber, formatYen, h, isObject, parseQuery, pick, safeString } from "../lib/utils.js";
import { benchmarkConfidenceTextKo, renderRiskChips, renderRiskFlags, stationWalkRiskTier } from "./riskTiers.js";
import { buildingStructureTextKo, renderBenchmarkCard, renderCompactBenchmarkSection } from "./benchmarkSection.js";
import { buildContextLabel, makeConditionDesc, makeCostDesc, makeLocationDesc, orientationTextKo, renderBullets, renderComponentScoreCard, renderDonutGauge, renderScoreCard } from "./scoreComponents.js";

function toNumberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : null;
}

function normalizeList(value) {
  if (Array.isArray(value)) return value.filter((x) => typeof x === "string" && x.trim().length > 0).map((x) => x.trim());
  if (typeof value === "string") return value.split(/\r?\n/).map((x) => x.trim()).filter((x) => x.length > 0);
  return [];
}

function formatSignedYenDelta(deltaYen) {
  const n = toNumberOrNull(deltaYen);
  if (n === null) return null;
  const sign = n >= 0 ? "+" : "-";
  return `${sign}${formatYen(Math.abs(n))}`;
}

function formatDeltaSummary({ deltaRatio, subjectYen, benchmarkYen, bandPct = 1.0, decimals = 1 }) {
  const d = toNumberOrNull(deltaRatio);
  const bench = toNumberOrNull(benchmarkYen);
  if (d === null || bench === null || bench <= 0) return null;

  const subj = toNumberOrNull(subjectYen);
  const deltaYen = subj !== null ? subj - bench : Math.round(bench * d);
  const deltaText = formatSignedYenDelta(deltaYen);
  const absPct = Math.abs(d) * 100;

  if (absPct < bandPct) {
    return {
      kind: "band",
      text: `시세 범위 내(±${bandPct.toFixed(1)}%, Δ ${deltaText || "N/A"})`,
      absPct,
      deltaYen,
    };
  }
  return {
    kind: "value",
    text: `Δ ${deltaText || "N/A"} (약 ${absPct.toFixed(decimals)}%) ${d < 0 ? "저렴" : "비쌈"}`,
    absPct,
    deltaYen,
  };
}

function computeWhatIfInput(baseInput, mode, whatIfState) {
  const base = isObject(baseInput) ? baseInput : {};
  const next = { ...base };

  const totalRaw = base.initial_cost_total_yen;
  const total = typeof totalRaw === "number" ? totalRaw : Number(totalRaw || 0);
  const safeTotal = Number.isFinite(total) ? total : 0;

  if (mode === "total") {
    const pct = Number(whatIfState?.reductionPct ?? 0);
    const factor = Number.isFinite(pct) ? Math.max(0, 1 - pct / 100) : 1;
    next.initial_cost_total_yen = Math.max(0, Math.round(safeTotal * factor));
    return next;
  }

  const keys = ["reikin_yen", "brokerage_fee_yen", "key_change_yen", "cleaning_fee_yen"];
  let delta = 0;
  for (const k of keys) {
    const curRaw = base[k];
    const cur = typeof curRaw === "number" ? curRaw : Number(curRaw || 0);
    const curVal = Number.isFinite(cur) ? cur : 0;

    const action = whatIfState?.[k] ?? "no_change";
    const nextVal = action === "half" ? Math.round(curVal / 2) : action === "waive" ? 0 : curVal;

    next[k] = nextVal;
    delta += nextVal - curVal;
  }

  next.initial_cost_total_yen = Math.max(0, Math.round(safeTotal + delta));
  return next;
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
  const derivedMonthlyBase = pick(response, [
    "derived.monthly_base_cost_yen",
    "derived.monthly_fixed_cost_yen",
    "monthly_fixed_cost_yen",
  ]);
  const derivedMonthlyExtra = pick(response, ["derived.monthly_extra_cost_yen"]);
  const derivedMonthlyAllIn = pick(response, ["derived.monthly_all_in_cost_yen"]);
  const derivedAgeYears = pick(response, ["derived.building_age_years", "building_age_years"]);
  const derivedInitialMultiple = pick(response, ["derived.initial_multiple", "initial_multiple"]);
  const derivedInitialMultipleMoveIn = pick(response, ["derived.initial_multiple_move_in_total"]);
  const derivedInitialContractOnly = pick(response, ["derived.initial_cost_contract_only_yen"]);
  const derivedMoveInTotal = pick(response, ["derived.move_in_total_yen"]);
  const derivedInitialIncludesFirstMonth = pick(response, ["derived.initial_cost_includes_first_month_rent"]);
  const derivedBenchmarkTotal = pick(response, [
    "derived.benchmark_total_yen",
    "derived.benchmark_monthly_fixed_cost_yen",
    "benchmark_monthly_fixed_cost_yen",
  ]);
  const derivedBenchmarkRentOnly = pick(response, ["derived.benchmark_rent_only_yen"]);
  const derivedBenchmarkConf = pick(response, [
    "derived.benchmark_confidence",
    "benchmark_confidence",
    "benchmark.benchmark_confidence",
    "derived.benchmark_confidence",
  ]);
  const derivedRentDeltaTotal = pick(response, ["derived.rent_delta_ratio_total", "derived.rent_delta_ratio", "rent_delta_ratio"]);
  const derivedRentDeltaRentOnly = pick(response, ["derived.rent_delta_ratio_rent_only"]);
  const subjectSanity = pick(response, ["derived.subject_pricing_sanity"]);
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
    const monthlyBase = toNumberOrNull(derivedMonthlyBase);
    const monthlyExtra = toNumberOrNull(derivedMonthlyExtra);
    const monthlyAllIn = toNumberOrNull(derivedMonthlyAllIn);
    const walk = toNumberOrNull(input?.station_walk_min);
    const initialTotalInput = toNumberOrNull(input?.initial_cost_total_yen);
    const initialContractOnly = toNumberOrNull(derivedInitialContractOnly) ?? initialTotalInput;
    const moveInTotal = toNumberOrNull(derivedMoveInTotal);
    const initialMultiple = toNumberOrNull(derivedInitialMultiple);
    const initialMultipleMoveIn = toNumberOrNull(derivedInitialMultipleMoveIn);
    const includesFirstMonth = Boolean(derivedInitialIncludesFirstMonth);
    const rentDeltaTotal = toNumberOrNull(derivedRentDeltaTotal);
    const rentDeltaRentOnly = toNumberOrNull(derivedRentDeltaRentOnly);
    const confText = benchmarkConfidenceTextKo(derivedBenchmarkConf);
    const out = [];
    const sanitySuspect = Boolean(subjectSanity && typeof subjectSanity === "object" && subjectSanity.suspect);

    if (loc !== null && cond !== null && cost !== null) {
      if (cond - cost >= 20) out.push("집 상태는 괜찮지만, 월 고정비/초기비용 중 하나가 부담일 수 있어요.");
      else if (loc - cond >= 20) out.push("교통/입지는 괜찮지만, 집 상태는 타협이 필요해요.");
      else if (cost - loc >= 20) out.push("가격은 괜찮지만, 이동/입지는 불편할 수 있어요.");
      else out.push("입지·집 상태·비용이 비교적 균형 잡힌 편이에요.");
    }

    if (monthlyBase !== null) {
      out.push(`매달 내는 돈(기본: 월세+관리비)은 ${formatYen(monthlyBase)}예요.`);
      if (monthlyExtra !== null && monthlyExtra > 0) {
        const allIn = monthlyAllIn !== null ? monthlyAllIn : monthlyBase + monthlyExtra;
        out.push(`월 추가 고정비(옵션)는 ${formatYen(monthlyExtra)}이고, 합계는 ${formatYen(allIn)}예요.`);
      }
    }

    const walkTier = stationWalkRiskTier(walk);
    if (walkTier) out.push(walkTier.summary);

    if (initialContractOnly !== null && initialMultiple !== null) {
      const imAvg = toNumberOrNull(derivedImMarketAvg);
      const imLevelText = derivedImAssessment ? ` (수준: ${derivedImAssessment})` : "";
      const imForeignText =
        derivedImAssessmentForeigner && derivedImAssessmentForeigner !== derivedImAssessment
          ? ` / 외국인 기준: ${derivedImAssessmentForeigner}`
          : "";
      const avgHint = imAvg !== null ? ` [이 지역 시장 평균: 약 ${formatCompactNumber(imAvg)}개월치]` : "";
      const defNote = includesFirstMonth ? " (입력 초기비용에 1개월치 월세 포함으로 처리)" : "";

      out.push(
        `계약 초기비용(월세 제외)은 ${formatYen(initialContractOnly)}이고, 월 고정비(기본) 기준 약 ${formatCompactNumber(
          initialMultiple
        )}개월치예요.${imLevelText}${imForeignText}${avgHint}${defNote}`
      );
    }
    if (moveInTotal !== null && initialMultipleMoveIn !== null) {
      const extraHint = monthlyExtra !== null && monthlyExtra > 0 ? ` (월 추가 고정비 ${formatYen(monthlyExtra)} 포함)` : "";
      out.push(
        `입주 시점 총 납부액(가정: 1개월치 월 고정비 포함)은 ${formatYen(moveInTotal)}이고, 월 고정비(기본) 기준 약 ${formatCompactNumber(
          initialMultipleMoveIn
        )}개월치예요.${extraHint}`
      );
    }

    if (sanitySuspect) {
      out.push("가격/초기비용 입력값이 비정상적으로 보일 수 있어요. (상세 항목/단위/파싱값을 한 번 더 확인해 주세요)");
    } else if (String(derivedBenchmarkConf || "none") === "none") {
      out.push("이 지역은 시세 비교 데이터가 부족해서, 가격 비교는 정확하지 않을 수 있어요.");
    } else if (rentDeltaTotal !== null) {
      const benchTotal = toNumberOrNull(derivedBenchmarkTotal);
      const subjectTotal = monthlyBase;
      const totalSummary =
        benchTotal !== null
          ? formatDeltaSummary({ deltaRatio: rentDeltaTotal, subjectYen: subjectTotal, benchmarkYen: benchTotal, bandPct: 1.0, decimals: 1 })
          : null;
      if (totalSummary) {
        out.push(`비슷한 집 시세와 비교하면, 월 고정비(월세+관리비)는 ${totalSummary.text}. (신뢰도: ${confText})`);
      } else {
        out.push(`비슷한 집 시세와 비교하면, 월 고정비(월세+관리비) 비교를 계산하지 못했어요. (신뢰도: ${confText})`);
      }

      const benchRentOnly = toNumberOrNull(derivedBenchmarkRentOnly);
      const subjectRentOnly = toNumberOrNull(input?.rent_yen);
      if (benchRentOnly !== null && subjectRentOnly !== null) {
        const rentOnlyRatio =
          rentDeltaRentOnly !== null && Number.isFinite(rentDeltaRentOnly)
            ? rentDeltaRentOnly
            : (subjectRentOnly - benchRentOnly) / benchRentOnly;
        const rentOnlySummary = formatDeltaSummary({
          deltaRatio: rentOnlyRatio,
          subjectYen: subjectRentOnly,
          benchmarkYen: benchRentOnly,
          bandPct: 1.0,
          decimals: 1,
        });
        if (rentOnlySummary && rentOnlySummary.absPct >= 1.0) {
          out.push(`(참고) 월세(관리비 제외) 기준: ${rentOnlySummary.text}.`);
        }
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

    const rentOnly = toNumberOrNull(input?.rent_yen);
    if (rentOnly !== null) bullets.push(`월세(관리비 제외): ${formatYen(rentOnly)}`);
    const mgmt = toNumberOrNull(input?.mgmt_fee_yen);
    if (mgmt !== null) bullets.push(`관리비: ${formatYen(mgmt)}`);
    const monthlyBase = toNumberOrNull(derivedMonthlyBase);
    if (monthlyBase !== null) bullets.push(`매달 내는 돈(기본: 월세+관리비): ${formatYen(monthlyBase)}`);
    const monthlyExtra = toNumberOrNull(derivedMonthlyExtra);
    if (monthlyExtra !== null && monthlyExtra > 0) bullets.push(`월 추가 고정비(옵션): ${formatYen(monthlyExtra)}`);
    const monthlyAllIn = toNumberOrNull(derivedMonthlyAllIn);
    if (monthlyAllIn !== null && monthlyExtra !== null && monthlyExtra > 0) {
      bullets.push(`월 고정비 합계(기본+옵션): ${formatYen(monthlyAllIn)}`);
    }

    const initialContractOnly = toNumberOrNull(derivedInitialContractOnly) ?? toNumberOrNull(input?.initial_cost_total_yen);
    const initialMultiple = toNumberOrNull(derivedInitialMultiple);
    if (initialContractOnly !== null && initialMultiple !== null) {
      bullets.push(`계약 초기비용(월세 제외): ${formatYen(initialContractOnly)} (약 ${formatCompactNumber(initialMultiple)}개월치)`);
    }
    const moveInTotal = toNumberOrNull(derivedMoveInTotal);
    const initialMultipleMoveIn = toNumberOrNull(derivedInitialMultipleMoveIn);
    if (moveInTotal !== null && initialMultipleMoveIn !== null) {
      bullets.push(`입주 총 납부액(가정): ${formatYen(moveInTotal)} (약 ${formatCompactNumber(initialMultipleMoveIn)}개월치)`);
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

    const sanitySuspect = Boolean(subjectSanity && typeof subjectSanity === "object" && subjectSanity.suspect);
    const benchTotal = toNumberOrNull(derivedBenchmarkTotal);
    const rentDeltaTotal = toNumberOrNull(derivedRentDeltaTotal);
    const conf = String(derivedBenchmarkConf || "none");
    if (!sanitySuspect && conf !== "none" && benchTotal !== null && rentDeltaTotal !== null) {
      const subjectTotal = toNumberOrNull(derivedMonthlyBase);
      const totalSummary = formatDeltaSummary({
        deltaRatio: rentDeltaTotal,
        subjectYen: subjectTotal,
        benchmarkYen: benchTotal,
        bandPct: 1.0,
        decimals: 1,
      });
      if (totalSummary) {
        bullets.push(`시세(월 고정비 기준): ${formatYen(benchTotal)} · ${totalSummary.text} (신뢰도: ${benchmarkConfidenceTextKo(conf)})`);
      }

      const benchRentOnly = toNumberOrNull(derivedBenchmarkRentOnly);
      const subjectRentOnly = toNumberOrNull(input?.rent_yen);
      const rentDeltaRentOnly = toNumberOrNull(derivedRentDeltaRentOnly);
      if (benchRentOnly !== null && subjectRentOnly !== null) {
        const rentOnlySummary = formatDeltaSummary({
          deltaRatio: rentDeltaRentOnly !== null ? rentDeltaRentOnly : (subjectRentOnly - benchRentOnly) / benchRentOnly,
          subjectYen: subjectRentOnly,
          benchmarkYen: benchRentOnly,
          bandPct: 1.0,
          decimals: 1,
        });
        if (rentOnlySummary) {
          bullets.push(`(참고) 시세(월세만 기준): ${formatYen(benchRentOnly)} · ${rentOnlySummary.text}`);
        }
      }
    }

    return bullets;
  }

  // What-if UI (calls /api/evaluate with modified inputs)
  function deriveBreakdownMode(inp) {
    const keys = ["reikin_yen", "brokerage_fee_yen", "key_change_yen", "cleaning_fee_yen"];
    for (const k of keys) {
      if (Number(inp[k]) > 0) return "breakdown";
    }
    return "total";
  }
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
      renderComponentScoreCard("비용", scores.cost, grades.cost, "💴", makeCostDesc(derivedRentDeltaTotal, derivedBenchmarkConf, derivedImAssessment)),
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
    rentDeltaRatio: derivedRentDeltaTotal,
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
      h("li", { text: "비용: 시세 대비 월 고정비(월세+관리비) + 초기비용(IM). 초기비용이 시장 평균보다 높을수록 비용 점수가 내려가요." }),
    ]),
    h("div", { class: "hint", text: "시세 비교 데이터가 부족하면(신뢰도 없음) 비용 평가는 중립적으로 나올 수 있어요." }),
    h("div", { class: "hint", text: "등급 기준: A(85+) / B(70+) / C(55+) / D(0+)" }),
  ]);

  const evidenceCard = h("div", { class: "card" }, [h("h3", { text: "근거" }), renderBullets(makeFriendlyEvidenceBullets())]);

  const negotiationCard = h("div", { class: "card" }, [
    h("h3", { text: "협상/대안" }),
    h("div", { class: "divider" }),
    h("div", { class: "hint", text: "협상 제안" }),
    renderBullets(normalizeList(negKo)),
    normalizeList(negJa).length || normalizeList(altJa).length
      ? h("details", { open: false }, [
        h("summary", { class: "pill", text: "일본어 문구/검색 쿼리(복사용)" }),
        h("div", { class: "divider" }),
        h("div", { class: "hint", text: "협상 문구(일본어)" }),
        renderBullets(normalizeList(negJa)),
        h("div", { class: "divider" }),
        h("div", { class: "hint", text: "대안 검색 쿼리(일본어)" }),
        renderBullets(normalizeList(altJa)),
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
