// Method section: interactive agent diagram, CoDe branching illustration, strategy grid.
import { el } from './render.js';

const SANS = 'system-ui, -apple-system, "Segoe UI", sans-serif';
const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace';

const NODES = {
  input: { x: 20, y: 30, w: 150, h: 60, title: 'Input 𝓘', sub: '𝒫 · 𝒜 · ℰ · ℳ' },
  icl: { x: 20, y: 203, w: 150, h: 64, title: 'ICL Initializer', sub: 'kNN exemplars → q⁽⁰⁾' },
  output: { x: 20, y: 376, w: 150, h: 60, title: 'Layout q★', sub: 'best-of-N after T steps' },
  verifier: { x: 390, y: 40, w: 160, h: 64, title: 'Verifier', sub: 'deterministic tools' },
  historian: { x: 556, y: 203, w: 150, h: 64, title: 'Historian', sub: 'window of W changes' },
  planner: { x: 390, y: 366, w: 160, h: 64, title: 'Planner', sub: 'supervisor + workers' },
  generator: { x: 226, y: 203, w: 150, h: 64, title: 'Generator', sub: 'K strategy candidates' },
};

const INFO = {
  input: {
    role: 'Problem instance', title: 'Input requirements',
    text: 'An instance 𝓘 = (𝒫, 𝒜, ℰ, ℳ) bundles the irregular premise, its access points, the elements with their fixed shapes and counts, and the design manual. Only the placements q = {(pᵢ, θᵢ)} are optimised.',
    io: ['Premise, access points', 'Element set ℰ, manual ℳ'],
    bullets: ['Shapes, sizes, and counts are fixed inputs', 'Hard constraints: containment, non-overlap, routing', 'Soft constraints: compositionality, orientation, proximity'],
  },
  icl: {
    role: 'Phase 1 · seeding', title: 'ICL Initializer',
    text: 'Embeds the new instance into a feature vector φ(𝓘) and retrieves structurally similar development layouts with a weighted kNN search. The exemplars are given to an LLM as input–output pairs. It generates K_init candidates in parallel, and the best one under a geometric proxy σ(q) is kept.',
    io: ['𝓘, 𝒟ᵈᵉᵛ', 'Seed layout q⁽⁰⁾'],
    bullets: ['Features: per-class counts and areas, access-point positions, size statistics, premise shape', 'σ(q) = 1 / (1 + V_out + V_ov) is 1 exactly when the candidate is contained and non-overlapping', 'Sparse repair loop when the best seed is infeasible'],
  },
  verifier: {
    role: 'Reward signal', title: 'Verifier',
    text: 'Scores every candidate against the design manual with deterministic tools and keeps the best one. It never sees the ground truth. Beyond the scores, it reports detailed diagnostics, such as which obstacle blocks a route.',
    io: ['Candidates 𝒬⁽ᵗ⁾', 'Best q̄⁽ᵗ⁾, feedback 𝓕⁽ᵗ⁾'],
    bullets: ['HC_g, HC_f, CO, PR, OR and CS', 'Voronoi motion planning for vehicle routes', 'Without tools (LLM-as-a-judge), it declares 95% of layouts compliant while the true CS is 0.28'],
  },
  historian: {
    role: 'Memory', title: 'Historian',
    text: 'Records how the design evolves. After each step it computes the change in feedback ΔF and annotates it with a short description of the spatial edits that caused it. The Planner receives a sliding window of the last W changes.',
    io: ['𝓗⁽ᵗ⁻¹⁾, 𝓕⁽ᵗ⁾', 'History 𝓗⁽ᵗ⁾'],
    bullets: ['Grounds planning in how the scores change over time', 'Each CoDe branch keeps its own local history', 'W = 6 in the reported configuration'],
  },
  planner: {
    role: 'Strategy', title: 'Planner',
    text: 'Drafts a remediation plan along the five evaluation categories. A large backbone handles everything in one context. Otherwise, specialised workers plan each category in parallel and a supervisor reconciles their plans into one plan Π⁽ᵗ⁾.',
    io: ['q, 𝓕, 𝓗', 'Plan Π⁽ᵗ⁾'],
    bullets: ['Multi-modal context: diagnostics, history, coordinates, rendered image', 'Resolves conflicts between categories and prioritises repairs', 'Without the Planner, compliance drops from 74% to 51%'],
  },
  generator: {
    role: 'Action', title: 'Generator',
    text: 'Turns the plan into K revised candidate layouts by editing positions and orientations. Each candidate follows a different strategy: conservative local fixes, a focus on one category, or larger unconstrained reorganisations.',
    io: ['q̄⁽ᵗ⁻¹⁾, Π⁽ᵗ⁾', 'K candidates 𝒬⁽ᵗ⁾'],
    bullets: ['Eight strategies, assigned at random per branch', 'Exploratory strategies ignore the plan so branches can escape local optima', 'K = 6 candidates per step'],
  },
  output: {
    role: 'Result', title: 'Final layout',
    text: 'After every block of B steps, a best-of-N reduction keeps the branch with the highest score, together with its history and feedback. The loop stops when the budget T is used up.',
    io: ['N branches', 'q̄⁽ᵀ⁾'],
    bullets: ['Block-wise BoN avoids committing to one trajectory too early', 'Without CoDe, AGG drops from 0.62 to 0.35', 'Setting B = T reduces CoDe to plain best-of-N'],
  },
};

const LOOP = ['verifier', 'historian', 'planner', 'generator'];

function anchor(n, side) {
  const N = NODES[n];
  return {
    top: [N.x + N.w / 2, N.y], bottom: [N.x + N.w / 2, N.y + N.h],
    left: [N.x, N.y + N.h / 2], right: [N.x + N.w, N.y + N.h / 2],
  }[side];
}

function edgePath(a, as, b, bs, bend = 0.5) {
  const p = anchor(a, as), q = anchor(b, bs);
  const dx = q[0] - p[0], dy = q[1] - p[1];
  const c1 = as === 'left' || as === 'right' ? [p[0] + dx * bend, p[1]] : [p[0], p[1] + dy * bend];
  const c2 = bs === 'left' || bs === 'right' ? [q[0] - dx * bend, q[1]] : [q[0], q[1] - dy * bend];
  return `M${p} C${c1} ${c2} ${q}`;
}

function initAgents() {
  const svg = document.getElementById('agent-svg');
  const panel = document.getElementById('agent-panel');
  const defs = el('defs', {}, svg);
  const marker = el('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
  el('path', { d: 'M0 0 L10 5 L0 10 z', fill: '#857e73' }, marker);

  // Loop backdrop.
  el('rect', { x: 206, y: 20, width: 510, height: 430, rx: 26, fill: 'rgba(143,179,207,.04)', stroke: 'rgba(143,179,207,.18)', 'stroke-dasharray': '4 6' }, svg);
  const lbl = el('text', { x: 700, y: 440, 'text-anchor': 'end', fill: '#a59e92', 'font-size': 11, 'font-family': SANS }, svg);
  lbl.textContent = '× N parallel branches · B steps per block';
  const center = el('text', { x: 466, y: 232, 'text-anchor': 'middle', fill: '#8fb3cf', 'font-size': 12, 'font-family': SANS, 'font-weight': 600 }, svg);
  center.textContent = 'refinement loop';
  const center2 = el('text', { x: 466, y: 250, 'text-anchor': 'middle', fill: '#a59e92', 'font-size': 11, 'font-family': SANS }, svg);
  center2.textContent = 'verify → remember → plan → edit';

  const edges = {
    'input-icl': edgePath('input', 'bottom', 'icl', 'top'),
    'icl-verifier': 'M170 225 C 205 225, 205 58, 260 58 L 390 58',
    'verifier-historian': edgePath('verifier', 'right', 'historian', 'top', 0.9),
    'historian-planner': edgePath('historian', 'bottom', 'planner', 'right', 0.9),
    'planner-generator': edgePath('planner', 'left', 'generator', 'bottom', 0.9),
    'generator-verifier': edgePath('generator', 'top', 'verifier', 'left', 0.9),
    'verifier-output': 'M410 104 C 410 150, 200 140, 200 200 L 200 360 C 200 396, 190 406, 172 406',
  };
  const edgeEls = {};
  for (const [k, d] of Object.entries(edges)) {
    edgeEls[k] = el('path', { d, class: 'agent-edge', 'marker-end': 'url(#arrow)', 'stroke-dasharray': k === 'verifier-output' ? '4 5' : null }, svg);
  }
  const bon = el('text', { x: 206, y: 300, fill: '#a59e92', 'font-size': 10.5, 'font-family': SANS }, svg);
  bon.textContent = 'BoN';

  const nodeEls = {};
  for (const [k, n] of Object.entries(NODES)) {
    const g = el('g', { class: 'agent-node', tabindex: 0, role: 'button', 'aria-label': n.title }, svg);
    el('rect', { x: n.x, y: n.y, width: n.w, height: n.h, rx: 14 }, g);
    const t = el('text', { x: n.x + n.w / 2, y: n.y + n.h / 2 - 3, 'text-anchor': 'middle' }, g);
    t.textContent = n.title;
    const s = el('text', { x: n.x + n.w / 2, y: n.y + n.h / 2 + 14, 'text-anchor': 'middle', class: 'sub' }, g);
    s.textContent = n.sub;
    const select = () => show(k);
    g.addEventListener('click', select);
    g.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(); } });
    nodeEls[k] = g;
  }
  const packet = el('circle', { r: 5, class: 'agent-packet', opacity: 0 }, svg);

  function show(k) {
    const d = INFO[k];
    Object.entries(nodeEls).forEach(([kk, g]) => g.classList.toggle('is-selected', kk === k));
    panel.innerHTML = `<span class="role">${d.role}</span><h4>${d.title}</h4><p>${d.text}</p>
      <div class="agent-io"><div><span>Input</span>${d.io[0]}</div><div><span>Output</span>${d.io[1]}</div></div>
      <ul>${d.bullets.map((b) => `<li>${b}</li>`).join('')}</ul>`;
    panel.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 350, easing: 'ease-out' });
  }
  show('verifier');

  // Animated packet travelling the loop.
  const sequence = ['input-icl', 'icl-verifier', ...Array(3).fill(['verifier-historian', 'historian-planner', 'planner-generator', 'generator-verifier']).flat(), 'verifier-output'];
  let playing = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let idx = 0, t0 = null;
  const playBtn = document.getElementById('agent-play');
  playBtn.textContent = playing ? 'Pause loop' : 'Play loop';
  playBtn.addEventListener('click', () => {
    playing = !playing;
    playBtn.textContent = playing ? 'Pause loop' : 'Play loop';
    playBtn.setAttribute('aria-pressed', playing);
    if (playing) { t0 = null; requestAnimationFrame(tick); }
  });
  let visible = false;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; if (visible && playing) { t0 = null; requestAnimationFrame(tick); } }, { threshold: 0.2 }).observe(svg);

  function tick(now) {
    if (!playing || !visible) return;
    if (t0 === null) t0 = now;
    const key = sequence[idx];
    const path = edgeEls[key];
    const dur = key === 'verifier-output' ? 1400 : 950;
    const p = Math.min(1, (now - t0) / dur);
    const pt = path.getPointAtLength(path.getTotalLength() * p);
    packet.setAttribute('cx', pt.x); packet.setAttribute('cy', pt.y); packet.setAttribute('opacity', 1);
    Object.entries(edgeEls).forEach(([k, e]) => e.classList.toggle('is-active', k === key));
    const [from, to] = key.split('-');
    Object.entries(nodeEls).forEach(([k, g]) => g.classList.toggle('is-active', k === (p < 0.15 ? from : p > 0.85 ? to : null)));
    if (p >= 1) { idx = (idx + 1) % sequence.length; t0 = now + (idx === 0 ? 900 : 120); }
    requestAnimationFrame(tick);
  }
}

// ---------------------------------------------------------------------------
// CoDe illustration
// ---------------------------------------------------------------------------

function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function initCoDe() {
  const svg = document.getElementById('code-svg');
  const nIn = document.getElementById('code-n'), bIn = document.getElementById('code-b');
  const T = 20, X0 = 44, X1 = 540, Y0 = 280, Y1 = 24;
  const colors = ['#6c93b8', '#7fae8c', '#d9ae58', '#c97b4a', '#93c0d9', '#b98868'];
  let seed = 7, timer = null;
  const sx = (t) => X0 + ((X1 - X0) * t) / T;
  const sy = (v) => Y0 - (Y0 - Y1) * v;

  function simulate(N, B) {
    const rng = mulberry32(seed * 31 + N * 7 + B);
    const gauss = () => Math.sqrt(-2 * Math.log(rng() + 1e-12)) * Math.cos(2 * Math.PI * rng());
    let best = 0.18;
    const blocks = [];
    for (let t = 0; t < T; t += B) {
      const len = Math.min(B, T - t);
      const branches = [];
      for (let n = 0; n < N; n++) {
        let s = best;
        const pts = [[t, s]];
        for (let b = 1; b <= len; b++) {
          s = Math.max(0.02, Math.min(0.97, s + 0.05 * (1 - s) + 0.05 * gauss()));
          pts.push([t + b, s]);
        }
        branches.push(pts);
      }
      const winner = branches.reduce((bi, br, i) => (br[br.length - 1][1] > branches[bi][branches[bi].length - 1][1] ? i : bi), 0);
      best = branches[winner][branches[winner].length - 1][1];
      blocks.push({ t, branches, winner });
    }
    return blocks;
  }

  function draw() {
    clearInterval(timer);
    const N = +nIn.value, B = +bIn.value;
    document.getElementById('code-n-v').textContent = N;
    document.getElementById('code-b-v').textContent = B;
    svg.innerHTML = '';
    for (let v = 0; v <= 1.001; v += 0.25) {
      el('line', { x1: X0, x2: X1, y1: sy(v), y2: sy(v), stroke: '#57524a', 'stroke-width': 1 }, svg);
      const t = el('text', { x: X0 - 8, y: sy(v) + 4, 'text-anchor': 'end', fill: '#a59e92', 'font-size': 10, 'font-family': MONO }, svg);
      t.textContent = v.toFixed(2);
    }
    const xl = el('text', { x: X1, y: Y0 + 26, 'text-anchor': 'end', fill: '#a59e92', 'font-size': 11, 'font-family': SANS }, svg);
    xl.textContent = 'refinement step t →';
    const yl = el('text', { x: X0, y: 14, fill: '#a59e92', 'font-size': 11, 'font-family': SANS }, svg);
    yl.textContent = 'verified score';
    const blocks = simulate(N, B);
    for (const blk of blocks) {
      el('line', { x1: sx(blk.t), x2: sx(blk.t), y1: Y1, y2: Y0, stroke: '#67615a', 'stroke-dasharray': '3 4' }, svg);
    }
    const groups = blocks.map(() => el('g', { opacity: 0 }, svg));
    const bestLine = [];
    blocks.forEach((blk, bi) => {
      blk.branches.forEach((pts, n) => {
        const win = n === blk.winner;
        el('polyline', {
          points: pts.map(([t, v]) => `${sx(t)},${sy(v)}`).join(' '), fill: 'none',
          stroke: win ? '#d9ae58' : colors[n % colors.length], 'stroke-width': win ? 2.6 : 1.4, opacity: win ? 1 : 0.55, 'stroke-linejoin': 'round',
        }, groups[bi]);
        if (win) bestLine.push(...pts);
      });
      const end = blk.branches[blk.winner].at(-1);
      el('circle', { cx: sx(end[0]), cy: sy(end[1]), r: 5, fill: '#d9ae58', stroke: '#423e38', 'stroke-width': 2 }, groups[bi]);
      const lbl = el('text', { x: sx(end[0]) - 4, y: sy(end[1]) - 10, 'text-anchor': 'end', fill: '#d9ae58', 'font-size': 10, 'font-family': SANS, 'font-weight': 600 }, groups[bi]);
      lbl.textContent = 'BoN';
    });
    let i = 0;
    const reveal = () => {
      if (i >= groups.length) { clearInterval(timer); return; }
      groups[i].animate([{ opacity: 0 }, { opacity: 1 }], { duration: 500, fill: 'forwards' });
      i++;
    };
    reveal();
    timer = setInterval(reveal, 700);
  }
  nIn.addEventListener('input', draw);
  bIn.addEventListener('input', draw);
  document.getElementById('code-replay').addEventListener('click', () => { seed++; draw(); });
  const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { draw(); io.disconnect(); } }, { threshold: 0.3 });
  io.observe(svg);
}

function initStrategies() {
  const S = [
    ['Conservative', 'full', 'Applies the full plan with small, cautious edits.'],
    ['Surgical', 'full', 'Applies the full plan with precise single edits.'],
    ['Hard-constraints focused', 'focus', 'Repairs hard-constraint violations only.'],
    ['Compositionality focused', 'focus', 'Repairs CO violations only.'],
    ['Proximity focused', 'focus', 'Repairs PR violations only.'],
    ['Aggressive', 'none', 'Large, unconstrained reorganisation of the layout.'],
    ['Macro spatial', 'none', 'Rearranges whole blocks of elements.'],
    ['Space utilization', 'none', 'Maximises space efficiency.'],
  ];
  const label = { full: 'full plan', focus: 'one category', none: 'no plan' };
  document.getElementById('strategy-grid').innerHTML = S.map(([n, g, d]) => `<div class="strategy"><b>${n}</b><span>${d}</span><em class="guid ${g}">${label[g]}</em></div>`).join('');
}

export function initMethod() {
  initAgents();
  initCoDe();
  initStrategies();
}
