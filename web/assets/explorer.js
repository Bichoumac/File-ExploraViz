// Page Explorateur : bandeau de statistiques, arbre, filtres, liste virtualisée.
import {
  CAT, UNIT_FACTORS, api, catColor, debounce, esc, fmtBytes, fmtDate, fmtInt, hashFor, icon, isMobile,
  pickFilters, qs, replaceParams, splitSize, t, toast, unitLabel,
} from './util.js';

const ROW_H = 36;
const COLS = [
  { key: 'name', label: t('ex.col.name') },
  { key: 'path', label: t('ex.col.path') },
  { key: 'ext', label: t('ex.col.ext') },
  { key: 'category', label: t('ex.col.cat'), title: t('ex.col.catTitle') },
  { key: 'size', label: t('ex.col.size'), right: true },
  { key: 'mtime', label: t('ex.col.mtime'), right: true },
];
const SORTS = ['path', 'name', '-name', '-size', 'size', '-mtime', 'mtime', 'ext', 'category'].map((v) => [v, t(`ex.sort.${v}`)]);
const PRESETS = ['', '7d', '30d', 'year', 'gt1y', 'gt5y', 'custom'].map((v) => [v, t(`ex.preset.${v || 'all'}`)]);

let el; let app; let P = {}; let roots = [];
const treeCache = new Map(); // "racine:chemin" -> sous-dossiers
const expanded = new Set();
let rows = []; let total = 0; let pages = 1; let pageSize = 100;
const ctrl = {}; // AbortController par type de requête
const mq = matchMedia('(max-width: 760px)');

// ---------------------------------------------------------------- utilitaires d'état

const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
function presetRange(preset) {
  const now = new Date();
  const back = (days = 0, years = 0) => { const d = new Date(now); d.setDate(d.getDate() - days); d.setFullYear(d.getFullYear() - years); return iso(d); };
  switch (preset) {
    case '7d': return { from: back(7), to: '' };
    case '30d': return { from: back(30), to: '' };
    case 'year': return { from: `${now.getFullYear()}-01-01`, to: '' };
    case 'gt1y': return { from: '', to: back(0, 1) };
    case 'gt5y': return { from: '', to: back(0, 5) };
    default: return null;
  }
}

function normalize(params) {
  const p = { ...params };
  const r = p.preset && presetRange(p.preset);
  if (r) Object.assign(p, r);
  if (!p.root) { delete p.path; delete p.recursive; }
  return p;
}

function filters() {
  const f = pickFilters(P);
  if (f.recursive === undefined && f.root) f.recursive = '1';
  return f;
}

function abortable(kind) {
  ctrl[kind]?.abort();
  ctrl[kind] = new AbortController();
  return ctrl[kind].signal;
}

/** Applique un changement de filtre : URL remplacée (pas d'historique) et rechargement. */
function setParams(patch, { resetPage = true, push = false } = {}) {
  const next = { ...P, ...patch };
  for (const [k, v] of Object.entries(next)) if (v === '' || v === undefined || v === null) delete next[k];
  if (resetPage) delete next.page;
  if (push) { location.hash = hashFor('explorer', next); return; } // déclenche update() via le routeur
  replaceParams(next);
  update(next);
}

// ---------------------------------------------------------------- cycle de vie

export async function mount(container, params, appCtx) {
  el = container;
  app = appCtx;
  el.innerHTML = layout();
  bind();
  window.addEventListener('data-changed', onDataChanged);
  mq.addEventListener('change', onLayoutChange);
  await loadRoots();
  await update(params);
}

export function unmount() {
  window.removeEventListener('data-changed', onDataChanged);
  mq.removeEventListener('change', onLayoutChange);
  Object.values(ctrl).forEach((c) => c.abort());
}

/** Passage téléphone <-> écran large : la liste change de présentation. */
function onLayoutChange() {
  el.querySelector('#vp').classList.toggle('flow', isMobile());
  el.querySelector('#vp-inner').style.height = isMobile() ? 'auto' : `${rows.length * ROW_H}px`;
  renderRows();
}

async function onDataChanged() {
  treeCache.clear();
  await loadRoots();
  update(P);
}

export async function update(params) {
  P = normalize(params);
  syncControls();
  renderCrumbs();
  await Promise.all([loadTree(), loadBand(), loadFiles()]);
}

async function loadRoots() {
  try {
    roots = await api('/api/roots');
  } catch (ex) {
    roots = [];
    toast(ex.message, 'error');
  }
  const sel = el.querySelector('#f-root');
  sel.innerHTML = `<option value="">${t('ex.allRoots')}</option>`
    + roots.filter((r) => r.indexed).map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
  sel.value = P.root || '';
}

// ---------------------------------------------------------------- gabarit

function layout() {
  const units = (id) => `<select id="${id}" aria-label="${t('ex.unit')}">${Object.keys(UNIT_FACTORS).map((u) => `<option value="${u}">${unitLabel(u)}</option>`).join('')}</select>`;
  return `
  <div class="explorer">
    <aside class="side card" id="side">
      <button class="side-toggle" type="button" id="side-toggle" aria-expanded="false" aria-controls="side-body">
        ${icon('folder')}<span class="lbl-cur" id="side-label">${t('ex.folders')}</span>${icon('chevron', 'chev')}
      </button>
      <div class="side-body" id="side-body">
        <div class="card-b">
          <label class="field">${t('ex.root')}<select id="f-root"></select></label>
          <label class="check small"><input type="checkbox" id="f-rec"> ${t('ex.recursive')}</label>
        </div>
        <div class="tree" id="tree" role="tree" aria-label="${t('ex.tree')}"></div>
      </div>
    </aside>
    <section class="content">
      <div class="band card" id="band"><div class="empty">${t('common.loading')}</div></div>
      <div class="filters card" id="filters">
        <div class="filters-top">
          <label class="field grow">${t('ex.search')}
            <input id="f-q" type="search" placeholder="${t('ex.searchPh')}" autocomplete="off"></label>
          <button class="btn filters-toggle" type="button" id="f-toggle" aria-expanded="false" aria-controls="filters-more">
            ${icon('filter')}<span>${t('ex.filters')}</span><span class="badge" id="f-count" hidden></span></button>
        </div>
        <div class="filters-more" id="filters-more">
        <label class="field">${t('ex.period')}<select id="f-preset">${PRESETS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
        <label class="field">${t('ex.from')}<input id="f-from" type="date"></label>
        <label class="field">${t('ex.to')}<input id="f-to" type="date"></label>
        <label class="field">${t('ex.dateField')}<select id="f-df"><option value="mtime">${t('ex.mtime')}</option><option value="ctime">${t('ex.ctime')}</option></select></label>
        <div class="field"><span>${t('ex.exts')}</span><div class="tags" id="f-ext"><input placeholder="jpg, pdf…" aria-label="${t('ex.addExt')}" autocomplete="off"><div class="suggest hidden"></div></div></div>
        <label class="field">${t('ex.minSize')}<span class="row"><input id="f-min" class="size" type="number" min="0" step="any" placeholder="—">${units('f-minu')}</span></label>
        <label class="field">${t('ex.maxSize')}<span class="row"><input id="f-max" class="size" type="number" min="0" step="any" placeholder="—">${units('f-maxu')}</span></label>
        <div class="row">
          <button class="btn" type="button" id="f-reset">${t('ex.reset')}</button>
          <a class="btn" id="f-csv" href="#" download>${icon('download')} CSV</a>
        </div>
        </div>
      </div>
      <nav class="crumbs" id="crumbs" aria-label="${t('common.breadcrumb')}"></nav>
      <div class="card tbl">
        <label class="mobile-sort">${t('ex.sortBy')}
          <select id="f-sort">${SORTS.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
        <div class="tbl-scroll">
          <div class="grid-row thead" id="thead" role="row"></div>
          <div class="vp" id="vp"><div class="vp-inner" id="vp-inner"></div></div>
        </div>
        <div class="pager" id="pager"></div>
      </div>
    </section>
  </div>`;
}

// ---------------------------------------------------------------- contrôles

function bind() {
  const $ = (s) => el.querySelector(s);
  $('#side-toggle').addEventListener('click', () => toggle($('#side'), $('#side-toggle')));
  $('#f-toggle').addEventListener('click', () => toggle($('#filters'), $('#f-toggle')));
  $('#f-sort').addEventListener('change', (e) => setParams({ sort: e.target.value === 'path' ? '' : e.target.value }));
  $('#vp').classList.toggle('flow', isMobile());
  $('#f-root').addEventListener('change', (e) => setParams({ root: e.target.value, path: '' }, { push: true }));
  $('#f-rec').addEventListener('change', (e) => setParams({ recursive: e.target.checked ? '1' : '0' }));
  $('#f-q').addEventListener('input', debounce((e) => setParams({ q: e.target.value.trim() }), 300));
  $('#f-preset').addEventListener('change', (e) => {
    const v = e.target.value;
    if (v === 'custom') setParams({ preset: 'custom' });
    else setParams({ preset: v, from: '', to: '', ...(presetRange(v) || {}) });
  });
  const dates = () => setParams({ preset: 'custom', from: $('#f-from').value, to: $('#f-to').value });
  $('#f-from').addEventListener('change', dates);
  $('#f-to').addEventListener('change', dates);
  $('#f-df').addEventListener('change', (e) => setParams({ date_field: e.target.value === 'mtime' ? '' : e.target.value }));
  const sizes = () => {
    const conv = (v, u) => (v === '' ? '' : String(Math.round(Number(v) * UNIT_FACTORS[u])));
    setParams({ min_size: conv($('#f-min').value, $('#f-minu').value), max_size: conv($('#f-max').value, $('#f-maxu').value) });
  };
  ['#f-min', '#f-max', '#f-minu', '#f-maxu'].forEach((s) => $(s).addEventListener('change', sizes));
  $('#f-reset').addEventListener('click', () => {
    const keep = P.root ? { root: P.root, path: P.path, recursive: P.recursive } : {};
    replaceParams(keep);
    update(keep);
  });
  bindExtInput();

  $('#tree').addEventListener('click', onTreeClick);
  $('#band').addEventListener('click', (e) => {
    const b = e.target.closest('[data-cat]');
    if (!b) return;
    const set = new Set((P.category || '').split(',').filter(Boolean));
    set.has(b.dataset.cat) ? set.delete(b.dataset.cat) : set.add(b.dataset.cat);
    setParams({ category: [...set].join(',') });
  });
  $('#thead').addEventListener('click', (e) => {
    const h = e.target.closest('[data-sort]');
    if (!h) return;
    const cur = P.sort || 'path';
    const key = h.dataset.sort;
    const desc = ['size', 'mtime'].includes(key); // tri décroissant d'abord pour tailles et dates
    let next = desc ? `-${key}` : key;
    if (cur === next) next = next.startsWith('-') ? key : `-${key}`;
    setParams({ sort: next === 'path' ? '' : next });
  });
  $('#vp').addEventListener('scroll', renderRows, { passive: true });
  $('#pager').addEventListener('click', (e) => {
    const b = e.target.closest('[data-page]');
    if (b) setParams({ page: b.dataset.page === '1' ? '' : b.dataset.page }, { resetPage: false });
  });
  $('#pager').addEventListener('change', (e) => {
    if (e.target.id === 'f-ps') setParams({ page_size: e.target.value });
  });
}

/** Panneaux repliables (téléphone) : arborescence et filtres. */
function toggle(panel, button, open = !panel.classList.contains('open')) {
  panel.classList.toggle('open', open);
  button.setAttribute('aria-expanded', String(open));
}

function syncControls() {
  const $ = (s) => el.querySelector(s);
  $('#f-sort').value = SORTS.some(([v]) => v === (P.sort || 'path')) ? (P.sort || 'path') : 'path';
  const active = [P.from || P.to, P.ext, P.min_size || P.max_size, P.date_field === 'ctime'].filter(Boolean).length;
  $('#f-count').hidden = !active;
  $('#f-count').textContent = active;
  $('#f-root').value = P.root || '';
  const rec = $('#f-rec');
  rec.checked = P.recursive !== '0';
  rec.disabled = !P.root;
  if (document.activeElement !== $('#f-q')) $('#f-q').value = P.q || '';
  $('#f-preset').value = P.preset || (P.from || P.to ? 'custom' : '');
  $('#f-from').value = P.from || '';
  $('#f-to').value = P.to || '';
  $('#f-df').value = P.date_field || 'mtime';
  const [minV, minU] = splitSize(P.min_size);
  const [maxV, maxU] = splitSize(P.max_size);
  $('#f-min').value = minV; $('#f-minu').value = minU;
  $('#f-max').value = maxV; $('#f-maxu').value = maxU;
  renderTags();
  $('#f-csv').href = `/api/files/export.csv${qs({ ...filters(), sort: P.sort })}`;
}

// ---------------------------------------------------------------- extensions (saisie multiple + autocomplétion)

function exts() { return (P.ext || '').split(',').filter(Boolean); }

function renderTags() {
  const box = el.querySelector('#f-ext');
  box.querySelectorAll('.tag').forEach((t) => t.remove());
  const input = box.querySelector('input');
  for (const e of exts()) {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.innerHTML = `.${esc(e)}<button type="button" aria-label="${esc(t('ex.removeExt', { ext: e }))}" data-rm="${esc(e)}">×</button>`;
    box.insertBefore(tag, input);
  }
}

function bindExtInput() {
  const box = el.querySelector('#f-ext');
  const input = box.querySelector('input');
  const sug = box.querySelector('.suggest');
  let items = []; let active = -1;
  const add = (v) => {
    const e = v.trim().toLowerCase().replace(/^\.+/, '');
    input.value = '';
    sug.classList.add('hidden');
    if (e && !exts().includes(e)) setParams({ ext: [...exts(), e].join(',') });
  };
  const show = async () => {
    try {
      items = await api('/api/extensions', { params: { root: P.root, prefix: input.value.trim() }, signal: abortable('ext') });
    } catch { return; }
    items = items.filter((i) => !exts().includes(i.ext)).slice(0, 12);
    active = -1;
    sug.innerHTML = items.map((i, k) => `<div data-k="${k}"><span>.${esc(i.ext)}</span><span class="muted">${fmtInt(i.files)}</span></div>`).join('');
    sug.classList.toggle('hidden', !items.length || document.activeElement !== input);
  };
  input.addEventListener('focus', show);
  input.addEventListener('input', debounce(show, 150));
  input.addEventListener('blur', () => setTimeout(() => sug.classList.add('hidden'), 150));
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = Math.max(-1, Math.min(items.length - 1, active + (e.key === 'ArrowDown' ? 1 : -1)));
      sug.querySelectorAll('div').forEach((d, k) => d.classList.toggle('on', k === active));
    } else if (e.key === 'Enter' || e.key === ',' || e.key === ' ') {
      e.preventDefault();
      add(active >= 0 ? items[active].ext : input.value);
    } else if (e.key === 'Backspace' && !input.value && exts().length) {
      setParams({ ext: exts().slice(0, -1).join(',') });
    }
  });
  sug.addEventListener('mousedown', (e) => {
    const d = e.target.closest('[data-k]');
    if (d) { e.preventDefault(); add(items[+d.dataset.k].ext); }
  });
  box.addEventListener('click', (e) => {
    const rm = e.target.closest('[data-rm]');
    if (rm) setParams({ ext: exts().filter((x) => x !== rm.dataset.rm).join(',') });
    else input.focus();
  });
}

// ---------------------------------------------------------------- arbre

const keyOf = (root, path) => `${root}:${path || ''}`;

async function loadTree() {
  if (P.root) {
    expanded.add(keyOf(P.root, ''));
    const parts = (P.path || '').split('/').filter(Boolean);
    for (let i = 1; i < parts.length; i += 1) expanded.add(keyOf(P.root, parts.slice(0, i).join('/')));
  }
  const missing = [...expanded].filter((k) => !treeCache.has(k));
  await Promise.all(missing.map(fetchChildren));
  renderTree();
}

async function fetchChildren(key) {
  const i = key.indexOf(':');
  const root = key.slice(0, i);
  const path = key.slice(i + 1);
  try {
    const r = await api('/api/tree', { params: { root, path } });
    treeCache.set(key, r.dirs);
  } catch {
    treeCache.set(key, []);
    expanded.delete(key);
  }
}

function link(root, path) {
  return hashFor('explorer', { ...P, root, path, page: '' });
}

function nodeHtml(root, path, name, size, hasChildren, error) {
  const key = keyOf(root, path);
  const open = expanded.has(key) && treeCache.has(key);
  const on = P.root === root && (P.path || '') === (path || '');
  let html = `<li role="treeitem" aria-expanded="${hasChildren ? open : 'false'}"${on ? ' aria-selected="true"' : ''}>
    <div class="node${on ? ' on' : ''}">
      <button class="tw${open ? ' open' : ''}${hasChildren ? '' : ' leaf'}" type="button" data-toggle="${esc(key)}" aria-label="${t('ex.expand')}">${icon('chevron')}</button>
      <a class="lbl" href="${esc(link(root, path))}" title="${esc(path || name)}"><span>${error ? `<span class="err-dot" title="${t('common.inaccessible')}">⚠</span> ` : ''}${esc(name)}</span><span class="sz">${fmtBytes(size)}</span></a>
    </div>`;
  if (open) {
    const kids = treeCache.get(key);
    html += `<ul role="group">${kids.map((d) => nodeHtml(root, d.path, d.name, d.size, d.has_children, d.error)).join('')}</ul>`;
  }
  return `${html}</li>`;
}

function renderTree() {
  const indexed = roots.filter((r) => r.indexed);
  el.querySelector('#tree').innerHTML = indexed.length
    ? `<ul>${indexed.map((r) => nodeHtml(r.id, '', r.name, r.bytes, (r.dirs || 0) > 1, false)).join('')}</ul>`
    : `<div class="empty small">${t('ex.noRoots')}</div>`;
}

async function onTreeClick(e) {
  if (e.target.closest('a.lbl') && isMobile()) toggle(el.querySelector('#side'), el.querySelector('#side-toggle'), false);
  const tw = e.target.closest('[data-toggle]');
  if (!tw) return;
  const key = tw.dataset.toggle;
  if (expanded.has(key)) expanded.delete(key);
  else {
    expanded.add(key);
    if (!treeCache.has(key)) await fetchChildren(key);
  }
  renderTree();
}

function renderCrumbs() {
  const c = el.querySelector('#crumbs');
  const parts = [`<a href="${esc(hashFor('explorer', { ...P, root: '', path: '', page: '' }))}">${t('ex.allRoots')}</a>`];
  if (P.root) {
    const r = roots.find((x) => x.id === P.root);
    const segs = (P.path || '').split('/').filter(Boolean);
    const rootLabel = esc(r ? r.name : P.root);
    parts.push(segs.length ? `<a href="${esc(link(P.root, ''))}">${rootLabel}</a>` : `<span class="cur">${rootLabel}</span>`);
    segs.forEach((s, i) => {
      const p = segs.slice(0, i + 1).join('/');
      parts.push(i === segs.length - 1 ? `<span class="cur">${esc(s)}</span>` : `<a href="${esc(link(P.root, p))}">${esc(s)}</a>`);
    });
  }
  c.innerHTML = parts.join('<span class="sep">›</span>');
  const cur = P.root ? ((P.path || '').split('/').filter(Boolean).pop() || roots.find((x) => x.id === P.root)?.name || P.root) : t('ex.allRoots');
  el.querySelector('#side-label').textContent = cur;
}

// ---------------------------------------------------------------- bandeau

async function loadBand() {
  const band = el.querySelector('#band');
  let st;
  try {
    st = await api('/api/stats', { params: { ...filters(), detail: 'false' }, signal: abortable('band') });
  } catch (ex) {
    if (ex.name === 'AbortError') return;
    band.innerHTML = `<div class="empty">${esc(ex.message)}</div>`;
    return;
  }
  const tot = st.totals;
  const active = new Set((P.category || '').split(',').filter(Boolean));
  const filtered = Object.keys(pickFilters(P)).some((k) => !['root', 'path', 'recursive'].includes(k));
  const segs = st.categories.filter((c) => c.bytes > 0)
    .map((c) => `<div style="flex:${c.bytes} 1 0;background:${catColor(c.id)}" title="${esc(t('ex.segTitle', { label: CAT[c.id].label, size: fmtBytes(c.bytes), n: fmtInt(c.files), count: c.files }))}"></div>`).join('');
  band.innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="v">${fmtInt(tot.files)}</div><div class="l">${t('ex.kpi.files', { count: tot.files })}</div></div>
      <div class="kpi"><div class="v">${fmtInt(filtered ? tot.dirs_with_files : tot.dirs)}</div><div class="l">${filtered ? t('ex.kpi.dirsMatching') : t('ex.kpi.dirs', { count: tot.dirs })}</div></div>
      <div class="kpi"><div class="v">${fmtBytes(tot.bytes)}</div><div class="l">${t('ex.kpi.size')}</div></div>
      ${st.source === 'summary' ? `<div class="kpi"><div class="l">${t('ex.precomputed')}</div></div>` : ''}
    </div>
    <div class="stack" role="img" aria-label="${t('ex.stackLabel')}">${segs}</div>
    <div class="cats" role="group" aria-label="${t('ex.catsLabel')}">
      ${st.categories.map((c) => `
        <button type="button" class="cat${active.has(c.id) ? ' on' : ''}${c.files ? '' : ' zero'}" data-cat="${c.id}" aria-pressed="${active.has(c.id)}"
          title="${esc(t('ex.filterBy', { label: CAT[c.id].label }))}">
          <span class="sw" style="background:${catColor(c.id)}">${icon(CAT[c.id].icon)}</span>
          <span class="n">${esc(CAT[c.id].label)}</span>
          <span class="m">${fmtInt(c.files)} · ${fmtBytes(c.bytes)}</span>
        </button>`).join('')}
    </div>`;
}

// ---------------------------------------------------------------- liste des fichiers

async function loadFiles() {
  const inner = el.querySelector('#vp-inner');
  renderHead();
  let r;
  try {
    r = await api('/api/files', {
      params: { ...filters(), sort: P.sort, page: P.page, page_size: P.page_size },
      signal: abortable('files'),
    });
  } catch (ex) {
    if (ex.name === 'AbortError') return;
    rows = []; total = 0; pages = 1;
    inner.style.height = 'auto';
    inner.innerHTML = `<div class="empty">${esc(ex.message)}</div>`;
    renderPager();
    return;
  }
  rows = r.rows; total = r.total; pages = r.pages; pageSize = r.page_size;
  P.page = String(r.page);
  const vp = el.querySelector('#vp');
  vp.scrollTop = 0;
  inner.style.height = isMobile() ? 'auto' : `${rows.length * ROW_H}px`;
  if (!rows.length) {
    inner.style.height = 'auto';
    inner.innerHTML = `<div class="empty">${r.found === false ? t('ex.notFound') : t('ex.noMatch')}</div>`;
  } else {
    inner.innerHTML = '';
    renderRows();
  }
  renderPager();
}

function renderHead() {
  const sort = P.sort || 'path';
  const key = sort.replace(/^-/, '');
  const arrow = sort.startsWith('-') ? '▼' : '▲';
  el.querySelector('#thead').innerHTML = COLS.map((c) => `
    <div data-sort="${c.key}" class="${c.right ? 'r' : ''}" role="columnheader" aria-sort="${c.key === key ? (sort.startsWith('-') ? 'descending' : 'ascending') : 'none'}" title="${esc(t('ex.sortByCol', { col: c.title || c.label }))}">
      ${c.label}${c.key === key ? ` <span class="arrow">${arrow}</span>` : ''}
    </div>`).join('');
}

/** Téléphone : une carte par fichier, toute la page affichée (la page entière défile). */
function renderCards() {
  el.querySelector('#vp-inner').innerHTML = rows.map((f) => {
    const c = CAT[f.category] || CAT.other;
    const dirLink = hashFor('explorer', { ...P, root: f.root, path: f.dir, recursive: '0', page: '' });
    return `<div class="mrow" role="row">
      <span class="caticon" style="background:${catColor(f.category)}" title="${esc(c.label)}">${icon(c.icon)}</span>
      <div class="mmain">
        <div class="mname">${esc(f.name)}</div>
        <a class="mpath" href="${esc(dirLink)}">${P.root ? '' : `${esc(f.root)} › `}${esc(f.dir ? `${f.dir}/` : './')}</a>
      </div>
      <div class="mside num"><div>${fmtBytes(f.size)}</div><div class="muted">${fmtDate(f.mtime)}</div></div>
    </div>`;
  }).join('');
}

/** Écran large : ne crée que les lignes visibles (+ une marge), fluide même avec 1 000 lignes par page. */
function renderRows() {
  if (!rows.length) return;
  if (isMobile()) { renderCards(); return; }
  const vp = el.querySelector('#vp');
  const inner = el.querySelector('#vp-inner');
  const first = Math.max(0, Math.floor(vp.scrollTop / ROW_H) - 8);
  const last = Math.min(rows.length, Math.ceil((vp.scrollTop + vp.clientHeight) / ROW_H) + 8);
  let html = '';
  for (let i = first; i < last; i += 1) {
    const f = rows[i];
    const c = CAT[f.category] || CAT.other;
    const dirLink = hashFor('explorer', { ...P, root: f.root, path: f.dir, recursive: '0', page: '' });
    html += `<div class="vrow grid-row" style="top:${i * ROW_H}px" role="row">
      <div class="name" title="${esc(f.name)}"><span class="dot" style="background:${catColor(f.category)}"></span><span>${esc(f.name)}</span></div>
      <div class="path" title="${esc(f.root)} › ${esc(f.path)}"><a href="${esc(dirLink)}">${P.root ? '' : `${esc(f.root)} › `}${esc(f.dir ? `${f.dir}/` : './')}</a></div>
      <div>${f.ext ? esc(f.ext) : '<span class="muted">—</span>'}</div>
      <div title="${esc(c.label)}"><span class="caticon" style="background:${catColor(f.category)}">${icon(c.icon)}</span></div>
      <div class="r num" title="${t('ex.bytes', { n: fmtInt(f.size), count: f.size })}">${fmtBytes(f.size)}</div>
      <div class="r num" title="${t('ex.created', { date: fmtDate(f.ctime) })}">${fmtDate(f.mtime)}</div>
    </div>`;
  }
  inner.innerHTML = html;
}

function renderPager() {
  const page = Number(P.page || 1);
  const btn = (p, label, dis) => `<button class="btn small" type="button" data-page="${p}" ${dis ? 'disabled' : ''}>${label}</button>`;
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total, page * pageSize);
  el.querySelector('#pager').innerHTML = `
    <span class="muted">${t('ex.range', { from: fmtInt(from), to: fmtInt(to), total: `<b>${fmtInt(total)}</b>`, count: total })}</span>
    <span class="row">
      ${btn(1, '«', page <= 1)}${btn(page - 1, t('ex.prev'), page <= 1)}
      <span class="num">${t('ex.page', { p: fmtInt(page), n: fmtInt(pages) })}</span>
      ${btn(page + 1, t('ex.next'), page >= pages)}${btn(pages, '»', page >= pages)}
      <select id="f-ps" aria-label="${t('ex.perPage')}">${[50, 100, 200, 500, 1000].map((n) => `<option ${n === pageSize ? 'selected' : ''}>${n}</option>`).join('')}</select>
    </span>`;
}
