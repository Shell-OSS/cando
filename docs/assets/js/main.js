import { decode } from './render.js';
import { initHero } from './hero.js';
import { initBenchmark } from './bench.js';
import { initExplorer } from './explorer.js';
import { initMethod } from './method.js';
import { initResults } from './results.js';
import { initPlayground } from './playground.js';

// Final URLs for the header buttons; leave empty to show a "coming soon" toast.
const LINKS = {
  paper: '',
  arxiv: 'https://arxiv.org/abs/2610.10044',
  code: 'https://github.com/Shell-OSS/cando',
  dataset: 'https://github.com/Shell-OSS/cando/tree/main/data',
};

export function toast(message) {
  const t = document.getElementById('toast');
  t.textContent = message;
  t.classList.add('is-on');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('is-on'), 2200);
}

function initLinks() {
  document.querySelectorAll('[data-link]').forEach((a) => {
    const url = LINKS[a.dataset.link];
    if (url) { a.href = url; a.target = '_blank'; a.rel = 'noopener'; return; }
    a.addEventListener('click', (ev) => { ev.preventDefault(); toast('Link coming soon'); });
  });
}

function initNav() {
  const nav = document.querySelector('.nav');
  const bar = document.querySelector('.scroll-progress');
  const links = [...document.querySelectorAll('.nav-links a')];
  const sections = links.map((a) => document.querySelector(a.getAttribute('href'))).filter(Boolean);
  const onScroll = () => {
    const y = window.scrollY;
    nav.classList.toggle('is-scrolled', y > 8);
    const h = document.documentElement.scrollHeight - window.innerHeight;
    bar.style.width = `${h > 0 ? (y / h) * 100 : 0}%`;
    let current = null;
    for (const s of sections) if (s.getBoundingClientRect().top < window.innerHeight * 0.35) current = s.id;
    links.forEach((a) => a.classList.toggle('is-active', a.getAttribute('href') === `#${current}`));
    const dark = document.querySelector('.section-dark').getBoundingClientRect();
    nav.classList.toggle('is-dark', dark.top < 40 && dark.bottom > 40);
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
  const toggle = document.querySelector('.nav-toggle');
  const menu = document.querySelector('.nav-links');
  toggle.addEventListener('click', () => {
    const open = menu.classList.toggle('is-open');
    toggle.setAttribute('aria-expanded', open);
  });
  menu.addEventListener('click', (e) => { if (e.target.tagName === 'A') menu.classList.remove('is-open'); });
}

function initReveal() {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target); }
  }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });
  document.querySelectorAll('.reveal').forEach((el) => io.observe(el));
}

function initCounters() {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const el = e.target;
      const target = parseFloat(el.dataset.count);
      const dec = parseInt(el.dataset.decimals || '0', 10);
      const suffix = el.dataset.suffix || '';
      const t0 = performance.now();
      const fmt = (v) => (el.dataset.format === 'comma' ? Math.round(v).toLocaleString('en-US') : v.toFixed(dec)) + suffix;
      const step = (now) => {
        const p = Math.min(1, (now - t0) / 1400);
        el.textContent = fmt(target * (1 - Math.pow(1 - p, 3)));
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }
  }, { threshold: 0.6 });
  document.querySelectorAll('[data-count]').forEach((el) => io.observe(el));
}

function initBibtex() {
  document.getElementById('copy-bib')?.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(document.getElementById('bibtex').textContent);
      toast('BibTeX copied to clipboard');
    } catch {
      toast('Copy failed. Please select the text manually.');
    }
  });
}

export function segmented(container, onChange) {
  container.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn || !container.contains(btn)) return;
    container.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', b === btn));
    onChange(btn);
  });
}

async function boot() {
  initNav();
  initLinks();
  initReveal();
  initCounters();
  initBibtex();
  initMethod();
  initResults();

  const [layoutsRes, statsRes] = await Promise.all([
    fetch('assets/data/val_layouts.json'),
    fetch('assets/data/dataset_stats.json'),
  ]);
  const data = await layoutsRes.json();
  const stats = await statsRes.json();
  const samples = data.layouts.map((rec, index) => ({ ...rec, index, elements: decode(data.classes, rec) }));

  initHero(samples);
  initBenchmark(samples, stats);
  const playground = initPlayground(samples);
  initExplorer(samples, (sample) => playground.load(sample));
}

boot();
