export function parseQuery() {
  const params = new URLSearchParams(window.location.search);
  const out = {};
  for (const [k, v] of params.entries()) out[k] = v;
  return out;
}

export function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  const booleanProps = new Set(["checked", "selected", "disabled", "open", "required", "readonly", "multiple"]);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null) continue;
    if (k === "class") el.className = String(v);
    else if (k === "text") el.textContent = String(v);
    else if (k === "html") el.innerHTML = String(v);
    else if (k === "dataset" && typeof v === "object") {
      for (const [dk, dv] of Object.entries(v)) el.dataset[dk] = String(dv);
    } else if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (booleanProps.has(k)) {
      el[k] = Boolean(v);
    } else {
      el.setAttribute(k, String(v));
    }
  }
  for (const child of children || []) {
    if (child === undefined || child === null) continue;
    if (typeof child === "string") el.appendChild(document.createTextNode(child));
    else el.appendChild(child);
  }
  return el;
}

export function replaceChildren(parent, children) {
  parent.replaceChildren(...children.filter(Boolean));
}

export function formatCompactNumber(value) {
  if (value === null || value === undefined || value === "") return "N/A";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return "N/A";
  return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 }).format(num);
}

export function formatYen(value) {
  if (value === null || value === undefined || value === "") return "N/A";
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return "N/A";
  return `${new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 }).format(num)} JPY`;
}

export function safeString(value) {
  if (value === null || value === undefined) return "";
  return String(value);
}

export function deepClone(obj) {
  return obj === undefined ? undefined : JSON.parse(JSON.stringify(obj));
}

export function firstDefined(...vals) {
  for (const v of vals) {
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

export function getByPath(obj, path) {
  const parts = path.split(".");
  let cur = obj;
  for (const p of parts) {
    if (cur === null || cur === undefined) return undefined;
    if (typeof cur !== "object") return undefined;
    cur = cur[p];
  }
  return cur;
}

export function pick(obj, paths) {
  for (const p of paths) {
    const v = getByPath(obj, p);
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

export function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
