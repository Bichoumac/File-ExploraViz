// Point d'entrée : authentification, routage, thème, progression du scan (SSE), pied de page.
import { api, esc, fmtBytes, fmtDateTime, fmtDuration, fmtInt, icon, parseHash, toast } from './util.js';

const PAGES = {
  explorer: () => import('./explorer.js'),
  stats: () => import('./stats.js'),
  treemap: () => import('./treemap.js'),
  config: () => import('./config.js'),
};

const main = document.getElementById('main');
const app = { status: null, page: null, module: null, events: null };
window.fev = app; // pratique pour déboguer depuis la console

// ---------------------------------------------------------------- thème

const THEME_KEY = 'fev-theme';
function storedTheme() {
  try { return localStorage.getItem(THEME_KEY); } catch { return null; }
}
function currentTheme() {
  const t = document.documentElement.dataset.theme;
  if (t) return t;
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
        <h1>${setup ? 'Création du compte' : 'Connexion'}</h1>
        <p class="muted small" style="margin:0">${setup
          ? 'Premier lancement : choisissez l’identifiant et le mot de passe (8 caractères minimum) qui protégeront l’interface.'
          : 'Identifiez-vous pour accéder à File ExploraViz.'}</p>
        <label class="field">Identifiant<input name="username" autocomplete="username" required value="${setup ? 'admin' : ''}"></label>
        <label class="field">Mot de passe<input name="password" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" required minlength="${setup ? 8 : 1}"></label>
        ${setup ? '<label class="field">Confirmation<input name="confirm" type="password" autocomplete="new-password" required></label>' : ''}
        <div class="err" role="alert"></div>
        <button class="btn primary" type="submit">${setup ? 'Créer le compte' : 'Se connecter'}</button>
      </form>
    </div>`;
  const form = main.querySelector('form');
  const err = form.querySelector('.err');
  form.querySelector(setup ? 'input[name=password]' : 'input[name=username]').focus();
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    if (setup && fd.get('password') !== fd.get('confirm')) { err.textContent = 'Les deux mots de passe diffèrent.'; return; }
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
  main.innerHTML = '<div class="empty">Chargement…</div>';
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
  const label = running ? 'Scan en cours…' : 'Lancer un scan maintenant';
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

const STATUS_LABEL = {
  idle: 'En attente', running: 'En cours', cancelling: 'Annulation…', done: 'Terminé', cancelled: 'Annulé', error: 'Erreur',
};
const PHASE_LABEL = { scan: 'parcours des dossiers', indexation: 'indexation en mémoire', 'agrégats': 'calcul des statistiques' };

function renderScan(st) {
  const bar = document.getElementById('scanbar');
  const running = st.running;
  scanBtn.disabled = running;
  setScanButton(running);
  if (!running && !['error', 'cancelled'].includes(st.status)) { bar.hidden = true; return; }
  bar.hidden = false;
  if (!running) {
    bar.innerHTML = `<div class="line"><b>${STATUS_LABEL[st.status]}</b><span>${esc(st.message || '')}</span></div>
      <button class="btn small" type="button" data-close>Fermer</button>`;
    bar.querySelector('[data-close]').onclick = () => { bar.hidden = true; };
    return;
  }
  const p = st.percent;
  const estimate = st.percent_basis === 'estimation';
  bar.innerHTML = `
    <div class="line">
      <b>${STATUS_LABEL[st.status] || st.status}</b>
      <span>${esc(PHASE_LABEL[st.phase] || '')}</span>
      <span><b>${fmtInt(st.files)}</b> fichiers · <b>${fmtInt(st.dirs)}</b> dossiers · <b>${fmtBytes(st.bytes)}</b></span>
      <span>${fmtDuration(st.elapsed_s)} écoulées${st.files_per_s ? ` · ${fmtInt(Math.round(st.files_per_s))} fichiers/s` : ''}</span>
      ${st.inaccessible ? `<span>${fmtInt(st.inaccessible)} inaccessibles</span>` : ''}
      <span>${p == null ? '' : `<b>${p.toLocaleString('fr-FR', { maximumFractionDigits: 0 })} %</b>${estimate ? ' (estimation)' : ''}`}</span>
    </div>
    <button class="btn small danger" type="button" data-cancel ${st.status === 'cancelling' ? 'disabled' : ''}>Annuler le scan</button>
    <div class="progress ${p == null ? 'indeterminate' : ''}"><div style="width:${p ?? 0}%"></div></div>
    <div class="dir" title="${esc(st.current_dir)}">${st.current_root ? `${esc(st.current_root)} › ` : ''}${esc(st.current_dir || '')}</div>`;
  bar.querySelector('[data-cancel]').onclick = cancelScan;
}

function renderFooter(st) {
  const last = st.last_scan;
  const parts = [];
  if (last?.finished_at) {
    parts.push(`Dernier scan : <b>${fmtDateTime(last.finished_at)}</b> (durée ${fmtDuration(last.duration_s)})`);
  } else {
    parts.push('Aucun scan terminé pour l’instant');
  }
  if (st.running) parts.push('Scan en cours');
  else if (st.next_run) parts.push(`Prochain scan : <b>${fmtDateTime(st.next_run)}</b>`);
  else if (st.interval_minutes === 0) parts.push('Scan automatique désactivé');
  if (!st.index_ready) parts.push('Chargement de l’index…');
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
    if (finished) toast(st.message || 'Scan terminé.');
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
    main.innerHTML = `<div class="empty">Serveur injoignable : ${esc(ex.message)}</div>`;
    return;
  }
  if (state.setup_required) return authForm({ setup: true });
  if (!state.user) return authForm({ setup: false });
  app.started = true;
  app.authDisabled = state.auth_disabled;
  scanBtn.hidden = false;
  document.getElementById('tabs').hidden = false;
  document.getElementById('logout-btn').hidden = state.auth_disabled;
  connectEvents();
  api('/api/scan/status').then(onStatus).catch(() => {});
  if (!location.hash) history.replaceState(null, '', '#/explorer');
  app.page = null;
  await route();
}

boot();
