// Utilitaires partagés : API, formats (selon la langue), catégories, icônes, état d'URL.
import { lang, locale, t } from './i18n.js';

export { t };

export const CATS = ['photo', 'video', 'audio', 'document', 'spreadsheet', 'presentation', 'pdf', 'archive', 'code', 'executable', 'other']
  .map((id) => ({ id, label: t(`cat.${id}`), icon: id }));
export const CAT = Object.fromEntries(CATS.map((c) => [c.id, c]));

const FILE = '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>';
const PATHS = {
  photo: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
  video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m22 8-6 4 6 4z"/>',
  audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  document: FILE + '<path d="M16 13H8M16 17H8M10 9H8"/>',
  spreadsheet: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
  presentation: '<path d="M2 3h20M21 3v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V3M7 21l5-5 5 5"/>',
  pdf: FILE + '<path d="M9 18v-5h1.5a1.5 1.5 0 0 1 0 3H9"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>',
  code: '<path d="m16 18 6-6-6-6M8 6l-6 6 6 6"/>',
  executable: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="2.5"/>',
  other: FILE,
  folder: '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  chevron: '<path d="m9 18 6-6-6-6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  scan: '<path d="M3 12a9 9 0 0 1 15-6.7L21 8M21 3v5h-5M21 12a9 9 0 0 1-15 6.7L3 16M3 21v-5h5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 16v-5M12 16V8M17 16v-3"/>',
  grid: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M12 3v18M12 12h9M3 14h9"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  filter: '<path d="M22 3H2l8 9.46V19l4 2v-8.54z"/>',
};

export function icon(name, cls = '') {
  return `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${PATHS[name] || PATHS.other}</svg>`;
}

/** Couleur CSS courante d'une catégorie (suit le thème clair/sombre). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
export const catColor = (id) => cssVar(`--c-${id}`) || cssVar('--c-other');

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
/** Échappement HTML : obligatoire pour tout nom de fichier injecté dans un gabarit. */
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ESC[c]);

const NF = new Intl.NumberFormat(locale);
export const fmtInt = (n) => (n == null ? '—' : NF.format(n));
export const fmtNum = (n, digits = 1) => Number(n).toLocaleString(locale, { maximumFractionDigits: digits });

/** Unités de taille : clés neutres, libellés selon la langue (Ko/Mo… ou KB/MB…). */
const UNIT_KEYS = ['B', 'K', 'M', 'G', 'T', 'P'];
export const unitLabel = (k) => t(`unit.${k}`);
export function fmtBytes(n) {
  if (n == null || Number.isNaN(n)) return '—';
  let v = Number(n);
  let i = 0;
  while (Math.abs(v) >= 1024 && i < UNIT_KEYS.length - 1) { v /= 1024; i += 1; }
  const digits = i === 0 ? 0 : v < 10 ? 2 : v < 100 ? 1 : 0;
  return `${v.toLocaleString(locale, { maximumFractionDigits: digits })} ${unitLabel(UNIT_KEYS[i])}`;
}
export const UNIT_FACTORS = { B: 1, K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 };

export function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(locale, { year: 'numeric', month: '2-digit', day: '2-digit' });
}
export function fmtDateTime(value) {
  if (!value) return '—';
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' });
}
export function fmtDuration(s) {
  if (s == null) return '—';
  s = Math.round(s);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}
/** Pourcentage typographié selon la langue : « 12,5 % » ou « 12.5% ». */
export const fmtPct = (value, digits = 1) => `${Number(value).toLocaleString(locale, { maximumFractionDigits: digits })}${lang === 'fr' ? ' %' : '%'}`;
export const pct = (part, total) => (total ? fmtPct((100 * part) / total) : '—');

// ---------------------------------------------------------------- API

export class ApiError extends Error {
  constructor(status, message, data) { super(message); this.status = status; this.data = data; }
}

export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) continue;
    sp.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

export async function api(path, { method = 'GET', body, params, signal } = {}) {
  const opts = { method, credentials: 'same-origin', headers: { Accept: 'application/json' }, signal };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path + qs(params), opts);
  let data = null;
  try {
    data = await res.json();
  } catch (ex) {
    // Requête remplacée pendant la lecture du corps : l'appelant ignore AbortError, ne pas renvoyer null.
    if (ex.name === 'AbortError') throw ex;
    /* corps vide */
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/auth/')) window.dispatchEvent(new CustomEvent('auth-required'));
    const detail = data && (typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' ; ') : null);
    throw new ApiError(res.status, detail || t('common.httpError', { status: res.status }), data);
  }
  return data;
}

// ---------------------------------------------------------------- toasts

export function toast(message, kind = 'info', ms = 4000) {
  const box = document.getElementById('toasts');
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  box.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

// ---------------------------------------------------------------- état dans l'URL (#/page?…)

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [page, query = ''] = raw.split('?');
  return { page: page || 'explorer', params: Object.fromEntries(new URLSearchParams(query)) };
}

export function hashFor(page, params) {
  return `#/${page}${qs(params)}`;
}

/** Remplace les paramètres de la page courante sans recharger (pas d'entrée d'historique). */
export function replaceParams(params) {
  const { page } = parseHash();
  history.replaceState(null, '', hashFor(page, params));
}

/** Paramètres de filtre communs Explorateur / Statistiques. */
export const FILTER_KEYS = ['root', 'path', 'recursive', 'ext', 'category', 'from', 'to', 'date_field', 'min_size', 'max_size', 'q', 'q_scope', 'q_mode'];
export function pickFilters(params) {
  const out = {};
  for (const k of FILTER_KEYS) if (params[k] !== undefined && params[k] !== '') out[k] = params[k];
  return out;
}

/** Vrai sur un écran de téléphone (même seuil que la feuille de style). */
export const isMobile = () => matchMedia('(max-width: 760px)').matches;

export function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

/** Charge un script classique une seule fois (ECharts, à la demande). */
const loaded = {};
export function loadScript(src) {
  if (!loaded[src]) {
    loaded[src] = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = src;
      s.onload = resolve;
      s.onerror = () => reject(new Error(t('common.loadFailed', { src })));
      document.head.appendChild(s);
    });
  }
  return loaded[src];
}

/** Ordre de grandeur lisible pour une taille saisie (min/max) : 1572864 → [1.5, 'M']. */
export function splitSize(bytes) {
  if (bytes === undefined || bytes === null || bytes === '') return ['', 'M'];
  const n = Number(bytes);
  for (const u of ['T', 'G', 'M', 'K']) {
    if (n >= UNIT_FACTORS[u] && n % (UNIT_FACTORS[u] / 100) === 0) return [String(+(n / UNIT_FACTORS[u]).toFixed(2)), u];
  }
  return [String(n), 'B'];
}
