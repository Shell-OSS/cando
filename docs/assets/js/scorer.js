// In-browser port of the ALPS-Bench constraint-satisfaction scorer
// (src/cando/agents/verifiers/{hard_constraints,assembly,orientation,proximity}).
import {
  area, bounds, centroid, edges, outsidePart, intersectionPieces, polyDist, pointInPolygon,
  segSegDist, segPolyDist, segLength, round, vertexMean,
} from './geometry.js';

export const PREMISE = 'Layout Polygon';
export const INPUT_CLASSES = ['Layout Polygon', 'Entrance', 'Exit'];
export const UNDERGROUND_CLASSES = ['Underground Service Facility'];
export const PARKING_SPACE_CLASSES = ['Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking', 'EV Service Area'];
export const WEIGHTS = { g: 0.2, f: 0.8, p: 0.6, c: 0.3, o: 0.1, alpha: 0.5 };

const byClass = (elements, cls) => elements.filter((e) => e.class === cls);
const clamp01 = (v) => Math.max(0, Math.min(0.99, v));

// ---------------------------------------------------------------------------
// Hard constraints: geometric validity (HC_g)
// ---------------------------------------------------------------------------

const OVERLAP_TOLERANCE = 0.1;
const OUT_OF_BOUNDARIES_TOLERANCE = 0.1;

export function evaluateHardConstraints(elements) {
  const premise = elements.find((e) => e.class === PREMISE);
  const others = elements.filter((e) => e.class !== PREMISE);
  const overlaps = [];
  const outOfBounds = [];

  for (let i = 0; i < others.length; i++) {
    for (let j = i + 1; j < others.length; j++) {
      let a = others[i], b = others[j];
      if (a.class !== b.class && (UNDERGROUND_CLASSES.includes(a.class) || UNDERGROUND_CLASSES.includes(b.class))) continue;
      const pieces = intersectionPieces(a.polygon, b.polygon);
      const interArea = pieces.reduce((s, p) => s + area(p), 0);
      if (interArea < 1e-9) continue;
      if (area(a.polygon) > area(b.polygon)) [a, b] = [b, a];
      const [x0, y0, x1, y1] = bounds(pieces.flat());
      if (round(x1 - x0) <= OVERLAP_TOLERANCE || round(y1 - y0) <= OVERLAP_TOLERANCE) continue;
      overlaps.push({ a, b, area: round(interArea) });
    }
  }

  if (premise) {
    for (const e of others) {
      const part = outsidePart(e.polygon, premise.polygon);
      if (part.outsideArea < 1e-6 || !part.bounds) continue;
      if (part.insideArea < 1e-9) {
        outOfBounds.push({ e, type: 'full', area: round(area(e.polygon)) });
        continue;
      }
      // The reference scorer ignores outside parts that split into several pieces.
      if (part.pieces !== 1) continue;
      const [x0, y0, x1, y1] = part.bounds;
      if (round(x1 - x0) <= OUT_OF_BOUNDARIES_TOLERANCE || round(y1 - y0) <= OUT_OF_BOUNDARIES_TOLERANCE) continue;
      outOfBounds.push({ e, type: 'partial', area: round(part.outsideArea) });
    }
  }

  let penalty = 0;
  for (const o of overlaps) penalty += 0.1 + 0.1 * (o.area / (Math.min(area(o.a.polygon), area(o.b.polygon)) || 1));
  for (const o of outOfBounds) penalty += 0.1 + 0.1 * (o.area / (area(o.e.polygon) || 1));

  const violations = [
    ...overlaps.map((o) => ({
      category: 'HC', kind: 'Overlap', ids: [o.a.id, o.b.id],
      text: `${o.a.class} #${o.a.id} overlaps ${o.b.class} #${o.b.id} (${o.area.toFixed(2)} m²).`,
    })),
    ...outOfBounds.map((o) => ({
      category: 'HC', kind: 'Boundary', ids: [o.e.id],
      text: `${o.e.class} #${o.e.id} is ${o.type === 'full' ? 'fully' : 'partially'} outside the premise (${o.area.toFixed(2)} m² outside).`,
    })),
  ];
  const score = violations.length ? clamp01(1 - penalty) : 1;
  return { score, violations, overlaps, outOfBounds };
}

// ---------------------------------------------------------------------------
// Compositionality (CO) — also produces the groups used by OR and PR
// ---------------------------------------------------------------------------

const ASSEMBLY_RULES = [
  { cls: 'Car Service Area', pattern: 'rectangular_or_single_line', counts: { odd: 'single_line', even: 'rectangular_blocks' }, threshold: 3.0, size: 4 },
  { cls: 'Bike Service Area', pattern: 'rectangular_or_single_line', counts: { odd: 'single_line', even: 'rectangular_blocks' }, threshold: 3.0, size: 2 },
  { cls: 'HGV Service Area', pattern: 'single_line', threshold: 10.0, size: 2 },
  { cls: 'Underground Service Facility', pattern: 'rectangular_or_single_line', threshold: 1.0, size: 2 },
  { cls: 'Priority Parking', pattern: 'single_line', threshold: 1.0, size: 2 },
  { cls: 'Car Parking', pattern: 'single_line', threshold: 0.5, size: 3 },
  { cls: 'Bike Parking', pattern: 'single_line', threshold: 0.5, size: 4 },
  { cls: 'EV Service Area', pattern: 'single_line', threshold: 10.0 },
];
const DEFAULT_THRESHOLD = 10.0;

export function clusterElements(elements) {
  const classes = new Map();
  for (const e of elements) {
    if (INPUT_CLASSES.includes(e.class)) continue;
    if (!classes.has(e.class)) classes.set(e.class, []);
    classes.get(e.class).push(e);
  }
  const groups = {};
  for (const [cls, list] of classes) {
    const rule = ASSEMBLY_RULES.find((r) => r.cls === cls);
    const threshold = rule ? rule.threshold : DEFAULT_THRESHOLD;
    const parent = list.map((_, i) => i);
    const find = (x) => (parent[x] === x ? x : (parent[x] = find(parent[x])));
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        if (polyDist(list[i].polygon, list[j].polygon) <= threshold) {
          const ri = find(i), rj = find(j);
          if (ri !== rj) parent[ri] = rj;
        }
      }
    }
    const buckets = new Map();
    list.forEach((e, i) => {
      const r = find(i);
      if (!buckets.has(r)) buckets.set(r, []);
      buckets.get(r).push(e);
    });
    groups[cls] = [...buckets.keys()].sort((a, b) => a - b).map((k, index) => ({
      index,
      elements: buckets.get(k),
      ids: buckets.get(k).map((e) => e.id).sort((a, b) => a - b),
    }));
  }
  return groups;
}

function detectSingleLine(elements, tolerance = 0.5) {
  const n = elements.length;
  if (n <= 2) return n > 0;
  const pts = elements.map((e) => vertexMean(e.polygon));
  const [mx, my] = [pts.reduce((s, p) => s + p[0], 0) / n, pts.reduce((s, p) => s + p[1], 0) / n];
  let sxx = 0, syy = 0, sxy = 0;
  for (const [x, y] of pts) { sxx += (x - mx) ** 2; syy += (y - my) ** 2; sxy += (x - mx) * (y - my); }
  // Principal direction of the centred point cloud (first right-singular vector).
  const theta = 0.5 * Math.atan2(2 * sxy, sxx - syy);
  const nx = -Math.sin(theta), ny = Math.cos(theta);
  const maxResidual = Math.max(...pts.map(([x, y]) => Math.abs((x - mx) * nx + (y - my) * ny)));
  let isLine = maxResidual < tolerance;
  if (n === 3 && isLine) {
    let maxPair = 0;
    for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) maxPair = Math.max(maxPair, Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]));
    if (maxResidual / Math.max(maxPair, 1e-6) > 0.05) isLine = false;
  }
  return isLine;
}

function gridShape(elements, tolerance = 1.5) {
  const poly = elements[0].polygon;
  const theta = Math.atan2(poly[1][1] - poly[0][1], poly[1][0] - poly[0][0]);
  const c = Math.cos(-theta), s = Math.sin(-theta);
  const xs = [], ys = [];
  for (const e of elements) {
    const [cx, cy] = vertexMean(e.polygon);
    xs.push(cx * c - cy * s);
    ys.push(cx * s + cy * c);
  }
  const count = (vals) => {
    const sorted = [...vals].sort((a, b) => a - b);
    let clusters = 1, last = sorted[0];
    for (const v of sorted.slice(1)) {
      if (v - last >= tolerance) clusters++;
      last = v;
    }
    return clusters;
  };
  return { rows: count(ys), cols: count(xs) };
}

function detectRectangular(elements) {
  const n = elements.length;
  if (n <= 1) return { isRect: false, rows: n, cols: 1 };
  const { rows, cols } = gridShape(elements);
  return { isRect: rows * cols === n && rows > 1 && cols > 1, rows, cols };
}

export function evaluateCompositionality(groups) {
  const violations = [];
  for (const rule of ASSEMBLY_RULES) {
    const clusters = groups[rule.cls];
    if (!clusters) continue;
    if (rule.size !== undefined) {
      const total = clusters.reduce((s, c) => s + c.elements.length, 0);
      const singletons = clusters.filter((c) => c.elements.length === 1).length;
      const avg = total / clusters.length;
      if (singletons / clusters.length > 0.5 && avg < rule.size * 0.75 && total >= 2) {
        violations.push({
          category: 'CO', kind: 'Fragmentation', ids: clusters.flatMap((c) => c.ids),
          text: `${rule.cls}: ${total} elements split into ${clusters.length} groups (${singletons} singletons); expected average group size ≈ ${rule.size}.`,
        });
      }
    }
    for (const cluster of clusters) {
      const n = cluster.elements.length;
      const required = rule.counts ? rule.counts[n % 2 === 1 ? 'odd' : 'even'] : rule.pattern;
      const isLine = detectSingleLine(cluster.elements);
      const rect = detectRectangular(cluster.elements);
      let bad = null;
      if (required === 'single_line' && !isLine) bad = 'a single line';
      else if (required === 'rectangular_blocks' && n > 2 && !rect.isRect) bad = 'a rectangular block (e.g. 2×2, 2×3)';
      else if (required === 'rectangular_or_single_line' && !isLine && !rect.isRect) bad = 'a single line or rectangular block';
      if (bad) {
        const detected = isLine ? 'a line' : rect.isRect ? `a ${rect.rows}×${rect.cols} block` : `an irregular ~${rect.rows}×${rect.cols} arrangement`;
        violations.push({
          category: 'CO', kind: 'Formation', ids: cluster.ids,
          text: `${rule.cls} group of ${n} should form ${bad}, but forms ${detected}.`,
        });
      }
    }
  }
  const score = violations.length ? clamp01(1 - 0.1 * violations.length) : 1;
  return { score, violations };
}

// ---------------------------------------------------------------------------
// Orientation (OR)
// ---------------------------------------------------------------------------

function longestEdgeAngle(poly) {
  let best = null;
  for (const e of edges(poly)) {
    const len = segLength(e);
    if (!best || len > best.len) best = { len, e };
  }
  const [[x1, y1], [x2, y2]] = best.e;
  return ((Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI % 180 + 180) % 180;
}

export function evaluateOrientation(groups, tolerance = 5) {
  const violations = [];
  let penalty = 0;
  for (const [cls, clusters] of Object.entries(groups)) {
    for (const cluster of clusters) {
      const els = cluster.elements;
      if (els.length < 2) continue;
      const diffs = [];
      const pairs = [];
      for (let i = 0; i < els.length; i++) {
        for (let j = i + 1; j < els.length; j++) {
          const a1 = longestEdgeAngle(els[i].polygon), a2 = longestEdgeAngle(els[j].polygon);
          const raw = Math.abs(a1 - a2);
          const parallel = Math.min(raw, 180 - raw) <= tolerance;
          const perpendicular = Math.abs(raw - 90) <= tolerance;
          if (!parallel && !perpendicular) {
            diffs.push(Math.min(raw, 180 - raw));
            pairs.push([els[i].id, els[j].id]);
          }
        }
      }
      if (diffs.length) {
        const avg = diffs.reduce((s, d) => s + d, 0) / diffs.length;
        // The reference scorer's violation records carry no angle, so its angle term falls back to 45°.
        penalty += 0.1 + (45 / 90) * 0.1;
        violations.push({
          category: 'OR', kind: 'Alignment', ids: cluster.ids,
          text: `${cls} group has ${pairs.length} misaligned pair(s) (avg. ${avg.toFixed(1)}° off parallel); rotate to be parallel or perpendicular within 5°.`,
        });
      }
    }
  }
  const score = violations.length ? clamp01(1 - penalty) : 1;
  return { score, violations };
}

// ---------------------------------------------------------------------------
// Proximity (PR)
// ---------------------------------------------------------------------------

const PRIORITIES = {
  'Priority Parking': 100, Shop: 80, 'Car Parking': 50, 'Bike Parking': 40, 'HGV Parking': 40,
  'EV Service Area': 30, 'Car Service Area': 20, 'Bike Service Area': 20, 'HGV Service Area': 20,
  'Underground Service Facility': 10, 'Underground Access Point': 10,
};

export const PROXIMITY_RULES = [
  { id: '1', type: 'stacking', source: 'Underground Access Point', sourceEdge: 'longer', targets: ['Layout Polygon'], targetEdge: 'any', tol: 0.5, proximityTargets: ['Car Service Area', 'Underground Service Facility'], maxProximity: 20, short: 'UAP (longer edge) stacked on the site boundary, ≤ 20 m from a CSA or USF' },
  { id: '2', type: 'stacking_or_adjacency', source: 'Car Parking', sourceEdge: 'shorter', primary: { classes: ['Shop'], edge: 'longer', relation: 'stacking' }, alternative: { cls: 'Layout Polygon', edge: 'any', relation: 'adjacency' }, stackTol: 0.5, adjTol: 2.0, short: 'Car Parking (shorter edge) stacked on the Shop’s longer edge, or adjacent to the site boundary' },
  { id: '3', type: 'clearance', sources: ['Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking', 'EV Service Area'], radius: 3.0, tol: 0.5, allowed: ['Layout Polygon', 'Underground Service Facility', 'Car Parking', 'Bike Parking', 'Priority Parking', 'HGV Parking', 'EV Service Area', 'Entrance', 'Exit'], short: 'Stacked parking keeps a 3 m, 180° egress zone in front of its exit edge' },
  { id: '4', type: 'stacking', source: 'Priority Parking', sourceEdge: 'shorter', targets: ['Shop'], targetEdge: 'any', tol: 0.5, entranceProximate: true, short: 'Priority Parking (shorter edge) stacked on the Shop, closest to the Entrance' },
  { id: '7', type: 'stacking_or_adjacency', source: 'EV Service Area', sourceEdge: 'any', primary: { classes: ['Layout Polygon'], edge: 'any', sourceEdge: 'any', relation: 'stacking' }, alternative: { cls: 'Shop', edge: 'any', sourceEdge: 'shorter', relation: 'stacking' }, stackTol: 0.5, adjTol: 2.0, short: 'EV Service Area stacked on the site boundary, or (shorter edge) on the Shop' },
  { id: '9', type: 'distance', source: 'HGV Service Area', targets: ['Car Service Area', 'Bike Service Area'], min: 6.0, tol: 0.5, short: 'HGV Service Area ≥ 6 m from Car/Bike Service Areas' },
  { id: '10', type: 'adjacency', sources: ['Underground Service Facility'], targets: ['Car Service Area', 'Bike Service Area', 'HGV Service Area'], max: 5.0, tol: 0.5, group: true, short: 'Each USF group has a member ≤ 5 m from a service area' },
  { id: '11', type: 'stacking', source: 'Bike Parking', sourceEdge: 'shorter', targets: ['Layout Polygon'], targetEdge: 'any', tol: 0.5, short: 'Bike Parking (shorter edge) stacked on the site boundary' },
  { id: '12', type: 'stacking', source: 'HGV Parking', sourceEdge: 'any', targets: ['Layout Polygon'], targetEdge: 'any', tol: 0.5, short: 'HGV Parking stacked on the site boundary' },
  { id: '14', type: 'stacking_or_adjacency', source: 'Shop', sourceEdge: 'longer', primary: { classes: ['Layout Polygon'], edge: 'any', relation: 'adjacency' }, alternative: { cls: 'Layout Polygon', edge: 'any', relation: 'stacking' }, stackTol: 5.0, adjTol: 5.0, short: 'Shop (longer edge) close to the site boundary' },
  { id: '15', type: 'adjacency', sources: ['Car Service Area', 'Bike Service Area', 'HGV Service Area'], targets: ['Underground Service Facility'], max: 10.0, tol: 0.5, group: true, short: 'Each service-area group has a USF within 10 m' },
];

function edgesByType(poly, type, thresholdPercent = 0.15) {
  if (poly.length < 3) return null;
  const all = edges(poly);
  if (type === 'any') return all;
  const data = all.map((e) => ({ len: segLength(e), e })).sort((a, b) => a.len - b.len);
  const ordered = type === 'shorter' ? data : [...data].reverse();
  const ref = ordered[0].len;
  const out = [];
  for (const d of ordered) {
    if (Math.abs(d.len - ref) <= ref * thresholdPercent) {
      out.push(d.e);
      if (out.length >= 2) break;
    }
  }
  return out.length ? out : null;
}

const edgeAngle = ([[x1, y1], [x2, y2]]) => (((Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI) % 180 + 180) % 180;

function parallel(e1, e2, tol) {
  const d = Math.abs(edgeAngle(e1) - edgeAngle(e2));
  return Math.min(d, 180 - d) <= tol;
}

function verifyStacking(src, tgt, srcEdge, tgtEdge, tol) {
  const se = edgesByType(src.polygon, srcEdge), te = edgesByType(tgt.polygon, tgtEdge);
  if (!se || !te) return { ok: false, distance: Infinity };
  let min = Infinity;
  for (const s of se) for (const t of te) if (parallel(s, t, 15)) min = Math.min(min, segSegDist(s, t));
  min = round(min);
  return { ok: min <= tol, distance: min };
}

function edgeTypeDistance(src, tgt, srcEdge, tgtEdge) {
  if (srcEdge === 'any' && tgtEdge === 'any') return polyDist(src.polygon, tgt.polygon);
  const se = edgesByType(src.polygon, srcEdge), te = edgesByType(tgt.polygon, tgtEdge);
  if (!se || !te) return polyDist(src.polygon, tgt.polygon);
  let min = Infinity;
  for (const s of se) for (const t of te) min = Math.min(min, segSegDist(s, t));
  return min;
}

function availableSpace(target, targetEdge, all, tol) {
  const tedges = edgesByType(target.polygon, targetEdge) || [];
  return tedges.map((edge) => {
    const occupying = all.filter((e) => e.id !== target.id && e.class !== PREMISE &&
      !UNDERGROUND_CLASSES.includes(e.class) && segPolyDist(edge, e.polygon) <= tol);
    let occupied = 0;
    for (const occ of occupying) {
      let best = null, bestDist = Infinity;
      for (const oe of edges(occ.polygon)) {
        if (!parallel(edge, oe, 5)) continue;
        const d = segSegDist(edge, oe);
        if (d < bestDist) { bestDist = d; best = oe; }
      }
      if (best && bestDist <= tol) occupied += segLength(best);
    }
    const total = segLength(edge);
    return { total, available: Math.max(0, total - occupied), occupying };
  });
}

function spaceCheck(src, srcEdge, tgt, tgtEdge, all, tol) {
  const se = edgesByType(src.polygon, srcEdge);
  if (!se) return { canFit: false };
  const required = segLength(se[0]);
  const spaces = availableSpace(tgt, tgtEdge, all, tol);
  if (!spaces.length) return { canFit: false };
  for (const s of spaces) {
    if (s.available >= required) return { canFit: true, available: true };
    if (s.total >= required) {
      const p = PRIORITIES[src.class] || 0;
      const lower = s.occupying.filter((o) => (PRIORITIES[o.class] || 0) < p);
      if (lower.length) return { canFit: true, lowerPriority: true };
      return { canFit: true, higherPriority: true };
    }
  }
  return { canFit: false };
}

function entranceProximate(src, shop, all, tol) {
  const entrances = byClass(all, 'Entrance');
  if (!entrances.length) return { ok: true };
  const minEnt = (e) => Math.min(...entrances.map((x) => polyDist(e.polygon, x.polygon)));
  const srcDist = minEnt(src);
  let closest = null, closestDist = Infinity;
  for (const e of all) {
    if (e.id === src.id || e.id === shop.id || !PARKING_SPACE_CLASSES.includes(e.class)) continue;
    if (polyDist(e.polygon, shop.polygon) > tol) continue;
    const d = minEnt(e);
    if (d < closestDist) { closestDist = d; closest = e; }
  }
  if (!closest || srcDist <= closestDist + tol) return { ok: true };
  return { ok: false, other: closest, otherDist: closestDist, srcDist };
}

function evalStacking(all, rule) {
  const sources = byClass(all, rule.source);
  const violations = [];
  if (!sources.length) return violations;
  const proxTargets = rule.maxProximity !== undefined ? rule.proximityTargets.flatMap((c) => byClass(all, c)) : [];
  for (const src of sources) {
    let matched = null, minDist = Infinity, space = null, bestDist = null;
    outer: for (const tc of rule.targets) {
      for (const tgt of byClass(all, tc)) {
        const r = verifyStacking(src, tgt, rule.sourceEdge, rule.targetEdge, rule.tol);
        if (r.ok) { matched = tgt; space = null; break outer; }
        if (r.distance < minDist) {
          minDist = r.distance; bestDist = r.distance;
          space = spaceCheck(src, rule.sourceEdge, tgt, rule.targetEdge, all, rule.tol);
        }
      }
    }
    if (matched) {
      if (proxTargets.length) {
        const d = Math.min(...proxTargets.map((p) => polyDist(src.polygon, p.polygon)));
        if (d > rule.maxProximity) {
          violations.push({ ids: [src.id], text: `${src.class} #${src.id} is stacked but ${d.toFixed(1)} m from the nearest ${rule.proximityTargets.join(' / ')} (limit ${rule.maxProximity} m).` });
          continue;
        }
      }
      if (rule.entranceProximate) {
        const r = entranceProximate(src, matched, all, rule.tol);
        if (!r.ok) {
          violations.push({ ids: [src.id, r.other.id], text: `${src.class} #${src.id} is not the parking bay closest to the Entrance on Shop #${matched.id} (${r.other.class} #${r.other.id} is closer: ${r.otherDist.toFixed(1)} m vs ${r.srcDist.toFixed(1)} m).` });
        }
      }
      continue;
    }
    const notStacked = { ids: [src.id], text: `${src.class} #${src.id} is not stacked against ${rule.targets.join(' or ')}${bestDist !== null && Number.isFinite(bestDist) ? ` (closest parallel edge ${bestDist.toFixed(2)} m away)` : ''}.` };
    if (!space) violations.push(notStacked);
    else if (!space.canFit || space.higherPriority) continue;
    else violations.push(notStacked);
  }
  return violations;
}

function evalStackingOrAdjacency(all, rule) {
  const violations = [];
  for (const src of byClass(all, rule.source)) {
    const attempt = (classes, edge, srcEdge, relation) => {
      for (const cls of classes) {
        for (const tgt of byClass(all, cls)) {
          if (relation === 'stacking') {
            if (verifyStacking(src, tgt, srcEdge, edge, rule.stackTol).ok) return true;
          } else if (edgeTypeDistance(src, tgt, srcEdge, edge) <= rule.adjTol + 0.5) return true;
        }
      }
      return false;
    };
    const p = rule.primary, a = rule.alternative;
    const ok = attempt(p.classes, p.edge, p.sourceEdge || rule.sourceEdge, p.relation) ||
      attempt([a.cls], a.edge, a.sourceEdge || rule.sourceEdge, a.relation);
    if (!ok) {
      violations.push({ ids: [src.id], text: `${src.class} #${src.id} is neither ${p.relation === 'stacking' ? 'stacked on' : 'adjacent to'} ${p.classes.join(' or ').replace(PREMISE, 'the site boundary')} nor ${a.relation === 'stacking' ? 'stacked on' : 'adjacent to'} ${a.cls.replace(PREMISE, 'the site boundary')}.` });
    }
  }
  return violations;
}

function evalDistance(all, rule) {
  const violations = [];
  const targets = rule.targets.flatMap((c) => byClass(all, c));
  if (!targets.length) return violations;
  for (const src of byClass(all, rule.source)) {
    for (const tgt of targets) {
      const d = polyDist(src.polygon, tgt.polygon);
      if (d < rule.min - rule.tol) {
        violations.push({ ids: [src.id, tgt.id], text: `${src.class} #${src.id} is only ${d.toFixed(1)} m from ${tgt.class} #${tgt.id} (minimum ${rule.min} m).` });
      }
    }
  }
  return violations;
}

function evalAdjacency(all, groups, rule) {
  const violations = [];
  const sources = rule.sources.flatMap((c) => byClass(all, c));
  const targets = rule.targets.flatMap((c) => byClass(all, c));
  if (!sources.length || !targets.length) return violations;
  for (const cls of rule.sources) {
    for (const g of groups[cls] || []) {
      const ok = g.elements.some((s) => targets.some((t) => polyDist(s.polygon, t.polygon) <= rule.max + rule.tol));
      if (!ok) {
        violations.push({ ids: g.ids, text: `No ${cls} in group [${g.ids.join(', ')}] is within ${rule.max} m of a ${rule.targets.join(' / ')}.` });
      }
    }
  }
  return violations;
}

function stackedContacts(src, all, tol) {
  const sedges = edges(src.polygon);
  const contacts = new Map();
  sedges.forEach((se, idx) => {
    const minOverlap = Math.max(tol / 2, segLength(se) * 0.1);
    for (const tgt of all) {
      if (tgt.id === src.id) continue;
      for (const te of edges(tgt.polygon)) {
        if (!parallel(se, te, 15)) continue;
        const d = segSegDist(se, te);
        if (d > tol) continue;
        if (projectionOverlap(se, te) < minOverlap) continue;
        if (!contacts.has(idx)) contacts.set(idx, []);
        contacts.get(idx).push({ cls: tgt.class, distance: round(d) });
        break;
      }
    }
  });
  return contacts;
}

function projectionOverlap(e1, e2) {
  const dx = e1[1][0] - e1[0][0], dy = e1[1][1] - e1[0][1];
  const m = Math.hypot(dx, dy);
  if (m === 0) return 0;
  const proj = (p) => (p[0] * dx + p[1] * dy) / m;
  const a = [proj(e1[0]), proj(e1[1])], b = [proj(e2[0]), proj(e2[1])];
  return Math.max(0, Math.min(Math.max(...a), Math.max(...b)) - Math.max(Math.min(...a), Math.min(...b)));
}

function outwardNormal(poly, edge) {
  const c = centroid(poly);
  const mid = [(edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2];
  const dx = edge[1][0] - edge[0][0], dy = edge[1][1] - edge[0][1];
  const m = Math.hypot(dx, dy);
  if (!m) return null;
  const n1 = [-dy / m, dx / m], n2 = [dy / m, -dx / m];
  const al = (n) => (mid[0] - c[0]) * n[0] + (mid[1] - c[1]) * n[1];
  return al(n1) >= al(n2) ? n1 : n2;
}

export function exitZone(poly, edge, radius) {
  const n = outwardNormal(poly, edge);
  if (!n) return null;
  const mid = [(edge[0][0] + edge[1][0]) / 2, (edge[0][1] + edge[1][1]) / 2];
  const base = Math.atan2(n[1], n[0]);
  const pts = [];
  for (let k = 0; k <= 64; k++) {
    const t = base - Math.PI / 2 + (Math.PI * k) / 64;
    pts.push([mid[0] + radius * Math.cos(t), mid[1] + radius * Math.sin(t)]);
  }
  return pts;
}

function evalClearance(all, rule) {
  const violations = [];
  const allowed = new Set([...rule.allowed, ...rule.sources]);
  const zones = [];
  for (const src of all.filter((e) => rule.sources.includes(e.class))) {
    const contacts = stackedContacts(src, all, rule.tol);
    if (!contacts.size) continue;
    const sedges = edges(src.polygon);
    const nonParking = [...contacts].filter(([, cs]) => cs.some((c) => !PARKING_SPACE_CLASSES.includes(c.cls))).map(([i]) => i);
    const candidates = nonParking.length ? nonParking : [...contacts.keys()].sort((a, b) => a - b);
    const minD = new Map(candidates.map((i) => [i, Math.min(...contacts.get(i).map((c) => c.distance))]));
    const best = Math.min(...minD.values());
    const refEdges = candidates.filter((i) => minD.get(i) <= best + rule.tol / 2);

    let exitCandidates = sedges.map((_, i) => i).filter((i) => !refEdges.includes(i));
    if (!exitCandidates.length) {
      violations.push({ ids: [src.id], text: `${src.class} #${src.id} is enclosed on every side, leaving no exit edge for vehicles.` });
      continue;
    }
    if (refEdges.length === 1) {
      let opp = null, oppDist = -Infinity;
      sedges.forEach((e, i) => {
        if (i === refEdges[0] || !parallel(sedges[refEdges[0]], e, 10)) return;
        const d = segSegDist(sedges[refEdges[0]], e);
        if (d > oppDist) { oppDist = d; opp = i; }
      });
      if (opp !== null) exitCandidates = [opp];
    }
    if (exitCandidates.length > 1 || (refEdges.length !== 1)) {
      let dx = 0, dy = 0;
      for (const i of refEdges) {
        const n = outwardNormal(src.polygon, sedges[i]);
        if (n) { dx -= n[0]; dy -= n[1]; }
      }
      const m = Math.hypot(dx, dy);
      if (m > 0) {
        const score = (i) => { const n = outwardNormal(src.polygon, sedges[i]); return n ? (n[0] * dx + n[1] * dy) / m : -Infinity; };
        exitCandidates = [...exitCandidates].sort((a, b) => score(b) - score(a));
      }
    }

    let bestIntrusions = null, bestZone = null;
    for (const idx of exitCandidates) {
      const zone = exitZone(src.polygon, sedges[idx], rule.radius + rule.tol);
      if (!zone) continue;
      const intrusions = all.filter((e) => e.id !== src.id && !allowed.has(e.class) && polyDist(zone, e.polygon) <= 1e-9);
      if (!intrusions.length) { bestIntrusions = []; bestZone = zone; break; }
      if (!bestIntrusions || intrusions.length < bestIntrusions.length) { bestIntrusions = intrusions; bestZone = zone; }
    }
    if (bestZone) zones.push({ id: src.id, zone: bestZone, ok: !bestIntrusions.length });
    for (const e of bestIntrusions || []) {
      violations.push({ ids: [src.id, e.id], text: `${e.class} #${e.id} intrudes into the 3 m egress zone of ${src.class} #${src.id}.` });
    }
  }
  return { violations, zones };
}

export function evaluateProximity(elements, groups) {
  const violations = [];
  let zones = [];
  for (const rule of PROXIMITY_RULES) {
    let vs;
    if (rule.type === 'stacking') vs = evalStacking(elements, rule);
    else if (rule.type === 'stacking_or_adjacency') vs = evalStackingOrAdjacency(elements, rule);
    else if (rule.type === 'distance') vs = evalDistance(elements, rule);
    else if (rule.type === 'adjacency') vs = evalAdjacency(elements, groups, rule);
    else if (rule.type === 'clearance') { const r = evalClearance(elements, rule); vs = r.violations; zones = r.zones; }
    for (const v of vs) violations.push({ category: 'PR', kind: `Rule ${rule.id}`, rule: rule.short, ...v });
  }
  const score = violations.length ? clamp01(1 - 0.1 * violations.length) : 1;
  return { score, violations, zones };
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

export function evaluateConstraints(elements) {
  const hc = evaluateHardConstraints(elements);
  const groups = clusterElements(elements);
  const co = evaluateCompositionality(groups);
  const or = evaluateOrientation(groups);
  const pr = evaluateProximity(elements, groups);
  const scm = round(WEIGHTS.p * pr.score + WEIGHTS.c * co.score + WEIGHTS.o * or.score, 3);
  return { hc, co, or, pr, scm, groups };
}

export function combineScores({ hcGeo, hcFlow, scm }) {
  const hcm = round(WEIGHTS.g * hcGeo + WEIGHTS.f * hcFlow, 3);
  const cs = hcm === 1 ? scm : round(WEIGHTS.alpha * hcm * scm, 3);
  return { hcm, cs };
}

export { pointInPolygon };
