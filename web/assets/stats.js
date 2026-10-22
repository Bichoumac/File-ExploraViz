// Page Statistiques : tuiles, graphiques ECharts (chargés à la demande) et tableaux.
import {
  CAT, api, catColor, cssVar, esc, fmtBytes, fmtDate, fmtDateTime, fmtInt, hashFor, isMobile, loadScript, pct, pickFilters, toast,
} from './util.js';

const ECHARTS = '/vendor/echarts-5.6.0.min.js';
let el; let P = {}; let data = null; let history = []; let roots = [];
const charts = new Map();
const mode = { cats: 'bytes', exts: 'bytes', sizes: 'files', years: 'bytes' };
let ctrl = null;

export async function mount(container, params) {
  el = container;
  el.innerHTML = '<div class="empty">Chargement des statistiques…</div>';
  window.addEventListener('resize', onResize);
  window.addEventListener('theme-changed', renderCharts);
  window.addEventListener('data-changed', reload);
  try {
    await loadScript(ECHARTS);
  } catch (ex) {
    toast(ex.message, 'error');
  }
  roots = await api('/api/roots').catch(() => []);
  await update(params);
}

export function unmount() {
  window.removeEventListener('resize', onResize);
  window.removeEventListener('theme-changed', renderCharts);
  window.removeEventListener('data-changed', reload);
  ctrl?.abort();
  disposeCharts();
}

function disposeCharts() {
  charts.forEach((c) => c.dispose());
  charts.clear();
}

let resizeTimer;
let wasMobile = isMobile();
function onResize() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    charts.forEach((c) => c.resize());
    if (wasMobile !== isMobile()) { wasMobile = isMobile(); renderCharts(); } // légendes repositionnées
  }, 120);
}

export async function update(params) {
  P = params;
  await reload();
}

async function reload() {
  ctrl?.abort();
  ctrl = new AbortController();
  try {
    [data, history] = await Promise.all([
      api('/api/stats', { params: pickFilters(P), signal: ctrl.signal }),
      api('/api/stats/history', { signal: ctrl.signal }),
    ]);
  } catch (ex) {
    if (ex.name === 'AbortError') return;
    disposeCharts();
    el.innerHTML = `<div class="empty">${esc(ex.message)}</div>`;
    return;
  }
  render();
}

// ---------------------------------------------------------------- gabarit

function seg(key, a, b) {
  return `<div class="seg" data-mode="${key}" role="group">
    <button type="button" data-v="bytes" class="${mode[key] === 'bytes' ? 'on' : ''}">${a}</button>
    <button type="button" data-v="files" class="${mode[key] === 'files' ? 'on' : ''}">${b}</button></div>`;
}

function filterChips() {
  const f = pickFilters(P);
  const chips = [];
  const r = roots.find((x) => x.id === f.root);
  chips.push(r ? `Racine : ${r.name}${f.path ? ` › ${f.path}` : ''}` : 'Toutes les racines');
  if (f.recursive === '0') chips.push('Sans les sous-dossiers');
  if (f.category) chips.push(`Catégories : ${f.category.split(',').map((c) => CAT[c]?.label || c).join(', ')}`);
  if (f.ext) chips.push(`Extensions : ${f.ext}`);
  if (f.from || f.to) chips.push(`${f.date_field === 'ctime' ? 'Création' : 'Modification'} : ${f.from || '…'} → ${f.to || '…'}`);
  if (f.min_size || f.max_size) chips.push(`Taille : ${f.min_size ? fmtBytes(+f.min_size) : '0'} → ${f.max_size ? fmtBytes(+f.max_size) : '∞'}`);
  if (f.q) chips.push(`Nom : ${f.q}`);
  return chips.map((c) => `<span class="chip">${esc(c)}</span>`).join('');
}

function tile(label, value, sub = '') {
  return `<div class="card tile"><div class="l">${label}</div><div class="v">${value}</div>${sub ? `<div class="s">${sub}</div>` : ''}</div>`;
}

function render() {
  disposeCharts();
  const d = data;
  const t = d.totals;
  const dup = d.duplicates || { groups: 0, files: 0, recoverable_bytes: 0, top: [] };
  const rootOpts = `<option value="">Toutes les racines</option>${roots.filter((r) => r.indexed)
    .map((r) => `<option value="${esc(r.id)}" ${r.id === P.root ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}`;
  el.innerHTML = `
    <div class="stats-head">
      <div class="chips">${filterChips()}</div>
      <div class="row" style="display:flex;gap:8px;align-items:center">
        <select id="s-root" aria-label="Racine">${rootOpts}</select>
        <a class="btn" href="${esc(hashFor('explorer', P))}">Modifier les filtres</a>
      </div>
    </div>
    ${d.source === 'summary' ? '<p class="help">Agrégats pré-calculés du dernier scan (index encore en chargement).</p>' : ''}
    <div class="tiles">
      ${tile('Fichiers', fmtInt(t.files), `${fmtInt(t.dirs)} dossiers`)}
      ${tile('Volume total', fmtBytes(t.bytes), t.extensions != null ? `${fmtInt(t.extensions)} extensions distinctes` : '')}
      ${tile('Taille moyenne', fmtBytes(t.mean_size), `médiane ${fmtBytes(t.median_size)}`)}
      ${tile('Doublons probables', fmtInt(dup.files), `${fmtBytes(dup.recoverable_bytes)} récupérables`)}
      ${tile('Fichiers vides', fmtInt(t.empty_files), pct(t.empty_files, t.files))}
      ${tile('Dossiers vides', fmtInt(d.empty_dirs?.count))}
      ${tile('Éléments inaccessibles', fmtInt(d.inaccessible?.count), d.truncated_dirs ? `${fmtInt(d.truncated_dirs)} dossiers tronqués (profondeur max)` : '')}
      ${tile('Fichiers cachés', fmtInt(t.hidden_files), 'nom commençant par « . »')}
      ${tile('Plus ancien', fmtDate(t.oldest_mtime), `plus récent : ${fmtDate(t.newest_mtime)}`)}
      ${t.future_mtime ? tile('Dates dans le futur', fmtInt(t.future_mtime), 'horloge ou métadonnées suspectes') : ''}
      ${tile('Profondeur max', fmtInt(t.max_depth), 'niveaux sous le dossier choisi')}
    </div>
    <div class="sgrid">
      <div class="card"><div class="card-h"><h2>Répartition par catégorie</h2>${seg('cats', 'Volume', 'Nombre')}</div><div class="card-b"><div class="chart" id="c-cats"></div></div></div>
      <div class="card"><div class="card-h"><h2>Top 20 extensions</h2>${seg('exts', 'Volume', 'Nombre')}</div><div class="card-b"><div class="chart tall" id="c-exts"></div></div></div>
      <div class="card"><div class="card-h"><h2>Distribution des tailles</h2>${seg('sizes', 'Volume', 'Nombre')}</div><div class="card-b"><div class="chart" id="c-sizes"></div></div></div>
      <div class="card"><div class="card-h"><h2>Année de modification</h2>${seg('years', 'Volume', 'Nombre')}</div><div class="card-b"><div class="chart" id="c-years"></div></div></div>
      <div class="card"><div class="card-h"><h2>Ancienneté (dernière modification)</h2></div><div class="card-b"><div class="chart" id="c-age"></div></div></div>
      <div class="card"><div class="card-h"><h2>Évolution dans le temps</h2><span class="muted small">${fmtInt(history.length)} scan(s) en historique · toutes racines</span></div>
        <div class="card-b"><h3>Volume total</h3><div class="chart" style="height:150px" id="c-hist-bytes"></div><h3>Nombre de fichiers</h3><div class="chart" style="height:150px" id="c-hist-files"></div></div></div>
      ${tableCard('Top 50 des plus gros fichiers', ['Fichier', 'Taille', 'Modifié'], (d.largest_files || []).map((f) => [
        `<span class="dot" style="background:${catColor(f.category)}"></span> ${esc(f.name)}<div class="small muted path">${esc(f.root)} › ${esc(f.path)}</div>`,
        fmtBytes(f.size), fmtDate(f.mtime)]), 'wide')}
      ${tableCard('Top 20 des plus gros dossiers (fichiers directs)', ['Dossier', 'Fichiers', 'Taille directe', 'Taille totale'], (d.largest_dirs || []).map((x) => [
        dirLink(x), fmtInt(x.files), fmtBytes(x.bytes), fmtBytes(x.total_bytes)]))}
      ${tableCard('Dossiers les plus peuplés', ['Dossier', 'Fichiers', 'Taille directe'], (d.busiest_dirs || []).map((x) => [dirLink(x), fmtInt(x.files), fmtBytes(x.bytes)]))}
      ${tableCard(`Doublons probables (même nom, même taille) — ${fmtInt(dup.groups)} groupes, ${fmtBytes(dup.recoverable_bytes)} récupérables`,
        ['Nom', 'Exemplaires', 'Taille unitaire', 'Récupérable'], dup.top.map((g) => [
          `${esc(g.name)}<details class="more"><summary>Emplacements</summary>${g.paths.map((p) => `<div class="small path">${esc(p.root)} › ${esc(p.path)}</div>`).join('')}</details>`,
          fmtInt(g.count), fmtBytes(g.size), fmtBytes(g.recoverable_bytes)]), 'wide')}
      ${tableCard(`Dossiers vides (${fmtInt(d.empty_dirs?.count)})`, ['Dossier'], (d.empty_dirs?.sample || []).map((x) => [dirLink(x)]))}
      ${tableCard(`Éléments inaccessibles (${fmtInt(d.inaccessible?.count)})`, ['Élément', 'Erreur'], (d.inaccessible?.sample || []).map((x) => [
        `${x.kind === 'dir' ? 'Dossier' : 'Fichier'} : ${esc(x.root)} › ${esc(x.path || '/')}`, esc(x.error)]))}
    </div>`;

  el.querySelector('#s-root').addEventListener('change', (e) => {
    location.hash = hashFor('stats', { ...P, root: e.target.value, path: '' });
  });
  el.querySelectorAll('.seg[data-mode]').forEach((s) => s.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    mode[s.dataset.mode] = b.dataset.v;
    s.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    renderCharts();
  }));
  renderCharts();
}

function dirLink(x) {
  const href = hashFor('explorer', { root: x.root, path: x.path, recursive: '0' });
  return `<a href="${esc(href)}">${esc(x.root)} › ${esc(x.path || '/')}</a>`;
}

function tableCard(title, heads, body, cls = '') {
  const content = body.length
    ? `<div class="scrollbox"><table class="data"><thead><tr>${heads.map((h, i) => `<th class="${i ? 'num' : ''}">${h}</th>`).join('')}</tr></thead>
        <tbody>${body.map((r) => `<tr>${r.map((c, i) => `<td class="${i ? 'num' : ''}">${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
    : '<div class="empty small">Rien à signaler.</div>';
  return `<div class="card ${cls}"><div class="card-h"><h2>${title}</h2></div><div class="card-b">${content}</div></div>`;
}

// ---------------------------------------------------------------- graphiques

function chart(id) {
  const node = el.querySelector(`#${id}`);
  if (!node || !window.echarts) return null;
  let c = charts.get(id);
  if (!c) { c = window.echarts.init(node, null, { renderer: 'canvas' }); charts.set(id, c); }
  return c;
}

function theme() {
  const text2 = cssVar('--text-2');
  const muted = cssVar('--muted');
  return {
    text: cssVar('--text'), text2, muted, surface: cssVar('--surface'), border: cssVar('--border'), grid: cssVar('--grid'), series: cssVar('--series'),
    tooltip: {
      backgroundColor: cssVar('--surface'), borderColor: cssVar('--border'), borderWidth: 1,
      textStyle: { color: cssVar('--text'), fontSize: 12 }, extraCssText: 'box-shadow:0 4px 16px rgba(0,0,0,.15);border-radius:8px;',
    },
    axis: (extra = {}) => ({
      axisLine: { lineStyle: { color: cssVar('--border') } }, axisTick: { show: false },
      axisLabel: { color: muted, fontSize: 11 }, splitLine: { lineStyle: { color: cssVar('--grid'), type: 'solid' } }, ...extra,
    }),
  };
}

const metric = (key) => (mode[key] === 'bytes' ? { f: (v) => fmtBytes(v), k: 'bytes' } : { f: (v) => fmtInt(v), k: 'files' });

function renderCharts() {
  if (!data || !window.echarts) return;
  const th = theme();
  const font = { fontFamily: getComputedStyle(document.body).fontFamily };

  // Donut par catégorie (étiquettes directes seulement pour les parts visibles ; légende toujours présente)
  const mc = metric('cats');
  const cats = data.categories.filter((c) => c[mc.k] > 0);
  const totalC = cats.reduce((a, c) => a + c[mc.k], 0);
  const mob = isMobile(); // téléphone : légende sous le donut, donut centré
  const legendPos = mob ? { orient: 'horizontal', left: 'center', bottom: 0 } : { orient: 'vertical', right: 0, top: 'middle' };
  const center = mob ? ['50%', '42%'] : ['36%', '50%'];
  chart('c-cats')?.setOption({
    textStyle: font,
    tooltip: { ...th.tooltip, trigger: 'item', confine: true, formatter: (p) => `<b>${esc(p.name)}</b><br>${mc.f(p.value)} · ${p.percent.toLocaleString('fr-FR')} %` },
    legend: { type: 'scroll', ...legendPos, textStyle: { color: th.text2, fontSize: 12 }, icon: 'roundRect', itemWidth: 10, itemHeight: 10 },
    series: [{
      type: 'pie', radius: mob ? ['40%', '60%'] : ['52%', '76%'], center, avoidLabelOverlap: true,
      itemStyle: { borderColor: th.surface, borderWidth: 2, borderRadius: 4 },
      label: { color: th.text2, fontSize: 11, formatter: (p) => (p.percent >= 6 ? `${p.percent.toFixed(0)} %` : '') },
      labelLine: { show: false },
      data: cats.map((c) => ({ name: CAT[c.id].label, value: c[mc.k], itemStyle: { color: catColor(c.id) } })),
    }],
    graphic: [{ type: 'text', left: center[0], top: center[1], style: { text: mc.f(totalC), fill: th.text, fontSize: 15, fontWeight: 600, textAlign: 'center', textVerticalAlign: 'middle' } }],
  }, true);

  // Top 20 extensions : barres horizontales colorées par la catégorie de l'extension
  const me = metric('exts');
  const exts = (mode.exts === 'bytes' ? data.extensions_by_size : data.extensions_by_count) || [];
  const rev = [...exts].reverse();
  chart('c-exts')?.setOption({
    textStyle: font,
    grid: { left: 70, right: 80, top: 6, bottom: 20 },
    tooltip: { ...th.tooltip, trigger: 'item', confine: true, formatter: (p) => { const x = rev[p.dataIndex]; return `<b>.${esc(x.ext)}</b> — ${esc(CAT[x.category]?.label || '')}<br>${fmtInt(x.files)} fichiers · ${fmtBytes(x.bytes)}`; } },
    xAxis: { type: 'value', ...th.axis({ axisLabel: { color: th.muted, fontSize: 11, formatter: (v) => me.f(v) } }) },
    yAxis: { type: 'category', data: rev.map((x) => `.${x.ext}`), ...th.axis({ splitLine: { show: false } }) },
    series: [{
      type: 'bar', barMaxWidth: 14, barCategoryGap: '30%',
      data: rev.map((x) => ({ value: x[me.k], itemStyle: { color: catColor(x.category), borderRadius: [0, 4, 4, 0] } })),
      label: { show: true, position: 'right', color: th.text2, fontSize: 11, formatter: (p) => me.f(p.value) },
    }],
  }, true);

  // Histogramme des tailles (tranches logarithmiques)
  const ms = metric('sizes');
  const hist = data.size_histogram || [];
  barChart('c-sizes', hist.map((b) => b.label), hist.map((b) => b[ms.k]), ms.f, th, font,
    (i) => `<b>${esc(hist[i].label)}</b><br>${fmtInt(hist[i].files)} fichiers · ${fmtBytes(hist[i].bytes)}`);

  // Années
  const my = metric('years');
  const years = data.years || [];
  barChart('c-years', years.map((y) => String(y.year)), years.map((y) => y[my.k]), my.f, th, font,
    (i) => `<b>${years[i].year}</b><br>${fmtInt(years[i].files)} fichiers · ${fmtBytes(years[i].bytes)}`);

  // Ancienneté
  const age = data.age || [];
  barChart('c-age', age.map((a) => a.label), age.map((a) => a.bytes), fmtBytes, th, font,
    (i) => `<b>${esc(age[i].label)}</b><br>${fmtInt(age[i].files)} fichiers · ${fmtBytes(age[i].bytes)}`);

  // Évolution : deux graphiques distincts (jamais de double axe)
  lineChart('c-hist-bytes', history.map((h) => h.date), history.map((h) => h.bytes), fmtBytes, th, font);
  lineChart('c-hist-files', history.map((h) => h.date), history.map((h) => h.files), fmtInt, th, font);
}

function barChart(id, labels, values, fmt, th, font, tip) {
  chart(id)?.setOption({
    textStyle: font,
    grid: { left: 64, right: 12, top: 16, bottom: 48 },
    tooltip: { ...th.tooltip, trigger: 'axis', confine: true, axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(127,127,127,.08)' } }, formatter: (ps) => tip(ps[0].dataIndex) },
    xAxis: { type: 'category', data: labels, ...th.axis({ splitLine: { show: false }, axisLabel: { color: th.muted, fontSize: 11, rotate: labels.length > 8 ? 35 : 0, interval: 0 } }) },
    yAxis: { type: 'value', ...th.axis({ axisLabel: { color: th.muted, fontSize: 11, formatter: (v) => fmt(v) } }) },
    series: [{ type: 'bar', data: values, barMaxWidth: 28, itemStyle: { color: th.series, borderRadius: [4, 4, 0, 0] } }],
  }, true);
}

function lineChart(id, dates, values, fmt, th, font) {
  const c = chart(id);
  if (!c) return;
  if (dates.length < 2) {
    c.setOption({ graphic: [{ type: 'text', left: 'center', top: 'middle', style: { text: 'Historique insuffisant (au moins 2 scans)', fill: th.muted, fontSize: 12 } }] }, true);
    return;
  }
  c.setOption({
    textStyle: font,
    grid: { left: 70, right: 16, top: 10, bottom: 26 },
    tooltip: { ...th.tooltip, trigger: 'axis', confine: true, axisPointer: { type: 'line', lineStyle: { color: th.muted } }, formatter: (ps) => `${fmtDateTime(dates[ps[0].dataIndex])}<br><b>${fmt(ps[0].value)}</b>` },
    xAxis: { type: 'category', boundaryGap: false, data: dates, ...th.axis({ splitLine: { show: false }, axisLabel: { color: th.muted, fontSize: 11, formatter: (v) => fmtDateTime(v).split(' ')[0] } }) },
    yAxis: { type: 'value', scale: true, ...th.axis({ axisLabel: { color: th.muted, fontSize: 11, formatter: (v) => fmt(v) } }) },
    series: [{ type: 'line', data: values, showSymbol: dates.length < 40, symbolSize: 8, lineStyle: { width: 2, color: th.series }, itemStyle: { color: th.series } }],
  }, true);
}

