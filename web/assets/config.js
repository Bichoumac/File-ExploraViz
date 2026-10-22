// Page Configuration : racines, planification, exclusions, catégories, compte, maintenance.
import { CATS, api, catColor, esc, fmtBytes, fmtDateTime, fmtInt, icon, toast } from './util.js';

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
    return '<tr><td colspan="5" class="muted">Aucun dossier détecté sous <code>/data</code>. Montez vos répertoires en lecture seule (<code>/mnt/tank/photos:/data/photos:ro</code>) puis redémarrez le conteneur.</td></tr>';
  }
  return cfg.roots.map((r, i) => {
    const info = roots.find((x) => x.id === r.id) || {};
    return `<tr>
      <td><input type="text" data-root-name="${i}" value="${esc(r.name)}" aria-label="Nom d’affichage de ${esc(r.folder)}" maxlength="100"></td>
      <td class="path"><code>/data/${esc(r.folder)}</code><div class="small muted">id : ${esc(r.id)}</div></td>
      <td>${r.available ? '<span class="badge ok">monté</span>' : '<span class="badge ko">absent</span>'}
        <div class="small muted">${info.files != null ? `${fmtInt(info.files)} fichiers · ${fmtBytes(info.bytes)}` : 'jamais scanné'}</div>
        ${info.scanned_at ? `<div class="small muted">${fmtDateTime(info.scanned_at)}</div>` : ''}</td>
      <td><label class="check"><input type="checkbox" data-root-enabled="${i}" ${r.enabled ? 'checked' : ''}> activée</label></td>
      <td class="num"><button class="btn small" type="button" data-scan-root="${esc(r.id)}" ${r.enabled && r.available ? '' : 'disabled'}>${icon('scan')} Scanner</button></td>
    </tr>`;
  }).join('');
}

function render() {
  const s = cfg.scan;
  el.innerHTML = `
  <form class="cfg" id="cfg-form" novalidate>
    <div class="card wide">
      <div class="card-h"><h2>Racines à scanner</h2><span class="muted small">Dossiers détectés automatiquement sous /data (montés en lecture seule)</span></div>
      <div class="card-b"><div class="tbl-scroll"><table class="data rtable">
        <thead><tr><th>Nom d’affichage</th><th>Dossier</th><th>État</th><th>Scan</th><th class="num"></th></tr></thead>
        <tbody id="roots-body">${rootRows()}</tbody></table></div></div>
    </div>

    <div class="card">
      <div class="card-h"><h2>Planification</h2></div>
      <div class="card-b">
        <label class="field">Intervalle entre deux scans (minutes, 0 = désactivé)
          <input type="number" id="c-interval" min="0" max="10080" step="1" value="${s.interval_minutes}"></label>
        <label class="check"><input type="checkbox" id="c-offpeak" ${s.offpeak.enabled ? 'checked' : ''}> Ne lancer les scans automatiques que pendant les heures creuses</label>
        <div class="inline">
          <label class="field">Début<input type="time" id="c-op-start" value="${esc(s.offpeak.start)}"></label>
          <label class="field">Fin<input type="time" id="c-op-end" value="${esc(s.offpeak.end)}"></label>
        </div>
        <p class="help">La plage peut traverser minuit (ex. 23:00 → 06:00). Les scans manuels restent possibles à tout moment.</p>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>Options de scan</h2></div>
      <div class="card-b">
        <label class="check"><input type="checkbox" id="c-symlinks" ${s.follow_symlinks ? 'checked' : ''}> Suivre les liens symboliques (seulement ceux qui restent dans une racine)</label>
        <div class="inline">
          <label class="field">Profondeur maximale (0 = illimitée)<input type="number" id="c-depth" min="0" max="1000" value="${s.max_depth}"></label>
          <label class="field">Scans conservés dans l’historique<input type="number" id="c-history" min="0" max="1000" value="${s.history_size}"></label>
          <label class="field">Lignes par page (Explorateur)<input type="number" id="c-pagesize" min="10" max="1000" value="${cfg.ui.page_size}"></label>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>Exclusions</h2><button class="btn small" type="button" id="c-excl-reset">Valeurs par défaut</button></div>
      <div class="card-b">
        <textarea id="c-exclusions" spellcheck="false" aria-label="Motifs d’exclusion, un par ligne">${esc(s.exclusions.join('\n'))}</textarea>
        <p class="help">Un motif glob par ligne. Sans « / », il s’applique au nom (<code>*.tmp</code>, <code>.Trash*</code>) ; avec « / », au chemin relatif à la racine (<code>2023/brouillons</code>).</p>
      </div>
    </div>

    <div class="card">
      <div class="card-h"><h2>Compte</h2></div>
      <div class="card-b">
        ${cfg.auth_disabled ? '<p class="help">Authentification désactivée (<code>AUTH_DISABLED=true</code>) : l’accès est contrôlé par le reverse proxy.</p>' : `
        <p class="help">Connecté en tant que <b>${esc(cfg.auth.username || '')}</b>.</p>
        <div class="inline">
          <label class="field">Mot de passe actuel<input type="password" id="c-pw-cur" autocomplete="current-password"></label>
          <label class="field">Nouveau mot de passe<input type="password" id="c-pw-new" autocomplete="new-password" minlength="8"></label>
          <label class="field">Confirmation<input type="password" id="c-pw-conf" autocomplete="new-password"></label>
          <button class="btn" type="button" id="c-pw-btn">Changer le mot de passe</button>
        </div>`}
        <label class="field">Durée de session (heures)<input type="number" id="c-session" min="1" max="8760" value="${cfg.auth.session_hours}"></label>
      </div>
    </div>

    <div class="card wide">
      <div class="card-h"><h2>Catégories</h2><button class="btn small" type="button" id="c-cat-reset">Valeurs par défaut</button></div>
      <div class="card-b">
        <p class="help">Extensions séparées par des espaces ou des virgules. Une extension ne peut appartenir qu’à une catégorie ; les autres vont dans « Autres ». Le changement s’applique sans rescanner.</p>
        <div class="catgrid">
          ${CATS.filter((c) => c.id !== 'other').map((c) => `
            <label class="field"><span><span class="dot" style="background:${catColor(c.id)}"></span> ${esc(c.label)}</span>
              <textarea data-cat="${c.id}" spellcheck="false">${esc((cfg.categories[c.id] || []).join(' '))}</textarea></label>`).join('')}
        </div>
      </div>
    </div>

    <div class="card wide">
      <div class="card-h"><h2>Maintenance</h2></div>
      <div class="card-b">
        <div class="inline">
          <button class="btn danger" type="button" id="c-clear">Vider le cache et rescanner</button>
          <a class="btn" href="/api/docs" target="_blank" rel="noopener">Documentation de l’API (OpenAPI)</a>
        </div>
        <p class="help">Supprime les index et le summary de /cache (l’historique est conservé), puis relance un scan complet.</p>
      </div>
    </div>

    <div class="card wide actions">
      <div id="c-errors"></div>
      <span class="muted small" id="c-saved"></span>
      <button class="btn primary" type="submit">Enregistrer</button>
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
    ? `<div class="errors" role="alert"><b>La configuration n’a pas été enregistrée :</b><ul>${list.map((e) => `<li>${esc(e)}</li>`).join('')}</ul></div>`
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
      $('#c-saved').textContent = `Enregistré à ${new Date().toLocaleTimeString('fr-FR')}`;
      toast('Configuration enregistrée.');
      roots = await api('/api/roots').catch(() => roots);
      $('#roots-body').innerHTML = rootRows();
    } catch (ex) {
      showErrors(ex.data?.errors || [ex.message]);
      toast('Configuration invalide.', 'error');
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
    if (!confirm('Vider le cache (index et statistiques) puis relancer un scan complet ?')) return;
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
    if (nw !== $('#c-pw-conf').value) { toast('Les deux nouveaux mots de passe diffèrent.', 'error'); return; }
    try {
      await api('/api/auth/password', { method: 'POST', body: { current: cur, new: nw } });
      ['#c-pw-cur', '#c-pw-new', '#c-pw-conf'].forEach((s) => { $(s).value = ''; });
      toast('Mot de passe modifié : les autres sessions sont déconnectées.');
    } catch (ex) {
      toast(ex.message, 'error');
    }
  });
}

