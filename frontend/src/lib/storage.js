const NS = "twh_eval";

function key(name) {
  return `${NS}:${name}`;
}

export function loadJson(name, fallback = null) {
  try {
    const raw = localStorage.getItem(key(name));
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function saveJson(name, value) {
  try {
    localStorage.setItem(key(name), JSON.stringify(value));
  } catch {
    // ignore
  }
}

export function loadBool(name, fallback = false) {
  const v = loadJson(name, null);
  return typeof v === "boolean" ? v : fallback;
}

export function saveBool(name, value) {
  saveJson(name, Boolean(value));
}

