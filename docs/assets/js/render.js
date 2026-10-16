// SVG layout renderer mirroring the matplotlib aesthetic of scripts/build_public_dataset.py.
import { area, centroid, edges, segLength } from './geometry.js';

export const LAYOUT_CLASS = 'Layout Polygon';
export const LAYOUT_BOUNDARY_COLOR = '#2B2B2B';
export const DEFAULT_FILL_COLOR = '#B0B0B0';
export const EDGE_DARKEN_FACTOR = 0.65;
export const LABEL_TEXT_COLOR = '#1A1A1A';
export const LABEL_HALO_COLOR = '#FFFFFF';

export const CLASS_COLORS = {
  Entrance: '#5B8C5A',
  Exit: '#B5654A',
  'Car Service Area': '#6C93B8',
  'Bike Service Area': '#93C0D9',
  'HGV Service Area': '#3F6C8A',
  'EV Service Area': '#7FAE8C',
  'Car Parking': '#D9AE58',
  'Bike Parking': '#E8CD8A',
  'Priority Parking': '#C97B4A',
  'HGV Parking': '#9C7A4E',
  Shop: '#B98868',
  'Underground Service Facility': '#8C7FA0',
  'Underground Access Point': '#5E5570',
};

export const ABBREVIATIONS = {
  Entrance: 'ENTRANCE',
  Exit: 'EXIT',
  'Car Service Area': 'CSA',
  'Bike Service Area': 'BSA',
  'HGV Service Area': 'HGVS',
  'EV Service Area': 'EVSA',
  'Car Parking': 'CP',
  'Bike Parking': 'BP',
  'Priority Parking': 'PP',
  'HGV Parking': 'HGVP',
  Shop: 'SHOP',
  'Underground Service Facility': 'USF',
  'Underground Access Point': 'UAP',
};

export const CLASS_ORDER = Object.keys(ABBREVIATIONS);
export const UNDERGROUND_CLASSES = new Set(['Underground Service Facility']);
const UNDERGROUND_FILL_ALPHA = 0.5;

// Matplotlib figure geometry used to convert point sizes to data units.
const FIGURE_WIDTH_IN = 12, MIN_FIGURE_HEIGHT_IN = 4, MAX_FIGURE_HEIGHT_IN = 18, PLOT_MARGIN_FRACTION = 0.04;
const AXES_WIDTH_FRACTION = 0.775, AXES_HEIGHT_FRACTION = 0.77;

const SVG_NS = 'http://www.w3.org/2000/svg';
let patternCounter = 0;

export function darken(hex, factor = EDGE_DARKEN_FACTOR) {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.round(v * factor));
  return `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

export function el(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

export const pointsAttr = (poly) => poly.map(([x, y]) => `${x.toFixed(3)},${(-y).toFixed(3)}`).join(' ');

// Text rotation (degrees, upright) aligned with the polygon's longest edge.
export function labelAngle(poly) {
  let max = 0, angle = 0;
  for (const [[x1, y1], [x2, y2]] of edges(poly)) {
    const len = Math.hypot(x2 - x1, y2 - y1);
    if (len > max) { max = len; angle = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI; }
  }
  if (angle > 90) angle -= 180; else if (angle < -90) angle += 180;
  return angle;
}

export function layoutFrame(elements) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const e of elements) for (const [x, y] of e.polygon) {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x); miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }
  const w = Math.max(maxx - minx, 1e-6), h = Math.max(maxy - miny, 1e-6);
  const margin = Math.max(w, h) * PLOT_MARGIN_FRACTION;
  const figH = Math.min(Math.max(FIGURE_WIDTH_IN * (h / w), MIN_FIGURE_HEIGHT_IN), MAX_FIGURE_HEIGHT_IN);
  const spanX = w + 2 * margin, spanY = h + 2 * margin;
  const inchPerUnit = Math.min((FIGURE_WIDTH_IN * AXES_WIDTH_FRACTION) / spanX, (figH * AXES_HEIGHT_FRACTION) / spanY);
  return { minx, miny, maxx, maxy, margin, spanX, spanY, unitsPerPoint: 1 / 72 / inchPerUnit };
}

function hatchPattern(defs, color, spacing) {
  const id = `hatch-${patternCounter++}`;
  const p = el('pattern', { id, patternUnits: 'userSpaceOnUse', width: spacing, height: spacing, patternTransform: 'rotate(-45)' }, defs);
  el('line', { x1: 0, y1: 0, x2: 0, y2: spacing, stroke: color, 'stroke-width': spacing * 0.18 }, p);
  return id;
}

/**
 * Renders a layout into an <svg>. Returns handles for interactive use.
 * opts: { labels, lineScale, frame, className, onElement(node, element) }
 */
export function renderLayout(svg, elements, opts = {}) {
  const { labels = true, lineScale = 1, frame = layoutFrame(elements), hatchSpacing } = opts;
  svg.innerHTML = '';
  svg.setAttribute('viewBox', `${frame.minx - frame.margin} ${-(frame.maxy + frame.margin)} ${frame.spanX} ${frame.spanY}`);
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  const defs = el('defs', {}, svg);
  const upp = frame.unitsPerPoint;

  const layers = {
    boundary: el('g', { class: 'lyr-boundary' }, svg),
    underground: el('g', { class: 'lyr-underground' }, svg),
    undergroundLabels: el('g', { class: 'lyr-ulabels', 'pointer-events': 'none' }, svg),
    elements: el('g', { class: 'lyr-elements' }, svg),
    labels: el('g', { class: 'lyr-labels', 'pointer-events': 'none' }, svg),
    overlay: el('g', { class: 'lyr-overlay' }, svg),
  };

  const nodes = new Map();
  const hatchIds = {};
  for (const e of elements) {
    if (e.class === LAYOUT_CLASS) {
      el('polygon', {
        points: pointsAttr(e.polygon), fill: 'none', stroke: LAYOUT_BOUNDARY_COLOR,
        'stroke-width': 1.4 * lineScale, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'round',
      }, layers.boundary);
      continue;
    }
    const color = CLASS_COLORS[e.class] || DEFAULT_FILL_COLOR;
    const underground = UNDERGROUND_CLASSES.has(e.class);
    const g = el('g', { class: 'elem', 'data-id': e.id, 'data-class': e.class }, underground ? layers.underground : layers.elements);
    const poly = el('polygon', {
      points: pointsAttr(e.polygon), fill: color, stroke: darken(color),
      'stroke-width': 0.8 * lineScale, 'vector-effect': 'non-scaling-stroke', 'stroke-linejoin': 'miter',
    }, g);
    if (underground) {
      g.setAttribute('opacity', UNDERGROUND_FILL_ALPHA);
      poly.setAttribute('stroke-dasharray', `${3.2 * lineScale} ${1.6 * lineScale}`);
      if (!hatchIds[color]) hatchIds[color] = hatchPattern(defs, darken(color), hatchSpacing || upp * 5);
      el('polygon', { points: pointsAttr(e.polygon), fill: `url(#${hatchIds[color]})`, stroke: 'none', 'pointer-events': 'none', class: 'hatch' }, g);
    }
    const node = { g, poly, label: null, element: e };
    if (labels && ABBREVIATIONS[e.class]) node.label = drawLabel(underground ? layers.undergroundLabels : layers.labels, e, upp);
    nodes.set(e.id, node);
    if (opts.onElement) opts.onElement(node, e);
  }
  return { svg, frame, layers, nodes, defs };
}

export function drawLabel(parent, e, upp) {
  const [cx, cy] = centroid(e.polygon);
  const fontPt = Math.min(Math.max(Math.sqrt(area(e.polygon)) * 0.55, 6), 11);
  const t = el('text', {
    x: 0, y: 0,
    transform: `translate(${cx.toFixed(3)} ${(-cy).toFixed(3)}) rotate(${(-labelAngle(e.polygon)).toFixed(2)})`,
    'font-size': (fontPt * upp).toFixed(4),
    'text-anchor': 'middle', 'dominant-baseline': 'central',
    fill: LABEL_TEXT_COLOR, stroke: LABEL_HALO_COLOR, 'stroke-width': (2.2 * upp).toFixed(4),
    'paint-order': 'stroke', 'stroke-linejoin': 'round', class: 'elem-label',
  }, parent);
  t.textContent = ABBREVIATIONS[e.class];
  return t;
}

export function updateElementNode(node, e, upp) {
  node.poly.setAttribute('points', pointsAttr(e.polygon));
  const hatch = node.g.querySelector('.hatch');
  if (hatch) hatch.setAttribute('points', pointsAttr(e.polygon));
  if (node.label) {
    const [cx, cy] = centroid(e.polygon);
    node.label.setAttribute('transform', `translate(${cx.toFixed(3)} ${(-cy).toFixed(3)}) rotate(${(-labelAngle(e.polygon)).toFixed(2)})`);
  }
}

export function legendItems(elements) {
  const present = new Set(elements.map((e) => e.class));
  return CLASS_ORDER.filter((c) => present.has(c));
}

export function legendSwatch(cls) {
  const color = CLASS_COLORS[cls] || DEFAULT_FILL_COLOR;
  const underground = UNDERGROUND_CLASSES.has(cls);
  const style = `background:${color};border-color:${darken(color)};${underground ? 'opacity:.5;border-style:dashed;background-image:repeating-linear-gradient(-45deg,transparent 0 3px,' + darken(color) + ' 3px 4px);' : ''}`;
  return `<span class="swatch" style="${style}"></span>`;
}

export function legendHTML(elements, { interactive = false } = {}) {
  const rows = [`<li class="legend-row legend-boundary"><span class="swatch-line"></span>Site Boundary</li>`];
  for (const cls of legendItems(elements)) {
    rows.push(`<li class="legend-row${interactive ? ' is-toggle' : ''}" data-class="${cls}">${legendSwatch(cls)}${cls} (${ABBREVIATIONS[cls]})</li>`);
  }
  return `<ul class="legend">${rows.join('')}</ul>`;
}

export function decode(classes, record) {
  return record.elements.map(([ci, poly], id) => ({ id, class: classes[ci], polygon: poly }));
}

export const edgeLength = segLength;
