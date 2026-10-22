// Point d'entrée : authentification, routage, thème, langue, progression du scan (SSE), pied de page.
import { applyStatic, lang, setLang } from './i18n.js';
import { api, esc, fmtBytes, fmtDateTime, fmtDuration, fmtInt, fmtPct, icon, parseHash, t, toast } from './util.js';

const PAGES = {
  explorer: () => import('./explorer.js'),
  stats: () => import('./stats.js'),
  treemap: () => import('./treemap.js'),
  config: () => import('./config.js'),
};

const main = document.getElementById('main');
const app = { status: null, page: null, module: null, events: null };
window.fev = app; // pratique pour déboguer depuis la console

applyStatic();

// ---------------------------------------------------------------- langue

const langBtn = document.getElementById('lang-btn');
langBtn.textContent = lang === 'fr' ? 'EN' : 'FR';
langBtn.title = t('app.switchLang');
langBtn.setAttribute('aria-label', t('app.switchLang'));
langBtn.addEventListener('click', () => setLang(lang === 'fr' ? 'en' : 'fr'));

// ---------------------------------------------------------------- thème

const THEME_KEY = 'fev-theme';
function storedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch { return null; }
}
function currentTheme() {
  const th = document.documentElement.dataset.theme;
  if (th) return th;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  const btn = document.getElementById('theme-btn');
  btn.innerHTML = icon(currentTheme() === 'dark' ? 'sun' : 'moon');
}
applyTheme(storedTheme());
document.getElementById('theme-btn').addEventListener('click', () => {
  const next = currentTheme() === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, next); } catch { /* navigation privée */ }
  applyTheme(next);
  window.dispatchEvent(new CustomEvent('theme-changed'));
});
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (!storedTheme()) { applyTheme(); window.dispatchEvent(new CustomEvent('theme-changed')); }
});

// ---------------------------------------------------------------- authentification

function authForm({ setup }) {
  stopApp();
  main.innerHTML = `
    <div class="auth-wrap">
      <form class="card auth" autocomplete="on">
        <h1>${setup ? t('auth.setupTitle') : t('auth.loginTitle')}</h1>
        <p class="muted small" style="margin:0">${setup ? t('auth.setupHelp') : t('auth.loginHelp')}</p>
        <label class="field">${t('auth.username')}<input name="username" autocomplete="username" required value="${setup ? 'admin' : ''}"></label>
        <label class="field">${t('auth.password')}<input name="password" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" required minlength="${setup ? 8 : 1}"></label>
        ${setup ? `<label class="field">${t('auth.confirm')}<input name="confirm" type="password" autocomplete="new-password" required></label>` : ''}
        <div class="err" role="alert"></div>
        <button class="btn primary" type="submit">${setup ? t('auth.create') : t('auth.login')}</button>
      </form>
    </div>`;
  const form = main.querySelector('form');
  const err = form.querySelector('.err');
  form.querySelector(setup ? 'input[name=password]' : 'input[name=username]').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    if (setup && fd.get('password') !== fd.get('confirm')) { err.textContent = t('auth.mismatch'); return; }
    const btn = form.querySelector('button');
    btn.disabled = true;
    err.textContent = '';
    try {
      await api(setup ? '/api/auth/setup' : '/api/auth/login', { method: 'POST', body: { username: fd.get('username'), password: fd.get('password') } });
      await boot();
    } catch (ex) {
      err.textContent = ex.message;
    } finally {
      btn.disabled = false;
    }
  });
}

window.addEventListener('auth-required', () => authForm({ setup: false }));

document.getElementById('logout-btn').innerHTML = icon('logout');
document.getElementById('logout-btn').addEventListener('click', async () => {
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  authForm({ setup: false });
});

// ---------------------------------------------------------------- routage

async function route() {
  if (!app.started) return;
  const { page, params } = parseHash();
  const name = PAGES[page] ? page : 'explorer';
  document.querySelectorAll('#tabs a').forEach((a) => a.classList.toggle('active', a.dataset.page === name));
  if (name === app.page && app.module?.update) {
    app.module.update(params);
    return;
  }
  app.module?.unmount?.();
  app.page = name;
  main.innerHTML = `<div class="empty">${t('common.loading')}</div>`;
  const mod = await PAGES[name]();
  if (app.page !== name) return; // navigation entre-temps
  app.module = mod;
  main.innerHTML = '';
  await mod.mount(main, params, app);
}
window.addEventListener('hashchange', route);

// ---------------------------------------------------------------- scan : barre de progression & pied de page

const scanBtn = document.getElementById('scan-btn');
document.querySelectorAll('#tabs a[data-icon]').forEach((a) => a.insertAdjacentHTML('afterbegin', icon(a.dataset.icon)));
function setScanButton(running) {
  const label = running ? t('scan.running') : t('scan.now');
  scanBtn.innerHTML = `${icon('scan')}<span>${label}</span>`;
  scanBtn.title = label;
  scanBtn.setAttribute('aria-label', label);
}
setScanButton(false);
scanBtn.addEventListener('click', () => startScan());

export async function startScan(root = null) {
  try {
    const r = await api('/api/scan', { method: 'POST', body: { root } });
    toast(r.message);
  } catch (ex) {
    toast(ex.message, 'error');
  }
}
app.startScan = startScan;

async function cancelScan() {
  const r = await api('/api/scan/cancel', { method: 'POST' }).catch((ex) => ({ message: ex.message }));
  toast(r.message);
}

const STATUS_KEYS = ['idle', 'running', 'cancelling', 'done', 'cancelled', 'error'];
const statusLabel = (s) => (STATUS_KEYS.includes(s) ? t(`scan.status.${s}`) : s);
const PHASE_KEYS = { scan: 'scan.phase.walk', indexation: 'scan.phase.index', aggregates: 'scan.phase.aggregate' };

function renderScan(st) {
  const bar = document.getElementById('scanbar');
  const running = st.running;
  scanBtn.disabled = running;
  setScanButton(running);
  if (!running && !['error', 'cancelled'].includes(st.status)) { bar.hidden = true; return; }
  bar.hidden = false;
  if (!running) {
    bar.innerHTML = `<div class="line"><b>${statusLabel(st.status)}</b><span>${esc(st.message || '')}</span></div>
      <button class="btn small" type="button" data-close>${t('common.close')}</button>`;
    bar.querySelector('[data-close]').onclick = () => { bar.hidden = true; };
    return;
  }
  const p = st.percent;
  const estimate = st.percent_basis === 'estimation';
  bar.innerHTML = `
    <div class="line">
      <b>${statusLabel(st.status)}</b>
      <span>${PHASE_KEYS[st.phase] ? t(PHASE_KEYS[st.phase]) : ''}</span>
      ${st.files == null ? '' : `<span>${t('scan.counts', { files: `<b>${fmtInt(st.files)}</b>`, dirs: `<b>${fmtInt(st.dirs)}</b>`, bytes: `<b>${fmtBytes(st.bytes)}</b>` })}</span>`}
      <span>${t('scan.elapsed', { d: fmtDuration(st.elapsed_s) })}${st.files_per_s ? ` · ${t('scan.speed', { n: fmtInt(Math.round(st.files_per_s)) })}` : ''}</span>
      ${st.inaccessible ? `<span>${t('scan.inaccessible', { n: fmtInt(st.inaccessible) })}</span>` : ''}
      <span>${p == null ? '' : `<b>${fmtPct(p, 0)}</b>${estimate ? ` (${t('scan.estimate')})` : ''}`}</span>
    </div>
    ${app.isAdmin ? `<button class="btn small danger" type="button" data-cancel ${st.status === 'cancelling' ? 'disabled' : ''}>${t('scan.cancel')}</button>` : '<span></span>'}
    <div class="progress ${p == null ? 'indeterminate' : ''}"><div style="width:${p ?? 0}%"></div></div>
    <div class="dir" title="${esc(st.current_dir)}">${st.current_root ? `${esc(st.current_root)} › ` : ''}${esc(st.current_dir || '')}</div>`;
  const cancelBtn = bar.querySelector('[data-cancel]');
  if (cancelBtn) cancelBtn.onclick = cancelScan;
}

function renderFooter(st) {
  const last = st.last_scan;
  const parts = [];
  if (last?.finished_at) {
    parts.push(t('footer.last', { date: `<b>${fmtDateTime(last.finished_at)}</b>`, d: fmtDuration(last.duration_s) }));
  } else {
    parts.push(t('footer.none'));
  }
  if (st.running) parts.push(t('footer.running'));
  else if (st.next_run) parts.push(t('footer.next', { date: `<b>${fmtDateTime(st.next_run)}</b>` }));
  else if (st.interval_minutes === 0) parts.push(t('footer.disabled'));
  if (!st.index_ready) parts.push(t('footer.loading'));
  document.getElementById('footer').innerHTML = parts.map((p) => `<span>${p}</span>`).join('');
}

function onStatus(st) {
  const prev = app.status;
  app.status = st;
  renderScan(st);
  renderFooter(st);
  const finished = prev && prev.running && !st.running && st.status === 'done';
  const loaded = prev && !prev.index_ready && st.index_ready;
  if (finished || loaded) {
    if (finished) toast(st.message || t('scan.status.done'));
    window.dispatchEvent(new CustomEvent('data-changed'));
  }
}

function connectEvents() {
  app.events?.close();
  const es = new EventSource('/api/scan/progress');
  es.addEventListener('progress', (e) => onStatus(JSON.parse(e.data)));
  es.onerror = () => {
    // Le navigateur se reconnecte seul ; on vérifie seulement que la session est toujours valide.
    api('/api/scan/status').then(onStatus).catch(() => {});
  };
  app.events = es;
}

function stopApp() {
  app.started = false;
  app.events?.close();
  app.events = null;
  app.module?.unmount?.();
  app.module = null;
  app.page = null;
  scanBtn.hidden = true;
  document.getElementById('tabs').hidden = true;
  document.getElementById('logout-btn').hidden = true;
  document.getElementById('scanbar').hidden = true;
}

// ---------------------------------------------------------------- démarrage

async function boot() {
  let state;
  try {
    state = await api('/api/auth/state');
  } catch (ex) {
    main.innerHTML = `<div class="empty">${t('app.unreachable', { error: esc(ex.message) })}</div>`;
    return;
  }
  if (state.setup_required) return authForm({ setup: true });
  if (!state.user) return authForm({ setup: false });
  app.started = true;
  app.authDisabled = state.auth_disabled;
  app.user = state.user;
  app.role = state.role;
  app.isAdmin = state.role === 'admin';
  scanBtn.hidden = !app.isAdmin; // lancer un scan : administrateurs seulement
  document.getElementById('tabs').hidden = false;
  document.getElementById('logout-btn').hidden = state.auth_disabled;
  connectEvents();
  api('/api/scan/status').then(onStatus).catch(() => {});
  if (!location.hash) history.replaceState(null, '', '#/explorer');
  app.page = null;
  await route();
}

boot();
