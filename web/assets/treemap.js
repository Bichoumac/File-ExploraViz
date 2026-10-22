// Page Treemap façon WinDirStat : squarified + rendu Canvas, coussins optionnels.
import { CAT, CATS, api, catColor, cssVar, esc, fmtBytes, fmtDate, fmtInt, hashFor, icon, pct, t, toast } from './util.js';

const MIN_AREA = 10; // px² (CSS) : en dessous, les éléments sont regroupés en « autres »
const CUSHION = { H: 0.4, F: 0.9, Ia: 0.2, Is: 0.85, L: normalize3([-1, -1, 10]) };

let el; let P = {}; let roots = []; let tree = null;
let rects = []; let tops = []; let hover = null; let hoverTop = -1;
let hidden = new Set(); let cushion = readPref('fev-cushion', '1') === '1';
let base = null; let ro = null; let clickTimer = null; let ctrl = null;
let canvas; let tip; let pointer = 'mouse'; // type du dernier pointeur (souris, doigt, stylet)

function readPref(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } }
function writePref(k, v) { try { localStorage.setItem(k, v); } catch { /* ignoré */ } }
function normalize3(v) { const n = Math.hypot(...v); return v.map((x) => x / n); }

// ---------------------------------------------------------------- cycle de vie

export async function mount(container, params) {
  el = container;
  el.innerHTML = `
    <div class="tm">
      <div class="card tm-main">
        <div class="tm-tools">
          <nav class="crumbs" id="tm-crumbs" aria-label="${t('common.breadcrumb')}"></nav>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select id="tm-root" aria-label="${t('ex.root')}"></select>
            <label class="check small"><input type="checkbox" id="tm-cushion"> ${t('tm.cushion')}</label>
            <a class="btn small" id="tm-open">${icon('folder')} ${t('nav.explorer')}</a>
          </div>
        </div>
        <div class="tm-canvas-wrap" id="tm-wrap"><canvas id="tm-canvas" role="img" aria-label="${t('tm.canvas')}"></canvas></div>
        <div class="tm-legend" id="tm-legend" role="group" aria-label="${t('tm.legendLabel')}"></div>
      </div>
      <aside class="card tm-side">
        <div class="card-h"><h2>${t('tm.subdirs')}</h2><span class="muted small" id="tm-total"></span></div>
        <div class="list" id="tm-list"></div>
      </aside>
    </div>`;
  tip = document.createElement('div');
  tip.className = 'tip hidden';
  document.body.appendChild(tip);
  canvas = el.querySelector('#tm-canvas');
  el.querySelector('#tm-cushion').checked = cushion;
  bind();
  ro = new ResizeObserver(() => draw());
  ro.observe(el.querySelector('#tm-wrap'));
  window.addEventListener('theme-changed', draw);
  window.addEventListener('data-changed', reload);
  roots = (await api('/api/roots').catch(() => [])).filter((r) => r.indexed || r.files);
  await update(params);
}

export function unmount() {
  ro?.disconnect();
  tip?.remove();
  ctrl?.abort();
  window.removeEventListener('theme-changed', draw);
  window.removeEventListener('data-changed', reload);
}

export async function update(params) {
  P = { ...params };
  if (!P.root && roots.length) P.root = roots[0].id;
  const sel = el.querySelector('#tm-root');
  sel.innerHTML = roots.map((r) => `<option value="${esc(r.id)}" ${r.id === P.root ? 'selected' : ''}>${esc(r.name)}</option>`).join('');
  await reload();
}

async function reload() {
  if (!P.root) {
    tree = null;
    renderChrome();
    draw();
    return;
  }
  ctrl?.abort();
  ctrl = new AbortController();
  try {
    tree = await api('/api/treemap', { params: { root: P.root, path: P.path, depth: 3 }, signal: ctrl.signal });
  } catch (ex) {
    if (ex.name === 'AbortError') return;
    tree = null;
    toast(ex.message, 'error');
  }
  renderChrome();
  draw();
}

// ---------------------------------------------------------------- interactions

function bind() {
  el.querySelector('#tm-root').addEventListener('change', (e) => { location.hash = hashFor('treemap', { root: e.target.value }); });
  el.querySelector('#tm-cushion').addEventListener('change', (e) => {
    cushion = e.target.checked;
    writePref('fev-cushion', cushion ? '1' : '0');
    draw();
  });
  el.querySelector('#tm-legend').addEventListener('click', (e) => {
    const b = e.target.closest('[data-cat]');
    if (!b) return;
    hidden.has(b.dataset.cat) ? hidden.delete(b.dataset.cat) : hidden.add(b.dataset.cat);
    renderChrome();
    draw();
  });
  const list = el.querySelector('#tm-list');
  list.addEventListener('click', (e) => {
    const it = e.target.closest('[data-path]');
    if (it) zoom(it.dataset.path);
  });
  list.addEventListener('mouseover', (e) => {
    const it = e.target.closest('[data-top]');
    if (it) { hoverTop = +it.dataset.top; hover = null; overlay(); }
  });
  list.addEventListener('mouseleave', () => { hoverTop = -1; overlay(); });

  canvas.addEventListener('pointerdown', (e) => { pointer = e.pointerType; });
  canvas.addEventListener('pointermove', (e) => { if (e.pointerType === 'mouse') onMove(e); });
  canvas.addEventListener('mouseleave', () => {
    if (pointer !== 'mouse') return;
    hover = null; hoverTop = -1; tip.classList.add('hidden'); overlay(); syncList();
  });
  tip.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    closeSheet();
    if (b.dataset.act === 'zoom') zoom(b.dataset.path);
    else if (b.dataset.act === 'open') location.hash = hashFor('explorer', { root: P.root, path: b.dataset.path, recursive: b.dataset.rec });
  });
  canvas.addEventListener('click', (e) => {
    clearTimeout(clickTimer);
    const hit = hitTest(e);
    if (pointer !== 'mouse') { openSheet(hit); return; } // écran tactile : fiche au lieu du survol
    clickTimer = setTimeout(() => {
      if (!hit) return;
      const top = tops[hit.top];
      if (top && top.node.type === 'dir') zoom(top.node.path);
    }, 230);
  });
  canvas.addEventListener('dblclick', (e) => {
    clearTimeout(clickTimer);
    const hit = hitTest(e);
    if (!hit) return;
    location.hash = hashFor('explorer', { root: P.root, path: hit.dir, recursive: hit.node.type === 'file' ? '0' : '1' });
  });
}

/** Écran tactile : un appui affiche une fiche (infos + actions) en bas de l'écran. */
function openSheet(hit) {
  hover = hit;
  hoverTop = hit ? hit.top : -1;
  overlay();
  syncList();
  if (!hit) { closeSheet(); return; }
  const top = tops[hit.top];
  const zoomPath = top && top.node.type === 'dir' ? top.node.path : null;
  const actions = [
    zoomPath !== null ? `<button class="btn small" type="button" data-act="zoom" data-path="${esc(zoomPath)}">${esc(t('tm.zoomOn', { name: top.node.name }))}</button>` : '',
    `<button class="btn small" type="button" data-act="open" data-path="${esc(hit.dir || '')}" data-rec="${hit.node.type === 'file' ? '0' : '1'}">${icon('folder')} ${t('nav.explorer')}</button>`,
    `<button class="btn small" type="button" data-act="close">${t('common.close')}</button>`,
  ].join('');
  tip.innerHTML = `${tooltip(hit)}<div class="sheet-actions">${actions}</div>`;
  tip.classList.add('sheet');
  tip.classList.remove('hidden');
  if (!matchMedia('(max-width: 760px)').matches) {
    // Tablette : fiche près du doigt, contenue dans l'écran
    const r = canvas.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(innerWidth - tip.offsetWidth - 8, r.left + hit.x + hit.w / 2 - tip.offsetWidth / 2))}px`;
    tip.style.top = `${Math.max(8, Math.min(innerHeight - tip.offsetHeight - 8, r.top + hit.y + hit.h / 2))}px`;
  }
}

function closeSheet() {
  tip.classList.add('hidden');
  tip.classList.remove('sheet');
  hover = null;
  hoverTop = -1;
  overlay();
  syncList();
}

function zoom(path) {
  location.hash = hashFor('treemap', { root: P.root, path });
}

function hitTest(e) {
  const r = canvas.getBoundingClientRect();
  const x = e.clientX - r.left;
  const y = e.clientY - r.top;
  for (let i = rects.length - 1; i >= 0; i -= 1) {
    const q = rects[i];
    if (x >= q.x && x < q.x + q.w && y >= q.y && y < q.y + q.h) return q;
  }
  return null;
}

function onMove(e) {
  const hit = hitTest(e);
  if (hit !== hover) {
    hover = hit;
    hoverTop = hit ? hit.top : -1;
    overlay();
    syncList();
  }
  if (!hit) { tip.classList.add('hidden'); return; }
  tip.innerHTML = tooltip(hit);
  tip.classList.remove('hidden', 'sheet');
  const pad = 14;
  const tw = tip.offsetWidth; const th = tip.offsetHeight;
  let left = e.clientX + pad; let top = e.clientY + pad;
  if (left + tw > innerWidth - 8) left = e.clientX - tw - pad;
  if (top + th > innerHeight - 8) top = e.clientY - th - pad;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${Math.max(8, top)}px`;
}

/** Nom affiché d'un nœud ; les groupes (« N autres … ») sont nommés côté client, dans la langue choisie. */
function nodeName(n) {
  if (n.type === 'group') return t(n.cat ? 'tm.otherFiles' : 'tm.otherDirs', { n: fmtInt(n.count), count: n.count });
  return n.name;
}

function tooltip(q) {
  const n = q.node;
  const full = (q.dir ? `${q.dir}/` : '') + (n.type === 'file' ? n.name : '');
  const share = pct(q.value, q.parentValue);
  const catLine = n.cat ? `<div><span class="dot" style="background:${catColor(n.cat)}"></span> ${esc(CAT[n.cat]?.label || n.cat)}</div>` : '';
  if (n.type === 'file') {
    return `<div class="t">${esc(n.name)}</div>${catLine}
      <div><span class="k">${t('tm.path')}</span> ${esc(full)}</div>
      <div><span class="k">${t('tm.size')}</span> ${fmtBytes(n.size)} · ${t('tm.ofFolder', { pct: share })}</div>
      <div><span class="k">${t('tm.modified')}</span> ${fmtDate(n.mtime)}</div>`;
  }
  if (n.type === 'cat') {
    return `<div class="t">${esc(q.dirName)}</div>${catLine}
      <div><span class="k">${t('tm.folder')}</span> ${esc(q.dir || '/')}</div>
      <div><span class="k">${t('tm.size')}</span> ${fmtBytes(q.value)} · ${t('tm.ofFolder', { pct: share })}</div>
      <div class="k">${pointer === 'mouse' ? t('tm.clickZoom') : t('tm.tapZoom')}</div>`;
  }
  return `<div class="t">${esc(nodeName(n))}</div>${catLine}
    <div><span class="k">${t('tm.in')}</span> ${esc(q.dir || '/')}</div>
    <div><span class="k">${t('tm.size')}</span> ${fmtBytes(q.value)} · ${t('tm.ofParent', { pct: share })}</div>
    ${n.count ? `<div><span class="k">${t('tm.items')}</span> ${fmtInt(n.count)}</div>` : ''}`;
}

// ---------------------------------------------------------------- chrome (fil d'Ariane, légende, liste)

function effective(n) {
  if (n.type === 'file' || (n.type === 'group' && n.cat)) return hidden.has(n.cat) ? 0 : n.size;
  if (n.type === 'cat') return hidden.has(n.cat) ? 0 : n.size;
  if (n.cats) return Object.entries(n.cats).reduce((a, [c, v]) => a + (hidden.has(c) ? 0 : v), 0);
  return n.size || 0;
}

function renderChrome() {
  const crumbs = el.querySelector('#tm-crumbs');
  const r = roots.find((x) => x.id === P.root);
  const segs = (P.path || '').split('/').filter(Boolean);
  const rootName = r?.name || P.root || '';
  const parts = [segs.length ? `<a href="${esc(hashFor('treemap', { root: P.root }))}">${esc(rootName)}</a>` : `<span class="cur">${esc(rootName)}</span>`];
  segs.forEach((s, i) => {
    const p = segs.slice(0, i + 1).join('/');
    parts.push(i === segs.length - 1 ? `<span class="cur">${esc(s)}</span>` : `<a href="${esc(hashFor('treemap', { root: P.root, path: p }))}">${esc(s)}</a>`);
  });
  crumbs.innerHTML = parts.join('<span class="sep">›</span>');
  el.querySelector('#tm-open').href = hashFor('explorer', { root: P.root, path: P.path });

  const cats = tree?.cats || {};
  el.querySelector('#tm-legend').innerHTML = CATS.filter((c) => cats[c.id]).map((c) => `
    <button type="button" class="legend-item${hidden.has(c.id) ? ' off' : ''}" data-cat="${c.id}" aria-pressed="${!hidden.has(c.id)}"
      title="${esc(t(hidden.has(c.id) ? 'tm.show' : 'tm.hide', { label: c.label }))}">
      <span class="dot" style="background:${catColor(c.id)}"></span>${esc(c.label)} <span class="muted">${fmtBytes(cats[c.id])}</span>
    </button>`).join('') || `<span class="muted small">${t('tm.emptyFolder')}</span>`;
  el.querySelector('#tm-total').textContent = tree ? t('tm.total', { size: fmtBytes(effective(tree)), n: fmtInt(tree.files), count: tree.files }) : '';
}

function renderList() {
  const list = el.querySelector('#tm-list');
  const total = tree ? effective(tree) : 0;
  const items = tops.map((top, i) => ({ top, i })).filter(({ top }) => top.node.type === 'dir' || (top.node.type === 'group' && !top.node.cat));
  items.sort((a, b) => b.top.value - a.top.value);
  list.innerHTML = items.length ? items.map(({ top, i }) => `
    <div class="item" data-top="${i}" ${top.node.type === 'dir' ? `data-path="${esc(top.node.path)}"` : ''} title="${esc(top.node.path || nodeName(top.node))}">
      <span class="n">${top.node.type === 'dir' ? icon('folder') : ''} ${esc(nodeName(top.node))}</span>
      <span class="s">${fmtBytes(top.value)} · ${pct(top.value, total)}</span>
      <span class="bar"><div style="width:${total ? (100 * top.value) / total : 0}%"></div></span>
    </div>`).join('') : `<div class="empty small">${t('tm.noSubdirs')}</div>`;
}

function syncList() {
  el.querySelectorAll('#tm-list .item').forEach((it) => it.classList.toggle('hl', +it.dataset.top === hoverTop));
  const cur = el.querySelector('#tm-list .item.hl');
  cur?.scrollIntoView({ block: 'nearest' });
}

// ---------------------------------------------------------------- disposition (squarified)

function worst(sum, min, max, side) {
  const s2 = side * side; const sum2 = sum * sum;
  return Math.max((s2 * max) / sum2, sum2 / (s2 * min));
}

/** Algorithme squarified (Bruls, Huizing, van Wijk) : items triés par valeur décroissante. */
function squarify(items, x, y, w, h) {
  const out = [];
  const total = items.reduce((a, i) => a + i.value, 0);
  if (total <= 0 || w <= 0 || h <= 0) return out;
  const scale = (w * h) / total;
  let i = 0;
  while (i < items.length) {
    const side = Math.min(w, h);
    let j = i; let sum = 0; let min = Infinity; let max = 0; let prev = Infinity;
    while (j < items.length) {
      const a = items[j].value * scale;
      const nsum = sum + a; const nmin = Math.min(min, a); const nmax = Math.max(max, a);
      const wst = worst(nsum, nmin, nmax, side);
      if (wst > prev) break;
      sum = nsum; min = nmin; max = nmax; prev = wst; j += 1;
    }
    if (w >= h) {
      const rw = sum / h; let cy = y;
      for (let k = i; k < j; k += 1) { const rh = (items[k].value * scale) / rw; out.push({ item: items[k], x, y: cy, w: rw, h: rh }); cy += rh; }
      x += rw; w -= rw;
    } else {
      const rh = sum / w; let cx = x;
      for (let k = i; k < j; k += 1) { const rw = (items[k].value * scale) / rh; out.push({ item: items[k], x: cx, y, w: rw, h: rh }); cx += rw; }
      y += rh; h -= rh;
    }
    i = j;
  }
  return out;
}

function childrenOf(n) {
  if (n.type === 'dir' && Array.isArray(n.children)) return n.children.map((c) => ({ node: c, value: effective(c) }));
  if (n.cats && (n.type === 'dir' || n.type === 'group')) {
    // Dossier non développé : blocs par catégorie (chargés en détail au zoom)
    return Object.entries(n.cats).map(([cat, size]) => ({ node: { type: 'cat', cat, size, name: CAT[cat]?.label || cat }, value: hidden.has(cat) ? 0 : size }));
  }
  return null;
}

/** Regroupe les éléments trop petits pour être visibles dans un bloc « autres ». */
function mergeSmall(items, area) {
  const total = items.reduce((a, i) => a + i.value, 0);
  if (!total) return [];
  const scale = area / total;
  const keep = []; let rest = 0; let count = 0;
  for (const it of items) {
    if (it.value <= 0) continue;
    if (it.value * scale < MIN_AREA) { rest += it.value; count += it.node.count || 1; } else keep.push(it);
  }
  if (rest > 0) keep.push({ node: { type: 'others', name: t('tm.tooSmall', { n: fmtInt(count), count }), size: rest, count }, value: rest });
  return keep.sort((a, b) => b.value - a.value);
}

function layout(width, height) {
  rects = []; tops = [];
  if (!tree) return;
  const rootVal = effective(tree);
  const walk = (node, x, y, w, h, depth, dir, dirName, topIndex, surface, parentValue, value) => {
    const kids = childrenOf(node);
    if (!kids || w < 2 || h < 2) {
      rects.push({ x, y, w, h, node, dir, dirName, top: topIndex, value, parentValue, surface, color: colorOf(node) });
      return;
    }
    const items = mergeSmall(kids, w * h).filter((k) => k.value > 0);
    if (!items.length) {
      rects.push({ x, y, w, h, node, dir, dirName, top: topIndex, value, parentValue, surface, color: colorOf(node) });
      return;
    }
    const childDir = node.type === 'dir' ? node.path : dir;
    const childDirName = node.type === 'dir' ? node.name : dirName;
    for (const r of squarify(items, x, y, w, h)) {
      const s = addRidges(surface, r.x, r.y, r.w, r.h, CUSHION.H * CUSHION.F ** depth);
      let t = topIndex;
      if (depth === 0) { t = tops.length; tops.push({ node: r.item.node, value: r.item.value, x: r.x, y: r.y, w: r.w, h: r.h }); }
      walk(r.item.node, r.x, r.y, r.w, r.h, depth + 1, childDir, childDirName, t, s, value, r.item.value);
    }
  };
  walk(tree, 0, 0, width, height, 0, tree.path, tree.name, -1, [0, 0, 0, 0], rootVal, rootVal);
}

function addRidges(s, x, y, w, h, hgt) {
  const out = s.slice();
  if (w > 0) { out[0] += (4 * hgt * (2 * x + w)) / w; out[1] -= (4 * hgt) / w; }
  if (h > 0) { out[2] += (4 * hgt * (2 * y + h)) / h; out[3] -= (4 * hgt) / h; }
  return out;
}

function colorOf(n) {
  if (n.cat) return catColor(n.cat);
  if (n.type === 'others' || n.type === 'group') return cssVar('--c-dir');
  return cssVar('--c-dir');
}

// ---------------------------------------------------------------- rendu

function hexToRgb(c) {
  const m = /^#?([0-9a-f]{6})$/i.exec(c.trim());
  if (!m) return [128, 128, 128];
  const v = parseInt(m[1], 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function draw() {
  if (!canvas || !el.isConnected) return;
  const wrap = el.querySelector('#tm-wrap');
  const w = wrap.clientWidth; const h = wrap.clientHeight;
  if (!w || !h) return;
  const dpr = Math.min(window.devicePixelRatio || 1, cushion ? 1.5 : 2);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  layout(w, h);
  renderList();
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = cssVar('--surface');
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!tree || !rects.length) {
    ctx.fillStyle = cssVar('--muted');
    ctx.font = '13px system-ui, sans-serif';
    const msg = tree ? t('tm.nothing') : P.root ? t('common.loading') : t('tm.noRoot');
    ctx.fillText(msg, 16 * dpr, 24 * dpr);
    base = null;
    return;
  }
  if (cushion) drawCushions(ctx, dpr);
  else drawFlat(ctx, dpr);
  // Contour léger des blocs de premier niveau pour lire la hiérarchie
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.strokeStyle = cssVar('--surface');
  ctx.lineWidth = 1.5;
  for (const t of tops) ctx.strokeRect(t.x, t.y, t.w, t.h);
  drawLabels(ctx);
  base = ctx.getImageData(0, 0, canvas.width, canvas.height);
  overlay();
}

function drawFlat(ctx, dpr) {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const gap = cssVar('--surface');
  for (const r of rects) {
    ctx.fillStyle = r.color;
    ctx.fillRect(r.x, r.y, r.w, r.h);
    if (r.w > 3 && r.h > 3) {
      ctx.strokeStyle = gap;
      ctx.lineWidth = 1;
      ctx.strokeRect(r.x + 0.5, r.y + 0.5, r.w - 1, r.h - 1);
    }
  }
}

/** Ombrage en coussin (van Wijk & van de Wetering), pixel par pixel. */
function drawCushions(ctx, dpr) {
  const W = canvas.width; const H = canvas.height;
  const img = ctx.createImageData(W, H);
  const px = img.data;
  const [Lx, Ly, Lz] = CUSHION.L;
  const { Ia, Is } = CUSHION;
  for (const r of rects) {
    const [cr, cg, cb] = hexToRgb(r.color);
    const [s1, s2, s3, s4] = r.surface;
    const x0 = Math.max(0, Math.round(r.x * dpr)); const x1 = Math.min(W, Math.round((r.x + r.w) * dpr));
    const y0 = Math.max(0, Math.round(r.y * dpr)); const y1 = Math.min(H, Math.round((r.y + r.h) * dpr));
    for (let yy = y0; yy < y1; yy += 1) {
      const cy = (yy + 0.5) / dpr;
      const ny = -(2 * s4 * cy + s3);
      let o = (yy * W + x0) * 4;
      for (let xx = x0; xx < x1; xx += 1) {
        const cx = (xx + 0.5) / dpr;
        const nx = -(2 * s2 * cx + s1);
        const cosa = (nx * Lx + ny * Ly + Lz) / Math.sqrt(nx * nx + ny * ny + 1);
        const k = Ia + Is * Math.max(0, cosa);
        px[o] = Math.min(255, cr * k); px[o + 1] = Math.min(255, cg * k); px[o + 2] = Math.min(255, cb * k); px[o + 3] = 255;
        o += 4;
      }
    }
  }
  ctx.putImageData(img, 0, 0);
}

function drawLabels(ctx) {
  ctx.font = '600 12px system-ui, sans-serif';
  ctx.textBaseline = 'top';
  for (const t of tops) {
    if (t.w < 70 || t.h < 22 || t.node.type !== 'dir') continue;
    const label = `${t.node.name} · ${fmtBytes(t.value)}`;
    const tw = Math.min(ctx.measureText(label).width + 10, t.w - 6);
    ctx.fillStyle = 'rgba(0,0,0,.55)';
    ctx.beginPath();
    ctx.roundRect(t.x + 3, t.y + 3, tw, 18, 4);
    ctx.fill();
    ctx.save();
    ctx.beginPath();
    ctx.rect(t.x + 3, t.y + 3, tw, 18);
    ctx.clip();
    ctx.fillStyle = '#fff';
    ctx.fillText(label, t.x + 8, t.y + 6);
    ctx.restore();
  }
}

function overlay() {
  if (!base) return;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.putImageData(base, 0, 0);
  const dpr = canvas.width / (el.querySelector('#tm-wrap').clientWidth || 1);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const ink = cssVar('--text');
  if (hoverTop >= 0 && tops[hoverTop]) {
    const t = tops[hoverTop];
    ctx.strokeStyle = ink;
    ctx.lineWidth = 2;
    ctx.strokeRect(t.x + 1, t.y + 1, t.w - 2, t.h - 2);
  }
  if (hover) {
    ctx.strokeStyle = cssVar('--surface');
    ctx.lineWidth = 3;
    ctx.strokeRect(hover.x + 1.5, hover.y + 1.5, hover.w - 3, hover.h - 3);
    ctx.strokeStyle = ink;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(hover.x + 1.5, hover.y + 1.5, hover.w - 3, hover.h - 3);
  }
}
