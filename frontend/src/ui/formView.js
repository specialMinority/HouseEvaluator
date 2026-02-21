import { saveJson } from "../lib/storage.js";
import { formatCompactNumber, h, parseQuery, safeString } from "../lib/utils.js";

function fieldLabelText(key, field) {
  if (key === "hub_station") return "주요 이용역(자주 이용할 역)";
  if (key === "hub_station_other_name") return "기타 역명(직접 입력)";
  if (key === "prefecture") return "도도부현";
  if (key === "layout_type") return "평면(1R/1K/1DK/1LDK)";
  if (key === "building_structure") return "건물 구조(構造)";
  if (key === "hub_station") return "주요 이용역(자주 이용할 역)";
  if (key === "hub_station_other_name") return "역 이름(직접 입력)";
  return field.label_ko || key;
}

function fieldHelpTextKo(key, field) {
  if (key === "hub_station") {
    return "자주 이용할 주요 역(허브 역)을 선택하세요. 오사카권이면 오사카/우메다/난바/덴노지 등을 선택할 수 있어요.";
  }
  if (key === "hub_station_other_name") {
    return "위에서 '기타'를 선택한 경우에만, 역명을 직접 입력해 주세요.";
  }
  const ui = field.ui || {};
  if (key === "hub_station") {
    return "출퇴근/생활권 기준이 되는 역을 선택하세요. (입지 점수는 ‘역까지 도보 시간’이 가장 크게 반영됩니다.)";
  }
  if (key === "hub_station_other_name") {
    return "위에서 ‘기타’를 선택한 경우에만, 역 이름을 적어주세요.";
  }
  return ui.help_text_ko || "";
}

function enumOptionText(fieldKey, value) {
  const fk = String(fieldKey || "");
  const v = String(value);
  if (fk === "prefecture") {
    const table = {
      tokyo: "도쿄",
      saitama: "사이타마",
      chiba: "치바",
      kanagawa: "가나가와",
      osaka: "오사카",
    };
    if (Object.prototype.hasOwnProperty.call(table, v)) return table[v];
  }
  if (fk === "hub_station") {
    const table = {
      tokyo_station: "도쿄역",
      shinjuku: "신주쿠",
      shibuya: "시부야",
      ikebukuro: "이케부쿠로",
      ueno: "우에노",
      shinagawa: "시나가와",
      osaka_station: "오사카역",
      umeda: "우메다",
      namba: "난바",
      tennoji: "덴노지",
      other: "기타(직접 입력)",
    };
    if (Object.prototype.hasOwnProperty.call(table, v)) return table[v];
  }
  if (fk === "orientation") {
    const table = {
      N: "북향(N)",
      NE: "북동향(NE)",
      E: "동향(E)",
      SE: "남동향(SE)",
      S: "남향(S)",
      SW: "남서향(SW)",
      W: "서향(W)",
      NW: "북서향(NW)",
      UNKNOWN: "모름(UNKNOWN)",
    };
    if (Object.prototype.hasOwnProperty.call(table, v)) return table[v];
  }
  if (fk === "building_structure") {
    const table = {
      wood: "목조(木造)",
      light_steel: "경량철골(軽量鉄骨)",
      steel: "철골(鉄骨/S造)",
      rc: "철근콘크리트(RC)",
      src: "철골철근콘크리트(SRC)",
      other: "기타/모름",
    };
    if (Object.prototype.hasOwnProperty.call(table, v)) return table[v];
  }
  const map = {
    prefecture: {
      tokyo: "도쿄",
      saitama: "사이타마",
      chiba: "치바",
      kanagawa: "카나가와",
      osaka: "오사카",
    },
    building_structure: {
      wood: "목조(木造)",
      light_steel: "경량철골(軽量鉄骨)",
      steel: "철골(鉄骨/S造)",
      rc: "철근콘크리트(RC)",
      src: "철골철근콘크리트(SRC)",
      other: "기타/모름",
    },
    hub_station: {
      tokyo_station: "도쿄역",
      shinjuku: "신주쿠",
      shibuya: "시부야",
      ikebukuro: "이케부쿠로",
      ueno: "우에노",
      shinagawa: "시나가와",
      osaka_station: "오사카역(大阪駅)",
      umeda: "우메다(梅田)",
      namba: "난바(難波)",
      tennoji: "텐노지(天王寺)",
      other: "기타(역 이름 직접 입력)",
    },
    orientation: {
      N: "북쪽(N)",
      NE: "북동쪽(NE)",
      E: "동쪽(E)",
      SE: "남동쪽(SE)",
      S: "남쪽(S)",
      SW: "남서쪽(SW)",
      W: "서쪽(W)",
      NW: "북서쪽(NW)",
      UNKNOWN: "모름(UNKNOWN)",
    },
  };

  const table = map[fieldKey];
  if (table && Object.prototype.hasOwnProperty.call(table, value)) return table[value];
  return value;
}

function groupLabel(group) {
  const ko = {
    location: "입지/교통",
    condition: "집 컨디션",
    cost: "비용(계약 기준)",
    contract: "계약 리스크(선택)",
    meta: "기타",
  };
  if (Object.prototype.hasOwnProperty.call(ko, group)) return ko[group];
  const map = {
    location: "입지/교통",
    condition: "집 컨디션",
    cost: "비용(계약 기준)",
    contract: "계약 리스크(선택)",
    meta: "기타",
  };
  return map[group] || group;
}

function unitLabel(unit) {
  const ko = { yen: "JPY", sqm: "m²", min: "분", year: "년", months: "개월", ratio: "", score: "", none: "" };
  if (Object.prototype.hasOwnProperty.call(ko, unit)) return ko[unit];
  const map = { yen: "JPY", sqm: "m²", min: "분", year: "년", months: "개월", ratio: "", score: "", none: "" };
  return map[unit] ?? unit ?? "";
}

function isVisible(field, values) {
  const dep = field.depends_on;
  if (!dep) return true;
  const key = dep.key;
  const actual = values[key];
  if (Object.prototype.hasOwnProperty.call(dep, "equals")) return actual === dep.equals;
  if (Object.prototype.hasOwnProperty.call(dep, "not_equals")) return actual !== dep.not_equals;
  return true;
}

function parseValue(field, raw) {
  const t = field.type;
  if (t === "boolean") return Boolean(raw);
  if (t === "integer") {
    if (raw === "" || raw === null || raw === undefined) return undefined;
    const n = Number(raw);
    if (!Number.isFinite(n)) return undefined;
    return Number.isInteger(n) ? n : undefined;
  }
  if (t === "number") {
    if (raw === "" || raw === null || raw === undefined) return undefined;
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  }
  if (t === "enum") {
    if (raw === "" || raw === null || raw === undefined) return undefined;
    return String(raw);
  }
  if (t === "string") {
    if (raw === "" || raw === null || raw === undefined) return undefined;
    const s = String(raw).trim();
    return s.length ? s : undefined;
  }
  return raw;
}

function validateField(field, value, values) {
  const visible = isVisible(field, values);
  if (!visible) return null;

  const required = Boolean(field.required) || Boolean(field.depends_on);
  if (required && (value === undefined || value === null || value === "")) return "필수 입력입니다.";

  if (value === undefined || value === null || value === "") return null;

  const c = field.constraints || {};

  if (field.type === "enum") {
    const allowed = new Set((c.enum_values || []).map(String));
    if (allowed.size && !allowed.has(String(value))) return "허용되지 않은 값입니다.";
  }

  if (field.type === "integer" || field.type === "number") {
    if (typeof value !== "number" || !Number.isFinite(value)) return "숫자 형식이 올바르지 않습니다.";
    if (Object.prototype.hasOwnProperty.call(c, "min") && value < Number(c.min)) return `최소값은 ${c.min} 입니다.`;
    const enforceMax = String(field.unit || "none") !== "yen";
    if (enforceMax && Object.prototype.hasOwnProperty.call(c, "max") && value > Number(c.max)) return `최대값은 ${c.max} 입니다.`;
  }

  return null;
}

function buildRequest(fields, values) {
  const out = {};
  for (const f of fields) {
    if (!isVisible(f, values)) continue;
    const v = values[f.key];
    if (v === undefined || v === null || v === "") continue;
    out[f.key] = v;
  }
  return out;
}

function renderField(field, { values, touched, onChange }) {
  const key = field.key;
  const ui = field.ui || {};
  const c = field.constraints || {};
  const value = values[key];

  const hintParts = [];
  if (field.unit && field.unit !== "none") hintParts.push(unitLabel(field.unit));
  const showRange = String(field.unit || "none") !== "yen";
  if (showRange && (field.type === "integer" || field.type === "number") && (c.min !== undefined || c.max !== undefined)) {
    hintParts.push(`${c.min ?? "?"} ~ ${c.max ?? "?"}`);
  }
  const hint = hintParts.filter(Boolean).join(" · ");

  const error = touched[key] ? validateField(field, value, values) : null;
  const errorEl = h("div", { class: "error", text: error || "" });

  const label = h("label", { for: `f_${key}`, text: fieldLabelText(key, field) });

  let control;
  if (field.type === "enum") {
    control = h(
      "select",
      {
        id: `f_${key}`,
        name: key,
        onChange: (e) => onChange(key, parseValue(field, e.target.value)),
      },
      [
        h("option", { value: "", text: "선택…" }),
        ...(c.enum_values || []).map((opt) =>
          h("option", { value: opt, text: enumOptionText(key, opt), selected: opt === value })
        ),
      ]
    );
  } else if (field.type === "boolean") {
    control = h("div", { class: "toggle" }, [
      h("input", {
        id: `f_${key}`,
        name: key,
        type: "checkbox",
        checked: Boolean(value),
        onChange: (e) => onChange(key, parseValue(field, e.target.checked)),
      }),
      h("span", { text: Boolean(value) ? "예" : "아니오" }),
    ]);
  } else if (ui.control === "slider" && (field.type === "integer" || field.type === "number")) {
    const min = c.min ?? 0;
    const max = c.max ?? 60;
    const step = c.step ?? 1;
    const current = value ?? min;
    const valueHint = h("div", { class: "hint", text: `현재: ${formatCompactNumber(current)} ${unitLabel(field.unit)}` });
    control = h("div", { class: "field" }, [
      h("input", {
        id: `f_${key}`,
        name: key,
        type: "range",
        min: String(min),
        max: String(max),
        step: String(step),
        value: String(current),
        onInput: (e) => {
          const next = parseValue(field, e.target.value);
          onChange(key, next);
          const shown = next ?? min;
          valueHint.textContent = `현재: ${formatCompactNumber(shown)} ${unitLabel(field.unit)}`;
        },
      }),
      valueHint,
    ]);
  } else {
    const type = field.type === "integer" || field.type === "number" ? "number" : "text";
    const attrs = {
      id: `f_${key}`,
      name: key,
      type,
      value: value ?? "",
      placeholder: ui.placeholder_ko || "",
      onInput: (e) => onChange(key, parseValue(field, e.target.value)),
    };
    if (type === "number") {
      if (c.min !== undefined) attrs.min = String(c.min);
      if (showRange && c.max !== undefined) attrs.max = String(c.max);
      if (showRange && c.step !== undefined) attrs.step = String(c.step);
    }
    control = h("input", attrs);
  }

  const hintEl = hint ? h("div", { class: "hint", text: hint }) : null;
  const helpText = fieldHelpTextKo(key, field);
  const helpEl = helpText ? h("div", { class: "hint", text: helpText }) : null;

  return h("div", { class: "field", dataset: { key } }, [label, control, hintEl, helpEl, errorEl]);
}

function applyYokohamaPlaceholder(fieldsByKey, values, field) {
  if (field.key !== "municipality") return field;
  if (values.prefecture !== "kanagawa") return field;
  const ui = { ...(field.ui || {}) };
  ui.placeholder_ko = ui.placeholder_ko || "예: 横浜市港北区";
  return { ...field, ui };
}

function renderGuideBanner() {
  const dismissed = sessionStorage.getItem("guideDismissed") === "1";
  if (dismissed) return null;

  const banner = h("div", { class: "guide-banner" }, [
    h("div", { class: "guide-banner__title", html: "\u{1F3E0} \uC774\uC6A9 \uAC00\uC774\uB4DC" }),
    h("div", {
      class: "guide-banner__text", html:
        "\u{1F4DD} \uB9E4\uBB3C \uC815\uBCF4\uB97C \uC785\uB825\uD558\uACE0 <strong>\uD3C9\uAC00\uD558\uAE30</strong>\uB97C \uB204\uB974\uBA74 \uC885\uD569 \uD3C9\uAC00 \uB9AC\uD3EC\uD2B8\uAC00 \uC0DD\uC131\uB429\uB2C8\uB2E4.<br>" +
        "\u{1F4CB} \uACAC\uC801\uC11C\uC5D0 \uB098\uC640\uC788\uB294 \uC0C1\uC138\uB0B4\uC6A9\uC744 \uC785\uB825\uD558\uBA74 \uC815\uD655\uB3C4\uAC00 \uB354\uC6B1 \uC62C\uB77C\uAC11\uB2C8\uB2E4."
    }),
    h("button", {
      class: "guide-banner__dismiss",
      text: "\uB2EB\uAE30",
      onClick: (e) => {
        sessionStorage.setItem("guideDismissed", "1");
        e.target.closest(".guide-banner").remove();
      },
    }),
  ]);
  return banner;
}

export function renderFormView({ spec, input, fixtures, loading, onSubmit, onReset }) {
  const s1 = spec.s1;
  const fields = s1.fields || [];
  const fieldsByKey = new Map(fields.map((f) => [f.key, f]));
  const mvp = new Set(s1.mvp_required_fields || []);

  const values = {};
  for (const f of fields) {
    values[f.key] = input?.[f.key];
  }
  for (const f of fields) {
    if (f.type === "boolean" && f.required === true && (values[f.key] === undefined || values[f.key] === null)) {
      values[f.key] = true;
    }
  }

  const touched = {};
  const visibleContainers = new Map();

  function markTouchedAll() {
    for (const f of fields) touched[f.key] = true;
  }

  function setValue(key, v) {
    values[key] = v;
    saveJson("draftInput", values);
    updateVisibility();
    updateErrors();
  }

  function updateVisibility() {
    for (const f of fields) {
      const cont = visibleContainers.get(f.key);
      if (!cont) continue;
      const vis = isVisible(f, values);
      cont.classList.toggle("hidden", !vis);
      if (!vis) values[f.key] = undefined;
    }
  }

  function updateErrors() {
    for (const f of fields) {
      const cont = visibleContainers.get(f.key);
      if (!cont) continue;
      const errEl = cont.querySelector(".error");
      if (!errEl) continue;
      const err = touched[f.key] ? validateField(f, values[f.key], values) : null;
      errEl.textContent = err || "";
    }
  }

  function onChange(key, v) {
    touched[key] = true;
    setValue(key, v);
  }

  function renderGroup(group, filterFn) {
    const groupFields = fields
      .filter((f) => (f.ui || {}).group === group)
      .filter(filterFn)
      .map((f) => applyYokohamaPlaceholder(fieldsByKey, values, f));

    const items = [];
    for (const f of groupFields) {
      const node = renderField(f, { values, touched, onChange });
      visibleContainers.set(f.key, node);
      items.push(node);
    }
    if (items.length === 0) return null;

    return h("div", {}, [h("div", { class: "section-title", text: groupLabel(group) }), h("div", { class: "row row--2" }, items)]);
  }

  function validateAllVisible() {
    const errs = [];
    for (const f of fields) {
      if (!isVisible(f, values)) continue;
      const err = validateField(f, values[f.key], values);
      if (err) errs.push({ key: f.key, err });
    }
    return errs;
  }

  function submit(e) {
    e.preventDefault();
    markTouchedAll();
    updateErrors();
    const errs = validateAllVisible();
    if (errs.length) return;
    const req = buildRequest(fields, values);
    onSubmit(req);
  }

  const fixtureOptions = Array.isArray(fixtures?.cases) ? fixtures.cases : Array.isArray(fixtures) ? fixtures : [];

  const fixtureSelect = h(
    "select",
    {
      onChange: (e) => {
        const id = e.target.value;
        const sel = fixtureOptions.find((c) => c.id === id);
        if (!sel) return;
        for (const f of fields) values[f.key] = sel.input?.[f.key];
        for (const f of fields) {
          if (f.type === "boolean" && f.required === true && (values[f.key] === undefined || values[f.key] === null)) {
            values[f.key] = true;
          }
        }
        for (const f of fields) touched[f.key] = false;
        updateVisibility();
        // update DOM inputs
        for (const f of fields) {
          const el = document.getElementById(`f_${f.key}`);
          if (!el) continue;
          const v = values[f.key];
          if (el.type === "checkbox") el.checked = Boolean(v);
          else if (el.tagName === "SELECT") el.value = v ?? "";
          else el.value = v ?? "";
        }
        updateErrors();
      },
    },
    [h("option", { value: "", text: "예시 불러오기…" }), ...fixtureOptions.map((c) => h("option", { value: c.id, text: c.label || c.id }))]
  );

  const basicFilter = (f) => mvp.has(f.key) || (f.depends_on && !(f.ui || {}).advanced);
  const advancedFilter = (f) => (f.ui || {}).advanced === true;

  const guideBanner = renderGuideBanner();

  const content = h("form", { onSubmit: submit, novalidate: true }, [
    guideBanner,
    h("div", { class: "panel" }, [
      h("div", { class: "panel__header" }, [
        h("div", {}, [
          h("h2", { class: "panel__title", text: "입력" }),
          h("div", {
            class: "panel__subtitle",
            text: "기본 입력만 먼저 채우고, 필요하면 ‘상세 입력’을 열어 추가 정보를 입력하세요.",
          }),
        ]),
        h("div", { class: "app-header__badges" }, [h("span", { class: "badge", text: `필수 항목: ${mvp.size}개` })]),
      ]),
      h("div", { class: "panel__body" }, [
        h("div", { class: "row" }, [
          h("div", { class: "card card--url-import" }, [
            h("h3", { text: "🔗 SUUMO URL로 자동 채우기 (선택)" }),
            h("div", {
              class: "hint",
              text: "SUUMOの物件詳細URL(suumo.jp/chintai/jnc_...)を貼り付けると、家賃・面積・間取りなどを自動で入力します。",
            }),
            h("div", { class: "row url-import__row" }, [
              (() => {
                const urlInput = h("input", {
                  type: "url",
                  id: "suumo-url-input",
                  class: "input url-import__input",
                  placeholder: "https://suumo.jp/chintai/jnc_... または 검색결과 URL",
                });
                const urlStatus = h("span", { class: "url-import__status" });
                const debugPre = h("pre", { class: "mono", text: "" });
                const debugDetails = h("details", { class: "hidden", open: false }, [
                  h("summary", { class: "pill", text: "debug: parse-url fields" }),
                  debugPre,
                ]);
                const parseBtn = h("button", {
                  type: "button",
                  class: "btn btn--ghost btn--sm",
                  text: "자동 채우기",
                  onClick: async () => {
                    const rawUrl = urlInput.value.trim();
                    if (!rawUrl) return;
                    parseBtn.disabled = true;
                    urlStatus.textContent = "⏳ 파싱 중…";
                    urlStatus.className = "url-import__status url-import__status--loading";
                    try {
                      const q = parseQuery();
                      const apiBase = q.apiBase ? String(q.apiBase).replace(/\/$/, "") : "";
                      const resp = await fetch(`${apiBase}/api/parse-url`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ url: rawUrl }),
                      });
                      const data = await resp.json();
                      if (!resp.ok || data.error) {
                        urlStatus.textContent = `❌ ${data.message || "파싱 실패"}`;
                        urlStatus.className = "url-import__status url-import__status--error";
                        return;
                      }
                      const fields = data.fields || {};
                      console.log("[parse-url] parsed fields:", fields);
                      debugPre.textContent = JSON.stringify(fields, null, 2);
                      debugDetails.classList.add("hidden");

                      const FIELD_ALIASES = {
                        management_fee_yen: "mgmt_fee_yen",
                        walk_min: "station_walk_min",
                      };

                      const normalizeBuildingStructure = (raw) => {
                        const t = String(raw ?? "").trim();
                        if (!t) return t;
                        const ascii = t.replace(/Ｒ/g, "R").replace(/Ｃ/g, "C").replace(/Ｓ/g, "S");
                        const up = ascii.toUpperCase();
                        if (up.includes("SRC") || t.includes("鉄骨鉄筋")) return "src";
                        if (up.includes("RC") || t.includes("鉄筋コンクリート")) return "rc";
                        if (t.includes("軽量鉄骨")) return "light_steel";
                        if (t.includes("鉄骨")) return "steel";
                        if (t.includes("木造") || t.includes("木")) return "wood";
                        return t.toLowerCase();
                      };

                      const filledKeys = new Set();
                      const failedFills = [];
                      const tryFill = (key, value) => {
                        if (!key) return false;
                        if (filledKeys.has(key)) return false;

                        const el = document.getElementById(`f_${key}`);
                        if (!el) {
                          failedFills.push({ key, reason: "no_element" });
                          return false;
                        }

                        if (el.type === "checkbox") {
                          el.checked = Boolean(value);
                          el.dispatchEvent(new Event("change", { bubbles: true }));
                        } else if (el.tagName === "SELECT") {
                          let next = String(value);
                          if (key === "building_structure") next = normalizeBuildingStructure(next);
                          el.value = next;
                          el.dispatchEvent(new Event("change", { bubbles: true }));
                          if (el.value !== next) {
                            const options = Array.from(el.options || []).map((o) => o.value);
                            failedFills.push({ key, reason: "invalid_option", value: next, options });
                            console.warn(`[parse-url] failed to set select '${key}' to '${next}'. options=`, options);
                            return false;
                          }
                        } else {
                          el.value = value;
                          el.dispatchEvent(new Event("input", { bubbles: true }));
                        }

                        filledKeys.add(key);
                        return true;
                      };

                      // Prefer built-year if provided; otherwise derive it from age.
                      if (fields.building_built_year !== undefined) {
                        tryFill("building_built_year", fields.building_built_year);
                      } else if (fields.building_age_years !== undefined) {
                        const yr = new Date().getFullYear() - Number(fields.building_age_years);
                        if (Number.isFinite(yr) && yr > 0) tryFill("building_built_year", yr);
                      }

                      for (const [src, value] of Object.entries(fields)) {
                        if (value === undefined) continue;
                        if (src === "building_age_years" || src === "building_built_year") continue;
                        const dst = FIELD_ALIASES[src] || src;
                        tryFill(dst, value);
                      }

                      const filledCount = filledKeys.size;
                      if (failedFills.length) console.log("[parse-url] failed fills:", failedFills);

                      const structureRaw =
                        fields.building_structure !== undefined && fields.building_structure !== null
                          ? String(fields.building_structure)
                          : null;
                      const structureFilled = filledKeys.has("building_structure");
                      const structureCode = structureRaw ? normalizeBuildingStructure(structureRaw) : null;
                      const structureText = structureCode ? enumOptionText("building_structure", structureCode) : null;
                      const structureBadge = structureRaw
                        ? ` / 구조: ${structureText}${structureFilled ? "" : " (미반영)"}`
                        : " / 구조: 없음";

                      if (failedFills.length || !structureRaw || !structureFilled) {
                        debugDetails.classList.remove("hidden");
                      }

                      urlStatus.textContent = filledCount > 0
                        ? `✅ ${filledCount}개 항목 자동 입력 완료${failedFills.length ? ` (실패: ${failedFills.map((f) => f.key).join(", ")})` : ""}${structureBadge}`
                        : "⚠️ 파싱됐지만 매칭 필드 없음";
                      urlStatus.className = "url-import__status url-import__status--ok";
                    } catch (err) {
                      urlStatus.textContent = `❌ 오류: ${err.message}`;
                      urlStatus.className = "url-import__status url-import__status--error";
                    } finally {
                      parseBtn.disabled = false;
                    }
                  },
                });
                return h("div", {}, [
                  h("div", { class: "url-import__inner" }, [urlInput, parseBtn, urlStatus]),
                  debugDetails,
                ]);
              })(),
            ]),
          ]),
          h("div", { class: "divider" }),
          h("div", { class: "card" }, [
            h("h3", { text: "예시" }),
            h("div", { class: "row" }, [fixtureSelect]),
            h("div", { class: "hint", text: "예시 입력을 불러올 수 있어요." }),
          ]),


          renderGroup("location", (f) => basicFilter(f) && !(f.ui || {}).advanced),
          renderGroup("condition", (f) => basicFilter(f) && !(f.ui || {}).advanced),
          renderGroup("cost", (f) => basicFilter(f) && !(f.ui || {}).advanced),

          h("details", { open: false }, [
            h("summary", { class: "pill", text: "상세 입력(선택)" }),
            h("div", { class: "toast", text: "선택 항목입니다. 정보가 있을 때만 추가로 입력해도 충분합니다." }),
            renderGroup("location", (f) => advancedFilter(f)),
            renderGroup("cost", (f) => advancedFilter(f)),
            renderGroup("contract", (f) => advancedFilter(f)),
            renderGroup("meta", (f) => advancedFilter(f)),
          ]),
        ]),

        h("div", { class: "actions" }, [
          h("button", { type: "button", class: "btn btn--ghost", onClick: () => onReset(), disabled: loading, text: "초기화" }),
          h("button", { type: "submit", class: "btn", disabled: loading, text: loading ? "평가 중…" : "평가하기" }),
        ]),
      ]),
    ]),
  ]);

  // register containers and initial vis/errors after nodes exist
  queueMicrotask(() => {
    updateVisibility();
    updateErrors();
  });

  return content;
}
