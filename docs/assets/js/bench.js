// ALPS-Bench section: anatomy figure, class statistics, design-manual visuals, vehicles.
import { segmented } from './main.js';
import { ABBREVIATIONS, CLASS_COLORS, darken, legendSwatch, renderLayout } from './render.js';
import { edges, segLength } from './geometry.js';

const GROUPS = [
  { name: 'Site premise & access', classes: ['Layout Polygon', 'Entrance', 'Exit'] },
  { name: 'Service infrastructure', classes: ['Car Service Area', 'Bike Service Area', 'HGV Service Area', 'EV Service Area', 'Underground Service Facility', 'Underground Access Point'] },
  { name: 'Building & auxiliary', classes: ['Shop'] },
  { name: 'Parking', classes: ['Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking'] },
];

const DESCRIPTIONS = {
  'Layout Polygon': 'The irregular site premise 𝒫. All elements must lie inside it.',
  Entrance: 'Access point where vehicles enter the site.',
  Exit: 'Access point where vehicles leave the site.',
  'Car Service Area': 'Drive-through service unit for cars.',
  'Bike Service Area': 'Service unit sized for bikes.',
  'HGV Service Area': 'Service unit for heavy goods vehicles. Must be at least 6 m from car and bike service areas.',
  'EV Service Area': 'Electric-vehicle charging bay.',
  'Underground Service Facility': 'Facility below grade. It can lie under surface elements and does not block routes.',
  'Underground Access Point': 'Surface hatch to the underground facilities. Stacked on the site boundary.',
  Shop: 'Retail building. Car and priority parking stack against it.',
  'Car Parking': 'Car parking bay. Bays form strict single lines.',
  'Bike Parking': 'Bike parking bay, stacked on the boundary.',
  'Priority Parking': 'Priority bay. Stacked on the Shop, closest to the Entrance.',
  'HGV Parking': 'Heavy-goods-vehicle bay, stacked on the boundary.',
};

// Paper statistics over all 1,000 layouts (Appendix: dataset statistics).
const PAPER_COVERAGE = {
  'Underground Service Facility': 1, Entrance: 1, Exit: 1, 'Underground Access Point': 0.999, Shop: 0.997,
  'Car Service Area': 0.995, 'Car Parking': 0.955, 'EV Service Area': 0.262, 'Priority Parking': 0.158,
  'Bike Parking': 0.154, 'HGV Parking': 0.138, 'HGV Service Area': 0.102, 'Bike Service Area': 0.086,
};

function dims(poly) {
  let best = null;
  for (const e of edges(poly)) if (!best || segLength(e) > segLength(best)) best = e;
  const a = Math.atan2(best[1][1] - best[0][1], best[1][0] - best[0][0]);
  const u = [Math.cos(a), Math.sin(a)], v = [-u[1], u[0]];
  const ext = (d) => { const p = poly.map((q) => q[0] * d[0] + q[1] * d[1]); return Math.max(...p) - Math.min(...p); };
  const [s, l] = [ext(v), ext(u)].sort((x, y) => x - y);
  return `${s.toFixed(1)} × ${l.toFixed(1)} m`;
}

function initAnatomy(samples) {
  const svg = document.getElementById('anatomy-svg');
  const fig = svg.parentElement;
  const tip = document.getElementById('anatomy-tip');
  const pick = [...samples]
    .filter((s) => s.n <= 45)
    .sort((a, b) => new Set(b.elements.map((e) => e.class)).size - new Set(a.elements.map((e) => e.class)).size || b.complexity - a.complexity)[0];
  const handle = renderLayout(svg, pick.elements, { lineScale: 1.2 });
  const present = new Set(pick.elements.map((e) => e.class));

  const groupsEl = document.getElementById('anatomy-groups');
  groupsEl.innerHTML = GROUPS.map((g) => `
    <div class="class-group"><h4>${g.name}</h4><div class="class-chips">
      ${g.classes.map((c) => `<span class="class-chip" data-class="${c}" title="${DESCRIPTIONS[c]}" style="${present.has(c) ? '' : 'opacity:.55'}">
        ${c === 'Layout Polygon' ? '<span class="swatch-line"></span>' : legendSwatch(c)}${c === 'Layout Polygon' ? 'Site boundary' : c}
        <span class="abbr">${c === 'Layout Polygon' ? '𝒫' : ABBREVIATIONS[c]}</span></span>`).join('')}
    </div></div>`).join('');

  const focus = (cls) => {
    fig.classList.toggle('is-focus', !!cls && cls !== 'Layout Polygon');
    for (const node of handle.nodes.values()) node.g.classList.toggle('is-hl', node.element.class === cls);
    groupsEl.querySelectorAll('.class-chip').forEach((c) => c.classList.toggle('is-hl', c.dataset.class === cls));
  };
  groupsEl.addEventListener('pointerover', (e) => { const c = e.target.closest('.class-chip'); if (c) focus(c.dataset.class); });
  groupsEl.addEventListener('pointerleave', () => focus(null));

  svg.addEventListener('pointermove', (e) => {
    const g = e.target.closest('.elem');
    if (!g) { tip.hidden = true; focus(null); return; }
    const node = handle.nodes.get(Number(g.dataset.id));
    const r = fig.getBoundingClientRect();
    tip.hidden = false;
    tip.style.left = `${e.clientX - r.left}px`;
    tip.style.top = `${e.clientY - r.top}px`;
    tip.textContent = `${node.element.class} · ${dims(node.element.polygon)}`;
    focus(node.element.class);
  });
  svg.addEventListener('pointerleave', () => { tip.hidden = true; focus(null); });
}

function initClassChart(stats) {
  const chart = document.getElementById('class-chart');
  const order = Object.keys(PAPER_COVERAGE);
  const draw = (mode) => {
    const rows = order.map((c) => ({ c, v: mode === 'coverage' ? PAPER_COVERAGE[c] : stats.classes[c]?.mean_count ?? 0 }))
      .sort((a, b) => b.v - a.v);
    const max = Math.max(...rows.map((r) => r.v));
    if (!chart.children.length) {
      chart.innerHTML = rows.map((r) => `
        <div class="hbar" data-class="${r.c}">
          <span class="hbar-label">${legendSwatch(r.c)}${r.c}</span>
          <span class="hbar-track"><span class="hbar-fill" style="background:${CLASS_COLORS[r.c]}"></span></span>
          <span class="hbar-val"></span>
        </div>`).join('');
    }
    // Re-order rows with a FLIP animation.
    const before = new Map([...chart.children].map((el) => [el.dataset.class, el.getBoundingClientRect().top]));
    for (const r of rows) chart.appendChild(chart.querySelector(`[data-class="${r.c}"]`));
    for (const el of chart.children) {
      const dy = before.get(el.dataset.class) - el.getBoundingClientRect().top;
      el.animate([{ transform: `translateY(${dy}px)` }, { transform: 'none' }], { duration: 600, easing: 'cubic-bezier(.2,.7,.2,1)' });
    }
    for (const r of rows) {
      const el = chart.querySelector(`[data-class="${r.c}"]`);
      el.querySelector('.hbar-fill').style.width = `${(r.v / max) * 100}%`;
      el.querySelector('.hbar-val').textContent = mode === 'coverage' ? `${(r.v * 100).toFixed(1)}%` : r.v.toFixed(1);
    }
  };
  const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { draw('coverage'); io.disconnect(); } }, { threshold: 0.3 });
  io.observe(chart);
  segmented(document.getElementById('dist-toggle'), (b) => {
    draw(b.dataset.mode);
    document.getElementById('class-chart-note').textContent = b.dataset.mode === 'coverage'
      ? 'Share of all 1,000 layouts that contain each class.'
      : 'Mean number of instances in the development layouts that contain the class.';
  });
}

function initFacts() {
  const facts = [
    ['Layouts', '1,000'],
    ['Semantic classes', '14'],
    ['Premise area', '390 – 19,359 m²'],
    ['Elements / layout', '7 – 101'],
    ['Mean premise area', '3,228 m²'],
    ['Median utilisation', '17.5%'],
  ];
  document.getElementById('facts').innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join('');
}

function initManual() {
  const tabs = document.querySelector('.manual-tabs');
  segmented(tabs, (b) => {
    document.querySelectorAll('.manual-panel').forEach((p) => p.classList.toggle('is-active', p.dataset.panel === b.dataset.tab));
  });
  const c = CLASS_COLORS;
  const r = (x, y, w, h, fill, extra = '') => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}" stroke="${darken(fill)}" stroke-width="1" ${extra}/>`;
  const premise = 'M20 18 L200 12 L260 60 L240 118 L60 122 L14 80 Z';
  const visuals = {
    containment: `<path d="${premise}" fill="#fff" stroke="#2b2b2b" stroke-width="1.6"/>
      <g>${r(0, 0, 44, 26, c.Shop)}<animateTransform attributeName="transform" type="translate" values="215 88; 215 88; 150 60; 150 60; 215 88" keyTimes="0;.15;.5;.85;1" dur="4s" repeatCount="indefinite"/>
      <animate attributeName="opacity" values="1;1;1" dur="4s" repeatCount="indefinite"/></g>
      <text x="136" y="112" font-size="10" fill="#646a74" text-anchor="middle">Ω ⊆ 𝒫</text>`,
    overlap: `<path d="${premise}" fill="#fff" stroke="#2b2b2b" stroke-width="1.6"/>
      ${r(70, 40, 60, 46, c['Car Service Area'])}
      <g>${r(0, 0, 42, 46, c['Car Parking'])}<animateTransform attributeName="transform" type="translate" values="112 40; 112 40; 150 40; 150 40; 112 40" keyTimes="0;.2;.5;.8;1" dur="4s" repeatCount="indefinite"/></g>
      <rect x="112" y="40" width="18" height="46" fill="#c2423a" opacity=".55"><animate attributeName="width" values="18;18;0;0;18" keyTimes="0;.2;.5;.8;1" dur="4s" repeatCount="indefinite"/></rect>`,
    routing: `<path d="${premise}" fill="#fff" stroke="#2b2b2b" stroke-width="1.6"/>
      ${r(70, 26, 40, 50, c.Shop)}${r(150, 70, 50, 26, c['HGV Parking'])}${r(125, 24, 30, 26, c['Car Service Area'])}
      <rect x="16" y="68" width="8" height="18" fill="${c.Entrance}"/><rect x="228" y="95" width="10" height="16" fill="${c.Exit}" transform="rotate(18 233 103)"/>
      <path id="rt-path" d="M24 77 C 50 100, 100 100, 118 88 S 140 52, 140 58 S 170 50, 190 58 S 225 70, 228 100" fill="none" stroke="#3f6c8a" stroke-width="2" stroke-dasharray="4 4"/>
      <circle r="4.5" fill="#3f6c8a"><animateMotion dur="3.6s" repeatCount="indefinite" rotate="auto"><mpath href="#rt-path"/></animateMotion></circle>`,
    composition: [[0, 0], [1, 0], [0, 1], [1, 1]].map(([i, j], k) => {
      const from = [[30, 20], [190, 30], [60, 96], [200, 92]][k];
      const to = [95 + i * 36, 34 + j * 34];
      return `<g>${r(0, 0, 34, 32, c['Car Service Area'])}<animateTransform attributeName="transform" type="translate" values="${from.join(' ')}; ${from.join(' ')}; ${to.join(' ')}; ${to.join(' ')}; ${from.join(' ')}" keyTimes="0;.15;.5;.85;1" dur="4.5s" repeatCount="indefinite"/></g>`;
    }).join('') + '<text x="130" y="122" font-size="10" fill="#646a74" text-anchor="middle">4 → 2×2 block</text>',
    orientation: [0, 1, 2, 3].map((k) => {
      const x = 60 + k * 36;
      const rot = k === 2 ? `<animateTransform attributeName="transform" type="rotate" values="28 ${x + 14} 65; 28 ${x + 14} 65; 0 ${x + 14} 65; 0 ${x + 14} 65; 28 ${x + 14} 65" keyTimes="0;.2;.5;.8;1" dur="4s" repeatCount="indefinite"/>` : '';
      return `<g>${r(x, 40, 28, 50, c['Car Parking'])}${rot}</g>`;
    }).join('') + '<text x="130" y="118" font-size="10" fill="#646a74" text-anchor="middle">parallel or ⟂ within 5°</text>',
    proximity: `${r(40, 20, 180, 34, c.Shop)}
      ${[0, 1, 2, 3].map((k) => `<g>${r(62 + k * 34, 54, 30, 42, c['Car Parking'])}<animateTransform attributeName="transform" type="translate" values="0 ${18 + k * 6}; 0 ${18 + k * 6}; 0 0; 0 0; 0 ${18 + k * 6}" keyTimes="0;.15;.45;.85;1" dur="4.5s" repeatCount="indefinite"/></g>`).join('')}
      <path d="M62 96 A 68 20 0 0 0 198 96 Z" fill="rgba(91,140,90,.15)" stroke="#5b8c5a" stroke-dasharray="3 2"><animate attributeName="opacity" values="0;0;1;1;0" keyTimes="0;.4;.5;.85;1" dur="4.5s" repeatCount="indefinite"/></path>
      <text x="130" y="124" font-size="10" fill="#646a74" text-anchor="middle">stacked on Shop · 3 m egress</text>`,
  };
  document.querySelectorAll('.rule-visual').forEach((el) => {
    el.innerHTML = `<svg viewBox="0 0 270 132" aria-hidden="true">${visuals[el.dataset.visual] || ''}</svg>`;
  });
}

function initVehicles() {
  const V = [
    { name: 'Car', dims: '4.5 × 1.8 m', icon: 'M8 22h48l-6-10H22z M14 26a4 4 0 1 0 0.1 0 M50 26a4 4 0 1 0 0.1 0', seqs: [['CSA'], ['CSA', 'CP'], ['CSA', 'PP'], ['CP'], ['EVSA']] },
    { name: 'Bike', dims: '2.0 × 0.75 m', icon: 'M14 26a7 7 0 1 0 .1 0 M50 26a7 7 0 1 0 .1 0 M14 26l10-12h14l12 12 M30 14l-4-6', seqs: [['BSA'], ['BSA', 'BP'], ['BP']] },
    { name: 'Heavy goods vehicle', dims: '16.5 × 2.55 m', icon: 'M2 8h40v16H2z M42 13h10l6 6v5H42z M10 28a4 4 0 1 0 .1 0 M48 28a4 4 0 1 0 .1 0', seqs: [['HGVS'], ['HGVS', 'HGVP'], ['HGVP']] },
  ];
  const abbrColor = Object.fromEntries(Object.entries(ABBREVIATIONS).map(([k, v]) => [v, CLASS_COLORS[k]]));
  const node = (a, label = a) => `<span class="node" style="background:${abbrColor[a]}40;border:1px solid ${abbrColor[a]}">${label}</span>`;
  const arrow = '<span class="arrow">→</span>';
  document.getElementById('vehicles').innerHTML = V.map((v) => `
    <article class="vehicle">
      <svg class="vehicle-icon" viewBox="0 0 64 34" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="${v.icon}"/></svg>
      <div class="vehicle-head"><h4>${v.name}</h4><span class="vehicle-dims">${v.dims}</span></div>
      ${v.seqs.map((s) => `<div class="route-seq">${[node('ENTRANCE', 'IN'), ...s.map((a) => node(a)), node('EXIT', 'OUT')].join(arrow)}</div>`).join('')}
    </article>`).join('');
}

export function initBenchmark(samples, stats) {
  initAnatomy(samples);
  initClassChart(stats);
  initFacts();
  initManual();
  initVehicles();
}
