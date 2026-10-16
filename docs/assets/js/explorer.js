// Dataset explorer: filterable gallery of validation layouts and a detail viewer.
import { segmented } from './main.js';
import { ABBREVIATIONS, layoutFrame, legendHTML, legendSwatch, renderLayout } from './render.js';
import { centroid, edges, segLength } from './geometry.js';

const FILTER_CLASSES = ['Car Service Area', 'Bike Service Area', 'HGV Service Area', 'EV Service Area', 'Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking'];
const PAGE = 12;
const FIXED = new Set(['Layout Polygon', 'Entrance', 'Exit']);

// Extents along the longest edge and its normal (robust to repeated vertices).
const sizeOf = (poly) => {
  const a = (angleOf(poly) * Math.PI) / 180;
  const u = [Math.cos(a), Math.sin(a)], v = [-u[1], u[0]];
  const pu = poly.map((p) => p[0] * u[0] + p[1] * u[1]), pv = poly.map((p) => p[0] * v[0] + p[1] * v[1]);
  return [Math.max(...pv) - Math.min(...pv), Math.max(...pu) - Math.min(...pu)].sort((x, y) => x - y);
};

function angleOf(poly) {
  let best = null;
  for (const e of edges(poly)) if (!best || segLength(e) > segLength(best)) best = e;
  const a = (Math.atan2(best[1][1] - best[0][1], best[1][0] - best[0][0]) * 180) / Math.PI;
  return ((a % 180) + 180) % 180;
}

export function initExplorer(samples, openInPlayground) {
  const gallery = document.getElementById('gallery');
  const filterEl = document.getElementById('class-filter');
  const sortEl = document.getElementById('explorer-sort');
  const countEl = document.getElementById('explorer-count');
  const moreBtn = document.getElementById('gallery-more');
  const active = new Set();
  let list = [];
  let shown = 0;

  filterEl.innerHTML = FILTER_CLASSES.map((c) => `<button aria-pressed="false" data-class="${c}">${legendSwatch(c)}${ABBREVIATIONS[c]}</button>`).join('');
  filterEl.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const on = b.getAttribute('aria-pressed') !== 'true';
    b.setAttribute('aria-pressed', on);
    if (on) active.add(b.dataset.class); else active.delete(b.dataset.class);
    refresh();
  });
  sortEl.addEventListener('change', refresh);

  const lazy = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      lazy.unobserve(en.target);
      const s = samples[Number(en.target.dataset.index)];
      renderLayout(en.target.querySelector('svg'), s.elements, { lineScale: 0.8 });
    }
  }, { rootMargin: '200px' });

  function tile(s, i) {
    const b = document.createElement('button');
    b.className = 'tile';
    b.dataset.index = s.index;
    b.style.animationDelay = `${(i % PAGE) * 25}ms`;
    b.setAttribute('aria-label', `Open layout ${s.id.slice(0, 8)}`);
    b.innerHTML = `<div class="tile-figure"><svg aria-hidden="true"></svg></div>
      <div class="tile-meta"><span class="mono">${s.id.slice(0, 8)}</span>
      <span>${s.n} el. · ${Math.round(s.area).toLocaleString('en-US')} m²</span>
      <span title="Complexity ${s.complexity}">cx<span class="cx-bar"><i style="width:${s.complexity}%"></i></span></span></div>`;
    b.addEventListener('click', () => openViewer(s));
    lazy.observe(b);
    return b;
  }

  function refresh() {
    const [key, dir] = sortEl.value.split('-');
    list = samples.filter((s) => [...active].every((c) => s.elements.some((e) => e.class === c)))
      .sort((a, b) => (dir === 'asc' ? a[key] - b[key] : b[key] - a[key]));
    gallery.innerHTML = '';
    shown = 0;
    countEl.textContent = `${list.length} layout${list.length === 1 ? '' : 's'}`;
    more();
  }

  function more() {
    const frag = document.createDocumentFragment();
    list.slice(shown, shown + PAGE).forEach((s, i) => frag.appendChild(tile(s, shown + i)));
    gallery.appendChild(frag);
    shown = Math.min(list.length, shown + PAGE);
    moreBtn.hidden = shown >= list.length;
  }
  moreBtn.addEventListener('click', more);
  refresh();

  // ----- viewer -----
  const dialog = document.getElementById('viewer');
  const vsvg = document.getElementById('viewer-svg');
  const hover = document.getElementById('viewer-hover');
  const inventory = document.getElementById('viewer-inventory');
  const legendEl = document.getElementById('viewer-legend');
  let current = null;
  let mode = 'gt';
  const hidden = new Set();

  function draw() {
    const els = mode === 'gt' ? current.elements : current.elements.filter((e) => FIXED.has(e.class));
    const handle = renderLayout(vsvg, els, { lineScale: 1.3, frame: layoutFrame(current.elements) });
    for (const node of handle.nodes.values()) {
      const off = hidden.has(node.element.class);
      node.g.classList.toggle('is-hidden', off);
      node.label?.classList.toggle('is-hidden', off);
    }
    inventory.hidden = mode === 'gt';
    if (mode === 'input') {
      const groups = new Map();
      for (const e of current.elements) {
        if (FIXED.has(e.class)) continue;
        const [w, h] = sizeOf(e.polygon);
        const key = `${e.class}|${w.toFixed(1)}×${h.toFixed(1)}`;
        groups.set(key, (groups.get(key) || 0) + 1);
      }
      inventory.innerHTML = `<b class="small" style="width:100%">Elements to place (ℰ): fixed shapes and counts</b>` +
        [...groups].map(([k, n]) => {
          const [cls, sz] = k.split('|');
          return `<span class="inv-item">${legendSwatch(cls)}${ABBREVIATIONS[cls]} ×${n} <span class="mono">${sz} m</span></span>`;
        }).join('');
    }
  }

  function openViewer(s) {
    current = s;
    hidden.clear();
    document.getElementById('viewer-title').textContent = s.id;
    const counts = {};
    for (const e of s.elements) counts[e.class] = (counts[e.class] || 0) + 1;
    legendEl.innerHTML = legendHTML(s.elements, { interactive: true });
    legendEl.querySelectorAll('.legend-row[data-class]').forEach((row) => {
      row.insertAdjacentHTML('beforeend', `<span class="muted mono" style="margin-left:auto">×${counts[row.dataset.class]}</span>`);
    });
    document.getElementById('viewer-facts').innerHTML = [
      ['Elements', s.n], ['Premise', `${Math.round(s.area).toLocaleString('en-US')} m²`],
      ['Utilisation', `${(s.utilization * 100).toFixed(1)}%`], ['Complexity', s.complexity.toFixed(0)],
      ['Entrances', counts.Entrance || 0], ['Exits', counts.Exit || 0],
    ].map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
    dialog.showModal();
    draw();
  }

  legendEl.addEventListener('click', (e) => {
    const row = e.target.closest('.legend-row[data-class]');
    if (!row) return;
    const cls = row.dataset.class;
    if (hidden.has(cls)) hidden.delete(cls); else hidden.add(cls);
    row.classList.toggle('is-off', hidden.has(cls));
    draw();
  });
  segmented(document.getElementById('viewer-mode'), (b) => { mode = b.dataset.mode; draw(); });
  document.getElementById('viewer-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  document.getElementById('viewer-play').addEventListener('click', () => {
    dialog.close();
    openInPlayground(current);
    document.getElementById('playground').scrollIntoView({ behavior: 'smooth' });
  });

  vsvg.addEventListener('pointermove', (e) => {
    const g = e.target.closest('.elem');
    if (!g) { hover.hidden = true; return; }
    const el = current.elements[Number(g.dataset.id)];
    const [w, h] = sizeOf(el.polygon);
    const [cx, cy] = centroid(el.polygon);
    const r = vsvg.parentElement.getBoundingClientRect();
    hover.hidden = false;
    hover.style.left = `${e.clientX - r.left}px`;
    hover.style.top = `${e.clientY - r.top}px`;
    hover.innerHTML = `<b>${el.class}</b> #${el.id}<br>${w.toFixed(2)} × ${h.toFixed(2)} m · θ ${angleOf(el.polygon).toFixed(1)}°<br>centre (${cx.toFixed(1)}, ${cy.toFixed(1)})`;
  });
  vsvg.addEventListener('pointerleave', () => { hover.hidden = true; });
}
