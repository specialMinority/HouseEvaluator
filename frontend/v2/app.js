"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const subjectForm = $("subject-form");
  const costForm = $("cost-form");
  const numberFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 });
  const dateFormat = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const fields = {
    city: "도시", municipality: "행정구역", station_id: "가까운 역", station_name: "역 이름",
    unit_id: "호실 ID", building_id: "건물 ID", layout: "평면", structure: "구조",
    property_type: "주택 유형", contract_type: "계약 종류", furnished: "가구 포함 여부",
    area_sqm: "면적", built_year: "준공 연도", walk_min: "역 도보", floor: "층",
    orientation: "방향", bathroom_separate: "욕실·화장실 분리", rent_yen: "월세", mgmt_fee_yen: "관리비",
  };
  const structures = { wood: "목조", light_steel: "경량철골", steel: "철골", rc: "RC", src: "SRC" };
  const orientations = { N: "북향", NE: "북동향", E: "동향", SE: "남동향", S: "남향", SW: "남서향", W: "서향", NW: "북서향" };
  const reasons = {
    policy_unvalidated: "이 도시·평면의 비교 정책은 아직 실제 시장 자료로 검증되지 않았습니다.",
    synthetic_data: "합성 데이터로 계산한 기능 시연입니다. 실제 시장 가격을 나타내지 않습니다.",
    source_rights_unverified: "공급 자료의 사용 권한이 확인되지 않았습니다.",
    identity_unverified: "대상 호실·건물 ID가 없어 자기 매물의 제외 여부를 확인할 수 없습니다.",
    self_building_excluded: "대상 호실 ID가 없어 같은 건물의 매물을 모두 제외했습니다.",
    missing_critical_fields: "동일 조건 확인에 필요한 입력 정보가 없습니다. 미상 항목을 확인해 주세요.",
    source_scope_limited: "비교 자료의 독립적인 원청 범위가 제한되어 있습니다.",
    source_independence_unverified: "비교 자료가 서로 독립된 원청에서 왔는지 확인되지 않았습니다.",
    orientation_unverified: "일부 방향 정보가 미상이라 같은 방향의 비교인지 확인할 수 없습니다.",
    conditions_relaxed: "충분한 비교 매물을 찾기 위해 아래 기록에 표시된 조건을 완화했습니다.",
    freshness_expired: "모집 상태 확인 시점이 기준을 지나 현재 가격 판정을 보류합니다.",
    insufficient_sample: "조건과 최신성 기준을 충족한 독립 매물이 부족합니다.",
    limited_sample: "표본이 제한적이어서 대표가격만 참고할 수 있으며 방향 판정은 보류합니다.",
    no_market_data: "현재 사용할 수 있는 실제 모집 매물 자료가 없습니다.",
    market_data_unavailable: "현재 사용할 수 있는 실제 모집 매물 자료가 없습니다.",
  };
  const excludedLabels = {
    inactive: "모집 중 아님", inactive_status: "모집 중 아님", stale: "최신성 기준 초과",
    stale_status: "모집 상태 확인 기한 초과", missing_status_verified_at: "모집 확인 시각 미상",
    future_timestamp: "미래 시각", invalid_timestamp: "시각 형식 오류", invalid_observation: "유효하지 않은 매물",
    self_unit: "대상 호실", self_building: "대상과 같은 건물", duplicate_unit: "중복 호실",
    conflicting_unit: "동일 호실의 상충 정보", conflicting_active_offers: "동일 호실의 상충 가격",
    missing_critical_conditions: "필수 조건 미상", outside_bounded_conditions: "허용 비교 범위 밖",
    floor_band_boundary: "층 구간 다름", new_build_boundary: "신축·기존 건물 구분",
    mode_mismatch: "자료 종류 다름", price_unknown: "총 월세 미상", source_rights: "사용권 미확인",
    data_kind_mismatch: "실제·합성 자료 종류 불일치", source_not_authorized: "허용된 공급처 아님",
    superseded_observation: "새 관측으로 대체됨", ambiguous_latest_observation: "최신 관측 정보 상충",
    duplicate_observation: "중복 관측", ambiguous_canonical_unit: "호실 정규 ID 상충",
    status_unverified: "모집 상태 미확인", stale_observation: "모집 확인 기한 초과",
    conflicting_status: "모집 상태 상충", inactive_observation: "모집 중 아님",
  };
  const state = {
    mode: "market", capabilities: null, result: null, evaluation: null, costRequest: null,
    demoRequest: null, sequence: 0, costSequence: 0, demoSequence: 0,
    stationRequest: null, stationSequence: 0, stationLoading: false, stationAvailable: false,
    accessToken: "",
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function isNumber(value) { return typeof value === "number" && Number.isFinite(value); }
  function number(value) { return isNumber(value) ? numberFormat.format(value) : "미상"; }
  function weightPercent(value) {
    if (!isNumber(value) || value < 0) return "확인 필요";
    const percent = value * 100;
    if (percent > 0 && percent < 0.01) return "<0.01%";
    return `${new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(percent)}%`;
  }
  function yen(value) { return isNumber(value) ? `${number(value)}엔` : "확인 필요"; }
  function date(value) {
    if (typeof value !== "string" || !value) return "미상";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "미상" : `${dateFormat.format(parsed)} JST`;
  }
  function list(value) { return Array.isArray(value) ? value : []; }
  function display(value) { return value === null || value === undefined || value === "" ? "미상" : String(value); }
  function formValue(name) { return subjectForm.elements.namedItem(name).value; }
  function numericInput(name) { const value = formValue(name).trim(); return value === "" ? null : Number(value); }
  function boolInput(name) { const value = formValue(name); return value === "" ? null : value === "true"; }
  function textInput(name) { return formValue(name).trim() || null; }
  function setError(id, message) { $(id).textContent = message || ""; $(id).hidden = !message; }
  function updateBanner() { $("demo-banner").hidden = !(state.mode === "demo" || state.result?.is_demo === true); }

  async function request(path, { body, signal } = {}) {
    const token = state.accessToken;
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST", cache: "no-store", credentials: "same-origin", signal,
      headers: { Accept: "application/json", ...(token ? { Authorization: `Bearer ${/[^\x21-\x7e]/.test(token) ? encodeURIComponent(token) : token}` } : {}), ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    let payload;
    try { payload = await response.json(); }
    catch { throw new Error("서버 응답을 읽을 수 없습니다. 서버 연결 상태를 확인해 주세요."); }
    if (response.status === 401 && token === state.accessToken) {
      state.accessToken = "";
      $("access-panel").hidden = false;
      $("access-logout").hidden = true;
      if (token) $("access-error").textContent = "접속 코드를 확인해 주세요.";
    }
    if (!response.ok) throw new Error(typeof payload?.message === "string" ? payload.message : `요청을 처리하지 못했습니다 (${response.status}).`);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new Error("응답 형식을 확인할 수 없습니다.");
    return payload;
  }

  async function loadCapabilities() {
    $("retry-capabilities").hidden = true;
    try {
      const capabilities = await request("/api/v2/capabilities");
      state.capabilities = capabilities;
      $("access-panel").hidden = true;
      $("access-logout").hidden = !state.accessToken;
      const available = capabilities.market_data_available === true;
      $("source-status").textContent = available ? "실제 공급 자료 연결됨" : "실제 시장 자료 미연결 · 가격 판정 보류";
      $("source-detail").textContent = available
        ? `현재 사용 가능한 공급원 ${number(capabilities.source_count)}개 · 도시·조건별 표본과 검증 기준을 통과한 경우에만 판정합니다.`
        : "사용권이 확인된 모집 자료가 연결되면 비교할 수 있습니다. 현재는 실제 시세를 산출하지 않습니다.";
      $("source-status").closest(".source-notice").classList.toggle("available", available);
      $("demo-controls").hidden = capabilities.demo_enabled !== true;
      if (capabilities.demo_enabled !== true && state.mode === "demo") switchToMarket();
      if (!available) $("placeholder-message").textContent = "실제 공급 자료가 아직 연결되지 않았습니다. 입력값을 확인할 수 있으며, 현재 시세 판단은 보류합니다.";
    } catch (error) {
      $("source-status").textContent = "자료 연결 상태를 확인하지 못했습니다";
      $("source-detail").textContent = error.message;
      $("retry-capabilities").hidden = false;
      $("demo-controls").hidden = true;
    }
  }

  function updateEvaluateButton() {
    const busy = state.evaluation !== null || state.demoRequest !== null;
    $("evaluate-button").disabled = busy || state.stationLoading || !state.stationAvailable;
    $("evaluate-button").textContent = busy ? "비교 자료 확인 중…" : state.stationLoading ? "역 목록 확인 중…" : !state.stationAvailable ? "역 자료 연결 후 비교 가능" : "비교 근거 확인하기 ↗";
  }
  function applyStationSelection() {
    const option = $("station_id").selectedOptions[0];
    $("station_name").value = option?.dataset.stationName || "";
    $("municipality").value = option?.dataset.municipality || "";
  }
  async function loadStations() {
    state.stationRequest?.abort();
    const controller = new AbortController(), token = ++state.stationSequence;
    const city = formValue("city"), mode = state.mode;
    state.stationRequest = controller;
    state.stationLoading = true;
    state.stationAvailable = false;
    const select = $("station_id");
    const placeholder = el("option", "", "역 목록 확인 중…"); placeholder.value = "";
    select.replaceChildren(placeholder);
    select.disabled = true;
    $("station_name").value = "";
    $("municipality").value = "";
    $("station-status").textContent = "연결된 공급 자료에서 선택 가능한 역을 확인하고 있습니다.";
    $("reload-stations").hidden = true;
    updateEvaluateButton();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const response = await request(`/api/v2/stations?city=${encodeURIComponent(city)}&mode=${encodeURIComponent(mode)}`, { signal: controller.signal });
      if (token !== state.stationSequence || city !== formValue("city") || mode !== state.mode) return false;
      if (response.city !== city || response.mode !== mode || !Array.isArray(response.stations)) throw new Error("현재 도시의 역 목록을 확인할 수 없습니다.");
      const stations = response.stations.filter((entry) => entry && typeof entry.station_id === "string" && entry.station_id.trim() && typeof entry.municipality === "string" && entry.municipality.trim());
      placeholder.textContent = stations.length ? "역을 선택하세요" : "선택할 역 자료 미연결";
      for (const station of stations) {
        const name = typeof station.station_name === "string" && station.station_name.trim() ? station.station_name.trim() : "역 이름 확인 필요";
        const option = el("option", "", `${name} · ${station.municipality}`);
        option.value = station.station_id;
        option.dataset.stationName = typeof station.station_name === "string" ? station.station_name : "";
        option.dataset.municipality = station.municipality;
        select.append(option);
      }
      state.stationAvailable = stations.length > 0;
      select.disabled = !state.stationAvailable;
      $("station-status").textContent = stations.length ? `선택 가능한 역 ${number(stations.length)}개 · 표시된 행정구역과 매물 주소가 같은지 확인하세요.` : "선택할 역 자료 미연결 · 공급 자료가 연결되면 역을 선택해 비교할 수 있습니다. 비용 계산은 사용할 수 있습니다.";
      $("reload-stations").hidden = stations.length > 0;
      return state.stationAvailable;
    } catch (error) {
      if (token !== state.stationSequence || city !== formValue("city") || mode !== state.mode) return false;
      placeholder.textContent = "역 목록 확인 필요";
      $("station-status").textContent = error.name === "AbortError" && timedOut ? "역 목록 응답이 지연되고 있습니다. 다시 확인해 주세요." : error.message;
      $("reload-stations").hidden = false;
      return false;
    } finally {
      clearTimeout(timer);
      if (token === state.stationSequence) {
        state.stationRequest = null;
        state.stationLoading = false;
        updateEvaluateButton();
      }
    }
  }

  function clearResult(message = "") {
    const hadResult = state.result !== null;
    state.result = null;
    $("result-card").hidden = true;
    $("evidence-section").hidden = true;
    $("result-placeholder").hidden = false;
    if (hadResult && message) $("request-status").textContent = message;
    updateBanner();
  }
  function updatePrice() {
    const rent = numericInput("rent_yen"), management = numericInput("mgmt_fee_yen");
    const known = Number.isSafeInteger(rent) && rent > 0 && Number.isSafeInteger(management) && management >= 0;
    $("input-total").textContent = known ? `${yen(rent + management)} / 월` : "관리비 포함 금액 확인 필요";
    $("cost-base").textContent = known ? `현재 입력 기준 · 월세 ${yen(rent)} + 관리비 ${yen(management)} = ${yen(rent + management)} / 월` : "월세와 관리비를 먼저 확인해 입력하세요. 미상 금액은 0으로 계산하지 않습니다.";
  }
  function clearCost(message = "") {
    const hadResult = !$("cost-result").hidden;
    state.costSequence += 1;
    state.costRequest?.abort();
    state.costRequest = null;
    costBusy(false);
    $("cost-result").hidden = true;
    if (hadResult || message) $("cost-status").textContent = message;
  }
  function resetSubject(city) {
    subjectForm.reset();
    subjectForm.elements.namedItem("city").value = city;
    costForm.reset();
    clearCost();
    setError("form-error", "");
    setError("cost-error", "");
    $("request-status").textContent = "";
    $("cost-status").textContent = "";
    clearResult();
    updatePrice();
  }
  function evaluationBusy(busy) {
    $("subject-fields").disabled = busy;
    updateEvaluateButton();
    $("cancel-button").hidden = !busy || state.demoRequest !== null;
    $("reload-demo").disabled = busy;
    subjectForm.setAttribute("aria-busy", String(busy));
  }
  function cancelEvaluation(message = "비교 요청을 취소했습니다.") {
    state.sequence += 1;
    state.evaluation?.abort();
    state.evaluation = null;
    evaluationBusy(false);
    $("request-status").textContent = message;
  }
  function switchToMarket() {
    const city = formValue("city");
    state.demoSequence += 1;
    state.demoRequest?.abort();
    state.demoRequest = null;
    cancelEvaluation("");
    state.mode = "market";
    $("demo-mode").checked = false;
    resetSubject(city);
    $("request-status").textContent = "실제 비교 모드로 전환했습니다. 합성 예시를 지웠으니 실제 매물 정보를 입력하세요.";
    updateBanner();
    loadStations();
  }
  async function loadDemo() {
    if (state.capabilities?.demo_enabled !== true || state.mode !== "demo") return;
    cancelEvaluation("");
    state.demoRequest?.abort();
    const controller = new AbortController();
    state.demoRequest = controller;
    const token = ++state.demoSequence;
    const city = formValue("city");
    resetSubject(city);
    updateBanner();
    evaluationBusy(true);
    $("request-status").textContent = "현재 도시의 합성 입력 예시를 불러오는 중…";
    try {
      const stationsReady = await loadStations();
      if (token !== state.demoSequence || state.mode !== "demo") return;
      if (!stationsReady) throw new Error("합성 시연의 역 목록이 준비되지 않았습니다. 다시 불러와 주세요.");
      const response = await request(`/api/v2/demo-subject?city=${encodeURIComponent(city)}`, { signal: controller.signal });
      if (token !== state.demoSequence || state.mode !== "demo") return;
      if (!response.subject || typeof response.subject !== "object") throw new Error("합성 입력 예시의 형식을 확인할 수 없습니다.");
      const stationOption = Array.from($("station_id").options).find((option) => option.value === response.subject.station_id && option.dataset.municipality === response.subject.municipality);
      if (!stationOption) throw new Error("합성 예시의 역이 현재 도시 목록에 없습니다. 다시 불러와 주세요.");
      for (const [key, value] of Object.entries(response.subject)) {
        if (["station_id", "station_name", "municipality"].includes(key)) continue;
        const input = subjectForm.elements.namedItem(key);
        if (input) input.value = value === null || value === undefined ? "" : String(value);
      }
      stationOption.selected = true;
      applyStationSelection();
      $("request-status").textContent = "합성 예시를 입력했습니다. ‘비교 근거 확인하기’를 눌러 기능을 시연하세요.";
      updatePrice();
    } catch (error) {
      if (token === state.demoSequence && error.name !== "AbortError") {
        setError("form-error", error.message);
        $("request-status").textContent = "합성 예시를 불러오지 못했습니다.";
      }
    } finally {
      if (token === state.demoSequence) { state.demoRequest = null; evaluationBusy(false); }
    }
  }
  function makeSubject() {
    const subject = { city: formValue("city"), property_type: "apartment" };
    for (const key of ["municipality", "station_id", "station_name", "unit_id", "building_id", "layout", "structure", "orientation", "contract_type"]) subject[key] = textInput(key);
    for (const key of ["rent_yen", "mgmt_fee_yen", "area_sqm", "built_year", "walk_min", "floor"]) subject[key] = numericInput(key);
    subject.bathroom_separate = boolInput("bathroom_separate");
    subject.furnished = boolInput("furnished");
    return subject;
  }

  function row(label, value) {
    const wrapper = el("div");
    wrapper.append(el("dt", "", label), el("dd", "", value));
    return wrapper;
  }
  function metric(value, label) {
    const wrapper = el("div", "metric");
    wrapper.append(el("strong", "", number(value)), el("span", "", label));
    return wrapper;
  }
  function resultPrice(label, value, detail) {
    const wrapper = el("div", "result-price");
    wrapper.append(el("div", "label", label));
    const amount = el("div", "amount", isNumber(value) ? number(value) : "산출하지 않음");
    if (isNumber(value)) amount.append(el("small", "", "엔 / 월"));
    wrapper.append(amount);
    if (detail) wrapper.append(el("div", "detail", detail));
    return wrapper;
  }
  function renderResult(result) {
    state.result = result;
    updateBanner();
    const isDemo = result.is_demo === true || state.mode === "demo";
    const judgmentAllowed = !isDemo && result.status === "comparable" && ["higher", "similar", "lower"].includes(result.judgment);
    const titles = { higher: "비슷한 집보다 높은 편이에요.", similar: "비교 매물과 비슷한 수준이에요.", lower: "비슷한 집보다 낮은 편이에요." };
    const title = isDemo ? "합성 데이터로 비교한 예시예요." : judgmentAllowed ? titles[result.judgment] : result.status === "limited" ? "비교 자료는 있어요. 판정은 보류해요." : "지금은 가격 판단을 보류해요.";
    const statuses = { comparable: "비교 기준 충족", limited: "제한된 근거 · 판정 보류", insufficient: "비교 근거 부족", stale: "정보 갱신 지연", unsupported: "지원 범위 밖" };
    const content = $("result-content");
    content.replaceChildren();
    const top = el("div", "result-topline");
    top.append(el("span", `state-pill${judgmentAllowed ? " valid" : ""}`, isDemo ? "합성 데이터 시연 · 실제 시세 아님" : statuses[result.status] || "판정 보류"));
    content.append(top, el("h2", "", title));
    const description = isDemo ? "이 가격과 매물은 기능 확인용으로 생성되었습니다. 실제 일본 임대 시장의 가격 근거로 사용할 수 없습니다." : judgmentAllowed ? "아래의 모집가격과 입력 조건을 비교한 결과입니다. 실제 계약 가격이나 계약 가능 여부를 보장하지 않습니다." : "자료의 최신성·독립 표본·입력 완전성·시장 검증 기준을 충족한 경우에만 높은지 낮은지 판단합니다.";
    content.append(el("p", "result-description", description));
    content.append(resultPrice("입력한 집 · 월세 + 관리비", result.subject_price_yen));
    content.append(resultPrice(isDemo ? "합성 비교 매물의 대표가격" : "비교 매물의 대표가격", result.benchmark_yen, "동일 건물의 영향력을 제한한 가중 중앙값"));
    if (judgmentAllowed && isNumber(result.delta_yen) && isNumber(result.delta_ratio)) {
      const sign = result.delta_yen > 0 ? "+" : "";
      content.append(el("p", "result-description", `대표가격 대비 ${sign}${yen(result.delta_yen)} (${sign}${number(result.delta_ratio * 100)}%)`));
    }
    const distribution = result.distribution || {};
    const quartiles = el("div", "distribution-row");
    for (const [label, value] of [["하위 25% 경계", distribution.q25_yen], ["중앙값", distribution.q50_yen], ["상위 25% 경계", distribution.q75_yen]]) {
      const item = el("div"); item.append(el("span", "", label), el("strong", "", isNumber(value) ? yen(value) : "미산출")); quartiles.append(item);
    }
    content.append(quartiles);
    const sample = result.sample || {};
    const metrics = el("div", "metric-grid");
    metrics.append(metric(sample.unit_count, "고유 호실"), metric(sample.building_count, "독립 건물"), metric(sample.effective_n, "유효 표본 수"));
    content.append(metrics, el("p", "fine-print", "유효 표본 수는 같은 건물의 집중도를 반영합니다. 호실 수와 다를 수 있습니다."));
    const dates = el("dl", "data-list");
    dates.append(row("가장 오래된 모집 확인", date(result.freshness?.status_verified_at_min)), row("가장 최근 모집 확인", date(result.freshness?.status_verified_at_max)), row("평가 시각", date(result.evaluated_at)));
    content.append(dates);
    const reasonList = el("ul", "reason-list");
    for (const reason of list(result.reason_codes)) reasonList.append(el("li", "", reasons[reason] || `추가 확인 사유: ${display(reason)}`));
    const missing = list(result.matching?.missing_fields);
    if (missing.length) reasonList.append(el("li", "", `미상 입력: ${missing.map((key) => fields[key] || key).join(", ")}`));
    if (reasonList.children.length) content.append(reasonList);
    $("result-placeholder").hidden = true;
    $("result-card").hidden = false;
    renderEvidence(result, isDemo);
    $("result-card").focus({ preventScroll: true });
    $("result-card").scrollIntoView({ behavior: "instant", block: "start" });
  }

  function conditionValue(field, value) {
    if (value === null || value === undefined || value === "unknown") return "미상";
    if (value === "any") return "방향 일치 제한 해제";
    if (field === "orientation") return orientations[value] || String(value);
    const units = { area_sqm: "m²", built_year: "년", walk_min: "분", floor: "층" };
    return `${display(value)}${units[field] || ""}`;
  }
  function differenceText(value) {
    if (typeof value !== "string") return "차이 정보 확인 필요";
    const colon = value.indexOf(":");
    if (colon < 0) return value;
    const field = value.slice(0, colon), detail = value.slice(colon + 1);
    const arrow = detail.indexOf("->");
    return `${fields[field] || field}: ${arrow >= 0 ? `${conditionValue(field, detail.slice(0, arrow))} → ${conditionValue(field, detail.slice(arrow + 2))}` : conditionValue(field, detail)}`;
  }
  function safePublicUrl(value) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password) return null;
      const host = url.hostname.toLowerCase();
      if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.startsWith("[") || /^(127\.|0\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host)) return null;
      return url.href;
    } catch { return null; }
  }
  function makeTable(headers, className) {
    const wrapper = el("div", "table-wrap");
    wrapper.tabIndex = 0;
    wrapper.setAttribute("role", "region");
    wrapper.setAttribute("aria-label", "비교 매물 표 · 좌우로 스크롤할 수 있습니다");
    const table = el("table", className), thead = el("thead"), headRow = el("tr"), tbody = el("tbody");
    for (const label of headers) { const cell = el("th", "", label); cell.scope = "col"; headRow.append(cell); }
    thead.append(headRow); table.append(thead, tbody); wrapper.append(table);
    return { wrapper, tbody };
  }
  function renderEvidence(result, isDemo) {
    const content = $("evidence-content");
    content.replaceChildren();
    $("evidence-mode").textContent = isDemo ? "합성 매물 · 실제 시세 아님" : "확인된 모집가격 기준";
    const sourceMap = new Map(list(result.sources).map((source) => [source.source_id, source.display_name || source.source_id]));
    const matching = result.matching || {};
    const summary = el("div", "evidence-summary");
    summary.append(el("span", "", `완화한 조건: ${list(matching.relaxed_fields).map((key) => fields[key] || key).join(", ") || "없음"}`));
    if (list(matching.missing_fields).length) summary.append(el("span", "", `미상 조건: ${matching.missing_fields.map((key) => fields[key] || key).join(", ")}`));
    content.append(summary);
    const comparables = list(result.comparables);
    if (comparables.length) {
      const { wrapper, tbody } = makeTable(["비교 매물 / 출처", "월세 + 관리비", "면적 / 평면", "건물 / 역 도보", "층 / 방향 / 욕실", "입력한 집 → 비교 매물", "모집 확인 시각"], "comparables-table");
      const buildingLabels = new Map();
      for (const [index, item] of comparables.entries()) {
        const tr = el("tr"), identity = el("td");
        const listingLabel = `비교 매물 ${index + 1}`;
        const safeUrl = isDemo ? null : safePublicUrl(item.public_url);
        const listingName = el(safeUrl ? "a" : "span", "", listingLabel);
        listingName.title = `호실 ID: ${display(item.unit_id)}`;
        if (safeUrl) { listingName.href = safeUrl; listingName.target = "_blank"; listingName.rel = "noopener noreferrer"; listingName.setAttribute("aria-label", `${listingLabel} 원문 · 새 창`); }
        identity.append(listingName);
        identity.append(el("span", "table-note", sourceMap.get(item.source_id) || display(item.source_id)));
        const buildingKnown = typeof item.building_id === "string" && item.building_id.trim() !== "";
        if (buildingKnown && !buildingLabels.has(item.building_id)) buildingLabels.set(item.building_id, buildingLabels.size + 1);
        const buildingLabel = el("span", "table-note", buildingKnown ? `건물 ${buildingLabels.get(item.building_id)}` : "건물 확인 필요");
        buildingLabel.title = `건물 ID: ${display(item.building_id)}`;
        identity.append(buildingLabel);
        const price = el("td", "table-price", yen(item.total_yen));
        price.append(el("span", "table-note", `월세 ${yen(item.rent_yen)} + 관리비 ${yen(item.mgmt_fee_yen)}`));
        const size = el("td", "", `${number(item.area_sqm)} m² · ${display(item.layout)}`);
        const building = el("td", "", `${structures[item.structure] || "구조 미상"} · ${isNumber(item.built_year) ? `${item.built_year}년` : "준공 미상"}`);
        building.append(el("span", "table-note", `${item.station_name || "역 이름 확인 필요"} · 도보 ${number(item.walk_min)}분`));
        const conditions = el("td", "", `${number(item.floor)}층 · ${orientations[item.orientation] || "방향 미상"}`);
        conditions.append(el("span", "table-note", item.bathroom_separate === true ? "욕실·화장실 분리" : item.bathroom_separate === false ? "욕실·화장실 일체형" : "욕실 분리 미상"));
        const differences = el("td", "table-differences");
        if (list(item.differences).length) for (const difference of item.differences) differences.append(el("div", "", differenceText(difference)));
        else differences.textContent = "보고된 조건 차이 없음";
        differences.append(el("span", "table-note", `통계 반영 비중 ${weightPercent(item.weight)}`));
        tr.append(identity, price, size, building, conditions, differences, el("td", "", date(item.status_verified_at)));
        tbody.append(tr);
      }
      content.append(wrapper, el("p", "fine-print", "같은 건물 번호는 같은 건물의 호실이며, 건물별 통계 반영 비중을 제한합니다. 매물·건물 이름에 마우스를 올리면 검증용 ID를 확인할 수 있습니다. 이 목록은 실제 계약 가능한 대안을 보장하지 않습니다."));
    } else content.append(el("p", "empty-evidence", "현재 비교 기준을 충족해 표시할 매물이 없습니다."));
    content.append(el("h3", "", "조건을 넓힌 과정"));
    if (list(matching.steps).length) {
      const steps = el("ol", "step-list");
      for (const step of matching.steps) {
        const item = el("li"), label = el("div");
        if (!step.changed_field) label.append(el("b", "", "동일 조건에서 시작"), el("span", "", "면적 ±5% (최소 1m²) · 연식 ±2년 · 도보 ±2분"));
        else {
          label.append(el("b", "", `${fields[step.changed_field] || step.changed_field} 조건 조정`));
          const prefix = ["area_sqm", "built_year", "walk_min"].includes(step.changed_field) ? "±" : "";
          label.append(el("span", "", `${prefix}${conditionValue(step.changed_field, step.before)} → ${prefix}${conditionValue(step.changed_field, step.after)}`));
        }
        item.append(label, el("strong", "", `${number(step.candidate_count)}호실`)); steps.append(item);
      }
      content.append(steps);
    } else content.append(el("p", "section-help", "입력·자료 기준을 먼저 확인해야 하므로 조건 완화를 진행하지 않았습니다."));
    content.append(el("h3", "", "실제로 사용한 공급 자료"));
    if (list(result.sources).length) {
      const sources = el("ul", "sources-list");
      for (const source of result.sources) sources.append(el("li", "", `${source.display_name || source.source_id || "공급처 이름 미상"}${isDemo ? " · 합성" : ""}`));
      content.append(sources);
    } else content.append(el("p", "section-help", "이번 평가에 사용한 공급 자료가 없습니다."));
    const metadata = el("details", "meta-details"); metadata.append(el("summary", "", "제외 내역과 평가 기록"));
    const details = el("dl", "data-list");
    for (const [key, count] of Object.entries(result.excluded_counts || {})) {
      const label = key.startsWith("mismatch_") ? `${fields[key.slice(9)] || key.slice(9)} 조건 불일치` : excludedLabels[key] || key;
      details.append(row(label, `${number(count)}건`));
    }
    details.append(row("평가 ID", display(result.assessment_id)), row("정책 버전", display(result.versions?.policy)), row("자료 버전", display(result.versions?.snapshot)), row("분위수 계산 방식", display(result.distribution?.weighting_method)));
    metadata.append(details); content.append(metadata);
    $("evidence-section").hidden = false;
  }

  subjectForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!subjectForm.reportValidity() || state.evaluation || state.demoRequest || state.stationLoading || !state.stationAvailable) return;
    setError("form-error", "");
    const subject = makeSubject();
    for (const key of ["rent_yen", "mgmt_fee_yen", "built_year", "floor"]) {
      if (subject[key] !== null && !Number.isSafeInteger(subject[key])) { setError("form-error", `${fields[key]}: 정확한 정수로 입력해 주세요.`); return; }
    }
    clearResult();
    const controller = new AbortController(), token = ++state.sequence;
    state.evaluation = controller;
    evaluationBusy(true);
    $("request-status").textContent = "서버에서 비교 자료와 조건을 확인하고 있습니다.";
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    try {
      const result = await request("/api/v2/evaluate", { body: { subject, mode: state.mode }, signal: controller.signal });
      if (token !== state.sequence) return;
      renderResult(result);
      $("request-status").textContent = "비교 기록을 확인했습니다.";
    } catch (error) {
      if (token !== state.sequence) return;
      setError("form-error", error.name === "AbortError" ? (timedOut ? "응답 대기 시간이 지났습니다. 잠시 후 다시 시도해 주세요." : "비교 요청을 취소했습니다.") : error.message);
      $("request-status").textContent = "";
    } finally {
      clearTimeout(timer);
      if (token === state.sequence) { state.evaluation = null; evaluationBusy(false); }
    }
  });
  subjectForm.addEventListener("input", (event) => {
    clearResult("입력 조건이 바뀌었습니다. 다시 비교해 주세요.");
    setError("form-error", "");
    updatePrice();
    if (["rent_yen", "mgmt_fee_yen"].includes(event.target.name)) clearCost("월세·관리비가 바뀌었습니다. 비용을 다시 계산해 주세요.");
  });
  subjectForm.addEventListener("change", (event) => {
    if (event.target.name === "city") {
      if (state.mode === "demo") loadDemo();
      else {
        $("unit_id").value = "";
        $("building_id").value = "";
        loadStations();
      }
    }
    if (event.target.name === "station_id") applyStationSelection();
  });
  $("demo-mode").addEventListener("change", () => {
    if ($("demo-mode").checked && state.capabilities?.demo_enabled === true) { state.mode = "demo"; updateBanner(); loadDemo(); }
    else switchToMarket();
  });
  $("reload-demo").addEventListener("click", loadDemo);
  $("cancel-button").addEventListener("click", () => cancelEvaluation());
  $("retry-capabilities").addEventListener("click", loadCapabilities);
  $("reload-stations").addEventListener("click", () => {
    clearResult("역 목록을 다시 확인합니다. 역을 선택한 뒤 다시 비교해 주세요.");
    if (state.mode === "demo") loadDemo();
    else loadStations();
  });

  function costBusy(busy) {
    $("cost-fields").disabled = busy;
    $("cost-button").disabled = busy;
    $("cost-button").textContent = busy ? "비용 계산 중…" : "기간 비용 계산";
    $("cost-cancel").hidden = !busy;
    costForm.setAttribute("aria-busy", String(busy));
  }
  function renderCosts(result, payload, demo) {
    const output = $("cost-result"); output.replaceChildren();
    output.append(el("h3", "", `${payload.stay_months}개월 거주 비용${demo ? " · 합성 입력 기준" : ""}`));
    if (demo) output.append(el("p", "section-help", "합성 데이터 시연 · 실제 매물의 견적이 아닙니다."));
    const grid = el("div", "cost-result-grid");
    for (const [label, value, highlight] of [["입주 시 준비할 현금", result.upfront_cash_yen, false], [`${payload.stay_months}개월 총비용 · 보증금 전액 환급 가정`, result.stay_cost_yen, true], ["기간 평균 월비용", result.average_monthly_cost_yen, false]]) {
      const item = el("div", `cost-value${highlight ? " highlight" : ""}`); item.append(el("span", "", label), el("strong", "", yen(value))); grid.append(item);
    }
    output.append(grid);
    const details = el("div", "cost-result-details");
    for (const [label, value] of [["월세 + 관리비", result.monthly_base_yen], ["추가비 포함 매월 비용", result.monthly_all_in_yen], ["환급되지 않는 초기비용", result.nonrefundable_initial_yen], ["환급 여부에 따라 달라지는 보증금", result.deposit_at_risk_yen]]) {
      const item = el("div"); item.append(el("span", "", label), el("strong", "", yen(value))); details.append(item);
    }
    output.append(details, el("p", "fine-print", "선납 월세는 기간 비용에서 중복 계산하지 않습니다. 보증금은 전액 환급을 가정하며, 공제·위약금이 생기면 실제 지출이 달라집니다. 입력하지 않은 변동 공과금·이사비 등은 포함되지 않습니다."));
    output.hidden = false;
  }
  costForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!costForm.reportValidity() || state.costRequest) return;
    setError("cost-error", "");
    const payload = { rent_yen: numericInput("rent_yen"), mgmt_fee_yen: numericInput("mgmt_fee_yen") };
    if (!Number.isSafeInteger(payload.rent_yen) || payload.rent_yen <= 0 || !Number.isSafeInteger(payload.mgmt_fee_yen) || payload.mgmt_fee_yen < 0) {
      setError("cost-error", "위 매물 입력에서 월세와 관리비를 확인해 입력하세요. 미상 금액으로는 비용을 계산하지 않습니다."); return;
    }
    for (const input of new FormData(costForm).entries()) {
      const [key, value] = input;
      if (String(value).trim() === "" || !Number.isSafeInteger(Number(value)) || Number(value) < 0) { setError("cost-error", "모든 비용을 0 이상의 정확한 정수로 입력하세요. 미상 금액을 0으로 바꾸지 마세요."); return; }
      payload[key] = Number(value);
    }
    const controller = new AbortController(), token = ++state.costSequence;
    const demo = state.mode === "demo";
    state.costRequest = controller;
    costBusy(true);
    $("cost-result").hidden = true;
    $("cost-status").textContent = "입력한 비용을 계산하고 있습니다.";
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const result = await request("/api/v2/costs", { body: payload, signal: controller.signal });
      if (token !== state.costSequence) return;
      renderCosts(result, payload, demo);
      $("cost-status").textContent = "입력 금액 기준으로 계산했습니다.";
    } catch (error) {
      if (token === state.costSequence) {
        setError("cost-error", error.name === "AbortError" ? (timedOut ? "비용 계산 응답이 지연되고 있습니다. 다시 시도해 주세요." : "비용 계산을 취소했습니다.") : error.message);
        $("cost-status").textContent = "";
      }
    } finally {
      clearTimeout(timer);
      if (token === state.costSequence) { state.costRequest = null; costBusy(false); }
    }
  });
  costForm.addEventListener("input", () => { clearCost("입력 금액이 바뀌었습니다. 비용을 다시 계산해 주세요."); setError("cost-error", ""); });
  $("cost-cancel").addEventListener("click", () => clearCost("비용 계산을 취소했습니다."));
  $("built_year").max = String(new Date().getFullYear());
  let accessComposing = false, accessSubmitPending = false;
  $("access-code").addEventListener("compositionstart", () => { accessComposing = true; });
  $("access-code").addEventListener("compositionend", () => {
    accessComposing = false;
    setTimeout(() => {
      if (accessSubmitPending && !accessComposing) { accessSubmitPending = false; $("access-form").requestSubmit(); }
    }, 0);
  });
  $("access-code").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (accessComposing || event.isComposing || event.keyCode === 229)) event.preventDefault();
  });
  $("access-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (accessComposing) { accessSubmitPending = true; return; }
    accessSubmitPending = false;
    if ($("access-submit").disabled) return;
    state.accessToken = $("access-code").value.trim().normalize("NFC");
    $("access-code").value = "";
    $("access-error").textContent = "";
    $("access-submit").disabled = true;
    try { await loadCapabilities(); if (state.accessToken) await loadStations(); }
    finally { $("access-submit").disabled = false; }
  });
  $("access-logout").addEventListener("click", () => {
    accessComposing = false; accessSubmitPending = false;
    state.accessToken = "";
    state.capabilities = null;
    $("access-error").textContent = "";
    $("access-logout").hidden = true;
    $("access-panel").hidden = false;
    switchToMarket();
    loadCapabilities();
  });
  updatePrice();
  loadCapabilities();
  loadStations();
})();
