// Carte « Comptes » de la page Réglages (administrateurs) : création, rôle, dossiers autorisés,
// réinitialisation du mot de passe d'un compte viewer, suppression.
import { api, debounce, esc, icon, t, toast } from './util.js';

let box; let users = []; let roots = [];
let editing = null; // { mode: 'create' | 'edit' | 'reset', user?: {...} }

export async function mountUsers(container) {
  box = container;
  box.innerHTML = `
    <div class="card wide" id="users-card">
      <div class="card-h"><h2>${t('usr.title')}</h2>
        <button class="btn small" type="button" id="u-add">${t('usr.add')}</button></div>
      <div class="card-b">
        <p class="help">${t('usr.help')}</p>
        <div class="tbl-scroll"><table class="data utable">
          <thead><tr><th>${t('usr.colUser')}</th><th>${t('usr.colRole')}</th><th>${t('usr.colFolders')}</th><th class="num"></th></tr></thead>
          <tbody id="u-body"><tr><td colspan="4" class="muted">${t('common.loading')}</td></tr></tbody>
        </table></div>
        <div id="u-editor" hidden></div>
      </div>
    </div>`;
  box.querySelector('#u-add').addEventListener('click', () => openEditor({ mode: 'create' }));
  box.querySelector('#u-body').addEventListener('click', onRowAction);
  roots = await api('/api/roots').catch(() => []);
  await reload();
}

async function reload() {
  try {
    users = await api('/api/users');
  } catch (ex) {
    box.querySelector('#u-body').innerHTML = `<tr><td colspan="4">${esc(ex.message)}</td></tr>`;
    return;
  }
  renderRows();
}

const rootName = (id) => roots.find((r) => r.id === id)?.name || id;

function foldersCell(u) {
  if (u.role === 'admin' || u.all_folders) return `<span class="badge">${t('usr.allFolders')}</span>`;
  if (!u.scopes.length) return `<span class="muted">${t('usr.noFolders')}</span>`;
  return u.scopes.map((s) => `<div class="small">${esc(rootName(s.root))}${s.path ? ` › ${esc(s.path)}` : ''}${
    s.found === false ? ` <span class="badge ko" title="${t('usr.scopeMissingHelp')}">${t('usr.scopeMissing')}</span>` : ''}</div>`).join('');
}

function renderRows() {
  box.querySelector('#u-body').innerHTML = users.map((u) => `
    <tr>
      <td><b>${esc(u.username)}</b>${u.is_self ? ` <span class="muted small">${t('usr.you')}</span>` : ''}</td>
      <td><span class="badge${u.role === 'admin' ? ' ok' : ''}">${t(`usr.role.${u.role}`)}</span></td>
      <td>${foldersCell(u)}</td>
      <td class="num"><div class="row-actions">
        <button class="btn small" type="button" data-act="edit" data-user="${esc(u.username)}">${t('usr.edit')}</button>
        ${u.role === 'viewer' ? `<button class="btn small" type="button" data-act="reset" data-user="${esc(u.username)}">${t('usr.resetPw')}</button>` : ''}
        ${u.is_self ? '' : `<button class="btn small danger" type="button" data-act="delete" data-user="${esc(u.username)}">${t('usr.delete')}</button>`}
      </div></td>
    </tr>`).join('');
}

async function onRowAction(e) {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const user = users.find((u) => u.username === b.dataset.user);
  if (!user) return;
  if (b.dataset.act === 'edit') openEditor({ mode: 'edit', user });
  else if (b.dataset.act === 'reset') openEditor({ mode: 'reset', user });
  else if (b.dataset.act === 'delete') {
    if (!confirm(t('usr.deleteConfirm', { name: user.username }))) return;
    try {
      await api(`/api/users/${encodeURIComponent(user.username)}`, { method: 'DELETE' });
      toast(t('usr.deleted'));
      closeEditor();
      await reload();
    } catch (ex) {
      toast(ex.message, 'error');
    }
  }
}

// ---------------------------------------------------------------- éditeur

function scopeRow(scope = { root: roots[0]?.id || '', path: '' }, i = Date.now() + Math.random()) {
  const id = `u-dl-${String(i).replace('.', '')}`;
  return `<div class="scope-row" data-scope>
    <select data-root aria-label="${t('ex.root')}">${roots.map((r) => `<option value="${esc(r.id)}" ${r.id === scope.root ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select>
    <input type="text" data-path list="${id}" value="${esc(scope.path)}" placeholder="${t('usr.pathPh')}" aria-label="${t('usr.pathLabel')}" autocomplete="off">
    <datalist id="${id}"></datalist>
    <button class="icon-btn" type="button" data-remove title="${t('usr.removeFolder')}" aria-label="${t('usr.removeFolder')}">${icon('x')}</button>
  </div>`;
}

function openEditor(state) {
  editing = state;
  const ed = box.querySelector('#u-editor');
  const u = state.user;
  if (state.mode === 'reset') {
    ed.innerHTML = `
      <h3>${t('usr.resetFor', { name: esc(u.username) })}</h3>
      <p class="help">${t('usr.resetHelp')}</p>
      <div class="inline">
        <label class="field">${t('cfg.pwNew')}<input type="password" id="u-pw" autocomplete="new-password" minlength="8"></label>
        <label class="field">${t('auth.confirm')}<input type="password" id="u-pw2" autocomplete="new-password"></label>
      </div>
      <div class="u-err errors" hidden></div>
      <div class="inline"><button class="btn primary" type="button" id="u-save">${t('usr.resetPw')}</button>
        <button class="btn" type="button" id="u-cancel">${t('usr.cancel')}</button></div>`;
  } else {
    const create = state.mode === 'create';
    const role = u?.role || 'viewer';
    ed.innerHTML = `
      <h3>${create ? t('usr.new') : t('usr.editTitle', { name: esc(u.username) })}</h3>
      <div class="inline">
        ${create ? `<label class="field">${t('auth.username')}<input type="text" id="u-name" maxlength="64" autocomplete="off"></label>
        <label class="field">${t('auth.password')}<input type="password" id="u-pw" autocomplete="new-password" minlength="8"></label>
        <label class="field">${t('auth.confirm')}<input type="password" id="u-pw2" autocomplete="new-password"></label>` : ''}
        <label class="field">${t('usr.colRole')}<select id="u-role">
          <option value="viewer" ${role === 'viewer' ? 'selected' : ''}>${t('usr.role.viewer')}</option>
          <option value="admin" ${role === 'admin' ? 'selected' : ''}>${t('usr.role.admin')}</option></select></label>
      </div>
      <p class="help" id="u-admin-note">${t('usr.adminSeesAll')}</p>
      <div id="u-folders">
        <label class="check"><input type="checkbox" id="u-all" ${u?.all_folders ? 'checked' : ''}> ${t('usr.allFoldersLabel')}</label>
        <div id="u-scopes-wrap">
          <p class="help">${t('usr.foldersHelp')}</p>
          <div id="u-scopes">${(u?.scopes || []).map((s, i) => scopeRow(s, i)).join('')}</div>
          <button class="btn small" type="button" id="u-add-scope">${icon('folder')} ${t('usr.addFolder')}</button>
        </div>
      </div>
      <div class="u-err errors" hidden></div>
      <div class="inline"><button class="btn primary" type="button" id="u-save">${create ? t('usr.create') : t('cfg.save')}</button>
        <button class="btn" type="button" id="u-cancel">${t('usr.cancel')}</button></div>`;
    const sync = () => {
      const admin = ed.querySelector('#u-role').value === 'admin';
      ed.querySelector('#u-admin-note').hidden = !admin;
      ed.querySelector('#u-folders').hidden = admin;
      ed.querySelector('#u-scopes-wrap').hidden = ed.querySelector('#u-all').checked;
    };
    ed.querySelector('#u-role').addEventListener('change', sync);
    ed.querySelector('#u-all').addEventListener('change', sync);
    ed.querySelector('#u-add-scope').addEventListener('click', () => {
      ed.querySelector('#u-scopes').insertAdjacentHTML('beforeend', scopeRow());
    });
    ed.querySelector('#u-scopes').addEventListener('click', (ev) => {
      if (ev.target.closest('[data-remove]')) ev.target.closest('[data-scope]').remove();
    });
    ed.querySelector('#u-scopes').addEventListener('input', debounce(suggest, 200));
    ed.querySelector('#u-scopes').addEventListener('focusin', suggest);
    sync();
  }
  ed.hidden = false;
  ed.querySelector('#u-cancel').addEventListener('click', closeEditor);
  ed.querySelector('#u-save').addEventListener('click', save);
  ed.querySelector('input, select')?.focus();
  ed.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function closeEditor() {
  editing = null;
  const ed = box.querySelector('#u-editor');
  ed.hidden = true;
  ed.innerHTML = '';
}

/** Suggestions de sous-dossiers (liste déroulante) d'après ce qui est déjà saisi. */
async function suggest(e) {
  const row = e.target.closest('[data-scope]');
  if (!row || !e.target.matches('[data-path]')) return;
  const root = row.querySelector('[data-root]').value;
  const typed = row.querySelector('[data-path]').value.replace(/^\/+/, '');
  const parent = typed.includes('/') ? typed.slice(0, typed.lastIndexOf('/')) : '';
  try {
    const r = await api('/api/tree', { params: { root, path: parent } });
    row.querySelector('datalist').innerHTML = r.dirs.map((d) => `<option value="${esc(d.path)}"></option>`).join('');
  } catch { /* dossier inconnu : pas de suggestion */ }
}

function showError(message) {
  const err = box.querySelector('.u-err');
  err.textContent = message;
  err.hidden = false;
}

async function save() {
  const ed = box.querySelector('#u-editor');
  const $ = (s) => ed.querySelector(s);
  const btn = $('#u-save');
  try {
    btn.disabled = true;
    if (editing.mode === 'reset') {
      if ($('#u-pw').value !== $('#u-pw2').value) { showError(t('auth.mismatch')); return; }
      const r = await api(`/api/users/${encodeURIComponent(editing.user.username)}/password`, {
        method: 'POST', body: { password: $('#u-pw').value },
      });
      toast(r.message);
    } else {
      const body = {
        role: $('#u-role').value,
        all_folders: $('#u-all').checked,
        scopes: [...ed.querySelectorAll('[data-scope]')].map((row) => ({
          root: row.querySelector('[data-root]').value,
          path: row.querySelector('[data-path]').value.trim(),
        })),
      };
      if (editing.mode === 'create') {
        if ($('#u-pw').value !== $('#u-pw2').value) { showError(t('auth.mismatch')); return; }
        await api('/api/users', { method: 'POST', body: { ...body, username: $('#u-name').value.trim(), password: $('#u-pw').value } });
        toast(t('usr.created'));
      } else {
        await api(`/api/users/${encodeURIComponent(editing.user.username)}`, { method: 'PUT', body });
        toast(t('usr.saved'));
      }
    }
    closeEditor();
    await reload();
  } catch (ex) {
    showError(ex.message);
  } finally {
    if (btn.isConnected) btn.disabled = false;
  }
}
