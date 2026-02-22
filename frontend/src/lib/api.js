import { deepClone, parseQuery } from "./utils.js";

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg = data?.message ? String(data.message) : `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function loadMockResponse() {
  const url = new URL("./fixtures/mock_response.json", new URL("./src/", window.location.href)).toString();
  return await fetchJson(url, { cache: "no-store" });
}

function computeDerivedForMock(input) {
  const rent = typeof input.rent_yen === "number" ? input.rent_yen : null;
  const mgmt = typeof input.mgmt_fee_yen === "number" ? input.mgmt_fee_yen : null;
  const monthly = rent !== null && mgmt !== null ? rent + mgmt : null;
  const initial = typeof input.initial_cost_total_yen === "number" ? input.initial_cost_total_yen : null;
  const im = monthly && monthly > 0 && initial !== null ? initial / monthly : null;
  return { monthly_fixed_cost_yen: monthly, initial_multiple: im };
}

export async function evaluate(input, { mockMode = false, signal } = {}) {
  const q = parseQuery();
  const apiBase = q.apiBase ? String(q.apiBase).replace(/\/$/, "") : "";

  if (mockMode || q.mock === "1") {
    const template = await loadMockResponse();
    const out = deepClone(template || {});
    out.input = input;
    out.derived = { ...(out.derived || {}), ...computeDerivedForMock(input) };
    return out;
  }

  const url = `${apiBase}/api/evaluate`;
  return await fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
}
