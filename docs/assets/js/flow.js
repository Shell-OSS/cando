// Grid-based approximation of the ALPS-Bench routing checks (HC_f) and routing
// efficiency (RE). The reference implementation plans on a Voronoi medial axis
// (src/cando/utils/path_finders/voronoi.py); here the same free space (inflated
// obstacles, shrunk premise, parking/service dividers) is rasterized and searched.
import { bounds, centroid, edges, pointInPolygon, pointSegDist, segLength, segSegDist, segPolyDist, round } from './geometry.js';

export const SCENARIOS = {
  car: [['Car Service Area'], ['Car Service Area', 'Car Parking'], ['Car Service Area', 'Priority Parking'], ['Car Parking'], ['EV Service Area']],
  bike: [['Bike Service Area'], ['Bike Service Area', 'Bike Parking'], ['Bike Parking']],
  hgv: [['HGV Service Area'], ['HGV Service Area', 'HGV Parking'], ['HGV Parking']],
};
export const VEHICLES = { car: [4.5, 1.8], bike: [2.0, 0.75], hgv: [16.5, 2.55] };
const SERVICE_AREAS = ['Car Service Area', 'Bike Service Area', 'HGV Service Area'];
const PARKING = ['Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking', 'EV Service Area'];
const IGNORED = ['Underground Service Facility'];
const PREMISE = 'Layout Polygon';
const MAX_CELLS = 450000;

// ---------------------------------------------------------------------------
// Per-layout base grid (premise and distance to its boundary)
// ---------------------------------------------------------------------------

export function buildBaseGrid(premisePoly) {
  const [x0, y0, x1, y1] = bounds(premisePoly);
  const pad = 1;
  const w0 = x1 - x0 + 2 * pad, h0 = y1 - y0 + 2 * pad;
  const cell = Math.max(0.25, Math.sqrt((w0 * h0) / MAX_CELLS));
  const W = Math.ceil(w0 / cell), H = Math.ceil(h0 / cell);
  const ox = x0 - pad, oy = y0 - pad;
  const boundaryDist = new Float32Array(W * H).fill(-1);
  const pedges = edges(premisePoly);
  for (let j = 0; j < H; j++) {
    const y = oy + (j + 0.5) * cell;
    // Scanline crossings for fast inside tests.
    const xs = [];
    for (const [[ax, ay], [bx, by]] of pedges) {
      if ((ay > y) !== (by > y)) xs.push(ax + ((y - ay) * (bx - ax)) / (by - ay));
    }
    xs.sort((a, b) => a - b);
    for (let k = 0; k + 1 < xs.length; k += 2) {
      const i0 = Math.max(0, Math.ceil((xs[k] - ox) / cell - 0.5));
      const i1 = Math.min(W - 1, Math.floor((xs[k + 1] - ox) / cell - 0.5));
      for (let i = i0; i <= i1; i++) {
        const p = [ox + (i + 0.5) * cell, y];
        let d = Infinity;
        for (const e of pedges) { const v = pointSegDist(p, e); if (v < d) d = v; }
        boundaryDist[j * W + i] = d;
      }
    }
  }
  return { ox, oy, cell, W, H, boundaryDist, premise: premisePoly };
}

// ---------------------------------------------------------------------------
// Scenario grid
// ---------------------------------------------------------------------------

function sidesOf(poly) {
  return edges(poly).map((e, i) => ({ e, i, len: segLength(e) }));
}

const mid = ([a, b]) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];

// Thin dividers that force straight-in access (voronoi.py: _add_csa_obstacles / _add_parking_obstacles).
function dividersFor(el) {
  const sides = sidesOf(el.polygon);
  if (SERVICE_AREAS.includes(el.class)) {
    const carLongEntrance = el.class === 'Car Service Area';
    const sorted = [...sides].sort((a, b) => (carLongEntrance ? a.len - b.len : b.len - a.len));
    const blocked = sorted.slice(0, 2);
    const segs = blocked.map((s) => s.e);
    const open = sides.filter((s) => !blocked.includes(s));
    let splitter = null;
    if (carLongEntrance && open.length >= 2) {
      splitter = [mid(open[0].e), mid(open[1].e)];
      segs.push(splitter);
    }
    return { segs, splitter, open };
  }
  if (PARKING.includes(el.class)) {
    const sorted = [...sides].sort((a, b) => b.len - a.len);
    return { segs: sorted.slice(0, 2).map((s) => s.e), splitter: null };
  }
  return { segs: [], splitter: null };
}

// Halves of a car service area on either side of its splitter.
function splitServiceArea(poly, splitter) {
  const [m1, m2] = splitter;
  const side = (p) => (m2[0] - m1[0]) * (p[1] - m1[1]) - (m2[1] - m1[1]) * (p[0] - m1[0]);
  const halves = [[], []];
  const n = poly.length;
  for (let i = 0; i < n; i++) {
    const a = poly[i], b = poly[(i + 1) % n];
    const sa = side(a), sb = side(b);
    (sa >= 0 ? halves[0] : halves[1]).push(a);
    if ((sa > 1e-9 && sb < -1e-9) || (sa < -1e-9 && sb > 1e-9)) {
      const t = sa / (sa - sb);
      const x = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      halves[0].push(x); halves[1].push(x);
    }
  }
  return halves.filter((h) => h.length >= 3);
}

function markNear(grid, blocked, bbox, test) {
  const { ox, oy, cell, W, H } = grid;
  const i0 = Math.max(0, Math.floor((bbox[0] - ox) / cell)), i1 = Math.min(W - 1, Math.ceil((bbox[2] - ox) / cell));
  const j0 = Math.max(0, Math.floor((bbox[1] - oy) / cell)), j1 = Math.min(H - 1, Math.ceil((bbox[3] - oy) / cell));
  for (let j = j0; j <= j1; j++) {
    for (let i = i0; i <= i1; i++) {
      const k = j * W + i;
      if (blocked[k]) continue;
      if (test([ox + (i + 0.5) * cell, oy + (j + 0.5) * cell])) blocked[k] = 1;
    }
  }
}

function buildScenarioGrid(base, elements, vehicle, classes) {
  const [L, Wv] = VEHICLES[vehicle];
  const half = Math.sqrt(L * L + Wv * Wv) / 4;
  const dividerHalf = 0.1 + Wv / 4;
  const interest = new Set([...classes, 'Entrance', 'Exit']);
  const { W, H, boundaryDist } = base;
  const blocked = new Uint8Array(W * H);
  const obstacles = [];
  const dividerSegs = [];
  const waypoints = [];

  for (const el of elements) {
    if (el.class === PREMISE || IGNORED.includes(el.class)) continue;
    if (!interest.has(el.class)) {
      obstacles.push(el);
      const b = bounds(el.polygon);
      markNear(base, blocked, [b[0] - half, b[1] - half, b[2] + half, b[3] + half],
        (p) => pointInPolygon(p, el.polygon) || edges(el.polygon).some((e) => pointSegDist(p, e) <= half));
      continue;
    }
    const { segs, splitter } = dividersFor(el);
    for (const s of segs) {
      dividerSegs.push(s);
      const b = bounds(s);
      markNear(base, blocked, [b[0] - dividerHalf, b[1] - dividerHalf, b[2] + dividerHalf, b[3] + dividerHalf],
        (p) => pointSegDist(p, s) <= dividerHalf);
    }
    if (splitter) {
      splitServiceArea(el.polygon, splitter).forEach((poly, part) => waypoints.push({ el, poly, part, point: centroid(poly) }));
    } else {
      waypoints.push({ el, poly: el.polygon, part: 0, point: centroid(el.polygon) });
    }
  }

  const free = new Uint8Array(W * H);
  for (let k = 0; k < W * H; k++) free[k] = boundaryDist[k] >= half && !blocked[k] ? 1 : 0;

  // Largest 4-connected component of free space (the reference keeps the largest medial-axis component).
  const comp = new Int32Array(W * H).fill(-1);
  let best = -1, bestSize = 0, label = 0;
  const queue = new Int32Array(W * H);
  for (let s = 0; s < W * H; s++) {
    if (!free[s] || comp[s] !== -1) continue;
    let head = 0, tail = 0;
    queue[tail++] = s; comp[s] = label;
    while (head < tail) {
      const k = queue[head++];
      const i = k % W;
      const nb = [i > 0 ? k - 1 : -1, i < W - 1 ? k + 1 : -1, k - W, k + W];
      for (const n of nb) {
        if (n < 0 || n >= W * H || !free[n] || comp[n] !== -1) continue;
        comp[n] = label; queue[tail++] = n;
      }
    }
    if (tail > bestSize) { bestSize = tail; best = label; }
    label++;
  }
  const main = new Uint8Array(W * H);
  for (let k = 0; k < W * H; k++) main[k] = comp[k] === best ? 1 : 0;

  const grid = { ...base, blocked, main, half, dividerHalf, obstacles, dividerSegs, vehicle, classes };
  for (const wp of waypoints) Object.assign(wp, attach(grid, wp.point));
  grid.waypoints = waypoints;
  return grid;
}

// Line-of-sight check against inflated obstacles and dividers (exact geometry).
function clearSight(grid, a, b) {
  const seg = [a, b];
  for (const s of grid.dividerSegs) if (segSegDist(seg, s) <= grid.dividerHalf) return false;
  for (const o of grid.obstacles) if (segPolyDist(seg, o.polygon) <= grid.half) return false;
  return true;
}

// Connects a waypoint to the nearest visible main-component cell (rays + exact confirmation).
function attach(grid, point) {
  const { ox, oy, cell, W, H, main, blocked } = grid;
  const candidates = [];
  const rays = 180;
  const step = cell / 2;
  const startFree = 1.0;
  for (let r = 0; r < rays; r++) {
    const t = (2 * Math.PI * r) / rays;
    const dx = Math.cos(t) * step, dy = Math.sin(t) * step;
    let x = point[0], y = point[1], dist = 0;
    for (;;) {
      const i = Math.floor((x - ox) / cell), j = Math.floor((y - oy) / cell);
      if (i < 0 || j < 0 || i >= W || j >= H) break;
      const k = j * W + i;
      if (main[k]) { candidates.push({ k, dist }); break; }
      if (blocked[k] && dist > startFree) break;
      x += dx; y += dy; dist += step;
    }
  }
  candidates.sort((a, b) => a.dist - b.dist);
  const seen = new Set();
  for (const c of candidates) {
    if (seen.has(c.k)) continue;
    seen.add(c.k);
    const cx = ox + ((c.k % W) + 0.5) * cell, cy = oy + (Math.floor(c.k / W) + 0.5) * cell;
    if (clearSight(grid, point, [cx, cy])) return { cellIndex: c.k, cellPoint: [cx, cy], linkCost: Math.hypot(cx - point[0], cy - point[1]) };
    if (seen.size > 24) break;
  }
  return { cellIndex: -1 };
}

// ---------------------------------------------------------------------------
// Scenario evaluation
// ---------------------------------------------------------------------------

export function scenariosFor(elements) {
  const present = new Set(elements.map((e) => e.class));
  const out = [];
  for (const [vehicle, seqs] of Object.entries(SCENARIOS)) {
    for (const seq of seqs) if (seq.every((c) => present.has(c))) out.push({ vehicle, seq, key: `${vehicle}:${seq.join('>')}` });
  }
  return out;
}

class MinHeap {
  constructor() { this.k = []; this.v = []; }
  push(key, val) {
    const k = this.k, v = this.v;
    let i = k.length; k.push(key); v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (v[p] <= v[i]) break;
      [k[p], k[i]] = [k[i], k[p]]; [v[p], v[i]] = [v[i], v[p]]; i = p;
    }
  }
  pop() {
    const k = this.k, v = this.v;
    const top = k[0], val = v[0];
    const lk = k.pop(), lv = v.pop();
    if (k.length) {
      k[0] = lk; v[0] = lv;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1;
        let m = i;
        if (l < k.length && v[l] < v[m]) m = l;
        if (r < k.length && v[r] < v[m]) m = r;
        if (m === i) break;
        [k[m], k[i]] = [k[i], k[m]]; [v[m], v[i]] = [v[i], v[m]]; i = m;
      }
    }
    return [top, val];
  }
  get size() { return this.k.length; }
}

function dijkstra(grid, sources) {
  const { W, H, main, cell } = grid;
  const dist = new Float64Array(W * H).fill(Infinity);
  const parent = new Int32Array(W * H).fill(-1);
  const heap = new MinHeap();
  for (const [k, d] of sources) if (d < dist[k]) { dist[k] = d; heap.push(k, d); }
  const diag = cell * Math.SQRT2;
  while (heap.size) {
    const [k, d] = heap.pop();
    if (d > dist[k]) continue;
    const i = k % W, j = (k - i) / W;
    for (let dj = -1; dj <= 1; dj++) {
      for (let di = -1; di <= 1; di++) {
        if (!di && !dj) continue;
        const ni = i + di, nj = j + dj;
        if (ni < 0 || nj < 0 || ni >= W || nj >= H) continue;
        const n = nj * W + ni;
        if (!main[n]) continue;
        if (di && dj && (!main[j * W + ni] || !main[nj * W + i])) continue;
        const nd = d + (di && dj ? diag : cell);
        if (nd < dist[n]) { dist[n] = nd; parent[n] = k; heap.push(n, nd); }
      }
    }
  }
  return { dist, parent };
}

const cellCenter = (grid, k) => [grid.ox + ((k % grid.W) + 0.5) * grid.cell, grid.oy + (Math.floor(k / grid.W) + 0.5) * grid.cell];

// Shortest Entrance -> waypoint classes -> Exit route via layered Dijkstra.
function bestRoute(grid, seq) {
  const layers = ['Entrance', ...seq, 'Exit'];
  const groups = layers.map((cls) => grid.waypoints.filter((w) => w.el.class === cls && w.cellIndex >= 0));
  if (groups.some((g) => !g.length)) return null;
  let current = groups[0].map((w) => [w.cellIndex, w.linkCost]);
  const trace = [];
  const entrySource = new Map(groups[0].map((w) => [w.cellIndex, w]));
  let sourceOf = entrySource;
  for (let layer = 1; layer < layers.length; layer++) {
    const { dist, parent } = dijkstra(grid, current);
    trace.push({ parent, sourceOf });
    if (layer === layers.length - 1) {
      let bestW = null, bestD = Infinity;
      for (const w of groups[layer]) {
        const d = dist[w.cellIndex] + w.linkCost;
        if (d < bestD) { bestD = d; bestW = w; }
      }
      if (!bestW || !Number.isFinite(bestD)) return null;
      return { length: bestD, points: reconstruct(grid, trace, bestW) };
    }
    const next = new Map();
    for (const w of groups[layer]) {
      const arrive = dist[w.cellIndex] + w.linkCost;
      if (!Number.isFinite(arrive)) continue;
      const out = arrive + w.linkCost;
      const prev = next.get(w.cellIndex);
      if (!prev || out < prev.cost) next.set(w.cellIndex, { cost: out, w });
    }
    if (!next.size) return null;
    current = [...next].map(([k, v]) => [k, v.cost]);
    sourceOf = new Map([...next].map(([k, v]) => [k, v.w]));
  }
  return null;
}

function reconstruct(grid, trace, exitW) {
  const pts = [exitW.point];
  let k = exitW.cellIndex;
  for (let layer = trace.length - 1; layer >= 0; layer--) {
    const { parent, sourceOf } = trace[layer];
    while (k >= 0) {
      pts.push(cellCenter(grid, k));
      if (parent[k] < 0) break;
      k = parent[k];
    }
    const w = sourceOf.get(k);
    pts.push(w.point);
    if (layer > 0) pts.push(cellCenter(grid, k));
  }
  return simplify(grid, pts.reverse());
}

// Greedy string pulling on the free-space mask.
function visible(grid, a, b) {
  const { ox, oy, cell, W, main } = grid;
  const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const n = Math.ceil(d / (cell / 2));
  for (let s = 1; s < n; s++) {
    const x = a[0] + ((b[0] - a[0]) * s) / n, y = a[1] + ((b[1] - a[1]) * s) / n;
    const i = Math.floor((x - ox) / cell), j = Math.floor((y - oy) / cell);
    if (!main[j * W + i]) return false;
  }
  return true;
}

function simplify(grid, pts) {
  const cleaned = pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
  if (cleaned.length <= 2) return cleaned;
  // Keep waypoint anchors (first/last and link endpoints) fixed; pull the grid runs between them.
  const out = [cleaned[0]];
  let i = 0;
  while (i < cleaned.length - 1) {
    let j = cleaned.length - 1;
    while (j > i + 1 && !(visible(grid, cleaned[i], cleaned[j]) && isGridRun(grid, cleaned, i, j))) j--;
    out.push(cleaned[j]);
    i = j;
  }
  return out;
}

function isGridRun(grid, pts, i, j) {
  // Pulling must not skip a waypoint anchor (points off the grid lattice).
  for (let k = i + 1; k < j; k++) if (!onLattice(grid, pts[k])) return false;
  return true;
}

function onLattice(grid, p) {
  const fx = (p[0] - grid.ox) / grid.cell - 0.5, fy = (p[1] - grid.oy) / grid.cell - 0.5;
  return Math.abs(fx - Math.round(fx)) < 1e-6 && Math.abs(fy - Math.round(fy)) < 1e-6;
}

function pathStats(points, elements) {
  let length = 0;
  for (let i = 1; i < points.length; i++) length += Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]);
  let sharp = 0;
  for (let i = 1; i < points.length - 1; i++) {
    const v1 = [points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1]];
    const v2 = [points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]];
    const m = Math.hypot(...v1) * Math.hypot(...v2);
    if (m < 1e-9) continue;
    const ang = Math.acos(Math.max(-1, Math.min(1, (v1[0] * v2[0] + v1[1] * v2[1]) / m))) * 180 / Math.PI;
    if (ang > 90) sharp++;
  }
  const allEdges = elements.flatMap((e) => edges(e.polygon));
  const samples = [];
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const n = Math.max(1, Math.ceil(Math.hypot(b[0] - a[0], b[1] - a[1])));
    for (let s = 0; s < n; s++) samples.push([a[0] + ((b[0] - a[0]) * s) / n, a[1] + ((b[1] - a[1]) * s) / n]);
  }
  samples.push(points[points.length - 1]);
  let clearance = 0;
  for (const p of samples) {
    let d = Infinity;
    for (const e of allEdges) { const v = pointSegDist(p, e); if (v < d) d = v; }
    clearance += d;
  }
  return { length, sharp, clearance: clearance / samples.length };
}

// Evaluates every applicable scenario: feasibility counts (HC_f) and best routes (RE).
export function analyzeRouting(elements, base) {
  const scenarios = scenariosFor(elements);
  let valid = 0, invalid = 0;
  const issues = new Map();
  const routes = {};
  const grids = {};
  for (const sc of scenarios) {
    const grid = buildScenarioGrid(base, elements, sc.vehicle, sc.seq);
    grids[sc.key] = grid;
    const at = (cls) => grid.waypoints.filter((w) => w.el.class === cls);
    const ents = at('Entrance').filter((w) => w.cellIndex >= 0).length;
    const exits = at('Exit').filter((w) => w.cellIndex >= 0).length;
    const layers = sc.seq.map(at);
    // Every combination of concrete waypoints is one core tuple (voronoi.py: _find_all_path_combinations).
    const reachableCounts = layers.map((l) => l.filter((w) => w.cellIndex >= 0).length);
    const totalTuples = layers.reduce((p, l) => p * l.length, 1);
    const okTuples = ents && exits ? reachableCounts.reduce((p, c) => p * c, 1) : 0;
    valid += okTuples * ents * exits;
    invalid += totalTuples - okTuples;
    for (const layer of layers) {
      for (const w of layer) {
        if (w.cellIndex >= 0 && ents && exits) continue;
        const id = w.el.id;
        if (!issues.has(id)) issues.set(id, { id, cls: w.el.class, vehicles: new Set(), scenarios: [] });
        const it = issues.get(id);
        it.vehicles.add(sc.vehicle);
        it.scenarios.push(sc.seq.join(' → '));
      }
    }
    const route = bestRoute(grid, sc.seq);
    routes[sc.key] = route ? { ...sc, ...pathStats(route.points, elements), points: route.points } : { ...sc, invalid: true };
  }
  const total = valid + invalid;
  const score = total ? round(valid / total, 4) : 1;
  const premise = elements.find((e) => e.class === PREMISE);
  const violations = [...issues.values()].map((it) => {
    const el = elements.find((e) => e.id === it.id);
    const outside = premise && el.polygon.some((p) => !pointInPolygon(p, premise.polygon));
    const why = outside ? 'it is placed (partly) outside the premise' : 'no drivable corridor reaches it (too narrow or blocked)';
    return {
      category: 'HC', kind: 'Routing', ids: [it.id],
      text: `${it.cls} #${it.id} is unreachable for ${[...it.vehicles].join('/')} traffic: ${why}.`,
    };
  });
  return { score, valid, invalid, routes, grids, violations, scenarios };
}

// RE = mean of path-length, manoeuvrability and clearance ratios against the ground truth.
export function routingEfficiency(genRouting, gtRouting) {
  const keys = Object.keys(gtRouting.routes).filter((k) => !gtRouting.routes[k].invalid);
  if (!keys.length) return { pl: 0.5, mn: 0.5, pc: 0.5, score: 0.5 };
  for (const k of keys) if (!genRouting.routes[k] || genRouting.routes[k].invalid) return { pl: 0, mn: 0, pc: 0, score: 0 };
  const mean = (src, f) => keys.reduce((s, k) => s + src.routes[k][f], 0) / keys.length;
  const lGt = mean(gtRouting, 'length'), lGen = mean(genRouting, 'length');
  const nGt = mean(gtRouting, 'sharp'), nGen = mean(genRouting, 'sharp');
  const cGt = mean(gtRouting, 'clearance'), cGen = mean(genRouting, 'clearance');
  const ratio = (a, b) => (b < 1e-9 ? 0.5 : a / b);
  const pl = ratio(lGt, lGt + lGen);
  const mn = nGt + nGen < 1e-9 ? (nGt + 1) / (nGt + nGen + 2) : nGt / (nGt + nGen);
  const pc = ratio(cGen, cGt + cGen);
  return { pl: round(pl, 3), mn: round(mn, 3), pc: round(pc, 3), score: round((pl + mn + pc) / 3, 3) };
}
