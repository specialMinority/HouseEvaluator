"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const yen = (value) => Number.isFinite(value) ? `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(value)}엔` : "미상";
  const timestamp = (value) => {
    const parsed = new Date(value);
    return value && Number.isFinite(parsed.getTime()) ? `${new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Tokyo", dateStyle: "short", timeStyle: "short" }).format(parsed)} (일본 시간)` : "시각 미상";
  };
  const structures = { rc: "철근콘크리트 · RC", src: "철골철근콘크리트 · SRC", steel: "철골 · 鉄骨造", light_steel: "경량철골 · 軽量鉄骨", wood: "목조 · 木造" };
  const buildingTypes = { mansion: "맨션 · マンション", apartment: "아파트 · アパート" };
  const orientations = { N: "북향", NE: "북동향", E: "동향", SE: "남동향", S: "남향", SW: "남서향", W: "서향", NW: "북서향" };
  const cities = [{ id: "tokyo", name: "도쿄 · 東京" }, { id: "osaka", name: "오사카 · 大阪" }, { id: "fukuoka", name: "후쿠오카 · 福岡" }];
  const fields = [
    { key: "city", label: "도시", required: true, options: cities.map((city) => [city.id, city.name]) },
    { key: "municipality", label: "시·구·정·촌 원문", japanese: "市区町村", required: true, placeholder: "예: 新宿区", maxLength: 120 },
    { key: "station_name", label: "가장 가까운 역 원문", japanese: "最寄り駅", required: true, placeholder: "예: 新宿", maxLength: 120, help: "일본어 역 이름을 직접 입력하세요." },
    { key: "layout", label: "평면", japanese: "間取り", required: true, options: ["1R", "1K", "1DK", "1LDK"] },
    { key: "rent_yen", label: "월세", japanese: "賃料", required: true, type: "number", min: 1, max: 10000000, step: 1, unit: "엔 / 월", placeholder: "예: 80000" },
    { key: "mgmt_fee_yen", label: "관리비·공익비", japanese: "管理費・共益費", type: "number", min: 0, max: 1000000, step: 1, unit: "엔 / 월" },
    { key: "area_sqm", label: "전용면적", japanese: "専有面積", required: true, type: "number", min: 0.1, max: 1000, step: 0.01, unit: "m²", placeholder: "예: 25.5" },
    { key: "building_type", label: "광고에 표시된 건물 종류", japanese: "建物種別", options: Object.entries(buildingTypes), help: "원문의 マンション·アパート 표시를 입력하세요. 구조로 추정하지 마세요." },
    { key: "structure", label: "건물 구조", japanese: "構造", options: Object.entries(structures) },
    { key: "built_year", label: "준공 연도", japanese: "築年月", type: "number", min: 1800, max: new Date().getFullYear(), step: 1, unit: "년" },
    { key: "walk_min", label: "역까지 도보", japanese: "駅徒歩", type: "number", min: 0, max: 180, step: 0.1, unit: "분" },
    { key: "floor", label: "거주 층", japanese: "所在階", type: "number", min: -10, max: 100, step: 1, unit: "층", help: "매물이 있는 층입니다. 지하 1층은 -1로 입력하세요." },
    { key: "building_floors", label: "건물 총층수(지상)", japanese: "建物階数", type: "number", min: 1, max: 100, step: 1, unit: "층", help: "예: 3階/8階建은 거주 3층, 건물 총 8층입니다. 지하층은 제외하세요." },
    { key: "elevator", label: "엘리베이터", japanese: "エレベーター", options: [["true", "있음"], ["false", "없음"]], boolean: true, help: "광고에 없다는 이유로 '없음'을 선택하지 마세요. 확인되지 않으면 미상입니다." },
    { key: "orientation", label: "주요 창 방향", japanese: "向き", options: Object.entries(orientations) },
    { key: "bathroom_separate", label: "욕실·화장실 분리", japanese: "バス・トイレ別", options: [["true", "분리"], ["false", "일체형"]], boolean: true },
    { key: "furnished", label: "가구 포함 여부", japanese: "家具付き", options: [["true", "포함됨"], ["false", "포함 안 됨"]], boolean: true },
    { key: "contract_type", label: "계약 종류", japanese: "契約種別", options: [["standard", "일반 임대 · 普通借家"], ["fixed_term", "정기 임대 · 定期借家"]] },
    { key: "property_type", label: "주택 유형", options: [["apartment", "공동주택 · 아파트/맨션"], ["house", "단독주택"], ["shared", "셰어하우스"]] },
    { key: "building_name", label: "건물 이름 원문", japanese: "建物名", placeholder: "미상", maxLength: 200, help: "같은 건물의 중복 비중을 줄이는 데 필요해요." },
    { key: "address", label: "주소 원문", japanese: "所在地", placeholder: "미상", maxLength: 240, help: "주소·건물 이름이 없으면 건물 구분이 어려워요. 추정 이름을 만들지 마세요." },
    { key: "source_url", label: "원문 링크", type: "url", placeholder: "https://…", maxLength: 2000, wide: true, help: "같은 매물이나 건물의 중복 확인에 사용합니다." },
  ];
  const fieldNames = Object.fromEntries(fields.map((field) => [field.key, field.label]));
  const state = { accessToken: "", authSequence: 0, options: null, optionsLoading: false, connectionFailed: false, listings: [], sourceReports: [], nextKey: 1, editingKey: null, search: null, searchJobId: null, compare: null, importing: null, importJobId: null, importSequence: 0, searchSequence: 0, compareSequence: 0, optionsSequence: 0 };

  function sourceName(id) {
    return { suumo: "SUUMO", chintai: "CHINTAI", yahoo_realestate: "Yahoo! 부동산", manual: "직접 입력" }[id] || "공개 검색";
  }
  function automaticSourceName() {
    const sources = Array.isArray(state.options?.sources) ? state.options.sources : [];
    const names = sources.filter((source) => source?.automatic === true && typeof source.name === "string" && source.name.trim()).map((source) => source.name.trim());
    return [...new Set(names)].join(" · ") || "공개 매물 사이트";
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function errorAt(id, message = "") {
    $(id).textContent = message;
    $(id).hidden = !message;
    if (id === "personal-compare-error" && message) reveal($(id));
  }
  function reveal(node) {
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "start", behavior: "instant" });
  }
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function sourceLink(value, label) {
    const url = safeUrl(value);
    if (!url) return null;
    const node = element("a", label);
    node.href = url; node.target = "_blank"; node.rel = "noopener noreferrer"; node.referrerPolicy = "no-referrer";
    return node;
  }
  function buildFields(container, prefix) {
    for (const field of fields) {
      const label = element("label", null, `field${field.wide ? " personal-wide-field" : ""}`);
      label.append(element("span", `${field.label}${field.required ? " *" : ""}`));
      if (field.japanese) label.append(element("span", field.japanese, "japanese"));
      const input = element(field.options ? "select" : "input");
      input.name = field.key; input.id = `${prefix}-${field.key}`; input.required = Boolean(field.required);
      if (field.options) {
        const first = element("option", field.required ? "선택하세요" : "미상"); first.value = ""; input.append(first);
        for (const option of field.options) {
          const pair = Array.isArray(option) ? option : [option, option];
          const node = element("option", pair[1]); node.value = pair[0]; input.append(node);
        }
      } else {
        input.type = field.type || "text"; input.autocomplete = "off";
        input.placeholder = field.placeholder || "미상";
        for (const key of ["min", "max", "step", "maxLength"]) if (field[key] !== undefined) input[key] = field[key];
        if (field.type === "number") input.inputMode = field.step === 1 ? "numeric" : "decimal";
      }
      if (field.unit) { const wrap = element("span", null, "input-unit"); wrap.append(input, element("span", field.unit)); label.append(wrap); }
      else label.append(input);
      if (field.help) label.append(element("small", field.help));
      if (field.key === "municipality") {
        const options = element("datalist"); options.id = `${prefix}-municipalities`; input.setAttribute("list", options.id); label.append(options);
      }
      container.append(label);
    }
  }
  function values(form) {
    const result = {};
    for (const field of fields) {
      const raw = form.elements.namedItem(field.key).value.trim();
      result[field.key] = raw === "" ? null : field.type === "number" ? Number(raw) : field.boolean ? raw === "true" : raw;
      if (field.type === "number" && raw && !Number.isFinite(result[field.key])) throw new Error(`${field.label}에 올바른 숫자를 입력하세요.`);
    }
    if (result.source_url && !safeUrl(result.source_url)) throw new Error("원문 링크에는 인증정보가 없는 http 또는 https 주소를 입력하세요.");
    if (Number.isFinite(result.floor) && Number.isFinite(result.building_floors) && result.floor > result.building_floors) throw new Error("거주 층은 건물 총층수보다 높을 수 없습니다. 두 항목을 확인하세요.");
    return result;
  }
  function fill(form, data) {
    for (const field of fields) form.elements.namedItem(field.key).value = data[field.key] === null || data[field.key] === undefined ? "" : String(data[field.key]);
  }
  function municipalityOptions(prefix) {
    const city = $(`${prefix}-city`).value;
    const options = $(`${prefix}-municipalities`); options.replaceChildren();
    for (const area of state.options?.cities?.find((item) => item.id === city)?.municipalities || []) {
      const option = element("option"); option.value = area.name; options.append(option);
    }
  }
  function total(data) { return Number.isFinite(data.rent_yen) && Number.isFinite(data.mgmt_fee_yen) ? data.rent_yen + data.mgmt_fee_yen : null; }
  function updateSubjectTotal() {
    const data = values($("personal-subject-form"));
    $("personal-subject-total").textContent = data.rent_yen === null ? "금액을 입력하세요" : data.mgmt_fee_yen === null ? "管理費를 확인하면 합산해요" : yen(total(data));
  }
  function clearResult(message = "") {
    state.compareSequence += 1; state.compare?.abort(); state.compare = null;
    $("personal-result").hidden = true; $("personal-result-content").replaceChildren();
    $("personal-compare-status").textContent = message; updateButtons();
  }
  function stopSearch(message = "") {
    const jobId = state.searchJobId; state.searchJobId = null;
    state.searchSequence += 1; state.search?.abort(); state.search = null;
    $("personal-search-status").textContent = message; updateButtons();
    if (jobId) request(`/api/v2/personal/search/${encodeURIComponent(jobId)}/cancel`, { method: "POST", data: {} }).catch(() => {});
  }
  function stopImport(message = "") {
    const jobId = state.importJobId; state.importJobId = null;
    state.importSequence += 1; state.importing?.abort(); state.importing = null;
    $("personal-import-status").textContent = message; updateButtons();
    cancelImportJob(jobId);
  }
  function cancelImportJob(jobId) {
    if (typeof jobId === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(jobId)) request(`/api/v2/personal/import/${encodeURIComponent(jobId)}/cancel`, { method: "POST", data: {} }).catch(() => {});
  }
  function unavailable(kind) {
    return state.connectionFailed || state.options?.[`${kind}_available`] === false || (state.options?.execution_mode === "worker" && state.options?.worker?.online !== true);
  }
  function serviceDisconnected(error) {
    if (error.code !== "worker_offline" && error.code !== "connection_failed") return;
    state.connectionFailed = true;
    $("personal-service-status").textContent = "조회 서비스 연결이 끊겼습니다. 잠시 후 연결을 다시 확인해 주세요. 입력한 내용은 유지됩니다.";
    $("personal-reconnect").hidden = false;
    updateButtons();
  }
  function clearImportReview() {
    $("personal-import-review").replaceChildren(); $("personal-import-review").hidden = true;
  }
  function updateButtons() {
    const selected = state.listings.filter((row) => row.selected).length;
    const busy = Boolean(state.search || state.compare || state.importing);
    const hasResult = !$("personal-result").hidden;
    $("personal-search").disabled = busy || unavailable("search") || state.options?.enabled === false || state.options?.search_enabled === false;
    $("personal-search-cancel").hidden = !state.search;
    $("personal-import-submit").disabled = busy || unavailable("import") || state.options?.enabled === false || state.options?.search_enabled === false || state.options?.import_enabled === false;
    $("personal-reconnect").disabled = busy || state.optionsLoading;
    $("personal-reconnect").textContent = state.optionsLoading ? "연결 확인 중…" : "연결 다시 확인";
    $("personal-import-submit").textContent = state.importing ? "불러오는 중…" : "조건 불러오기";
    $("personal-import-form").setAttribute("aria-busy", String(Boolean(state.importing)));
    $("personal-import-cancel").hidden = !state.importing;
    $("personal-add").disabled = Boolean(state.importing);
    for (const control of document.querySelectorAll(".personal-listing button, .personal-listing-check input, #personal-select-all")) control.disabled = Boolean(state.importing);
    for (const button of document.querySelectorAll("[data-compare-action]")) {
      button.disabled = busy || state.options?.enabled === false || selected === 0;
      button.textContent = state.compare ? "비교 중…" : button.dataset.idleLabel;
      button.setAttribute("aria-busy", String(Boolean(state.compare)));
    }
    $("personal-compare-cancel").hidden = !state.compare;
    $("personal-example").disabled = busy;
    const dockVisible = state.listings.length > 0 || busy;
    $("personal-action-dock").hidden = !dockVisible;
    $("personal-dock-count").textContent = `${selected}개 선택`;
    $("personal-dock-status").textContent = state.importing ? "링크에서 집의 조건을 불러오고 있습니다" : state.search ? "매물을 검색하고 있습니다" : state.compare ? "조건과 가격을 비교하고 있습니다" : hasResult ? "비교 결과를 확인하세요" : selected ? "선택한 조건으로 비교할 수 있어요" : "비교할 매물을 선택하세요";
    $("personal-dock-result").disabled = !hasResult;
    $("personal-dock-cancel").hidden = !busy;
    $("personal-dock-cancel").textContent = state.importing ? "불러오기 중단" : state.search ? "검색 중단" : "비교 취소";
    updateDockSpace();
  }
  function updateDockSpace() {
    const dock = $("personal-action-dock");
    document.documentElement.style.setProperty("--personal-dock-space", dock.hidden ? "0px" : `${Math.ceil(dock.getBoundingClientRect().height) + 30}px`);
  }
  function cancelActive() {
    if (state.importing) stopImport("불러오기를 중단했습니다. 기존 입력은 유지됩니다.");
    else if (state.search) stopSearch("검색 결과 대기를 중단했습니다. 시작된 검색에는 취소를 요청하며 비교 목록은 유지됩니다.");
    else if (state.compare) clearResult("비교를 취소했습니다.");
  }
  async function request(path, { method = "GET", data, signal } = {}) {
    const authSequence = state.authSequence;
    const accessToken = state.accessToken;
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    try {
      const headers = { Accept: "application/json" };
      if (data !== undefined) headers["Content-Type"] = "application/json";
      if (accessToken) headers.Authorization = `Bearer ${/[^\x21-\x7e]/.test(accessToken) ? encodeURIComponent(accessToken) : accessToken}`;
      const response = await fetch(path, { method, headers, body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal, credentials: "same-origin", cache: "no-store" });
      let result;
      try { result = await response.json(); } catch { throw new Error("서버 응답을 읽지 못했습니다. 잠시 뒤 다시 시도하세요."); }
      if (response.status === 401) {
        if (authSequence === state.authSequence) {
          const hadToken = Boolean(state.accessToken); state.accessToken = ""; state.authSequence += 1;
          $("personal-access-panel").hidden = false; $("personal-logout").hidden = true;
          if (hadToken) errorAt("personal-access-error", "접속 코드가 맞지 않습니다. 다시 입력하세요.");
        }
        throw new Error("접속 코드를 입력한 뒤 다시 시도하세요.");
      }
      if (!response.ok) {
        const detail = typeof result.message === "string" ? result.message : typeof result.error === "string" && /[가-힣]/.test(result.error) ? result.error : null;
        const error = new Error(detail || (response.status === 429 ? "요청이 많습니다. 잠시 뒤 다시 시도하세요." : response.status === 413 ? "비교할 매물이 너무 많습니다. 선택 수를 줄여주세요." : "요청을 처리하지 못했습니다. 입력 조건을 확인해 주세요."));
        error.code = typeof result.error === "string" ? result.error : result.error?.code;
        throw error;
      }
      if (authSequence === state.authSequence) { $("personal-access-panel").hidden = true; $("personal-logout").hidden = !state.accessToken; }
      return result;
    } catch (error) {
      if (timedOut) throw new Error("응답이 지연되고 있습니다. 잠시 뒤 다시 시도하세요.");
      if (error.name === "TypeError") { const disconnected = new Error("서버에 연결하지 못했습니다. 직접 입력한 내용은 이 페이지에 남아 있습니다."); disconnected.code = "connection_failed"; throw disconnected; }
      throw error;
    } finally { clearTimeout(timer); signal?.removeEventListener("abort", abort); }
  }
  async function loadOptions() {
    const sequence = ++state.optionsSequence;
    const authSequence = state.authSequence;
    state.optionsLoading = true; updateButtons();
    try {
      const data = await request("/api/v2/personal/options");
      if (sequence !== state.optionsSequence || authSequence !== state.authSequence) return;
      state.options = data; state.connectionFailed = false;
      municipalityOptions("subject"); municipalityOptions("editor");
      $("personal-service-status").textContent = data.enabled === false ? "개인 비교 기능이 현재 비활성화되어 있습니다." : data.execution_mode === "worker" ? data.worker?.online === true ? "조회 서비스가 연결되어 있습니다." : "조회 서비스 연결 대기 중입니다. 잠시 후 연결을 다시 확인해 주세요. 직접 입력과 비교는 계속할 수 있습니다." : "";
      $("personal-reconnect").hidden = data.execution_mode !== "worker";
      $("personal-search-help").textContent = data.search_enabled === false ? "자동검색이 꺼져 있습니다. 원문에서 확인한 매물을 직접 입력하면 계속 비교할 수 있습니다." : unavailable("search") ? "조회 서비스에 연결되면 자동검색을 시작할 수 있습니다. 입력한 조건은 유지됩니다." : `자동검색 출처: ${automaticSourceName()}. 사이트 응답이나 페이지 변경으로 찾지 못하면 직접 입력해 계속 비교할 수 있습니다.`;
      const importNames = Array.isArray(data.import_sources) ? data.import_sources.filter((source) => typeof source?.name === "string" && source.name.trim()).map((source) => source.name.trim()).join(" · ") : "지원하는 공개 매물 사이트";
      $("personal-import-help").textContent = data.import_enabled === false || data.search_enabled === false ? "현재 링크 불러오기가 꺼져 있습니다. 매물 원문을 확인해 아래에 직접 입력하세요." : unavailable("import") ? "조회 서비스에 연결되면 링크의 조건을 불러올 수 있습니다. 지금은 아래에 직접 입력할 수 있습니다." : `${importNames || "지원하는 공개 매물 사이트"} 상세 링크를 지원합니다. 불러오기에 성공하면 아래 입력을 교체하고 확인하지 못한 항목은 비웁니다.`;
      updateButtons();
      errorAt("personal-access-error");
    } catch (error) { if (sequence === state.optionsSequence && authSequence === state.authSequence) { state.connectionFailed = true; $("personal-service-status").textContent = error.message; $("personal-reconnect").hidden = false; } }
    finally { if (sequence === state.optionsSequence) { state.optionsLoading = false; updateButtons(); } }
  }
  function renderImportReview(data, imported) {
    const review = $("personal-import-review"); review.replaceChildren();
    const source = state.options?.import_sources?.find((item) => item.id === data.source?.id)?.name || "공개 매물 사이트";
    review.append(element("strong", `${source}에서 불러온 조건을 확인하세요`));
    const knownMissing = new Set(Array.isArray(data.missing_fields) ? data.missing_fields : []);
    const missing = fields.filter((field) => knownMissing.has(field.key) || imported[field.key] === null || imported[field.key] === undefined || imported[field.key] === "");
    const required = missing.filter((field) => field.required).map((field) => field.label);
    const optional = missing.filter((field) => !field.required).map((field) => field.label);
    if (required.length) review.append(element("p", `검색 전에 확인할 필수 항목: ${required.join(" · ")}`, "personal-import-required"));
    if (optional.length) {
      const details = element("details"); details.append(element("summary", `추가로 확인할 미상 항목 ${optional.length}개`), element("p", optional.join(" · "))); review.append(details);
    }
    if (Array.isArray(data.warnings)) for (const warning of data.warnings) if (typeof warning === "string" && warning.trim()) review.append(element("p", warning));
    const link = sourceLink(imported.source_url, "불러온 매물 원문 확인"); if (link) review.append(link);
    review.append(element("p", `${data.source?.fetched_at ? `페이지 조회: ${timestamp(data.source.fetched_at)}. ` : ""}공실 여부와 최종 계약 조건은 원문에서 확인하세요. 자동 검색은 아래 버튼으로 시작할 수 있습니다.`, "fine-print"));
    review.hidden = false;
  }
  function jobWaitMilliseconds(job, fallback) {
    const seconds = job?.max_wait_seconds;
    return (Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds, 450) : state.options?.execution_mode === "worker" ? 450 : fallback) * 1000;
  }
  function jobPollMilliseconds(job) {
    const seconds = job?.poll_after_seconds;
    return (Number.isFinite(seconds) ? Math.min(10, Math.max(5, seconds)) : state.options?.execution_mode === "worker" ? 5 : 2) * 1000;
  }
  function queueMessage(job, running) {
    if (job.status !== "pending") return running;
    const position = job.queue_position;
    return Number.isInteger(position) && position >= 1 && position <= 6
      ? `요청이 접수되었습니다. 처리 순서 ${position}번째${position > 1 ? ` · 앞에 ${position - 1}건` : ""}. 순서대로 조회하며, 혼잡할 때는 몇 분 걸릴 수 있습니다.`
      : "요청이 접수되었습니다. 순서대로 조회하며, 혼잡할 때는 몇 분 걸릴 수 있습니다.";
  }
  async function importSubject(event) {
    event.preventDefault();
    if (state.importing || state.search || state.compare || unavailable("import") || state.options?.enabled === false || state.options?.search_enabled === false || state.options?.import_enabled === false || !$("personal-import-form").reportValidity()) return;
    const url = $("personal-import-url").value.trim();
    if (!safeUrl(url)) { errorAt("personal-import-error", "인증정보가 없는 매물 상세 URL을 입력하세요."); return; }
    const sequence = ++state.importSequence; const authSequence = state.authSequence;
    const controller = new AbortController(); state.importing = controller; state.importJobId = null;
    const started = Date.now();
    errorAt("personal-import-error"); $("personal-import-status").textContent = "매물 원문에서 조건을 확인하고 있습니다…"; updateButtons();
    try {
      let data = await request("/api/v2/personal/import", { method: "POST", data: { url }, signal: controller.signal });
      if (sequence !== state.importSequence || authSequence !== state.authSequence) { if (authSequence === state.authSequence) cancelImportJob(data?.job_id); return; }
      if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("매물 조건 응답을 확인하지 못했습니다. 기존 입력은 유지됩니다.");
      if (data.job_id !== undefined) {
        if (typeof data.job_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(data.job_id)) throw new Error("불러오기 작업을 확인하지 못했습니다. 기존 입력은 유지됩니다.");
        const jobId = data.job_id; state.importJobId = jobId;
        let job = data;
        const waitMilliseconds = jobWaitMilliseconds(job, 100);
        while (["pending", "running"].includes(job.status)) {
          if (Date.now() - started > waitMilliseconds) throw new Error("불러오기가 오래 걸려 대기를 마쳤습니다. 기존 입력을 유지하며 직접 수정할 수 있습니다.");
          $("personal-import-status").textContent = queueMessage(job, "매물 원문에서 조건을 확인하고 있습니다…");
          await delay(jobPollMilliseconds(job), controller.signal);
          job = await request(`/api/v2/personal/import/${encodeURIComponent(jobId)}`, { signal: controller.signal });
          if (sequence !== state.importSequence || authSequence !== state.authSequence) return;
          if (!job || job.job_id !== jobId) throw new Error("불러오기 작업 응답이 일치하지 않습니다. 기존 입력은 유지됩니다.");
        }
        if (job.status !== "complete") { const error = new Error(job.error?.message || (job.status === "cancelled" ? "불러오기가 중단되었습니다. 기존 입력은 유지됩니다." : "매물 조건을 불러오지 못했습니다. 기존 입력은 유지됩니다.")); error.code = job.error?.code; throw error; }
        data = job.result; state.importJobId = null;
      }
      if (authSequence !== state.authSequence || url !== $("personal-import-url").value.trim()) { stopImport("입력이나 접속 상태가 바뀌어 불러오기를 중단했습니다."); return; }
      if (!data || !["ok", "partial"].includes(data.status) || !data.listing || typeof data.listing !== "object" || Array.isArray(data.listing)) throw new Error("매물 조건을 읽지 못했습니다. 기존 입력은 유지됩니다.");
      const imported = Object.fromEntries(fields.map((field) => [field.key, data.listing[field.key] ?? null]));
      imported.source_url = imported.source_url || data.source?.url || null;
      if (!safeUrl(imported.source_url)) throw new Error("매물 원문 링크를 확인하지 못했습니다. 기존 입력은 유지됩니다.");
      const form = $("personal-subject-form");
      const previous = Object.fromEntries(fields.map((field) => [field.key, form.elements.namedItem(field.key).value]));
      try { fill(form, imported); updateSubjectTotal(); } catch (error) { fill(form, previous); throw error; }
      municipalityOptions("subject"); clearResult("대상 집을 불러왔습니다. 조건을 확인한 뒤 비교할 매물을 모아 주세요.");
      state.listings = []; renderListings(); renderReports([]); $("personal-search-status").textContent = "";
      $("personal-example-note").hidden = true; errorAt("personal-subject-error");
      renderImportReview(data, imported);
      $("personal-import-status").textContent = "조건을 불러왔습니다. 미상 항목과 원문을 확인해 주세요.";
      reveal($("personal-import-review"));
    } catch (error) {
      if (sequence === state.importSequence && error.name !== "AbortError") { cancelImportJob(state.importJobId); serviceDisconnected(error); errorAt("personal-import-error", error.message); $("personal-import-status").textContent = "불러오지 못했습니다. 기존 입력을 유지하며 직접 수정할 수 있습니다."; }
    } finally { if (sequence === state.importSequence) { state.importing = null; state.importJobId = null; updateButtons(); } }
  }
  function reportCard(report) {
    const card = element("div", null, "personal-source-report");
    if (!["ok", "success", "complete"].includes(report.status)) card.classList.add("is-error");
    card.append(element("strong", `${sourceName(report.source_id)} · ${Number.isInteger(report.listing_count) ? `광고 ${report.listing_count}개를 목록에 가져옴` : "검색 결과"}`));
    const scopeLabel = report.search_scope_label || { station: "대상 역을 지정한 검색", municipality_fallback: "역을 지정하지 못해 시·구·정·촌 범위로 검색" }[report.search_scope];
    if (scopeLabel) card.append(element("p", `검색 범위: ${scopeLabel}`, "personal-search-scope"));
    if (report.area_scope_label) card.append(element("p", `면적 조건: ${report.area_scope_label}`));
    if (report.message) card.append(element("p", report.message));
    const metrics = element("dl", null, "personal-search-metrics");
    for (const [key, label, unit, note] of [
      ["search_page_count", "해석한 목록", "페이지", ""],
      ["discovered_count", "읽은 광고 행", "개", "페이지 간 중복 포함"],
      ["listing_count", "가져온 광고", "개", "비교 목록에 반환"],
      ["station_count", "대상 역 포함 광고", "개", "가져온 광고 중"],
      ["distinct_building_count", "구분한 건물", "개", "주소 + 건물명 기준"],
      ["detail_attempt_count", "상세 조회 요청", "개", ""],
      ["detail_count", "상세 확인·반영", "개", ""],
    ]) {
      if (!Number.isInteger(report[key]) || report[key] < 0) continue;
      const metric = element("div"); metric.append(element("dt", label), element("dd", `${report[key]}${unit}`));
      if (note) metric.append(element("small", note)); metrics.append(metric);
    }
    if (metrics.childElementCount) card.append(metrics);
    card.append(element("p", "같은 건물·호실의 광고가 중복될 수 있습니다. 건물 수는 주소+건물명으로 구분한 잠정치이며, 둘 중 하나가 없으면 집계에서 제외합니다.", "personal-search-count-note"));
    const stopLabels = { page_limit: "목록 조회 페이지 상한에 도달", time_limit: "검색 시간 상한에 도달", results_exhausted: "확인한 검색 결과의 마지막 페이지에 도달", blocked: "사이트의 접근 제한으로 중단", parse_changed: "페이지 구성을 해석하지 못해 중단", unavailable: "사이트 응답을 받지 못해 중단", station_unresolved: "대상 역을 확인하지 못함" };
    const stopLabel = report.stop_reason_label || stopLabels[report.stop_reason];
    if (stopLabel) card.append(element("p", `검색 종료: ${stopLabel}`, "personal-search-stop"));
    if (typeof report.result_order === "string" && report.result_order) card.append(element("p", `검색 정렬·선택: ${report.result_order}`));
    if (report.partial === true) card.append(element("p", "일부 검색 결과만 읽었습니다. 조회하지 않은 페이지나 상세 조건을 확인하지 못한 광고가 있을 수 있습니다. 주변 매물 전체를 확인한 결과가 아닙니다."));
    if (report.fetched_at) card.append(element("p", `페이지 조회: ${timestamp(report.fetched_at)}`));
    const urls = Array.isArray(report.search_urls) ? report.search_urls.filter((url) => safeUrl(url)) : [];
    if (urls.length) {
      const details = element("details", null, "personal-search-links"); details.append(element("summary", `실제로 조회한 목록 ${urls.length}개 보기`));
      for (const [index, url] of urls.entries()) details.append(sourceLink(url, `조회 목록 ${index + 1} ↗`)); card.append(details);
    } else { const link = sourceLink(report.search_url, "원문 검색 결과 열기 ↗"); if (link) card.append(link); }
    return card;
  }
  function renderReports(reports) {
    state.sourceReports = Array.isArray(reports) ? reports : [];
    const container = $("personal-source-reports"); container.replaceChildren();
    for (const report of state.sourceReports) container.append(reportCard(report));
  }
  function titleFor(data) { return data.building_name || data.title || `${data.station_name || "역 미상"} · ${data.layout || "평면 미상"}`; }
  function updateSelection() {
    $("personal-selection").hidden = state.listings.length === 0;
    const selected = state.listings.filter((row) => row.selected).length;
    $("personal-selected-count").textContent = `광고 ${state.listings.length}개 중 ${selected}개 선택`;
    $("personal-select-all").checked = state.listings.length > 0 && selected === state.listings.length;
    $("personal-select-all").indeterminate = selected > 0 && selected < state.listings.length;
    updateButtons();
  }
  function renderListings() {
    const container = $("personal-listings"); container.replaceChildren();
    $("personal-listings-empty").hidden = state.listings.length > 0;
    updateSelection();
    for (const row of state.listings) {
      const data = row.data;
      const card = element("article", null, "personal-listing");
      const top = element("div", null, "personal-listing-top");
      const checkLabel = element("label", null, "personal-listing-check");
      const check = element("input"); check.type = "checkbox"; check.checked = row.selected; check.setAttribute("aria-label", `${titleFor(data)} 비교에 포함`);
      check.addEventListener("change", () => { row.selected = check.checked; clearResult("선택한 매물이 바뀌었습니다. 다시 비교해 주세요."); updateSelection(); });
      checkLabel.append(check); top.append(checkLabel);
      const heading = element("div", null, "personal-listing-heading"); heading.append(element("h3", titleFor(data)));
      const price = element("div", yen(total(data)), "personal-listing-price"); price.append(element("small", total(data) === null ? "관리비 포함 금액 미상" : "관리비 포함 / 월"));
      heading.append(price, element("p", `월세 ${yen(data.rent_yen)} + 관리비 ${yen(data.mgmt_fee_yen)}`, "personal-listing-sub")); top.append(heading); card.append(top);
      const tags = element("div", null, "personal-listing-tags");
      const descriptions = [data.municipality, data.station_name ? `${data.station_name}역` : null, data.layout,
        Number.isFinite(data.area_sqm) ? `${data.area_sqm}m²` : "면적 미상", buildingTypes[data.building_type] || "건물 종류 미상", structures[data.structure] || "구조 미상",
        Number.isFinite(data.built_year) ? `${data.built_year}년 준공` : "준공 연도 미상", Number.isFinite(data.walk_min) ? `도보 ${data.walk_min}분` : "도보 미상",
        Number.isFinite(data.floor) ? `거주 ${data.floor}층` : "거주 층 미상", Number.isFinite(data.building_floors) ? `건물 총 ${data.building_floors}층` : "총층수 미상",
        data.elevator === true ? "엘리베이터 있음" : data.elevator === false ? "엘리베이터 없음" : "엘리베이터 미상"];
      for (const description of descriptions.filter(Boolean)) tags.append(element("span", description)); card.append(tags);
      const missing = ["mgmt_fee_yen", "building_type", "structure", "built_year", "walk_min", "floor", "building_floors", "elevator"].filter((key) => data[key] === null || data[key] === undefined || data[key] === "");
      if (missing.length) card.append(element("p", `확인 필요: ${missing.map((key) => fieldNames[key]).join(" · ")}`, "personal-listing-missing"));
      const bottom = element("div", null, "personal-listing-bottom");
      const link = sourceLink(data.source_url, "매물 원문 ↗"); if (link) bottom.append(link);
      const edit = element("button", "조건 확인·수정", "text-button"); edit.type = "button"; edit.addEventListener("click", () => openEditor(row.key));
      const remove = element("button", "삭제", "text-button personal-delete"); remove.type = "button"; remove.addEventListener("click", () => { state.listings = state.listings.filter((item) => item.key !== row.key); clearResult("비교 목록이 바뀌었습니다. 다시 비교해 주세요."); renderListings(); });
      bottom.append(edit, remove); card.append(bottom);
      card.append(element("p", `${row.origin === "manual" ? "직접 입력·수정" : sourceName(data.source_id)} · ${data.fetched_at ? `페이지 조회 ${timestamp(data.fetched_at)}` : "조회 시각 미상"}`, "personal-listing-time"));
      container.append(card);
    }
    updateButtons();
  }
  function openEditor(key = null) {
    state.editingKey = key;
    const row = state.listings.find((item) => item.key === key);
    const base = row ? row.data : Object.fromEntries(["city", "municipality", "station_name", "layout"].map((name) => [name, $("personal-subject-form").elements.namedItem(name).value]));
    fill($("personal-editor-form"), base); municipalityOptions("editor"); errorAt("personal-editor-error");
    $("personal-editor-title").textContent = row ? "비교 매물 조건 확인·수정" : "비교 매물 직접 입력";
    $("personal-editor-help").textContent = row ? "원문에 있는 정보를 확인하세요. 모르는 값은 미상으로 두어도 됩니다." : "위치·평면은 비교 대상의 값을 가져왔습니다. 원문과 맞는지 확인한 뒤 나머지 정보를 입력하세요. 모르는 값은 미상으로 두어도 됩니다.";
    $("personal-editor-save").textContent = row ? "수정 저장" : "목록에 추가";
    $("personal-editor").showModal();
  }
  const delay = (milliseconds, signal) => new Promise((resolve, reject) => {
    const finish = () => { signal.removeEventListener("abort", abort); resolve(); };
    const timer = setTimeout(finish, milliseconds);
    const abort = () => { clearTimeout(timer); signal.removeEventListener("abort", abort); reject(new DOMException("Aborted", "AbortError")); };
    signal.addEventListener("abort", abort, { once: true }); if (signal.aborted) abort();
  });
  async function search(event) {
    event.preventDefault();
    if (unavailable("search") || state.options?.enabled === false || state.options?.search_enabled === false) return;
    if (state.importing || state.search || state.compare || !$("personal-subject-form").reportValidity()) return;
    let subject;
    try { subject = values($("personal-subject-form")); } catch (error) { errorAt("personal-subject-error", error.message); return; }
    errorAt("personal-subject-error"); clearResult();
    const sequence = ++state.searchSequence; const controller = new AbortController(); state.search = controller; state.searchJobId = null; updateButtons();
    $("personal-search-status").textContent = `${automaticSourceName()}에서 조건이 비슷한 매물을 찾고 있습니다…`;
    try {
      let job = await request("/api/v2/personal/search", { method: "POST", data: { subject }, signal: controller.signal });
      const started = Date.now();
      if (!job.job_id) throw new Error("검색 작업을 시작하지 못했습니다. 직접 입력으로 계속할 수 있습니다.");
      if (sequence !== state.searchSequence) return;
      state.searchJobId = job.job_id;
      const jobId = job.job_id;
      const waitMilliseconds = jobWaitMilliseconds(job, 120);
      while (["pending", "running"].includes(job.status)) {
        if (Date.now() - started > waitMilliseconds) throw new Error("검색이 오래 걸려 대기를 마쳤습니다. 원문에서 찾은 매물을 직접 입력해 주세요.");
        $("personal-search-status").textContent = queueMessage(job, `${automaticSourceName()}에서 조건이 비슷한 매물을 찾고 있습니다…`);
        await delay(jobPollMilliseconds(job), controller.signal);
        job = await request(`/api/v2/personal/search/${encodeURIComponent(jobId)}`, { signal: controller.signal });
        if (!job || job.job_id !== jobId) throw new Error("검색 작업 응답이 일치하지 않습니다.");
      }
      if (sequence !== state.searchSequence) return;
      if (job.status !== "complete") { const error = new Error(job.error?.message || job.message || "자동검색을 완료하지 못했습니다. 직접 입력한 매물은 유지됩니다."); error.code = job.error?.code; throw error; }
      renderReports(job.result?.source_reports || []);
      const found = Array.isArray(job.result?.listings) ? job.result.listings : [];
      state.listings = state.listings.filter((row) => row.origin === "manual");
      for (const data of found) state.listings.push({ key: state.nextKey++, data, selected: true, origin: "search" });
      renderListings();
      $("personal-search-status").textContent = found.length ? `광고 ${found.length}개를 목록에 가져왔습니다. 건물 수와 검색 범위를 확인한 뒤 비교하세요.` : "이번 검색에서 광고를 가져오지 못했습니다. 주변에 매물이 없다는 의미는 아닙니다. 검색 범위와 원문을 확인하거나 직접 입력으로 계속할 수 있습니다.";
    } catch (error) { if (sequence === state.searchSequence && error.name !== "AbortError") { serviceDisconnected(error); errorAt("personal-subject-error", error.message); $("personal-search-status").textContent = "자동검색에 실패해도 직접 입력으로 비교할 수 있습니다."; } }
    finally { if (sequence === state.searchSequence) { state.search = null; state.searchJobId = null; updateButtons(); } }
  }
  function comparableDetails(listings, basis, label, heading) {
    const rentOnly = basis === "rent";
    const details = element("details"); details.append(element("summary", heading));
    const wrap = element("div", null, "table-wrap"); wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", "비교 매물 표, 가로로 스크롤할 수 있습니다");
    const table = element("table"); const head = element("thead"); const headings = element("tr");
    for (const name of ["매물", label, "건물 종류 / 규모 / 엘리베이터", "면적 / 구조", "준공 / 도보 / 거주 층", "조건 차이·확인 필요"]) headings.append(element("th", name)); head.append(headings); table.append(head);
    const body = element("tbody");
    const shown = (value, unit) => Number.isFinite(value) ? `${value}${unit}` : "미상";
    for (const data of listings) {
      const row = element("tr"); const title = element("td"); const link = sourceLink(data.source_url, titleFor(data)); title.append(link || element("span", titleFor(data)));
      if (data.fetched_at) title.append(element("small", `조회 ${timestamp(data.fetched_at)}`, "table-note"));
      const price = element("td", yen(data.comparison_price_yen ?? (rentOnly ? data.rent_yen : data.monthly_total_yen)), "table-price");
      if (rentOnly) price.append(element("small", `관리비 ${yen(data.mgmt_fee_yen)} · 비교에서 제외`, "table-note"));
      const building = element("td", buildingTypes[data.building_type] || "건물 종류 미상");
      building.append(element("small", `건물 총 ${shown(data.building_floors, "층")} · 엘리베이터 ${data.elevator === true ? "있음" : data.elevator === false ? "없음" : "미상"}`, "table-note"));
      row.append(title, price, building, element("td", `${shown(data.area_sqm, "m²")} / ${structures[data.structure] || "미상"}`), element("td", `${shown(data.built_year, "년")} / ${shown(data.walk_min, "분")} / 거주 ${shown(data.floor, "층")}`));
      const differences = data.differences?.length ? data.differences : (data.missing_fields || []).map((key) => `${fieldNames[key] || key} 미상`);
      row.append(element("td", differences.length ? differences.join(" · ") : "표시할 조건 차이 없음", "table-differences")); body.append(row);
    }
    table.append(body); wrap.append(table); details.append(wrap);
    return details;
  }
  function referenceGroups(groups, count) {
    const section = element("section", null, "personal-reference-section");
    section.append(element("h3", `건물 조건별 별도 참고 매물 ${count}개`));
    section.append(element("p", "건물 종류·총층수·구조·엘리베이터·거주 층의 위치가 다르거나 확인되지 않은 매물입니다. 각 묶음의 가격을 따로 보여주며 내 집과의 가격 차이나 주 비교 중앙값에는 합치지 않습니다.", "personal-reference-intro"));
    const grid = element("div", null, "personal-reference-grid");
    for (const group of groups) {
      const card = element("article", null, "personal-reference-card");
      const relation = group.relation === "different" ? "건물 조건 다름" : "건물 정보 미확인";
      if (group.same_building_features === true) {
        card.classList.add("has-related-features");
        card.tabIndex = -1;
        card.append(element("p", "건물 종류·구조군·승강기 일치", "personal-reference-related"));
      }
      card.append(element("p", relation, "personal-reference-relation"), element("h4", group.label || relation));
      if (group.same_building_features === true) card.append(element("p", "총층수·준공 연도·면적까지 같다는 의미는 아닙니다. 아래 조건 차이를 확인하세요.", "personal-reference-related-note"));
      const basis = group.price_basis_label || (group.price_basis === "rent" ? "월세만 · 관리비 제외" : "월세 + 관리비");
      card.append(element("p", `${group.sample_count ?? 0}개 매물 · ${group.building_groups ?? 0}개 건물 그룹 · ${basis}`, "personal-reference-count"));
      if (group.price_range && Number.isFinite(group.price_range.min_yen) && Number.isFinite(group.price_range.max_yen)) {
        card.append(element("strong", `${yen(group.price_range.min_yen)} ~ ${yen(group.price_range.max_yen)}`, "personal-reference-range"));
        card.append(element("p", "이 묶음의 최저·최고 모집가격", "personal-reference-count"));
      }
      if (group.summary && Number.isFinite(group.summary.median_yen)) card.append(element("p", `이 묶음의 중앙값 ${yen(group.summary.median_yen)}`, "personal-reference-median"));
      if (group.reasons?.length) { const reasons = element("ul", null, "personal-reference-reasons"); for (const reason of group.reasons) reasons.append(element("li", reason)); card.append(reasons); }
      for (const warning of group.warnings || []) card.append(element("p", warning, "personal-reference-count"));
      if (group.condition_notes?.length || group.unverified_fields?.length) {
        const context = element("div", null, "personal-reference-context");
        for (const note of group.condition_notes || []) context.append(element("p", note));
        if (group.unverified_fields?.length) context.append(element("p", `확인 필요: ${group.unverified_fields.map((key) => fieldNames[key] || key).join(" · ")}`));
        card.append(context);
      }
      if (group.comparables?.length) card.append(comparableDetails(group.comparables, group.price_basis, basis, `참고 매물 ${group.comparables.length}개 · 원문과 조건 보기`));
      grid.append(card);
    }
    section.append(grid);
    return section;
  }
  function visualComparison(visual) {
    if (!visual || visual.mode === "empty" || !Array.isArray(visual.comparables) || !visual.comparables.length) return null;
    const comparables = visual.comparables;
    if (!Number.isFinite(visual.subject?.price_yen) || visual.subject.price_yen < 0 || comparables.some((row) => !Number.isFinite(row.price_yen) || row.price_yen < 0)) return null;
    const section = element("section", null, "personal-price-visual"); section.setAttribute("aria-labelledby", "personal-price-visual-title");
    const basis = visual.price_basis_label || (visual.price_basis === "rent" ? "월세만 · 관리비 제외" : "월세 + 관리비");
    section.append(element("p", visual.title || "내 집과 가까운 매물의 가격", "personal-visual-kicker"));
    const rank = visual.rank;
    let headline = `내 집과 참고 매물 ${comparables.length}개의 가격을 비교해 보세요`;
    if (rank && Number.isInteger(rank.low_to_high) && Number.isInteger(rank.tied_to) && rank.total === comparables.length + 1) {
      headline = rank.low_to_high === rank.tied_to ? `내 집 가격은 ${rank.total}개 중 ${rank.low_to_high}번째` : `내 집 가격은 ${rank.total}개 중 공동 ${rank.low_to_high}위`;
    }
    const title = element("h3", headline); title.id = "personal-price-visual-title"; section.append(title);
    let rankNote = `낮은 가격순 · 내 집 포함 · ${basis}`;
    if (rank && rank.tied_to > rank.low_to_high) rankNote += ` · 같은 가격이 ${rank.low_to_high}~${rank.tied_to}번째에 있습니다`;
    section.append(element("p", rankNote, "personal-visual-rank-note"));
    section.append(element("p", visual.scope_label || "이번에 표시한 모집가격 사이의 순위입니다. 시장 전체 순위나 적정가격 판정은 아닙니다.", "personal-visual-scope"));
    if (visual.selection_label) section.append(element("p", visual.selection_label, "personal-visual-selection"));
    const maximum = Math.max(visual.subject.price_yen, ...comparables.map((row) => row.price_yen), 1);
    const scale = Math.ceil(maximum / 10000) * 10000;
    const plot = element("figure", null, "personal-price-plot");
    const caption = element("figcaption", `표시한 ${comparables.length + 1}개 가격 · 막대는 0엔부터 시작합니다`); plot.append(caption);
    const bars = element("ol", null, "personal-price-bars"); bars.setAttribute("aria-label", "내 집과 비교 매물의 낮은 가격순 목록");
    const priced = comparables.map((data) => ({ data, subject: false })); priced.push({ data: visual.subject, subject: true });
    priced.sort((left, right) => left.data.price_yen - right.data.price_yen);
    for (const item of priced) {
      const row = element("li", null, `personal-price-row${item.subject ? " is-subject" : ""}`);
      const heading = element("div", null, "personal-price-row-heading");
      heading.append(element("span", item.subject ? "내 집" : titleFor(item.data), "personal-price-row-name"), element("strong", yen(item.data.price_yen)));
      const track = element("div", null, "personal-price-track"); track.setAttribute("aria-hidden", "true");
      const bar = element("span", null, "personal-price-bar"); bar.style.width = `${Math.max(0, Math.min(100, item.data.price_yen / scale * 100))}%`; track.append(bar); row.append(heading, track); bars.append(row);
    }
    plot.append(bars);
    const axis = element("div", null, "personal-price-axis"); axis.setAttribute("aria-hidden", "true"); axis.append(element("span", "0엔"), element("span", yen(scale))); plot.append(axis); section.append(plot);
    if (visual.warnings?.length) { const warnings = element("ul", null, "personal-visual-warnings"); for (const warning of visual.warnings) warnings.append(element("li", warning)); section.append(warnings); }
    const heading = element("h4", `그래프에 사용한 비교 매물 ${comparables.length}개`, "personal-visual-list-title"); section.append(heading);
    section.append(element("p", "각 모집가격과 내 집의 금액 차이입니다. 건물·연식·면적 등의 차이를 함께 확인하세요.", "personal-visual-list-help"));
    const cards = element("div", null, "personal-visual-list");
    const displayed = [...comparables].sort((left, right) => left.price_yen - right.price_yen);
    for (const data of displayed) {
      const card = element("article", null, "personal-visual-listing");
      const header = element("div", null, "personal-visual-listing-heading"); header.append(element("h5", titleFor(data)), element("strong", yen(data.price_yen))); card.append(header);
      if (Number.isFinite(data.delta_yen)) card.append(element("p", data.delta_yen === 0 ? "내 집과 같은 금액" : `내 집이 ${yen(Math.abs(data.delta_yen))} ${data.delta_yen > 0 ? "더 비쌉니다" : "더 저렴합니다"}`, "personal-visual-difference"));
      card.append(element("p", visual.price_basis === "rent" ? `월세만 비교 · 관리비 ${yen(data.mgmt_fee_yen)} 제외` : `월세 ${yen(data.rent_yen)} + 관리비 ${yen(data.mgmt_fee_yen)}`, "personal-visual-cost"));
      const facts = element("div", null, "personal-visual-facts");
      const shown = (value, unit) => Number.isFinite(value) ? `${value}${unit}` : "미상";
      for (const fact of [buildingTypes[data.building_type] || "건물 종류 미상", structures[data.structure] || "구조 미상", `면적 ${shown(data.area_sqm, "m²")}`, `준공 ${shown(data.built_year, "년")}`, `거주 ${shown(data.floor, "층")} / 건물 총 ${shown(data.building_floors, "층")}`, `역 도보 ${shown(data.walk_min, "분")}`, `엘리베이터 ${data.elevator === true ? "있음" : data.elevator === false ? "없음" : "미상"}`]) facts.append(element("span", fact)); card.append(facts);
      if (data.match_labels?.length) card.append(element("p", `확인된 공통 조건: ${data.match_labels.join(" · ")}`, "personal-visual-matches"));
      const differences = data.differences?.length ? data.differences : (data.missing_fields || []).map((key) => `${fieldNames[key] || key} 미상`);
      if (differences.length) { const list = element("ul", null, "personal-visual-differences"); for (const difference of differences) list.append(element("li", difference)); card.append(list); }
      if (data.reference_group_label) card.append(element("p", `참고군: ${data.reference_group_label}`, "personal-visual-group"));
      const source = element("div", null, "personal-visual-source"); const url = safeUrl(data.source_url);
      source.append(element("span", "매물 원문")); source.append(url ? sourceLink(url, url) : element("span", "원문 링크 미상"));
      source.append(element("small", data.fetched_at ? `페이지 조회 ${timestamp(data.fetched_at)}` : "조회 시각 미상")); card.append(source); cards.append(card);
    }
    section.append(cards);
    return section;
  }
  function renderResult(result) {
    const root = $("personal-result-content"); root.replaceChildren();
    const visual = visualComparison(result.visual_comparison);
    let container = root;
    if (visual) {
      root.append(visual);
      const details = element("details", null, "personal-method-details"); details.append(element("summary", "주 비교 요약 · 검색 범위 · 전체 참고군 자세히 보기"));
      container = element("div", null, "personal-method-content"); details.append(container); root.append(details);
    }
    const summary = result.summary;
    const priceRange = result.price_range;
    const groups = Array.isArray(result.reference_groups) ? result.reference_groups : [];
    const referenceCount = result.reference_count ?? groups.reduce((count, group) => count + (group.sample_count || 0), 0);
    const relatedCount = result.related_reference_count ?? groups.filter((group) => group.same_building_features === true).reduce((count, group) => count + (group.sample_count || 0), 0);
    const noPrimary = (result.matched_count ?? 0) === 0;
    const searched = state.listings.some((row) => row.selected && row.origin === "search");
    const rentOnly = result.price_basis === "rent";
    const basisLabel = result.price_basis_label || (rentOnly ? "월세만 · 관리비 제외" : "월세 + 관리비");
    const basisShort = rentOnly ? "월세만" : "관리비 포함";
    const levelLabels = { exact: "엄격한 조건으로 비교", relaxed: "일부 조건을 넓혀 비교", broad: "조건 차이를 넓힌 참고 표본", partial: "미상 조건이 있는 참고 표본" };
    const levelLabel = noPrimary ? "이번 목록에서 확인된 주 비교 매물 0개" : result.comparison_label || levelLabels[result.comparison_level] || "선택 표본 비교";
    const uncertain = noPrimary || ["broad", "partial"].includes(result.comparison_level);
    container.append(element("p", levelLabel, `personal-level-label${uncertain ? " is-cautious" : ""}`));
    if (result.status === "reference" && summary && Number.isFinite(summary.delta_pct)) {
      const delta = summary.delta_pct;
      container.append(element("p", `선택 표본 중앙값 대비 ${delta > 0 ? "+" : ""}${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1 }).format(delta)}%`, "personal-result-summary"));
    } else if (priceRange && Number.isFinite(priceRange.min_yen) && Number.isFinite(priceRange.max_yen)) {
      container.append(element("p", `참고 매물 ${priceRange.sample_count}개: ${yen(priceRange.min_yen)} ~ ${yen(priceRange.max_yen)}`, "personal-result-summary is-range"));
    } else container.append(element("p", `${searched ? "이번 검색" : "이번 비교 목록"}에서 주 비교 매물을 확인하지 못했습니다.`, "personal-result-summary is-hold"));
    if (noPrimary) {
      container.append(element("p", "검색에 포함되지 않았거나 상세 조건을 확인하지 못한 매물이 있을 수 있습니다. 주변에 비슷한 매물이 없다고 판단할 수 없습니다.", "personal-info-warning"));
      if (relatedCount > 0) {
        const related = element("div", null, "personal-related-intro");
        related.append(element("p", `건물 종류·구조군·승강기 등이 확인된 참고 광고 ${relatedCount}개를 먼저 확인할 수 있습니다. 총층수 등 조건 차이를 남겨 둔 별도 참고가격입니다.`));
        const jump = element("button", "확인된 건물 특징의 참고 매물 보기", "secondary-button personal-related-jump"); jump.type = "button";
        jump.addEventListener("click", () => { const card = container.querySelector(".has-related-features"); if (card) reveal(card); }); related.append(jump); container.append(related);
      }
      else if (referenceCount > 0) container.append(element("p", `조건별로 나눈 별도 참고 광고 ${referenceCount}개를 아래에서 확인할 수 있습니다.`, "personal-result-intro"));
    }
    container.append(element("p", `비교 금액 기준: ${basisLabel}`, `personal-price-basis${rentOnly ? " is-rent-only" : ""}`));
    container.append(element("p", result.scope || "선택한 매물의 모집가격을 비교한 참고 결과입니다. 전체 시장의 적정 가격을 판정하지 않습니다.", "personal-result-intro"));
    if (noPrimary && searched && state.sourceReports.length) {
      const searchScope = element("section", null, "personal-result-search"); searchScope.append(element("h3", "이번 검색에서 확인한 범위"));
      for (const report of state.sourceReports) searchScope.append(reportCard(report)); container.append(searchScope);
    }
    if (result.relaxed_fields?.length || result.unverified_fields?.length || result.condition_notes?.length) {
      const context = element("div", null, "personal-comparison-context");
      for (const note of result.condition_notes || []) context.append(element("p", note));
      if (result.relaxed_fields?.length) context.append(element("p", `넓힌 조건: ${result.relaxed_fields.map((key) => fieldNames[key] || key).join(" · ")}`));
      if (result.unverified_fields?.length) context.append(element("p", `확인되지 않은 조건: ${result.unverified_fields.map((key) => fieldNames[key] || key).join(" · ")}`));
      container.append(context);
    }
    const metrics = element("div", null, "personal-result-metrics");
    const subjectPrice = result.subject_comparison_yen ?? (rentOnly ? null : result.subject_total_yen);
    const middleLabel = summary ? `표본 중앙값 · ${basisShort}` : priceRange ? `표본 최저·최고 · ${basisShort}` : `가격 요약 · ${basisShort}`;
    const middleValue = summary ? yen(summary.median_yen) : priceRange ? `${yen(priceRange.min_yen)} ~ ${yen(priceRange.max_yen)}` : "산출 보류";
    const middleNote = summary ? `중간 50%의 표본 가격: ${yen(summary.q1_yen)} ~ ${yen(summary.q3_yen)}` : priceRange ? "이번에 비교한 매물의 최저·최고 모집가격" : "비교 가능한 매물의 가격과 조건을 확인하세요";
    for (const [index, [label, value, note]] of [[`내 집 · ${basisShort}`, yen(subjectPrice), basisLabel], [middleLabel, middleValue, middleNote], ["같은 건물 조건의 주 비교 매물", `${result.matched_count ?? 0}개`, summary ? `${summary.building_groups ?? 0}개 건물 그룹 · 입력 광고 ${result.input_count ?? 0}개 중` : `입력 광고 ${result.input_count ?? 0}개 중 · 별도 참고 ${referenceCount}개`]].entries()) {
      const box = element("div", null, `personal-result-metric${index === 1 && !summary && priceRange ? " is-range" : ""}`); box.append(element("span", label), element("strong", value), element("small", note)); metrics.append(box);
    }
    container.append(metrics);
    const coverage = [];
    if (Number.isInteger(result.strict_count)) coverage.push(`엄격한 조건 ${result.strict_count}개 → 주 비교 ${result.matched_count ?? 0}개 · 별도 참고 ${referenceCount}개`);
    if (result.fee_coverage && Number.isInteger(result.fee_coverage.known_count) && Number.isInteger(result.fee_coverage.missing_count)) coverage.push(`관리비 확인 ${result.fee_coverage.known_count}개 · 미상 ${result.fee_coverage.missing_count}개`);
    if (coverage.length) container.append(element("p", coverage.join(" / "), "personal-condition-help"));
    if (result.missing_subject_fields?.length) container.append(element("p", `내 집에서 더 확인할 정보: ${result.missing_subject_fields.map((key) => fieldNames[key] || key).join(" · ")}`, "personal-info-warning"));
    if (groups.length) container.append(referenceGroups(groups, referenceCount));
    if (result.warnings?.length) { const list = element("ul", null, "reason-list"); for (const warning of result.warnings) list.append(element("li", warning)); container.append(list); }
    if (result.steps?.length) {
      const details = element("details"); details.append(element("summary", "조건을 넓힌 순서와 매물 수 보기")); const list = element("ol", null, "step-list");
      for (const step of result.steps) { const item = element("li"); const stepBasis = step.price_basis_label || (step.price_basis === "rent" ? "월세만" : step.price_basis === "total" ? "관리비 포함" : ""); item.append(element("span", `${step.label}${stepBasis ? ` · ${stepBasis}` : ""}${step.selected ? " · 적용" : ""}`), element("strong", `${step.count}개`)); list.append(item); } details.append(list); container.append(details);
    }
    if (result.comparables?.length) container.append(comparableDetails(result.comparables, result.price_basis, basisLabel, `주 비교 매물 ${result.comparables.length}개와 조건 차이 보기`));
    if (result.excluded?.length) {
      const details = element("details"); details.append(element("summary", `제외한 매물 ${result.excluded.length}개와 이유`)); const list = element("ul");
      for (const row of result.excluded) list.append(element("li", `${row.title || row.id || "매물"}: ${row.reason}`)); details.append(list); container.append(details);
    }
    container.append(element("p", "모집 상태, 중복 매물, 입력 오류와 검색에서 빠진 매물에 따라 결과가 달라질 수 있습니다.", "personal-result-stamp"));
    $("personal-result").hidden = false; reveal($("personal-result"));
  }
  async function compare() {
    if (state.importing || state.search || state.compare || state.options?.enabled === false || !$("personal-subject-form").reportValidity()) return;
    const keys = [...fields.map((field) => field.key), "source_id", "title", "fetched_at"];
    const listings = state.listings.filter((row) => row.selected).map((row) => Object.fromEntries(keys.filter((key) => row.data[key] !== null && row.data[key] !== undefined && row.data[key] !== "").map((key) => [key, row.data[key]])));
    if (!listings.length) return;
    if (listings.length > 60) { errorAt("personal-compare-error", "비교할 매물은 최대 60개까지 선택해 주세요."); return; }
    let subject;
    try { subject = values($("personal-subject-form")); } catch (error) { errorAt("personal-subject-error", error.message); return; }
    clearResult(); errorAt("personal-compare-error");
    const sequence = ++state.compareSequence; const controller = new AbortController(); state.compare = controller; updateButtons();
    $("personal-compare-status").textContent = "선택한 매물의 조건과 가격을 비교하고 있습니다…";
    try {
      const result = await request("/api/v2/personal/compare", { method: "POST", data: { subject, listings }, signal: controller.signal });
      if (sequence === state.compareSequence) { renderResult(result); $("personal-compare-status").textContent = "비교를 마쳤습니다."; }
    } catch (error) { if (sequence === state.compareSequence && error.name !== "AbortError") { errorAt("personal-compare-error", error.message); $("personal-compare-status").textContent = ""; } }
    finally { if (sequence === state.compareSequence) { state.compare = null; updateButtons(); } }
  }

  buildFields($("personal-subject-fields"), "subject"); buildFields($("personal-editor-fields"), "editor");
  $("personal-import-form").addEventListener("submit", importSubject);
  $("personal-reconnect").addEventListener("click", loadOptions);
  $("personal-import-url").addEventListener("input", () => { stopImport(state.importing ? "링크가 바뀌어 불러오기를 중단했습니다. 새 링크로 다시 불러오세요." : ""); errorAt("personal-import-error"); });
  $("personal-import-cancel").addEventListener("click", cancelActive);
  $("personal-subject-form").addEventListener("submit", search);
  const subjectEdited = () => {
    stopImport(state.importing ? "집의 조건을 수정해 불러오기를 중단했습니다. 수정한 입력은 유지됩니다." : ""); clearImportReview();
    if (state.search) stopSearch("집의 조건이 바뀌어 검색 대기를 중단했습니다. 다시 검색해 주세요.");
    clearResult(); errorAt("personal-subject-error");
    try { updateSubjectTotal(); } catch { /* Invalid in-progress text is checked on submission. */ }
  };
  $("personal-subject-form").addEventListener("input", subjectEdited);
  $("personal-subject-form").addEventListener("change", subjectEdited);
  $("subject-city").addEventListener("change", () => { $("subject-municipality").value = ""; $("subject-station_name").value = ""; municipalityOptions("subject"); });
  $("editor-city").addEventListener("change", () => { $("editor-municipality").value = ""; $("editor-station_name").value = ""; municipalityOptions("editor"); });
  for (const id of ["personal-search-cancel", "personal-compare-cancel", "personal-dock-cancel"]) $(id).addEventListener("click", cancelActive);
  for (const button of document.querySelectorAll("[data-compare-action]")) button.addEventListener("click", compare);
  $("personal-dock-result").addEventListener("click", () => { if (!$("personal-result").hidden) reveal($("personal-result")); });
  $("personal-back-to-listings").addEventListener("click", () => reveal($("personal-listings-card")));
  $("personal-add").addEventListener("click", () => openEditor());
  for (const id of ["personal-editor-close", "personal-editor-cancel"]) $(id).addEventListener("click", () => $("personal-editor").close());
  $("personal-editor-form").addEventListener("submit", (event) => {
    event.preventDefault(); if (!$("personal-editor-form").reportValidity()) return;
    try {
      const data = values($("personal-editor-form"));
      const existing = state.listings.find((row) => row.key === state.editingKey);
      if (!existing && state.listings.length >= 60) throw new Error("비교 매물은 최대 60개입니다. 사용하지 않는 매물을 삭제한 뒤 추가하세요.");
      if (existing) { existing.data = { ...existing.data, ...data, source_id: "manual" }; existing.origin = "manual"; }
      else state.listings.push({ key: state.nextKey++, data: { ...data, source_id: "manual" }, selected: true, origin: "manual" });
      clearResult("비교 목록이 바뀌었습니다. 다시 비교해 주세요."); renderListings(); $("personal-editor").close();
    } catch (error) { errorAt("personal-editor-error", error.message); }
  });
  $("personal-select-all").addEventListener("change", () => {
    const checked = $("personal-select-all").checked;
    for (const row of state.listings) row.selected = checked;
    for (const checkbox of document.querySelectorAll(".personal-listing-check input")) checkbox.checked = checked;
    clearResult("선택한 매물이 바뀌었습니다. 다시 비교해 주세요."); updateSelection();
  });
  $("personal-example").addEventListener("click", () => {
    stopImport(); clearImportReview();
    fill($("personal-subject-form"), { city: "tokyo", municipality: "新宿区", station_name: "新宿", layout: "1K", area_sqm: 25, rent_yen: 95000, mgmt_fee_yen: 5000, structure: "rc", built_year: 2015, walk_min: 8, floor: 3, orientation: "S", bathroom_separate: true, furnished: false, contract_type: "standard", property_type: "apartment" });
    municipalityOptions("subject"); updateSubjectTotal(); clearResult(); $("personal-example-note").hidden = false; errorAt("personal-subject-error");
  });
  let accessComposing = false, accessSubmitPending = false;
  $("personal-access-code").addEventListener("compositionstart", () => { accessComposing = true; });
  $("personal-access-code").addEventListener("compositionend", () => {
    accessComposing = false;
    setTimeout(() => {
      if (accessSubmitPending && !accessComposing) { accessSubmitPending = false; $("personal-access-form").requestSubmit(); }
    }, 0);
  });
  $("personal-access-code").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (accessComposing || event.isComposing || event.keyCode === 229)) event.preventDefault();
  });
  $("personal-access-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (accessComposing) { accessSubmitPending = true; return; }
    accessSubmitPending = false;
    if ($("personal-access-submit").disabled) return;
    stopImport(); state.authSequence += 1; state.accessToken = $("personal-access-code").value.trim().normalize("NFC"); $("personal-access-code").value = "";
    errorAt("personal-access-error"); $("personal-access-submit").disabled = true;
    try { await loadOptions(); } finally { $("personal-access-submit").disabled = false; }
  });
  $("personal-logout").addEventListener("click", () => {
    accessComposing = false; accessSubmitPending = false;
    stopImport(); clearImportReview(); stopSearch(); state.authSequence += 1; state.accessToken = ""; state.optionsSequence += 1; state.options = null; state.optionsLoading = false; state.connectionFailed = false;
    clearResult(); state.listings = []; renderListings(); renderReports([]);
    $("personal-import-form").reset(); $("personal-subject-form").reset(); $("personal-editor-form").reset(); $("personal-editor").close();
    $("personal-example-note").hidden = true; $("personal-logout").hidden = true; $("personal-access-panel").hidden = false;
    $("personal-service-status").textContent = "접속 코드와 입력·비교 목록을 지웠습니다.";
    for (const id of ["personal-access-error", "personal-import-error", "personal-subject-error", "personal-compare-error", "personal-editor-error"]) errorAt(id);
    updateSubjectTotal();
  });
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(updateDockSpace).observe($("personal-action-dock"));
  window.addEventListener("resize", updateDockSpace);
  updateSubjectTotal(); renderListings(); loadOptions();
})();
