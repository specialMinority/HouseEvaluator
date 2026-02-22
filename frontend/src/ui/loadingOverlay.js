import { h, safeString } from "../lib/utils.js";

function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(Number(ms || 0) / 1000));
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function resolveRootAsset(pathFromRoot) {
  // Served by backend/src/server.py from repo root.
  // Use an absolute URL so it works regardless of /frontend/ nesting.
  const p = String(pathFromRoot || "").replace(/^\/+/, "");
  return new URL(`/${p}`, window.location.href).toString();
}

const MASCOT = {
  searching: resolveRootAsset("image/Searching.png"),
  analyzing: resolveRootAsset("image/Analyzing.png"),
  success: resolveRootAsset("image/Success.png"),
  error: resolveRootAsset("image/Error.png"),
};

const DEFAULT_STEPS = [
  { id: "normalize", label: "검색 조건 정리" },
  { id: "suumo_fetch", label: "SUUMO에서 매물 수집" },
  { id: "chintai_fetch", label: "CHINTAI에서 매물 수집" },
  { id: "match", label: "조건 일치 매물 선별" },
  { id: "median", label: "중간값(중위수) 계산" },
  { id: "compare", label: "선택 매물과 비교" },
];

const ROTATION_MESSAGES = [
  "실시간으로 조건에 맞는 매물을 찾고 있어요.",
  "SUUMO에서 방 구조/면적/임대료를 확인 중이에요.",
  "CHINTAI에서도 같은 조건 매물을 모으는 중이에요.",
  "비슷한 매물들로 중간값을 계산해서 시세를 만들고 있어요.",
  "거의 다 됐어요! 끝까지 확인해볼게요.",
  "사이트가 잠깐 느릴 수 있어요. 잠시만 기다려 주세요.",
];

function iconFor(status) {
  if (status === "done") return "✓";
  if (status === "warn") return "!";
  if (status === "error") return "×";
  if (status === "running") return "↻";
  return "○";
}

export function createLoadingOverlay({ showDebug = false } = {}) {
  let mounted = false;
  let root = null;
  let modal = null;
  let titleEl = null;
  let subtitleEl = null;
  let timerEl = null;
  let bubbleEl = null;
  let mascotImg = null;
  let progressTextEl = null;
  let progressFillEl = null;
  let stepsOl = null;
  let logPre = null;
  let detailsEl = null;
  let btnBackground = null;
  let btnCancel = null;
  let btnClose = null;
  let btnRetry = null;
  let chip = null;

  let stepEls = new Map();
  let stepStatus = new Map();
  let startAt = 0;
  let timers = [];
  let rotationTimer = null;
  let clockTimer = null;
  let hiddenByUser = false;
  let lastActiveEl = null;
  let currentState = "idle"; // idle | running | success | partial | error | cancelled

  let onCancelCb = null;
  let onRetryCb = null;

  function mount() {
    if (mounted) return;
    mounted = true;

    titleEl = h("div", { class: "loading__title", id: "loadingTitle", text: "실시간 시세 평가 중" });
    subtitleEl = h("div", {
      class: "loading__subtitle",
      id: "loadingDesc",
      text: "SUUMO / CHINTAI에서 조건에 맞는 매물을 찾고 있어요",
    });
    timerEl = h("span", { class: "pill loading__timer", text: "00:00" });

    const header = h("div", { class: "loading__header" }, [
      h("div", {}, [titleEl, subtitleEl]),
      h("div", { class: "loading__headerRight" }, [timerEl]),
    ]);

    mascotImg = h("img", {
      class: "loading__mascot",
      src: MASCOT.searching,
      alt: "진행 중 캐릭터",
      decoding: "async",
    });
    bubbleEl = h("div", { class: "loading__bubble", text: ROTATION_MESSAGES[0] });

    progressTextEl = h("div", { class: "loading__progressText", text: "진행 0/6" });
    progressFillEl = h("div", { class: "loading__progressFill", style: "width: 0%" });
    const progress = h("div", { class: "loading__progress" }, [progressFillEl]);

    const left = h("div", { class: "loading__left" }, [mascotImg, bubbleEl, progressTextEl, progress]);

    stepsOl = h("ol", { class: "loading__steps" });
    for (const s of DEFAULT_STEPS) {
      const iconEl = h("span", { class: "loading__stepIcon", text: iconFor("pending") });
      const labelEl = h("div", { class: "loading__stepLabel", text: s.label });
      const li = h("li", { class: "loading__step loading__step--pending", dataset: { step: s.id } }, [iconEl, labelEl]);
      stepsOl.appendChild(li);
      stepEls.set(s.id, { li, iconEl, labelEl });
      stepStatus.set(s.id, "pending");
    }

    logPre = h("pre", { class: "card loading__log", text: "" });
    detailsEl = h("details", { class: "loading__details", open: false }, [
      h("summary", { class: "pill", text: "자세히 보기" }),
      h("div", { class: "loading__detailsBody" }, [logPre]),
    ]);
    if (showDebug) detailsEl.open = true;

    const right = h("div", { class: "loading__right" }, [stepsOl, detailsEl]);
    const body = h("div", { class: "loading__body" }, [left, right]);

    btnBackground = h("button", {
      type: "button",
      class: "btn btn--ghost",
      text: "백그라운드에서 계속",
      onClick: () => background(),
    });
    btnCancel = h("button", {
      type: "button",
      class: "btn btn--danger",
      text: "취소",
      onClick: () => cancel(),
    });
    btnClose = h("button", {
      type: "button",
      class: "btn btn--ghost hidden",
      text: "닫기",
      onClick: () => hide({ keepChip: false }),
    });
    btnRetry = h("button", {
      type: "button",
      class: "btn hidden",
      text: "재시도",
      onClick: () => {
        if (typeof onRetryCb === "function") onRetryCb();
      },
    });

    const actions = h("div", { class: "actions loading__actions" }, [btnBackground, btnCancel, btnClose, btnRetry]);

    modal = h(
      "div",
      {
        class: "loading__modal",
        role: "dialog",
        "aria-modal": "true",
        "aria-labelledby": "loadingTitle",
        "aria-describedby": "loadingDesc",
        tabIndex: "-1",
      },
      [header, body, actions]
    );

    root = h("div", { class: "loading hidden", "aria-hidden": "true" }, [
      h("div", {
        class: "loading__backdrop",
        onClick: (e) => {
          // Click outside should not cancel the job; just keep running in background.
          e.preventDefault();
          background();
        },
      }),
      modal,
    ]);

    root.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        background();
      }
    });

    document.body.appendChild(root);

    // Background chip (hidden by default)
    chip = h("button", {
      type: "button",
      class: "loadingChip hidden",
      text: "평가 진행 중… 보기",
      onClick: () => show({ focus: true }),
    });
    document.body.appendChild(chip);
  }

  function preload() {
    // Do not block initial render; best-effort warm up.
    for (const src of Object.values(MASCOT)) {
      const img = new Image();
      img.decoding = "async";
      img.src = src;
    }
  }

  function addLog(line) {
    if (!logPre) return;
    const cur = safeString(logPre.textContent || "");
    const next = cur ? `${cur}\n${line}` : String(line);
    logPre.textContent = next;
    // Keep the log pinned to bottom.
    logPre.scrollTop = logPre.scrollHeight;
  }

  function updateProgress() {
    const total = DEFAULT_STEPS.length;
    const done = Array.from(stepStatus.values()).filter((s) => s === "done").length;
    const running = Array.from(stepStatus.values()).some((s) => s === "running");
    const shown = done + (running ? 0 : 0);
    if (progressTextEl) progressTextEl.textContent = `진행 ${Math.min(total, shown)}/${total}`;
    const pct = Math.min(0.95, total ? done / total : 0) * 100;
    if (progressFillEl) progressFillEl.style.width = `${pct.toFixed(1)}%`;
  }

  function setMascot(kind) {
    if (!mascotImg) return;
    const src = MASCOT[kind] || MASCOT.searching;
    if (mascotImg.src !== src) mascotImg.src = src;
  }

  function setBubble(text) {
    if (!bubbleEl) return;
    bubbleEl.textContent = String(text || "");
  }

  function setTitle(title, subtitle) {
    if (titleEl) titleEl.textContent = String(title || "");
    if (subtitleEl) subtitleEl.textContent = String(subtitle || "");
  }

  function setStep(stepId, status) {
    const info = stepEls.get(stepId);
    if (!info) return;
    stepStatus.set(stepId, status);

    info.iconEl.textContent = iconFor(status);
    info.li.className = `loading__step loading__step--${status}`;
    updateProgress();
  }

  function resetUI() {
    currentState = "idle";
    hiddenByUser = false;
    onCancelCb = null;
    onRetryCb = null;

    setTitle("실시간 시세 평가 중", "SUUMO / CHINTAI에서 조건에 맞는 매물을 찾고 있어요");
    setMascot("searching");
    setBubble(ROTATION_MESSAGES[0]);

    if (timerEl) timerEl.textContent = "00:00";
    if (logPre) logPre.textContent = "";

    for (const s of DEFAULT_STEPS) setStep(s.id, "pending");

    if (detailsEl) detailsEl.open = Boolean(showDebug);

    if (btnBackground) btnBackground.classList.remove("hidden");
    if (btnCancel) btnCancel.classList.remove("hidden");
    if (btnClose) btnClose.classList.add("hidden");
    if (btnRetry) btnRetry.classList.add("hidden");
  }

  function clearTimers() {
    for (const t of timers) clearTimeout(t);
    timers = [];
    if (rotationTimer) clearInterval(rotationTimer);
    rotationTimer = null;
    if (clockTimer) clearInterval(clockTimer);
    clockTimer = null;
  }

  function show({ focus = false } = {}) {
    if (!root) return;
    root.classList.remove("hidden");
    root.setAttribute("aria-hidden", "false");
    document.body.classList.add("noScroll");
    if (chip) chip.classList.add("hidden");

    if (focus && modal) {
      queueMicrotask(() => modal && modal.focus());
    }
  }

  function hide({ keepChip = false } = {}) {
    if (!root) return;
    root.classList.add("hidden");
    root.setAttribute("aria-hidden", "true");
    document.body.classList.remove("noScroll");
    if (keepChip && chip) chip.classList.remove("hidden");
    else if (chip) chip.classList.add("hidden");
    if (lastActiveEl && typeof lastActiveEl.focus === "function") {
      try {
        lastActiveEl.focus();
      } catch {
        // ignore
      }
    }
    lastActiveEl = null;
  }

  function background() {
    if (currentState !== "running") {
      hide({ keepChip: false });
      return;
    }
    hiddenByUser = true;
    hide({ keepChip: true });
  }

  function cancel() {
    if (currentState !== "running") {
      hide({ keepChip: false });
      return;
    }
    currentState = "cancelled";
    setMascot("error");
    setTitle("취소했어요", "요청을 중단했습니다. 다시 시도할 수 있어요.");
    setBubble("요청을 취소했어요. 필요하면 다시 평가해 주세요.");
    if (btnBackground) btnBackground.classList.add("hidden");
    if (btnCancel) btnCancel.classList.add("hidden");
    if (btnClose) btnClose.classList.remove("hidden");
    if (chip) chip.classList.add("hidden");
    clearTimers();
    try {
      if (typeof onCancelCb === "function") onCancelCb();
    } catch {
      // ignore
    }
    // Auto-hide after a short delay.
    timers.push(setTimeout(() => hide({ keepChip: false }), 550));
  }

  function fail(err) {
    currentState = "error";
    clearTimers();
    setMascot("error");
    const msg = err?.message ? String(err.message) : String(err || "오류가 발생했습니다.");
    setTitle("평가에 실패했어요", "네트워크/서버 오류로 요청을 완료하지 못했습니다.");
    setBubble("잠시 후 다시 시도해 주세요. 동일 조건으로 재시도할 수 있어요.");
    addLog(`[error] ${msg}`);

    if (btnBackground) btnBackground.classList.add("hidden");
    if (btnCancel) btnCancel.classList.add("hidden");
    if (btnClose) btnClose.classList.remove("hidden");
    if (btnRetry) btnRetry.classList.toggle("hidden", typeof onRetryCb !== "function");
    if (chip) chip.classList.add("hidden");
    show({ focus: true });
  }

  function finish({ partial = false, note = null } = {}) {
    currentState = partial ? "partial" : "success";
    clearTimers();
    setMascot("success");
    setTitle(partial ? "완료! (일부 데이터로 계산)" : "완료!", partial ? "가능한 데이터로 시세를 계산했어요." : "결과를 표시할게요.");
    setBubble(note || (partial ? "한 곳은 응답이 느렸어요. 가능한 데이터로 계산했어요." : "완료! 시세 대비 어느 정도인지 바로 보여드릴게요."));

    for (const s of DEFAULT_STEPS) setStep(s.id, "done");
    if (btnBackground) btnBackground.classList.add("hidden");
    if (btnCancel) btnCancel.classList.add("hidden");
    if (btnClose) btnClose.classList.add("hidden");
    if (btnRetry) btnRetry.classList.add("hidden");
    if (chip) chip.classList.add("hidden");

    // Let the user perceive completion, then fade away.
    timers.push(setTimeout(() => hide({ keepChip: false }), 650));
  }

  function start({ onCancel, onRetry } = {}) {
    mount();
    preload();
    resetUI();

    currentState = "running";
    startAt = Date.now();
    lastActiveEl = document.activeElement;
    onCancelCb = typeof onCancel === "function" ? onCancel : null;
    onRetryCb = typeof onRetry === "function" ? onRetry : null;

    show({ focus: true });
    addLog(`[start] ${new Date().toISOString()}`);

    // Clock
    clockTimer = setInterval(() => {
      if (!timerEl) return;
      timerEl.textContent = formatElapsed(Date.now() - startAt);
      const elapsed = Date.now() - startAt;
      if (elapsed > 25000) setBubble("사이트가 잠깐 느린가 봐요. 끝까지 확인해볼게요.");
    }, 1000);

    // Message rotation (lightweight, no re-render)
    let msgIdx = 0;
    rotationTimer = setInterval(() => {
      if (currentState !== "running") return;
      msgIdx = (msgIdx + 1) % ROTATION_MESSAGES.length;
      setBubble(ROTATION_MESSAGES[msgIdx]);
    }, 4200);

    // Simulated stepper (front-only)
    const schedule = [
      { t: 0, fn: () => (setStep("normalize", "running"), setBubble("검색 조건을 정리하고 있어요."), setMascot("searching")) },
      { t: 350, fn: () => setStep("normalize", "done") },
      { t: 350, fn: () => (setStep("suumo_fetch", "running"), setBubble("SUUMO에서 매물을 수집 중이에요."), setMascot("searching")) },
      { t: 6500, fn: () => setStep("suumo_fetch", "done") },
      { t: 6500, fn: () => (setStep("chintai_fetch", "running"), setBubble("CHINTAI에서 매물을 수집 중이에요."), setMascot("searching")) },
      { t: 14500, fn: () => setStep("chintai_fetch", "done") },
      { t: 14500, fn: () => (setStep("match", "running"), setBubble("조건에 맞는 매물을 선별 중이에요."), setMascot("analyzing")) },
      { t: 20500, fn: () => setStep("match", "done") },
      { t: 20500, fn: () => (setStep("median", "running"), setBubble("비슷한 매물로 중간값을 계산 중이에요."), setMascot("analyzing")) },
      { t: 24000, fn: () => setStep("median", "done") },
      { t: 24000, fn: () => (setStep("compare", "running"), setBubble("선택한 매물과 시세를 비교 중이에요."), setMascot("analyzing")) },
    ];
    for (const item of schedule) {
      timers.push(setTimeout(item.fn, item.t));
    }
  }

  function finishFromResponse(resp) {
    const live = resp && typeof resp === "object" ? resp?.derived?.live_benchmark : null;
    const used = Boolean(live && live.used);
    const attempted = Boolean(live && live.attempted);
    const partial = attempted && !used;
    const note = partial ? "실시간 비교가 어려워 벤치마크 데이터로 계산했어요." : null;
    finish({ partial, note });
  }

  return {
    start,
    finishFromResponse,
    fail,
    cancel,
    background,
    hide,
    addLog,
  };
}
