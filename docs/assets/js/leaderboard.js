// ALPS-Bench leaderboard: assets/data/leaderboard_snapshot.json is refreshed from Codabench every
// hour by .github/workflows/update-leaderboard.yml (Codabench's API has no CORS headers),
// then moderated here with assets/data/leaderboard_config.json.
// Participant-supplied text is only ever set via textContent.
const CONFIG_URL = 'assets/data/leaderboard_config.json';
const PAGE_SIZE = 10;
const METRICS = [
  { key: 'cs', digits: 3 },
  { key: 're', digits: 3 },
  { key: 'agg', digits: 3, cls: 'lb-agg' },
];
const LIMITS = { method_name: 60, team_name: 40 };
const ALLOWED_TEXT = /^[\p{L}\p{N}_ .,:;()[\]/+&'@#-]*$/u;
const LINK_DOMAINS = {
  paper_url: ['arxiv.org', 'openreview.net', 'aclanthology.org', 'doi.org', 'neurips.cc', 'nips.cc'],
  code_url: ['github.com', 'gitlab.com', 'huggingface.co'],
};
const LEET = { 0: 'o', 1: 'i', 3: 'e', 4: 'a', 5: 's', 7: 't', '@': 'a', $: 's', '!': 'i' };
const SVG_NS = 'http://www.w3.org/2000/svg';
const ICONS = {
  github: ['0 0 16 16', 'M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z'],
  gitlab: ['0 0 24 24', 'M12 21.5 3.2 15.1a.9.9 0 0 1-.33-1L4.3 9.7 7.1 1.1a.45.45 0 0 1 .86 0L10.8 9.7h2.4l2.8-8.6a.45.45 0 0 1 .86 0l2.8 8.6 1.42 4.4a.9.9 0 0 1-.33 1z'],
  paper: ['0 0 24 24', 'M6 2h9l5 5v15H6z M14 2v6h6 M9 13h7 M9 17h7'],
  code: ['0 0 24 24', 'M8 7l-5 5 5 5M16 7l5 5-5 5'],
};

let entries = [];
let shown = PAGE_SIZE;

const normalize = (text) => text.normalize('NFKC').toLowerCase().replace(/[013457@$!]/g, (c) => LEET[c]).replace(/[^a-z]/g, '');
const onDomain = (host, domain) => host === domain || host.endsWith(`.${domain}`);

function textOk(value, field, blocked) {
  if (value.length > LIMITS[field] || !ALLOWED_TEXT.test(value) || /https?:\/\/|www\./i.test(value)) return false;
  const flat = normalize(value);
  return !blocked.some((term) => term && flat.includes(normalize(term)));
}

function cleanLink(value, field) {
  try {
    const url = new URL(String(value ?? '').trim());
    const host = url.hostname.toLowerCase();
    if (url.protocol !== 'https:' || !LINK_DOMAINS[field].some((d) => onDomain(host, d))) return null;
    url.hash = '';
    return url.href;
  } catch {
    return null;
  }
}

function moderate(submissions, cfg) {
  const approved = new Set((cfg.approved_owners || []).map((o) => String(o).toLowerCase()));
  const hidden = new Set((cfg.hidden_submissions || []).map(Number));
  const blocked = cfg.blocked_terms || [];
  const list = [];
  for (const s of submissions) {
    const id = Number(s.id);
    const owner = String(s.owner ?? '');
    const answers = s.fact_sheet_answers || {};
    const method = String(answers.method_name ?? '').trim();
    const team = String(answers.team_name ?? '').trim();
    const scores = Object.fromEntries((s.scores || []).map((x) => [x.column_key, parseFloat(x.score)]));
    if (hidden.has(id) || !(approved.has('*') || approved.has(owner.toLowerCase()))) continue;
    if (!method || !Number.isFinite(scores.agg)) continue;
    if (!textOk(method, 'method_name', blocked) || !textOk(team, 'team_name', blocked)) continue;
    list.push({
      id, owner, method, team,
      paper: cleanLink(answers.paper_url, 'paper_url'),
      code: cleanLink(answers.code_url, 'code_url'),
      agg: scores.agg, cs: scores.cs, re: scores.re,
      submitted: String(s.created_when ?? ''),
    });
  }
  list.sort((a, b) => b.agg - a.agg || a.submitted.localeCompare(b.submitted));
  let rank = 0;
  let prev = null;
  list.forEach((e, i) => {
    if (e.agg !== prev) { rank = i + 1; prev = e.agg; }
    e.rank = rank;
  });
  return list;
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function svg(viewBox, cls, label) {
  const node = document.createElementNS(SVG_NS, 'svg');
  node.setAttribute('viewBox', viewBox);
  node.setAttribute('class', cls);
  if (label) {
    node.setAttribute('role', 'img');
    node.setAttribute('aria-label', label);
  } else {
    node.setAttribute('aria-hidden', 'true');
  }
  return node;
}

function svgChild(tag, attrs, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

function medal(rank) {
  const node = svg('0 0 24 28', `lb-medal lb-medal-${rank}`, `Rank ${rank}`);
  node.append(
    svgChild('path', { class: 'ribbon', d: 'M7 1h4l2.5 9h-4z M17 1h-4l-2.5 9h4z' }),
    svgChild('circle', { class: 'disc', cx: 12, cy: 18.5, r: 8 }),
    svgChild('text', { x: 12, y: 22, 'text-anchor': 'middle' }, String(rank)),
  );
  return node;
}

function icon(name) {
  const [viewBox, d] = ICONS[name];
  const node = svg(viewBox, `lb-icon lb-icon-${name}`);
  node.append(svgChild('path', { d }));
  return node;
}

function linkIcon(url, kind) {
  if (!url) return null;
  const host = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
  const a = el('a', null, 'lb-link');
  a.href = url;
  a.target = '_blank';
  a.rel = 'nofollow ugc noopener';
  a.title = `${kind === 'paper' ? 'Paper' : 'Code'} · ${host}`;
  a.setAttribute('aria-label', a.title);
  if (onDomain(host, 'arxiv.org')) a.append(el('span', 'arXiv', 'lb-badge'));
  else if (onDomain(host, 'huggingface.co')) a.append(el('span', '🤗', 'lb-emoji'));
  else if (onDomain(host, 'github.com')) a.append(icon('github'));
  else if (onDomain(host, 'gitlab.com')) a.append(icon('gitlab'));
  else a.append(icon(kind));
  return a;
}

function messageRow(text) {
  const td = el('td', text, 'lb-empty');
  td.colSpan = 8;
  const tr = el('tr');
  tr.append(td);
  return tr;
}

function render() {
  const body = document.getElementById('lb-body');
  const more = document.getElementById('lb-more');
  if (!entries.length) {
    body.replaceChildren(messageRow('No entries yet. Be the first to submit on Codabench.'));
    more.hidden = true;
    return;
  }
  const best = Object.fromEntries(METRICS.map(({ key }) => [key, Math.max(...entries.map((e) => (Number.isFinite(e[key]) ? e[key] : -Infinity)))]));
  body.replaceChildren(...entries.slice(0, shown).map((e) => {
    const tr = el('tr');
    const rank = el('td', e.rank <= 3 ? null : String(e.rank), 'lb-rank');
    if (e.rank <= 3) rank.append(medal(e.rank));
    tr.append(rank, el('td', e.method), el('td', e.team || e.owner, 'lb-text'), el('td', e.submitted.slice(0, 10), 'lb-text'));

    const links = el('td', null, 'lb-text lb-links');
    for (const a of [linkIcon(e.paper, 'paper'), linkIcon(e.code, 'code')]) if (a) links.append(a);
    tr.append(links);

    for (const { key, digits, cls = '' } of METRICS) {
      const v = e[key];
      const ok = Number.isFinite(v);
      const isBest = ok && entries.length > 1 && v === best[key];
      tr.append(el('td', ok ? v.toFixed(digits) : '–', `${cls}${isBest ? ' best' : ''}`.trim()));
    }
    return tr;
  }));

  const remaining = entries.length - shown;
  more.hidden = remaining <= 0;
  more.textContent = `Show ${Math.min(PAGE_SIZE, remaining)} more (${remaining} remaining)`;
}

async function load() {
  const meta = document.getElementById('lb-meta');
  try {
    const config = await (await fetch(CONFIG_URL, { cache: 'no-cache' })).json();
    const competition = String(config.competition_url).startsWith('https://www.codabench.org/') ? config.competition_url : null;
    if (competition) {
      const results = new URL(competition);
      results.hash = '/results-tab';
      const button = document.getElementById('lb-codabench');
      button.href = results.href;
      button.hidden = false;
    }
    if (!config.api_url) throw new Error('api_url is not configured');
    const res = await fetch(config.api_url, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    entries = moderate(Array.isArray(data.submissions) ? data.submissions : [], config);
    render();
    const when = new Date(data.fetched_at || Date.now()).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    meta.textContent = `Scored by the ALPS-Bench evaluation server against the held-out test set. Last updated (hourly): ${when}.`;
  } catch {
    document.getElementById('lb-body').replaceChildren(messageRow('The leaderboard is unavailable right now. Please try again later.'));
    meta.textContent = 'Could not load the leaderboard.';
  }
}

export function initLeaderboard() {
  document.getElementById('lb-more').addEventListener('click', () => {
    shown += PAGE_SIZE;
    render();
  });
  load();
}
