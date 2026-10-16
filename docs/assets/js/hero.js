// Hero animation: a validation layout is scrambled, then repaired over a few
// "refinement steps" while agent chips light up and live scores update.
import { centroid, rotatePoint } from './geometry.js';
import { layoutFrame, renderLayout, updateElementNode } from './render.js';
import { evaluateConstraints } from './scorer.js';

const FIXED = new Set(['Layout Polygon', 'Entrance', 'Exit']);
const STEPS = 6;
const AGENTS = ['verifier', 'historian', 'planner', 'generator'];

function mulberry32(seed) {
  return () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pose(gt, off) {
  const c = centroid(gt);
  return gt.map((p) => {
    const r = rotatePoint(p, c, off.rot);
    return [r[0] + off.dx, r[1] + off.dy];
  });
}

export function initHero(samples) {
  const svg = document.getElementById('hero-svg');
  const stepEl = document.getElementById('hero-step');
  document.getElementById('hero-steps').textContent = STEPS;
  const chips = Object.fromEntries([...document.querySelectorAll('#hero-agents span')].map((s) => [s.dataset.agent, s]));
  const meters = {
    hc: [document.getElementById('hero-hc'), document.getElementById('hero-hc-v')],
    scm: [document.getElementById('hero-scm'), document.getElementById('hero-scm-v')],
  };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const pool = samples
    .filter((s) => s.n >= 14 && s.n <= 30 && s.scm === 1 && ['Shop', 'Car Service Area', 'Car Parking'].every((c) => s.elements.some((e) => e.class === c)))
    .sort((a, b) => b.complexity - a.complexity)
    .slice(0, 6);
  if (!pool.length) return;

  let visible = true;
  new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { threshold: 0.05 }).observe(svg);
  const isHidden = () => !visible;

  const setMeter = (key, v) => {
    const [bar, label] = meters[key];
    bar.style.width = `${(v * 100).toFixed(1)}%`;
    bar.style.backgroundPosition = `${(1 - v) * 100}% 0`;
    label.textContent = v.toFixed(2);
  };

  async function run(sample, seed) {
    const rng = mulberry32(seed);
    const gt = sample.elements;
    const offsets = new Map();
    for (const e of gt) {
      if (FIXED.has(e.class) || rng() < 0.35) continue;
      const ang = rng() * Math.PI * 2, dist = 2.5 + rng() * 5;
      offsets.set(e.id, { dx: Math.cos(ang) * dist, dy: Math.sin(ang) * dist, rot: (rng() < 0.55 ? (rng() - 0.5) * 1.1 : 0) });
    }
    const current = gt.map((e) => ({ ...e, polygon: offsets.has(e.id) ? pose(e.polygon, offsets.get(e.id)) : e.polygon }));
    const frame = layoutFrame(gt);
    svg.parentElement.style.aspectRatio = Math.min(1.7, Math.max(1.05, frame.spanX / frame.spanY)).toFixed(3);
    const handle = renderLayout(svg, current, { labels: true, frame });
    svg.style.opacity = 0;
    requestAnimationFrame(() => { svg.style.transition = 'opacity .6s'; svg.style.opacity = 1; });

    const score = () => {
      const r = evaluateConstraints(current);
      setMeter('hc', r.hc.score);
      setMeter('scm', r.scm);
      const bad = new Set([...r.hc.overlaps.flatMap((o) => [o.a.id, o.b.id]), ...r.hc.outOfBounds.map((o) => o.e.id)]);
      for (const [id, node] of handle.nodes) node.g.classList.toggle('is-bad', bad.has(id));
    };
    score();
    stepEl.textContent = 0;

    const ids = [...offsets.keys()].sort(() => rng() - 0.5);
    const batches = Array.from({ length: STEPS }, (_, i) => ids.filter((_, k) => k % STEPS === i));
    await sleep(1200);

    for (let s = 0; s < STEPS; s++) {
      while (isHidden()) await sleep(300);
      for (const a of AGENTS.slice(0, 3)) {
        chips[a].classList.add('is-on');
        await sleep(reduced ? 80 : 420);
        chips[a].classList.remove('is-on');
      }
      chips.generator.classList.add('is-on');
      const start = new Map(batches[s].map((id) => [id, { ...offsets.get(id) }]));
      const t0 = performance.now();
      const dur = reduced ? 50 : 900;
      await new Promise((resolve) => {
        const frame = (now) => {
          const p = Math.min(1, (now - t0) / dur);
          const k = 1 - ease(p);
          for (const [id, o] of start) {
            const e = current[id];
            e.polygon = pose(gt[id].polygon, { dx: o.dx * k, dy: o.dy * k, rot: o.rot * k });
            updateElementNode(handle.nodes.get(id), e, handle.frame.unitsPerPoint);
          }
          if (p < 1) requestAnimationFrame(frame); else resolve();
        };
        requestAnimationFrame(frame);
      });
      chips.generator.classList.remove('is-on');
      chips.verifier.classList.add('is-on');
      stepEl.textContent = s + 1;
      score();
      await sleep(reduced ? 80 : 380);
      chips.verifier.classList.remove('is-on');
    }
    await sleep(2600);
    svg.style.opacity = 0;
    await sleep(650);
  }

  const loop = (i) => {
    run(pool[i % pool.length], 17 + i * 101).then(() => loop(i + 1));
  };
  loop(0);
}
