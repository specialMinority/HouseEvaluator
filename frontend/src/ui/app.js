import { evaluate } from "../lib/api.js";
import { loadBenchmarkDatasetMeta } from "../lib/benchmarkMeta.js";
import { loadSpec } from "../lib/specLoader.js";
import { loadJson, saveJson } from "../lib/storage.js";
import { h, parseQuery, replaceChildren } from "../lib/utils.js";
import { renderFormView } from "./formView.js";
import { createLoadingOverlay } from "./loadingOverlay.js";
import { renderResultView } from "./resultView.js";

export function createApp(container, badgesEl) {
  const q = parseQuery();
  const showDebug = q.debug === "1";
  const overlay = createLoadingOverlay({ showDebug });
  let activeAbort = null;

  const state = {
    spec: null,
    benchmarkMeta: null,
    view: "form", // "form" | "result"
    input: loadJson("lastInput", {}),
    response: null,
    fixtures: null,
    loading: false,
    error: null,
  };

  function set(partial) {
    Object.assign(state, partial);
    render();
  }

  async function init() {
    try {
      const spec = await loadSpec();
      set({ spec });
      if (showDebug) {
        badgesEl && badgesEl.appendChild(h("span", { class: "badge badge--accent", text: `S1: ${spec.base}` }));
      }
    } catch (e) {
      set({ error: e?.message || String(e) });
      if (e?.details && Array.isArray(e.details)) {
        console.error("Spec load details:", e.details);
      }
      return;
    }

    loadFixtures();
    loadBenchmarkDatasetMeta()
      .then((m) => set({ benchmarkMeta: m }))
      .catch(() => set({ benchmarkMeta: null }));
  }

  async function loadFixtures() {
    try {
      const url = new URL("./fixtures/mock_inputs.json", new URL("./src/", window.location.href)).toString();
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ fixtures: data });
    } catch {
      set({ fixtures: null });
    }
  }

  async function onSubmit(nextInput) {
    saveJson("lastInput", nextInput);
    activeAbort?.abort();
    const ctrl = new AbortController();
    activeAbort = ctrl;
    overlay.start({
      onCancel: () => ctrl.abort(),
      onRetry: () => onSubmit(nextInput),
    });

    set({ input: nextInput, loading: true, error: null });
    try {
      const resp = await evaluate(nextInput, { mockMode: false, signal: ctrl.signal });
      overlay.finishFromResponse(resp);
      set({ response: resp, view: "result", loading: false });
    } catch (e) {
      if (ctrl.signal.aborted || e?.name === "AbortError") {
        set({ loading: false, error: "평가를 취소했습니다." });
        return;
      }
      overlay.fail(e);
      set({ loading: false, error: e?.message || String(e) });
    } finally {
      if (activeAbort === ctrl) activeAbort = null;
    }
  }

  function onBack() {
    set({ view: "form", error: null });
  }

  function onReset() {
    saveJson("lastInput", {});
    set({ input: {}, error: null });
  }

  async function onWhatIf(nextInput) {
    await onSubmit(nextInput);
  }

  function render() {
    const children = [];

    if (state.error) {
      children.push(h("div", { class: "toast", text: state.error }));
    }

    if (!state.spec) {
      children.push(
        h("div", { class: "panel" }, [
          h("div", { class: "panel__body" }, [
            h("div", { class: "panel__title", text: "스펙 로딩 중…" }),
            h("div", { class: "panel__subtitle", text: "S1_InputSchema.json을 불러오는 중입니다." }),
          ]),
        ])
      );
      replaceChildren(container, children);
      return;
    }

    if (state.view === "form") {
      children.push(
        renderFormView({
          spec: state.spec,
          input: state.input,
          fixtures: state.fixtures,
          loading: state.loading,
          onSubmit,
          onReset,
        })
      );
    } else {
      children.push(
        renderResultView({
          spec: state.spec,
          response: state.response,
          input: state.input,
          benchmarkMeta: state.benchmarkMeta,
          loading: state.loading,
          onBack,
          onWhatIf,
        })
      );
    }

    replaceChildren(container, children);
  }

  render();
  init();
}
