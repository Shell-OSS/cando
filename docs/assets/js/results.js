// Results section: ALPS-Bench bar race, effort chart, public benchmark tables, ablation.
import { segmented } from './main.js';

const ALPS = [
  { name: 'Qwen-LoRA-ALPS', sym: '▲', trained: true, agg: [0.07, 0.12], cs: [0.01, 0.08], re: [0.13, 0.22] },
  { name: 'LayoutDM', sym: '◆', trained: true, agg: [0.13, 0.13], cs: [0.01, 0.03], re: [0.24, 0.25] },
  { name: 'LayoutPrompter (GPT-5.2)', sym: '▲', agg: [0.17, 0.11], cs: [0.01, 0.06], re: [0.33, 0.21] },
  { name: 'LayoutCoT (GPT-5.2)', sym: '▲', agg: [0.19, 0.2], cs: [0.13, 0.24], re: [0.25, 0.24] },
  { name: 'Single-Agent (GPT-5.2)', sym: '●', agg: [0.4, 0.27], cs: [0.47, 0.37], re: [0.32, 0.22], compliance: 22.4 },
  { name: 'CANDO (BoN / GPT-5.2)', sym: '●', ours: true, agg: [0.59, 0.25], cs: [0.77, 0.36], re: [0.41, 0.17], compliance: 60.6 },
  { name: 'CANDO (CoDe / GPT-5.2)', sym: '●', ours: true, agg: [0.6, 0.25], cs: [0.78, 0.35], re: [0.42, 0.17], compliance: 63.6 },
  { name: 'CANDO (CoDe / Qwen3.6)', sym: '●', ours: true, agg: [0.62, 0.19], cs: [0.83, 0.25], re: [0.41, 0.16], compliance: 74.0 },
];

const EFFORT = [
  { name: 'Single-Agent (GPT-5.2)', v: 80.6, it: 80.6 },
  { name: 'CANDO (BoN / GPT-5.2)', v: 34.2, it: 8.55, ours: true },
  { name: 'CANDO (CoDe / GPT-5.2)', v: 35.24, it: 8.81, ours: true },
  { name: 'CANDO (CoDe / Qwen3.6)', v: 20.36, it: 5.09, ours: true },
];

const PUB = {
  rico: {
    cols: ['mIoU ↑', 'FID ↓', 'Align ↓', 'Over ↓'], better: ['max', 'min', 'min', 'min'],
    groups: {
      'Gen-T': [
        ['LayoutGAN++', 0.298, 5.954, 0.261, 0.620], ['BLT', 0.216, 25.633, 0.150, 0.983], ['LayoutFormer++', 0.432, 1.096, 0.230, 0.530],
        ['LayoutPrompter', 0.604, 2.134, 0.002, 0.029], ['LayoutCoT', 0.719, 2.189, 0.001, 0.022], ['CANDO (ours)', 0.866, 2.101, 0.000, 0.005],
      ],
      'Gen-TS': [
        ['BLT', 0.604, 0.951, 0.181, 0.660], ['LayoutFormer++', 0.620, 0.757, 0.202, 0.542], ['LayoutPrompter', 0.707, 3.072, 0.002, 0.051],
        ['LayoutCoT', 0.795, 3.506, 0.000, 0.031], ['CANDO (ours)', 0.879, 1.371, 0.000, 0.052],
      ],
    },
    note: 'CANDO runs on an open 35B-parameter MoE backbone (3B active). FID rewards similarity to the training distribution, so it is not a target of CANDO’s verification-driven optimisation.',
  },
  publaynet: {
    cols: ['mIoU ↑', 'FID ↓', 'Align ↓', 'Over ↓'], better: ['max', 'min', 'min', 'min'],
    groups: {
      'Gen-T': [
        ['LayoutGAN++', 0.297, 14.875, 0.124, 0.148], ['BLT', 0.140, 38.684, 0.036, 0.196], ['LayoutFormer++', 0.348, 8.411, 0.020, 0.008],
        ['LayoutPrompter', 0.619, 1.648, 0.001, 0.004], ['LayoutCoT', 0.772, 1.227, 0.000, 0.002], ['CANDO (ours)', 0.893, 3.455, 0.000, 0.000],
      ],
      'Gen-TS': [
        ['BLT', 0.428, 7.914, 0.021, 0.419], ['LayoutFormer++', 0.471, 0.720, 0.024, 0.037], ['LayoutPrompter', 0.727, 1.863, 0.001, 0.076],
        ['LayoutCoT', 0.847, 1.430, 0.000, 0.004], ['CANDO (ours)', 0.881, 2.023, 0.000, 0.000],
      ],
    },
    note: 'CANDO has the highest mIoU and perfect alignment in both settings. Overlap and alignment are checked by geometric tools rather than by the LLM.',
  },
  pku: {
    cols: ['Occ ↓', 'Rea ↓', 'Und_S ↑', 'Ove ↓', 'FID ↓'], better: ['min', 'min', 'max', 'min', 'min'],
    groups: {
      'Gen-T': [['RALF', 0.136, 0.0127, 0.890, 0.0091, 2.36], ['UniLayDiff', 0.122, 0.0129, 0.963, 0.0018, 3.09], ['CANDO (ours)', 0.144, 0.0196, 1.000, 0.0009, 9.83]],
      'Gen-TS': [['RALF', 0.138, 0.0123, 0.876, 0.0113, 0.61], ['UniLayDiff', 0.128, 0.0132, 0.918, 0.0046, 0.66], ['CANDO (ours)', 0.145, 0.0197, 0.990, 0.0000, 4.47]],
    },
    note: 'CANDO has the best strict-underlay and overlap scores, the metrics tied most directly to explicit spatial constraints.',
  },
};

const ABLATION = [
  { name: 'CANDO (full)', agg: 0.62, compliant: 74, regression: 7.0, full: true },
  { name: 'w/o CoDe', agg: 0.35, compliant: 44, regression: 6.3 },
  { name: 'w/o Historian', agg: 0.61, compliant: 59, regression: 6.7 },
  { name: 'w/o ICL', agg: 0.57, compliant: 53, regression: 5.8 },
  { name: 'w/o Planner', agg: 0.62, compliant: 51, regression: 6.7 },
  { name: 'w/o Strategies', agg: 0.61, compliant: 53, regression: 18.4 },
  { name: 'w/o Tool-grounded Verifier', agg: 0.27, compliant: 95, regression: 19.6, star: true },
  { name: 'w/o Visual Rendering', agg: 0.61, compliant: 61, regression: 7.3 },
];

function onVisible(el, fn) {
  const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { fn(); io.disconnect(); } }, { threshold: 0.25 });
  io.observe(el);
}

function initAlps() {
  const chart = document.getElementById('alps-chart');
  chart.innerHTML = ALPS.map((m, i) => `
    <div class="race-row${m.ours ? ' is-ours' : ''}" data-i="${i}">
      <span class="race-name"><span class="sym">${m.sym}</span>${m.name}${m.trained ? '<sup>†</sup>' : ''}</span>
      <span class="race-track"><span class="race-fill"></span><span class="race-err"></span><span class="race-val"></span></span>
    </div>`).join('');
  let current = 'agg';
  const draw = (metric) => {
    current = metric;
    ALPS.forEach((m, i) => {
      const row = chart.children[i];
      const fill = row.querySelector('.race-fill'), val = row.querySelector('.race-val'), err = row.querySelector('.race-err');
      let v, sd = 0, label;
      if (metric === 'compliance') {
        v = m.compliance !== undefined ? m.compliance / 100 : 0;
        label = m.compliance !== undefined ? `${m.compliance.toFixed(1)}%` : 'n/a';
      } else {
        [v, sd] = m[metric];
        label = `${v.toFixed(2)} ± ${sd.toFixed(2)}`;
      }
      fill.style.width = `${v * 100}%`;
      val.textContent = label;
      // A label that would overflow the track sits inside the bar instead.
      const end = Math.min(1, v + sd);
      const trackW = fill.parentElement.clientWidth;
      const inside = end * trackW + val.offsetWidth + 8 > trackW;
      val.classList.toggle('is-inside', inside);
      val.style.left = `${inside ? 0 : end * 100}%`;
      err.style.left = `${Math.max(0, v - sd) * 100}%`;
      err.style.width = `${(Math.min(1, v + sd) - Math.max(0, v - sd)) * 100}%`;
      err.style.opacity = metric === 'compliance' ? 0 : 1;
    });
  };
  onVisible(chart, () => draw('agg'));
  segmented(document.getElementById('alps-metric'), (b) => draw(b.dataset.metric));
  window.addEventListener('resize', () => { if (chart.offsetParent) draw(current); });

  const eff = document.getElementById('effort-chart');
  eff.innerHTML = EFFORT.map((e) => `<div class="effort-row${e.ours ? ' is-ours' : ''}"><span>${e.name.replace(' (', '<br><span class="small muted">(').replace(')', ')</span>')}</span><span class="effort-bar"><i></i></span><b>${e.v}τ</b></div>`).join('');
  onVisible(eff, () => eff.querySelectorAll('.effort-bar i').forEach((i, k) => { i.style.width = `${(EFFORT[k].v / 80.6) * 100}%`; }));
}

function initPublic() {
  const wrap = document.getElementById('pub-table');
  const note = document.getElementById('pub-note');
  const draw = (key) => {
    const b = PUB[key];
    let rows = '';
    for (const [task, list] of Object.entries(b.groups)) {
      const ranks = b.cols.map((_, c) => {
        const vals = [...new Set(list.map((r) => r[c + 1]))].sort((x, y) => (b.better[c] === 'max' ? y - x : x - y));
        return vals;
      });
      list.forEach((r, i) => {
        const ours = r[0].startsWith('CANDO');
        rows += `<tr class="${ours ? 'is-ours' : ''}${i === 0 ? ' group-start' : ''}"><td>${i === 0 ? `<b>${task}</b>` : ''}</td><td>${r[0]}</td>${r.slice(1).map((v, c) => {
          const cls = v === ranks[c][0] ? 'best' : v === ranks[c][1] ? 'second' : '';
          return `<td class="${cls}">${v.toFixed(key === 'pku' && (c === 1 || c === 3) ? 4 : 3)}</td>`;
        }).join('')}</tr>`;
      });
    }
    wrap.innerHTML = `<table class="results"><thead><tr><th>Task</th><th>Method</th>${b.cols.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
    note.textContent = b.note;
    wrap.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300 });
  };
  draw('rico');
  segmented(document.getElementById('pub-tabs'), (btn) => draw(btn.dataset.bench));
}

function initAblation() {
  const chart = document.getElementById('ablation-chart');
  chart.innerHTML = ABLATION.map((a) => `<div class="abl-row${a.full ? ' is-full' : ''}"><span>${a.name}${a.star ? '<sup>*</sup>' : ''}</span><span class="abl-track"><span class="abl-fill"></span><span class="abl-ref"></span></span><b></b></div>`).join('');
  const draw = (metric) => {
    const max = metric === 'agg' ? 1 : metric === 'compliant' ? 100 : 25;
    const full = ABLATION[0][metric];
    ABLATION.forEach((a, i) => {
      const row = chart.children[i];
      const v = a[metric];
      const worse = metric === 'regression' ? v > full + 0.5 : v < full - 0.005;
      row.classList.toggle('is-worse', !a.full && worse);
      row.querySelector('.abl-fill').style.width = `${(v / max) * 100}%`;
      row.querySelector('.abl-ref').style.left = `${(full / max) * 100}%`;
      row.querySelector('b').textContent = metric === 'agg' ? v.toFixed(2) : `${v}%`;
    });
  };
  onVisible(chart, () => draw('compliant'));
  segmented(document.getElementById('abl-metric'), (b) => draw(b.dataset.metric));
}

export function initResults() {
  initAlps();
  initPublic();
  initAblation();
}
