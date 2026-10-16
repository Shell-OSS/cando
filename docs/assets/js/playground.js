// Layout playground: edit a validation layout and score it with the in-browser verifier.
import { centroid, edges, intersectionPieces, pointInPolygon, rotatePoint, segLength, round } from './geometry.js';
import { ABBREVIATIONS, el, layoutFrame, legendItems, legendSwatch, pointsAttr, renderLayout, updateElementNode } from './render.js';
import { combineScores, evaluateConstraints, evaluateHardConstraints, evaluateProximity, clusterElements, WEIGHTS } from './scorer.js';
import { analyzeRouting, buildBaseGrid, routingEfficiency } from './flow.js';
import { toast } from './main.js';

const FIXED = new Set(['Layout Polygon', 'Entrance', 'Exit']);
const CATEGORY_TITLES = { HC: 'Hard constraints', PR: 'Proximity', CO: 'Compositionality', OR: 'Orientation' };
const RING = 2 * Math.PI * 52;

const clone = (els) => els.map((e) => ({ ...e, polygon: e.polygon.map((p) => [...p]) }));
const scoreColor = (v) => (v >= 0.75 ? '#5b8c5a' : v >= 0.45 ? '#d9ae58' : '#c2423a');
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 16)));
const random = () => crypto.getRandomValues(new Uint32Array(1))[0] / 2 ** 32;

function longestAngle(poly) {
  let best = null;
  for (const e of edges(poly)) if (!best || segLength(e) > segLength(best)) best = e;
  return Math.atan2(best[1][1] - best[0][1], best[1][0] - best[0][0]);
}

const wrap90 = (a) => {
  const q = Math.PI / 2;
  return ((a % q) + q + q / 2) % q - q / 2;
};

export function initPlayground(samples) {
  const svg = document.getElementById('pg-svg');
  const select = document.getElementById('pg-sample');
  const toggles = {
    live: document.getElementById('pg-live'), routes: document.getElementById('pg-routes'),
    zones: document.getElementById('pg-zones'), snap: document.getElementById('pg-snap'),
  };
  const evalBtn = document.getElementById('pg-eval');
  const feedbackEl = document.getElementById('pg-feedback');
  const barsEl = document.getElementById('pg-bars');

  // Featured samples: moderately sized layouts whose ground truth satisfies every soft constraint.
  const featured = [...samples]
    .filter((s) => s.n >= 12 && s.n <= 34 && s.scm === 1)
    .sort((a, b) => new Set(b.elements.map((e) => e.class)).size - new Set(a.elements.map((e) => e.class)).size || a.n - b.n)
    .slice(0, 8);
  const featuredIds = new Set(featured.map((s) => s.id));
  const option = (s) => `<option value="${s.index}">${s.id.slice(0, 8)} · ${s.n} elements · ${Math.round(s.area).toLocaleString('en-US')} m²</option>`;
  select.innerHTML = `<optgroup label="Featured">${featured.map(option).join('')}</optgroup>
    <optgroup label="All validation layouts">${samples.filter((s) => !featuredIds.has(s.id)).map(option).join('')}</optgroup>`;

  const state = {
    sample: null, gt: null, elements: null, handle: null, selected: null, undo: [],
    base: null, gtRouting: null, gtCS: null, view: null, dirty: true,
  };

  // ---------------------------------------------------------------------
  // Loading & rendering
  // ---------------------------------------------------------------------

  function load(sample, { scramble = true } = {}) {
    state.sample = sample;
    state.gt = sample.elements;
    state.elements = clone(sample.elements);
    state.undo = [];
    state.selected = null;
    state.base = null;
    state.gtRouting = null;
    state.gtCS = null;
    select.value = sample.index;
    document.getElementById('pg-gt-note').textContent = '';
    const present = legendItems(sample.elements);
    document.getElementById('pg-legend').innerHTML = present.map((c) => `<span title="${c}">${legendSwatch(c)}${ABBREVIATIONS[c]}</span>`).join('') +
      '<span class="route-key"><i style="background:#3f6c8a"></i>car <i style="background:#5b8c5a"></i>bike <i style="background:#c97b4a"></i>HGV route</span>';
    if (scramble) scrambleLayout(false);
    draw(true);
    resetScores();
    // Ground-truth reference scores are computed lazily once the page is idle.
    const idle = window.requestIdleCallback ? window.requestIdleCallback.bind(window) : (fn) => setTimeout(fn, 400);
    idle(() => ensureReference(), { timeout: 2000 });
  }

  function ensureReference() {
    if (!state.base) {
      const premise = state.gt.find((e) => e.class === 'Layout Polygon');
      state.base = buildBaseGrid(premise.polygon);
    }
    if (!state.gtRouting) {
      state.gtRouting = analyzeRouting(state.gt, state.base);
      const c = evaluateConstraints(state.gt);
      const { hcm, cs } = combineScores({ hcGeo: c.hc.score, hcFlow: state.gtRouting.score, scm: c.scm });
      state.gtCS = cs;
      document.getElementById('pg-gt-note').innerHTML = `Ground truth for reference: CS <b>${cs.toFixed(2)}</b> (HCM ${hcm.toFixed(2)}, SCM ${c.scm.toFixed(2)}). RE compares your routes with it, so 0.50 means parity.`;
    }
  }

  function draw(resetView = false) {
    const frame = layoutFrame(state.gt);
    state.handle = renderLayout(svg, state.elements, { lineScale: 1.25, frame });
    if (resetView || !state.view) state.view = { x: frame.minx - frame.margin, y: -(frame.maxy + frame.margin), w: frame.spanX, h: frame.spanY };
    applyView();
    for (const [id, node] of state.handle.nodes) {
      if (!FIXED.has(node.element.class)) node.g.classList.add('is-movable');
      node.g.dataset.id = id;
    }
    state.overlay = el('g', { class: 'pg-overlay' }, svg);
    state.routeLayer = el('g', { class: 'pg-routes' }, svg);
    state.handleLayer = el('g', { class: 'pg-handles' }, svg);
    liveCheck();
    drawHandle();
  }

  function applyView() {
    const v = state.view;
    svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
  }

  const upp = () => state.handle.frame.unitsPerPoint;

  function refreshNode(id) {
    updateElementNode(state.handle.nodes.get(id), state.elements[id], upp());
  }

  function markDirty() {
    state.dirty = true;
    state.routeLayer.innerHTML = '';
    svg.querySelectorAll('.elem.is-flagged').forEach((g) => g.classList.remove('is-flagged'));
    evalBtn.querySelector('.btn-label').textContent = 'Evaluate layout';
  }

  // ---------------------------------------------------------------------
  // Live geometry feedback
  // ---------------------------------------------------------------------

  let liveQueued = false;
  function liveCheck() {
    if (liveQueued) return;
    liveQueued = true;
    requestAnimationFrame(() => {
      liveQueued = false;
      state.overlay.innerHTML = '';
      if (toggles.live.checked) {
        const hc = evaluateHardConstraints(state.elements);
        for (const o of hc.overlaps) {
          for (const piece of intersectionPieces(o.a.polygon, o.b.polygon)) {
            el('polygon', { points: pointsAttr(piece), class: 'overlap-shape', 'vector-effect': 'non-scaling-stroke' }, state.overlay);
          }
        }
        const out = new Set(hc.outOfBounds.map((o) => o.e.id));
        for (const [id, node] of state.handle.nodes) node.g.classList.toggle('is-flagged', out.has(id) && !FIXED.has(node.element.class));
      }
      if (toggles.zones.checked) {
        const pr = evaluateProximity(state.elements, clusterElements(state.elements));
        for (const z of pr.zones) {
          el('polygon', { points: pointsAttr(z.zone), class: `egress-zone${z.ok ? '' : ' bad'}`, 'vector-effect': 'non-scaling-stroke' }, state.overlay);
        }
      }
    });
  }

  // ---------------------------------------------------------------------
  // Editing
  // ---------------------------------------------------------------------

  function pushUndo() {
    state.undo.push(clone(state.elements));
    if (state.undo.length > 60) state.undo.shift();
  }

  function transformElement(id, { dx = 0, dy = 0, rot = 0, pivot = null }) {
    const e = state.elements[id];
    const c = pivot || centroid(e.polygon);
    e.polygon = e.polygon.map((p) => {
      const r = rot ? rotatePoint(p, c, rot) : p;
      return [r[0] + dx, r[1] + dy];
    });
    refreshNode(id);
  }

  // Aligns to a same-class neighbour's orientation (mod 90°) when close; otherwise snaps to 5° steps.
  function snapRotation(id, proposed) {
    const e = state.elements[id];
    const current = longestAngle(e.polygon);
    const target = current + proposed;
    let best = null;
    for (const o of state.elements) {
      if (o.id === id || o.class !== e.class) continue;
      const d = wrap90(longestAngle(o.polygon) - target);
      if (Math.abs(d) < (6 * Math.PI) / 180 && (!best || Math.abs(d) < Math.abs(best))) best = d;
    }
    if (best !== null) return proposed + best;
    const step = (5 * Math.PI) / 180;
    return Math.round(target / step) * step - current;
  }

  // Pulls the element so a nearly parallel edge sits flush against a nearby edge (stacking).
  function snapEdges(id) {
    const e = state.elements[id];
    let best = null;
    for (const se of edges(e.polygon)) {
      const sa = Math.atan2(se[1][1] - se[0][1], se[1][0] - se[0][0]);
      const mid = [(se[0][0] + se[1][0]) / 2, (se[0][1] + se[1][1]) / 2];
      for (const o of state.elements) {
        if (o.id === id || o.class === 'Underground Service Facility' || e.class === 'Underground Service Facility') continue;
        for (const te of edges(o.polygon)) {
          const ta = Math.atan2(te[1][1] - te[0][1], te[1][0] - te[0][0]);
          if (Math.abs(Math.sin(sa - ta)) > Math.sin((4 * Math.PI) / 180)) continue;
          const len = segLength(te);
          const ux = (te[1][0] - te[0][0]) / len, uy = (te[1][1] - te[0][1]) / len;
          const t = (mid[0] - te[0][0]) * ux + (mid[1] - te[0][1]) * uy;
          if (t < -segLength(se) / 2 || t > len + segLength(se) / 2) continue;
          const nx = -uy, ny = ux;
          const d = (mid[0] - te[0][0]) * nx + (mid[1] - te[0][1]) * ny;
          if (Math.abs(d) > 0.9 || Math.abs(d) < 0.03) continue;
          const c = centroid(e.polygon);
          const side = Math.sign((c[0] - te[0][0]) * nx + (c[1] - te[0][1]) * ny) || 1;
          const desired = 0.05 * side;
          const shift = desired - d;
          if (!best || Math.abs(shift) < Math.abs(best.shift)) best = { shift, nx, ny };
        }
      }
    }
    if (best) transformElement(id, { dx: best.shift * best.nx, dy: best.shift * best.ny });
  }

  function toData(ev) {
    const pt = svg.createSVGPoint();
    pt.x = ev.clientX; pt.y = ev.clientY;
    const p = pt.matrixTransform(svg.getScreenCTM().inverse());
    return [p.x, -p.y];
  }

  function select_(id) {
    state.selected = id;
    for (const [nid, node] of state.handle.nodes) node.g.classList.toggle('is-selected', nid === id);
    drawHandle();
  }

  function drawHandle() {
    state.handleLayer.innerHTML = '';
    if (state.selected === null) return;
    const e = state.elements[state.selected];
    const c = centroid(e.polygon);
    const ang = longestAngle(e.polygon);
    const radius = Math.max(...e.polygon.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1]))) + state.view.w * 0.03;
    const hx = c[0] - Math.sin(ang) * radius, hy = c[1] + Math.cos(ang) * radius;
    const g = el('g', { class: 'rot-handle' }, state.handleLayer);
    el('line', { x1: c[0], y1: -c[1], x2: hx, y2: -hy, 'vector-effect': 'non-scaling-stroke' }, g);
    el('circle', { cx: hx, cy: -hy, r: state.view.w * 0.009, 'vector-effect': 'non-scaling-stroke' }, g);
    g.addEventListener('pointerdown', (ev) => startRotate(ev, c));
  }

  let drag = null;
  function startRotate(ev, pivot) {
    ev.stopPropagation();
    pushUndo();
    const [x, y] = toData(ev);
    drag = { mode: 'rotate', id: state.selected, pivot, start: Math.atan2(y - pivot[1], x - pivot[0]), applied: 0 };
    svg.setPointerCapture(ev.pointerId);
  }

  svg.addEventListener('pointerdown', (ev) => {
    if (drag) return;
    const g = ev.target.closest('.elem.is-movable');
    const p = toData(ev);
    if (g) {
      const id = Number(g.dataset.id);
      select_(id);
      pushUndo();
      drag = { mode: 'move', id, last: p, moved: false };
      g.classList.add('is-dragging');
    } else {
      select_(null);
      drag = { mode: 'pan', start: [ev.clientX, ev.clientY], view: { ...state.view } };
    }
    svg.setPointerCapture(ev.pointerId);
  });

  svg.addEventListener('pointermove', (ev) => {
    if (!drag) return;
    if (drag.mode === 'pan') {
      const r = svg.getBoundingClientRect();
      const s = Math.max(drag.view.w / r.width, drag.view.h / r.height);
      state.view.x = drag.view.x - (ev.clientX - drag.start[0]) * s;
      state.view.y = drag.view.y - (ev.clientY - drag.start[1]) * s;
      applyView();
      return;
    }
    const p = toData(ev);
    if (drag.mode === 'move') {
      transformElement(drag.id, { dx: p[0] - drag.last[0], dy: p[1] - drag.last[1] });
      drag.last = p;
      drag.moved = true;
    } else {
      let delta = Math.atan2(p[1] - drag.pivot[1], p[0] - drag.pivot[0]) - drag.start;
      if (toggles.snap.checked && !ev.altKey) delta = drag.applied + snapRotation(drag.id, delta - drag.applied);
      transformElement(drag.id, { rot: delta - drag.applied, pivot: drag.pivot });
      drag.applied = delta;
    }
    markDirty();
    drawHandle();
    liveCheck();
  });

  const endDrag = () => {
    if (!drag) return;
    if (drag.mode === 'move') {
      svg.querySelector(`.elem[data-id="${drag.id}"]`)?.classList.remove('is-dragging');
      if (drag.moved && toggles.snap.checked) { snapEdges(drag.id); liveCheck(); drawHandle(); }
      if (!drag.moved) state.undo.pop();
    }
    drag = null;
  };
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);

  svg.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const [x, y] = toData(ev);
    const k = Math.exp(ev.deltaY * 0.0015);
    const v = state.view;
    const full = state.handle.frame;
    const w = Math.min(full.spanX * 1.6, Math.max(full.spanX * 0.08, v.w * k));
    const f = w / v.w;
    state.view = { x: x - (x - v.x) * f, y: -y - (-y - v.y) * f, w: v.w * f, h: v.h * f };
    applyView();
    drawHandle();
  }, { passive: false });
  svg.addEventListener('dblclick', (ev) => {
    if (ev.target.closest('.elem')) return;
    state.view = null;
    const f = state.handle.frame;
    state.view = { x: f.minx - f.margin, y: -(f.maxy + f.margin), w: f.spanX, h: f.spanY };
    applyView();
    drawHandle();
  });

  svg.addEventListener('keydown', (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key.toLowerCase() === 'z') { ev.preventDefault(); undo(); return; }
    if (state.selected === null) return;
    const id = state.selected;
    const deg = ev.shiftKey ? 1 : 5;
    const nudge = ev.shiftKey ? 1 : 0.25;
    const actions = {
      q: { rot: (deg * Math.PI) / 180 }, e: { rot: (-deg * Math.PI) / 180 }, r: { rot: Math.PI / 2 },
      arrowleft: { dx: -nudge }, arrowright: { dx: nudge }, arrowup: { dy: nudge }, arrowdown: { dy: -nudge },
    };
    const a = actions[ev.key.toLowerCase()];
    if (ev.key === 'Escape') { select_(null); return; }
    if (!a) return;
    ev.preventDefault();
    pushUndo();
    transformElement(id, a);
    markDirty();
    drawHandle();
    liveCheck();
  });

  function undo() {
    const prev = state.undo.pop();
    if (!prev) { toast('Nothing to undo'); return; }
    state.elements = prev;
    const sel = state.selected;
    draw();
    if (sel !== null) select_(sel);
    markDirty();
  }

  function scrambleLayout(redraw = true) {
    pushUndo();
    const premise = state.elements.find((e) => e.class === 'Layout Polygon').polygon;
    const movable = state.elements.filter((e) => !FIXED.has(e.class));
    const pick = movable.filter(() => random() < 0.3);
    for (const e of (pick.length ? pick : movable.slice(0, 3))) {
      // Keep displaced elements roughly on site so the puzzle stays solvable.
      for (let attempt = 0; attempt < 8; attempt++) {
        const ang = random() * 2 * Math.PI, dist = 1.5 + random() * 4;
        const rot = random() < 0.35 ? ((random() - 0.5) * Math.PI) / 4 : 0;
        const c = centroid(e.polygon);
        const moved = e.polygon.map((p) => {
          const r = rotatePoint(p, c, rot);
          return [r[0] + Math.cos(ang) * dist, r[1] + Math.sin(ang) * dist];
        });
        if (pointInPolygon(centroid(moved), premise) || attempt === 7) { e.polygon = moved; break; }
      }
    }
    if (redraw) { draw(); markDirty(); resetScores(); }
  }

  // ---------------------------------------------------------------------
  // Scoring
  // ---------------------------------------------------------------------

  function setRing(v) {
    const fg = document.getElementById('pg-ring-fg');
    fg.style.strokeDashoffset = v === null ? RING : RING * (1 - v);
    fg.style.stroke = v === null ? '' : scoreColor(v);
    document.getElementById('pg-agg').textContent = v === null ? '–' : v.toFixed(2);
  }

  function resetScores() {
    setRing(null);
    document.getElementById('pg-cs').textContent = '–';
    document.getElementById('pg-re').textContent = '–';
    barsEl.innerHTML = '';
    feedbackEl.innerHTML = '<p class="muted small">Press <b>Evaluate</b> to run the Verifier.</p>';
  }

  function bar(label, v, sub = false, group = false) {
    return `<div class="sbar${sub ? ' sub' : ''}${group ? ' is-group' : ''}"><span>${label}</span><span class="sbar-track"><i style="width:${v * 100}%;background:${scoreColor(v)}"></i></span><b>${v.toFixed(2)}</b></div>`;
  }

  async function evaluate() {
    evalBtn.classList.add('is-busy');
    evalBtn.disabled = true;
    await nextFrame();
    try {
      ensureReference();
      const c = evaluateConstraints(state.elements);
      const routing = analyzeRouting(state.elements, state.base);
      const { hcm, cs } = combineScores({ hcGeo: c.hc.score, hcFlow: routing.score, scm: c.scm });
      const re = routingEfficiency(routing, state.gtRouting);
      const agg = round(0.5 * cs + 0.5 * re.score, 3);

      setRing(agg);
      document.getElementById('pg-cs').textContent = cs.toFixed(2);
      document.getElementById('pg-re').textContent = re.score.toFixed(2);
      barsEl.innerHTML = [
        bar('HCM', hcm, false, true), bar('HC_g', c.hc.score, true), bar('HC_f≈', routing.score, true),
        bar('SCM', c.scm, false, true), bar('PR', c.pr.score, true), bar('CO', c.co.score, true), bar('OR', c.or.score, true),
        bar('RE≈', re.score, false, true), bar('PL', re.pl, true), bar('MN', re.mn, true), bar('PC', re.pc, true),
      ].join('');

      const violations = [...c.hc.violations, ...routing.violations, ...c.pr.violations, ...c.co.violations, ...c.or.violations];
      renderFeedback(violations, { hcm, scm: c.scm, cs });
      drawRoutes(routing);
      const flagged = new Set(routing.violations.flatMap((v) => v.ids));
      for (const [id, node] of state.handle.nodes) if (flagged.has(id)) node.g.classList.add('is-flagged');
      state.dirty = false;
      evalBtn.querySelector('.btn-label').textContent = 'Re-evaluate';
    } catch (err) {
      console.error(err);
      toast('Evaluation failed for this configuration');
    } finally {
      evalBtn.classList.remove('is-busy');
      evalBtn.disabled = false;
    }
  }

  function drawRoutes(routing) {
    state.routeLayer.innerHTML = '';
    if (!toggles.routes.checked) return;
    const widths = { car: 2.2, bike: 1.6, hgv: 3 };
    for (const r of Object.values(routing.routes)) {
      if (r.invalid || !r.points) continue;
      el('polyline', {
        points: r.points.map(([x, y]) => `${x},${-y}`).join(' '),
        class: `route-path ${r.vehicle}`, 'stroke-width': widths[r.vehicle], 'vector-effect': 'non-scaling-stroke',
        'stroke-dasharray': r.seq.length > 1 ? '6 4' : null,
      }, state.routeLayer);
    }
    state.lastRouting = routing;
  }

  function renderFeedback(violations, { hcm, scm, cs }) {
    let banner;
    if (hcm === 1 && scm === 1) banner = '<div class="fb-banner good">Fully compliant. This is exactly what the Verifier is looking for.</div>';
    else if (hcm === 1) banner = '<div class="fb-banner good">Valid layout: every hard constraint is satisfied. Improve the soft constraints to raise CS.</div>';
    else banner = `<div class="fb-banner bad">Invalid layout: HCM = ${hcm.toFixed(2)} &lt; 1, so CS is capped at ${WEIGHTS.alpha} × HCM × SCM = ${cs.toFixed(2)}.</div>`;
    if (state.gtCS !== null && cs > state.gtCS + 1e-9) banner += `<div class="fb-banner good">Your CS beats the ground-truth layout (${state.gtCS.toFixed(2)}).</div>`;

    const groups = ['HC', 'PR', 'CO', 'OR'].map((cat) => {
      const vs = violations.filter((v) => v.category === cat);
      const items = vs.map((v, i) => `<button class="fb-item" data-cat="${cat}" data-i="${i}">${v.text}${v.rule ? `<small>${v.kind}: ${v.rule}</small>` : `<small>${v.kind}</small>`}</button>`).join('');
      return `<div class="fb-group"><h5>${CATEGORY_TITLES[cat]}<span class="${vs.length ? '' : 'ok'}">${vs.length ? `${vs.length} issue${vs.length > 1 ? 's' : ''}` : '✓'}</span></h5>${items || '<div class="fb-ok">No violations</div>'}</div>`;
    }).join('');
    feedbackEl.innerHTML = banner + groups;
    feedbackEl.querySelectorAll('.fb-item').forEach((b) => {
      const v = violations.filter((x) => x.category === b.dataset.cat)[Number(b.dataset.i)];
      b.addEventListener('click', () => focusIds(v.ids));
    });
  }

  function focusIds(ids) {
    const set = new Set(ids);
    for (const [id, node] of state.handle.nodes) {
      node.g.classList.toggle('is-focus', set.has(id));
      node.g.classList.toggle('is-dim', !set.has(id) && !FIXED.has(node.element.class));
    }
    if (ids.length === 1 && !FIXED.has(state.elements[ids[0]].class)) select_(ids[0]);
    clearTimeout(focusIds.t);
    focusIds.t = setTimeout(() => {
      for (const node of state.handle.nodes.values()) node.g.classList.remove('is-focus', 'is-dim');
    }, 2200);
  }

  // ---------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------

  select.addEventListener('change', () => load(samples[Number(select.value)]));
  document.getElementById('pg-scramble').addEventListener('click', () => scrambleLayout(true));
  document.getElementById('pg-reset').addEventListener('click', () => {
    pushUndo();
    state.elements = clone(state.gt);
    draw();
    markDirty();
    resetScores();
  });
  document.getElementById('pg-undo').addEventListener('click', undo);
  evalBtn.addEventListener('click', evaluate);
  toggles.live.addEventListener('change', liveCheck);
  toggles.zones.addEventListener('change', liveCheck);
  toggles.routes.addEventListener('change', () => {
    if (!toggles.routes.checked) state.routeLayer.innerHTML = '';
    else if (state.lastRouting && !state.dirty) drawRoutes(state.lastRouting);
  });

  load(featured[0] || samples[0]);
  return { load: (s) => load(s, { scramble: true }) };
}
