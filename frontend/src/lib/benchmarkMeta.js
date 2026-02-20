import { firstDefined, parseQuery } from "./utils.js";

async function fetchText(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.text();
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.json();
}

function maxDate(isoDates) {
  const filtered = isoDates.filter((d) => typeof d === "string" && d.length >= 8);
  if (filtered.length === 0) return null;
  return filtered.reduce((a, b) => (a > b ? a : b));
}

function summarizeRows(rows) {
  const sourceNames = new Set();
  const prefectures = new Set();
  const updated = [];
  const collected = [];
  const notes = new Set();
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    if (r.source_name) sourceNames.add(String(r.source_name));
    if (r.prefecture) prefectures.add(String(r.prefecture));
    if (r.source_updated_at) updated.push(String(r.source_updated_at));
    if (r.collected_at) collected.push(String(r.collected_at));
    if (r.method_notes) notes.add(String(r.method_notes));
  }
  return {
    rowCount: rows.length,
    sourceNames: Array.from(sourceNames).sort(),
    prefectures: Array.from(prefectures).sort(),
    latestSourceUpdatedAt: maxDate(updated),
    latestCollectedAt: maxDate(collected),
    methodNotes: Array.from(notes).slice(0, 3),
  };
}

function parseCsvHeader(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (lines.length === 0) return { rowCount: 0 };
  return { rowCount: Math.max(0, lines.length - 1) };
}

export async function loadBenchmarkDatasetMeta() {
  const q = parseQuery();
  const jsonPath = firstDefined(q.benchmarkJson, "../agents/agent_D_benchmark_data/out/benchmark_rent_raw.json");
  const csvPath = firstDefined(q.benchmarkCsv, "../agents/agent_D_benchmark_data/out/benchmark_rent_raw.csv");

  try {
    const url = new URL(jsonPath, window.location.href).toString();
    const rows = await fetchJson(url);
    if (Array.isArray(rows)) {
      return { kind: "json", path: jsonPath, url, ...summarizeRows(rows) };
    }
  } catch {
    // fall through
  }

  try {
    const url = new URL(csvPath, window.location.href).toString();
    const text = await fetchText(url);
    return { kind: "csv", path: csvPath, url, ...parseCsvHeader(text) };
  } catch {
    return null;
  }
}

