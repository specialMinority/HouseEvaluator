import { parseQuery } from "./utils.js";

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.json();
}

async function tryLoadS1FromDirectory(base) {
  const dir = base.replace(/[\\/]+$/, "");
  const s1Url = new URL(`${dir}/S1_InputSchema.json`, window.location.href).toString();
  const s1 = await fetchJson(s1Url);
  return { base, s1, s1Url, bundleUrl: null };
}

async function tryLoadFromSpecBundle(base) {
  const url = new URL(base, window.location.href).toString();
  const bundle = await fetchJson(url);
  const s1 = bundle?.S1;
  if (!s1) throw new Error("spec_bundle.json missing S1");
  return { base, s1, s1Url: null, bundleUrl: url };
}

async function tryLoadS1(base) {
  // If user passes a file path, treat it as spec_bundle.json
  if (String(base).toLowerCase().endsWith(".json")) return await tryLoadFromSpecBundle(base);

  try {
    return await tryLoadS1FromDirectory(base);
  } catch {
    const dir = base.replace(/[\\/]+$/, "");
    return await tryLoadFromSpecBundle(`${dir}/spec_bundle.json`);
  }
}

export async function loadSpec() {
  const q = parseQuery();
  const candidates = [];

  if (q.specBase) candidates.push(q.specBase);
  candidates.push("../spec_bundle_v0.1.2");
  candidates.push("../spec_bundle_v0.1.1");
  candidates.push("../spec_bundle_v0.1.0");
  candidates.push("../integrate/merged");

  const errors = [];
  for (const base of candidates) {
    try {
      const loaded = await tryLoadS1(base);
      return {
        base: loaded.base,
        s1: loaded.s1,
        s1Url: loaded.s1Url,
        bundleUrl: loaded.bundleUrl,
      };
    } catch (e) {
      errors.push(`${base}: ${e?.message || String(e)}`);
    }
  }

  const message = `Failed to load S1_InputSchema.json from any spec base. Tried: ${candidates.join(", ")}`;
  const err = new Error(message);
  err.details = errors;
  throw err;
}
