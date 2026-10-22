// Page Configuration : racines, planification, exclusions, catégories, compte, maintenance.
import { locale } from './i18n.js';
import { CATS, api, catColor, esc, fmtBytes, fmtDateTime, fmtInt, icon, t, toast } from './util.js';

let el; let app; let cfg = null; let roots = [];

export async function mount(container, _params, appCtx) {
  el = container;
  app = appCtx;
  window.addEventListener('data-changed', refreshRoots);
  await load();
}

export function unmount() {
  window.removeEventListener('data-changed', refreshRoots);
}

async function load() {
  try {
    [cfg, roots] = await Promise.all([api('/api/config'), api('/api/roots')]);
  } catch (ex) {
    el.innerHTML = `<div class="empty">${esc(ex.message)}</div>`;
    return;
  }
  render();
}

async function refreshRoots() {
  roots = await api('/api/roots').catch(() => roots);
  el.querySelector('#roots-body').innerHTML = rootRows();
}

const splitList = (text) => text.split(/[\s,;]+/).map((x) => x.trim().replace(/^\.+/, '').toLowerCase()).filter(Boolean);

function rootRows() {
  if (!cfg.roots.length) {
    return `<tr><td colspan="5" class="muted">${t('cfg.noRoots')}</td></tr>`;
  }
  return cfg.roots.map((r, i) => {
    const info = roots.find((x) => x.id === r.id) || {};
    return `<tr>
      <td><input type="text" data-root-name="${i}" value="${esc(r.name)}" aria-label="${esc(t('cfg.displayNameOf', { folder: r.folder }))}" maxlength="100"></td>
      <td class="path"><code>/data/${esc(r.folder)}</code><div class="small muted">id : ${esc(r.id)}</div></td>
      <td>${r.available ? `<span class="badge ok">${t('cfg.mounted')}</span>` : `<span class="badge ko">${t('cfg.missing')}</span>`}
        <div class="small muted">${info.files != null ? `${t('st.nFiles', { n: fmtInt(info.files), count: info.files })} · ${fmtBytes(info.bytes)}` : t('cfg.neverScanned')}</div>
        ${info.scanned_at ? `<div class="small muted">${fmtDateTime(info.scanned_at)}</div>` : ''}</td>
      <td><label class="check"><input type="checkbox" data-root-enabled="${i}" ${r.enabled ? 'checked' : ''}> ${t('cfg.enabled')}</label></td>
      <td class="num"><button class="btn small" type="button" data-scan-root="${esc(r.id)}" ${r.enabled && r.available ? '' : 'disabled'}>${icon('scan')} ${t('cfg.scan')}</button></td>
    </tr>`;
  }).join('');
}

function render() {
  const s = cfg.scan;
  el.innerHTML = `
  <form class="cfg" id="cfg-form" novalidate>
    <div class="card wide">
      <div class="card-h"><h2>${t('cfg.roots')}</h2><span class="muted small">${t('cfg.rootsHelp')}</span></div>
      <div class="card-b"><div class="tbl-scroll"><table class="data rtable">
        <thead><tr><th>${t('cfg.displayName')}</th><th>${t('st.h.dir')}</th><th>${t('cfg.state')}</th><th>${t('cfg.scanCol')}</th><th class="num"></th></tr></thead>
        <tbody id="roots-body">${rootRows()}</tbody></table></div></div>
    </div>

    <div class="card">
      <div class="card-h"><h2>${t('cfg.schedule')}</h2></div>
      <div class="card-b">
        <label class="field">${t('cfg.interval')}
          <input type="number" id="c-interval" min="0" max="10080" step="1" value="${s.interval_minutes}"></label>
        <label class="check"><input type="checkbox" id="c-offpeak" ${s.offpeak.enabled ? 'checked' : ''}> ${t('cfg.offpeak')}</label>
        <div class="inline">
          <label class="field">${t('cfg.start')}<input type="time" id="c-op-start" value="${esc(s.offpeak.start)}"></label>
          <label class="field">${t('cfg.end')}<input type="time" id="c-op-end" value="${esc(s.offpeak.end)}"></label>
        </div>
        <p class="help">${t('cfg.offpeakHelp')}</p>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>${t('cfg.options')}</h2></div>
      <div class="card-b">
        <label class="check"><input type="checkbox" id="c-symlinks" ${s.follow_symlinks ? 'checked' : ''}> ${t('cfg.symlinks')}</label>
        <div class="inline">
          <label class="field">${t('cfg.depth')}<input type="number" id="c-depth" min="0" max="1000" value="${s.max_depth}"></label>
          <label class="field">${t('cfg.history')}<input type="number" id="c-history" min="0" max="1000" value="${s.history_size}"></label>
          <label class="field">${t('cfg.pageSize')}<input type="number" id="c-pagesize" min="10" max="1000" value="${cfg.ui.page_size}"></label>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>${t('cfg.exclusions')}</h2><button class="btn small" type="button" id="c-excl-reset">${t('cfg.defaults')}</button></div>
      <div class="card-b">
        <textarea id="c-exclusions" spellcheck="false" aria-label="${t('cfg.exclusionsLabel')}">${esc(s.exclusions.join('\n'))}</textarea>
        <p class="help">${t('cfg.exclusionsHelp')}</p>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>${t('cfg.account')}</h2></div>
      <div class="card-b">
        ${cfg.auth_disabled ? `<p class="help">${t('cfg.authDisabled')}</p>` : `
        <p class="help">${t('cfg.loggedAs', { user: `<b>${esc(cfg.auth.username || '')}</b>` })}</p>
        <div class="inline">
          <label class="field">${t('cfg.pwCurrent')}<input type="password" id="c-pw-cur" autocomplete="current-password"></label>
          <label class="field">${t('cfg.pwNew')}<input type="password" id="c-pw-new" autocomplete="new-password" minlength="8"></label>
          <label class="field">${t('auth.confirm')}<input type="password" id="c-pw-conf" autocomplete="new-password"></label>
          <button class="btn" type="button" id="c-pw-btn">${t('cfg.pwChange')}</button>
        </div>`}
        <label class="field">${t('cfg.session')}<input type="number" id="c-session" min="1" max="8760" value="${cfg.auth.session_hours}"></label>
      </div>
    </div>

    <div class="card wide">
      <div class="card-h"><h2>${t('cfg.categories')}</h2><button class="btn small" type="button" id="c-cat-reset">${t('cfg.defaults')}</button></div>
      <div class="card-b">
        <p class="help">${t('cfg.categoriesHelp')}</p>
        <div class="catgrid">
          ${CATS.filter((c) => c.id !== 'other').map((c) => `
            <label class="field"><span><span class="dot" style="background:${catColor(c.id)}"></span> ${esc(c.label)}</span>
              <textarea data-cat="${c.id}" spellcheck="false">${esc((cfg.categories[c.id] || []).join(' '))}</textarea></label>`).join('')}
        </div>
      </div>
    </div>

    <div class="card wide">
      <div class="card-h"><h2>${t('cfg.maintenance')}</h2></div>
      <div class="card-b">
        <div class="inline">
          <button class="btn danger" type="button" id="c-clear">${t('cfg.clear')}</button>
          <a class="btn" href="/api/docs" target="_blank" rel="noopener">${t('cfg.apiDocs')}</a>
        </div>
        <p class="help">${t('cfg.clearHelp')}</p>
      </div>
    </div>

    <div class="card wide actions">
      <div id="c-errors"></div>
      <span class="muted small" id="c-saved"></span>
      <button class="btn primary" type="submit">${t('cfg.save')}</button>
    </div>
  </form>`;
  bind();
}

function collect() {
  const $ = (s) => el.querySelector(s);
  const num = (s) => (($(s).value === '') ? NaN : Number($(s).value));
  const rootsOut = cfg.roots.map((r, i) => ({
    id: r.id, folder: r.folder,
    name: el.querySelector(`[data-root-name="${i}"]`).value.trim() || r.folder,
    enabled: el.querySelector(`[data-root-enabled="${i}"]`).checked,
  }));
  const categories = {};
  el.querySelectorAll('textarea[data-cat]').forEach((t) => { categories[t.dataset.cat] = splitList(t.value); });
  return {
    roots: rootsOut,
    scan: {
      interval_minutes: num('#c-interval'),
      offpeak: { enabled: $('#c-offpeak').checked, start: $('#c-op-start').value, end: $('#c-op-end').value },
      exclusions: $('#c-exclusions').value.split('\n').map((x) => x.trim()).filter(Boolean),
      follow_symlinks: $('#c-symlinks').checked,
      max_depth: num('#c-depth'),
      history_size: num('#c-history'),
    },
    ui: { page_size: num('#c-pagesize') },
    categories,
    auth: { session_hours: num('#c-session') },
  };
}

function showErrors(list) {
  const box = el.querySelector('#c-errors');
  box.innerHTML = list?.length
    ? `<div class="errors" role="alert"><b>${t('cfg.notSaved')}</b><ul>${list.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>`
    : '';
}

function bind() {
  const $ = (s) => el.querySelector(s);
  $('#cfg-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const btn = $('#cfg-form button[type=submit]');
    btn.disabled = true;
    try {
      cfg = await api('/api/config', { method: 'PUT', body: collect() });
      showErrors(null);
      $('#c-saved').textContent = t('cfg.savedAt', { time: new Date().toLocaleTimeString(locale) });
      toast(t('cfg.saved'));
      roots = await api('/api/roots').catch(() => roots);
      $('#roots-body').innerHTML = rootRows();
    } catch (ex) {
      showErrors(ex.data?.errors || [ex.message]);
      toast(t('cfg.invalid'), 'error');
    } finally {
      btn.disabled = false;
    }
  });
  $('#c-excl-reset').addEventListener('click', () => { $('#c-exclusions').value = cfg.defaults.exclusions.join('\n'); });
  $('#c-cat-reset').addEventListener('click', () => {
    el.querySelectorAll('textarea[data-cat]').forEach((t) => { t.value = (cfg.defaults.categories[t.dataset.cat] || []).join(' '); });
  });
  el.addEventListener('click', (e) => {
    const b = e.target.closest('[data-scan-root]');
    if (b) app.startScan(b.dataset.scanRoot);
  });
  $('#c-clear').addEventListener('click', async () => {
    if (!confirm(t('cfg.clearConfirm'))) return;
    try {
      const r = await api('/api/cache/clear', { method: 'POST' });
      toast(r.message);
      window.dispatchEvent(new CustomEvent('data-changed'));
    } catch (ex) {
      toast(ex.message, 'error');
    }
  });
  $('#c-pw-btn')?.addEventListener('click', async () => {
    const cur = $('#c-pw-cur').value; const nw = $('#c-pw-new').value;
    if (nw !== $('#c-pw-conf').value) { toast(t('auth.mismatch'), 'error'); return; }
    try {
      await api('/api/auth/password', { method: 'POST', body: { current: cur, new: nw } });
      ['#c-pw-cur', '#c-pw-new', '#c-pw-conf'].forEach((s) => { $(s).value = ''; });
      toast(t('cfg.pwChanged'));
    } catch (ex) {
      toast(ex.message, 'error');
    }
  });
}

