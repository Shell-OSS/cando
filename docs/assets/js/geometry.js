// Plane geometry helpers for the in-browser ALPS-Bench scorer and renderer.
// Polygons are arrays of [x, y] points (open rings, no repeated closing point).

export const EPS = 1e-9;

export function signedArea(poly) {
  let s = 0;
  for (let i = 0, n = poly.length; i < n; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % n];
    s += x1 * y2 - x2 * y1;
  }
  return s / 2;
}

export const area = (poly) => Math.abs(signedArea(poly));

export function ccw(poly) {
  return signedArea(poly) < 0 ? [...poly].reverse() : poly;
}

// Area-weighted centroid (shapely `Polygon.centroid`).
export function centroid(poly) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0, n = poly.length; i < n; i++) {
    const [x1, y1] = poly[i];
    const [x2, y2] = poly[(i + 1) % n];
    const f = x1 * y2 - x2 * y1;
    a += f; cx += (x1 + x2) * f; cy += (y1 + y2) * f;
  }
  if (Math.abs(a) < EPS) return vertexMean(poly);
  return [cx / (3 * a), cy / (3 * a)];
}

export function vertexMean(poly) {
  let x = 0, y = 0;
  for (const p of poly) { x += p[0]; y += p[1]; }
  return [x / poly.length, y / poly.length];
}

export function bounds(points) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const [x, y] of points) {
    if (x < minx) minx = x; if (y < miny) miny = y;
    if (x > maxx) maxx = x; if (y > maxy) maxy = y;
  }
  return [minx, miny, maxx, maxy];
}

export function edges(poly) {
  const out = [];
  for (let i = 0, n = poly.length; i < n; i++) out.push([poly[i], poly[(i + 1) % n]]);
  return out;
}

export const segLength = ([a, b]) => Math.hypot(b[0] - a[0], b[1] - a[1]);

export function pointInPolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function pointSegDist([px, py], [[ax, ay], [bx, by]]) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function orient(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

export function segmentsIntersect(p1, p2, q1, q2) {
  const d1 = orient(q1, q2, p1), d2 = orient(q1, q2, p2);
  const d3 = orient(p1, p2, q1), d4 = orient(p1, p2, q2);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
  const on = (a, b, c) => Math.min(a[0], b[0]) - EPS <= c[0] && c[0] <= Math.max(a[0], b[0]) + EPS &&
    Math.min(a[1], b[1]) - EPS <= c[1] && c[1] <= Math.max(a[1], b[1]) + EPS;
  if (Math.abs(d1) < EPS && on(q1, q2, p1)) return true;
  if (Math.abs(d2) < EPS && on(q1, q2, p2)) return true;
  if (Math.abs(d3) < EPS && on(p1, p2, q1)) return true;
  if (Math.abs(d4) < EPS && on(p1, p2, q2)) return true;
  return false;
}

// shapely LineString.distance between two segments.
export function segSegDist(s, t) {
  if (segmentsIntersect(s[0], s[1], t[0], t[1])) return 0;
  return Math.min(pointSegDist(s[0], t), pointSegDist(s[1], t), pointSegDist(t[0], s), pointSegDist(t[1], s));
}

// shapely Polygon.distance (0 when the polygons touch, cross, or contain each other).
export function polyDist(a, b) {
  if (pointInPolygon(a[0], b) || pointInPolygon(b[0], a)) return 0;
  let best = Infinity;
  const ea = edges(a), eb = edges(b);
  for (const s of ea) {
    for (const t of eb) {
      const d = segSegDist(s, t);
      if (d < best) { best = d; if (best === 0) return 0; }
    }
  }
  return best;
}

// shapely LineString.distance(Polygon).
export function segPolyDist(seg, poly) {
  if (pointInPolygon(seg[0], poly) || pointInPolygon(seg[1], poly)) return 0;
  let best = Infinity;
  for (const e of edges(poly)) best = Math.min(best, segSegDist(seg, e));
  return best;
}

export function pointPolyBoundaryDist(p, poly) {
  let best = Infinity;
  for (const e of edges(poly)) best = Math.min(best, pointSegDist(p, e));
  return best;
}

// Sutherland-Hodgman: clips `subject` (any simple polygon) by a convex `clip` polygon.
export function clipConvex(subject, clip) {
  const c = ccw(clip);
  let output = subject;
  for (let i = 0; i < c.length && output.length; i++) {
    const a = c[i], b = c[(i + 1) % c.length];
    const input = output;
    output = [];
    const inside = (p) => orient(a, b, p) >= -EPS;
    const cross = (p, q) => {
      const dx = q[0] - p[0], dy = q[1] - p[1];
      const ex = b[0] - a[0], ey = b[1] - a[1];
      const den = dx * ey - dy * ex;
      if (Math.abs(den) < EPS) return q;
      const t = ((a[0] - p[0]) * ey - (a[1] - p[1]) * ex) / den;
      return [p[0] + t * dx, p[1] + t * dy];
    };
    for (let j = 0; j < input.length; j++) {
      const cur = input[j], prev = input[(j + input.length - 1) % input.length];
      if (inside(cur)) {
        if (!inside(prev)) output.push(cross(prev, cur));
        output.push(cur);
      } else if (inside(prev)) {
        output.push(cross(prev, cur));
      }
    }
  }
  return output;
}

function segIntersectionPoint(p1, p2, q1, q2) {
  const rx = p2[0] - p1[0], ry = p2[1] - p1[1];
  const sx = q2[0] - q1[0], sy = q2[1] - q1[1];
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < EPS) return null;
  const t = ((q1[0] - p1[0]) * sy - (q1[1] - p1[1]) * sx) / den;
  const u = ((q1[0] - p1[0]) * ry - (q1[1] - p1[1]) * rx) / den;
  if (t < -EPS || t > 1 + EPS || u < -EPS || u > 1 + EPS) return null;
  return [p1[0] + t * rx, p1[1] + t * ry];
}

export function isConvex(poly) {
  let sign = 0;
  for (let i = 0, n = poly.length; i < n; i++) {
    const o = orient(poly[i], poly[(i + 1) % n], poly[(i + 2) % n]);
    if (Math.abs(o) < EPS) continue;
    if (sign === 0) sign = Math.sign(o);
    else if (Math.sign(o) !== sign) return false;
  }
  return true;
}

// Ear-clipping triangulation of a simple polygon.
export function triangulate(poly) {
  const pts = ccw(poly).slice();
  const tris = [];
  let guard = 0;
  while (pts.length > 3 && guard++ < 10000) {
    let clipped = false;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[(i + pts.length - 1) % pts.length], b = pts[i], c = pts[(i + 1) % pts.length];
      if (orient(a, b, c) <= EPS) continue;
      const tri = [a, b, c];
      if (pts.some((p) => p !== a && p !== b && p !== c && pointInPolygon(p, tri))) continue;
      tris.push(tri);
      pts.splice(i, 1);
      clipped = true;
      break;
    }
    if (!clipped) break;
  }
  if (pts.length === 3) tris.push(pts);
  return tris;
}

// Pieces of `subject ∩ other` (areas sum to the intersection area).
export function intersectionPieces(subject, other) {
  const parts = isConvex(other) ? [other] : triangulate(other);
  return parts.map((p) => clipConvex(subject, p)).filter((p) => p.length >= 3);
}

export const intersectionArea = (a, b) => intersectionPieces(a, b).reduce((s, p) => s + area(p), 0);

// Number of separate runs of `poly`'s boundary lying outside `container` (sampled).
function outsideRuns(poly, container, samples = 240) {
  const perim = edges(poly).reduce((s, e) => s + segLength(e), 0);
  const flags = [];
  for (const [a, b] of edges(poly)) {
    const k = Math.max(2, Math.round((segLength([a, b]) / perim) * samples));
    for (let i = 0; i < k; i++) {
      const t = (i + 0.5) / k;
      const p = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
      flags.push(!pointInPolygon(p, container) && pointPolyBoundaryDist(p, container) > 1e-6);
    }
  }
  let runs = 0;
  for (let i = 0; i < flags.length; i++) if (flags[i] && !flags[(i + flags.length - 1) % flags.length]) runs++;
  if (runs === 0 && flags.length && flags.every(Boolean)) runs = 1;
  return runs;
}

// Area, bounding box, and piece count of `elem \ premise`.
export function outsidePart(elem, premise) {
  const insideArea = intersectionArea(premise, elem);
  const outsideArea = Math.max(0, area(elem) - insideArea);
  const pts = [];
  for (const p of elem) if (!pointInPolygon(p, premise)) pts.push(p);
  for (const p of premise) if (pointInPolygon(p, elem)) pts.push(p);
  for (const [a, b] of edges(elem)) {
    for (const [c, d] of edges(premise)) {
      const x = segIntersectionPoint(a, b, c, d);
      if (x) pts.push(x);
    }
  }
  return {
    insideArea, outsideArea,
    bounds: pts.length ? bounds(pts) : null,
    pieces: outsideArea > 1e-6 ? outsideRuns(elem, premise) : 0,
  };
}

export function rotatePoint([x, y], [cx, cy], rad) {
  const c = Math.cos(rad), s = Math.sin(rad);
  const dx = x - cx, dy = y - cy;
  return [cx + dx * c - dy * s, cy + dx * s + dy * c];
}

export const round = (v, d = 2) => {
  const f = 10 ** d;
  return Math.round(v * f) / f;
};
