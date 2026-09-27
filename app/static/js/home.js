// Schedule (home): the whole day at once. A to-scale time band over a compact agenda.
// Week: seven mini bands. Month: a calendar heatmap. All times are the server's wall clock.
import {
  h, clear, get, post, sheet, segmented, state, addDays, mondayOf, parseDate, ymd, pad, wallNum, hm, fmtMin,
  longDate, timeRange, fmtDuration, DAYS_LONG, MONTHS, MONTHS_LONG, WEEKDAYS, rerender, emptyLine,
  loadingLine, showError, changed, addMinutesWall,
} from './core.js';
import { openCommitment, findNewSlot } from './commitment.js';
import { pendingBlock, pendingQuestion } from './plan.js';
import { openAdd } from './add.js';
import { icon } from './icons.js';

const view = { mode: null, date: null };
const cache = { dash: null, settings: null, rules: null, ranges: new Map(), days: new Map() };
let els = null;
let clockOffset = 0; // server wall-minute minus browser epoch-minute

function loadMode() {
  if (view.mode) return;
  try { view.mode = localStorage.getItem('home.mode') || 'day'; } catch { view.mode = 'day'; }
  if (!['day', 'week', 'month'].includes(view.mode)) view.mode = 'day';
}
function saveMode() { try { localStorage.setItem('home.mode', view.mode); } catch { /* private mode */ } }

// ---------------------------------------------------------------- data
async function loadDash(force = false) {
  if (cache.dash && !force) return cache.dash;
  const d = await get('/api/dashboard');
  cache.dash = d;
  state.now = d.now; state.today = d.now.slice(0, 10); state.tz = d.timezone;
  clockOffset = wallNum(d.now) - Date.now() / 60000;
  return d;
}
async function loadSettings() {
  if (!cache.settings) cache.settings = await get('/api/settings');
  return cache.settings;
}
async function loadRules() {
  if (!cache.rules) cache.rules = await get('/api/rules');
  return cache.rules;
}
async function range(startYmd, endYmdEx) {
  const key = `${startYmd}|${endYmdEx}`;
  if (!cache.ranges.has(key)) {
    cache.ranges.set(key, get(`/api/schedule?start=${startYmd}T00:00&end=${endYmdEx}T00:00`).then(r => r.occurrences)
      .catch(e => { cache.ranges.delete(key); throw e; }));
  }
  return cache.ranges.get(key);
}
async function dayBlocks(d) {
  if (!cache.days.has(d)) {
    cache.days.set(d, get(`/api/day/${d}`).catch(e => { cache.days.delete(d); throw e; }));
  }
  return cache.days.get(d);
}
function nowNum() { return Date.now() / 60000 + clockOffset; }
function searchWindow() {
  const s = cache.settings.search;
  return { lo: hm(s.day_start), hi: hm(s.day_end) };
}

document.addEventListener('ta:changed', e => {
  const what = e.detail;
  if (what === 'settings') cache.settings = null;
  if (what === 'rules') cache.rules = null;
  cache.ranges.clear();
  cache.days.clear();
  cache.dash = null;
  if (mounted()) refreshHome();
});

function mounted() { return !!(els && els.stage.isConnected); }

// ---------------------------------------------------------------- page
export async function renderHome(root) {
  loadMode();
  await Promise.all([loadDash(true), loadSettings(), loadRules()]);
  view.date = view.date || state.today;

  const title = h('h1.sched-date#sched-date', { tabindex: '-1' });
  const prev = h('button.icon-btn', { type: 'button', onclick: () => step(-1), dataset: { key: 'nav:prev' } }, icon('chevron-left'));
  const next = h('button.icon-btn', { type: 'button', onclick: () => step(1), dataset: { key: 'nav:next' } }, icon('chevron-right'));
  const todayBtn = h('button.btn.btn-quiet', { type: 'button', onclick: goToday }, 'Today');
  const seg = segmented('View', [['day', 'Day'], ['week', 'Week'], ['month', 'Month']], view.mode, setMode);
  const attention = h('p.attention', { hidden: true });
  const stage = h('section.stage', { 'aria-labelledby': 'sched-date' }, loadingLine());
  els = { title, prev, next, seg, attention, stage, todayBtn };

  clear(root, h('div.sched',
    h('header.sched-head', title, h('div.sched-ctrl', h('div.date-step', prev, todayBtn, next), seg)),
    attention,
    stage));
  renderAttention();
  await renderStage();
  startClock();
}

export async function refreshHome() {
  if (!mounted()) return;
  try {
    await Promise.all([loadDash(true), loadSettings(), loadRules()]);
    renderAttention();
    await renderStage();
  } catch (e) { showError(e); }
}

// Keyboard shortcuts while the schedule is showing. Returns true if handled.
export function homeKey(key) {
  if (!mounted()) return false;
  if (key === 't') { goToday(); return true; }
  if (key === 'd') { setMode('day'); return true; }
  if (key === 'w') { setMode('week'); return true; }
  if (key === 'm') { setMode('month'); return true; }
  if (key === 'ArrowLeft') { step(-1); return true; }
  if (key === 'ArrowRight') { step(1); return true; }
  return false;
}

function setMode(m) {
  view.mode = m; saveMode();
  els.seg.setValue(m);
  renderStage();
}
function goToday() { view.date = state.today; renderStage(); }
function step(dir) {
  if (view.mode === 'day') view.date = addDays(view.date, dir);
  else if (view.mode === 'week') view.date = addDays(view.date, 7 * dir);
  else {
    const d = parseDate(view.date);
    const first = new Date(d.getFullYear(), d.getMonth() + dir, 1);
    const t = parseDate(state.today);
    view.date = first.getFullYear() === t.getFullYear() && first.getMonth() === t.getMonth() ? state.today : ymd(first);
  }
  renderStage();
}
function openDay(d) {
  view.mode = 'day'; view.date = d; saveMode();
  els.seg.setValue('day');
  renderStage().then(() => els.title.focus({ preventScroll: true }));
}

function headerText() {
  const d = parseDate(view.date);
  const year = d.getFullYear() !== parseDate(state.today).getFullYear() ? ` ${d.getFullYear()}` : '';
  if (view.mode === 'day') return longDate(view.date) + year;
  if (view.mode === 'month') return `${MONTHS_LONG[d.getMonth()]} ${d.getFullYear()}`;
  const a = parseDate(mondayOf(view.date)), b = parseDate(addDays(mondayOf(view.date), 6));
  return a.getMonth() === b.getMonth()
    ? `${a.getDate()}–${b.getDate()} ${MONTHS_LONG[a.getMonth()]}${year}`
    : `${a.getDate()} ${MONTHS[a.getMonth()]}–${b.getDate()} ${MONTHS[b.getMonth()]}${year}`;
}

let renderSeq = 0;
async function renderStage() {
  if (!mounted()) return;
  const seq = ++renderSeq;
  const unit = { day: 'day', week: 'week', month: 'month' }[view.mode];
  els.title.textContent = headerText();
  els.prev.setAttribute('aria-label', `Previous ${unit}`);
  els.next.setAttribute('aria-label', `Next ${unit}`);
  document.title = view.mode === 'day' && view.date === state.today ? 'Today · Time Assistant' : `${headerText()} · Time Assistant`;
  let content;
  try {
    if (view.mode === 'week') content = await buildWeek();
    else if (view.mode === 'month') content = await buildMonth();
    else content = await buildDay();
  } catch (e) {
    content = h('p.form-error', { role: 'alert' }, `Could not load the schedule. ${e.message || ''}`);
  }
  if (seq !== renderSeq || !mounted()) return;
  rerender(els.stage, () => clear(els.stage, content));
  layoutBands();
}

// ---------------------------------------------------------------- attention line
function renderAttention() {
  const d = cache.dash;
  if (!d || !els) return;
  const items = [];
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  if (d.open_requests) items.push(h('a', { href: '#/requests' }, plural(d.open_requests, 'request', 'requests')));
  if (d.pending.length) items.push(h('button.link', { type: 'button', dataset: { key: 'att:pending' }, onclick: openPendingSheet }, `${d.pending.length} to confirm`));
  if (d.needs_reschedule.length) {
    items.push(h('button.link', {
      type: 'button', dataset: { key: 'att:resched' },
      onclick: () => d.needs_reschedule.length === 1 ? findNewSlot(d.needs_reschedule[0]) : openReschedSheet(),
    }, `${d.needs_reschedule.length} to reschedule`));
  }
  if (d.at_risk_tasks.length) items.push(h('a', { href: '#/tasks' }, plural(d.at_risk_tasks.length, 'task at risk', 'tasks at risk')));
  if (d.notifications.length) items.push(h('button.link', { type: 'button', dataset: { key: 'att:rem' }, onclick: openRemindersSheet }, plural(d.notifications.length, 'reminder', 'reminders')));
  const parts = [];
  items.forEach((it, i) => { if (i) parts.push(h('span.sep', { 'aria-hidden': 'true' }, ' · ')); parts.push(it); });
  rerender(els.attention, () => clear(els.attention, parts));
  els.attention.hidden = !items.length;
}

function openPendingSheet() {
  const list = cache.dash.pending;
  sheet({
    title: list.length === 1 ? 'Confirm' : 'To confirm',
    content: h('div.stack', list.map(p => pendingBlock(p, { onResolved: st => { if (st !== 'executed') changed('pending'); } }))),
  });
}

function openReschedSheet() {
  const s = sheet({
    title: 'To reschedule',
    content: h('ul.rows', cache.dash.needs_reschedule.map(c => h('li.simple-row',
      h('div.grow', h('p.row-strong', c.title), h('p.faint', `Was ${c.when}`)),
      h('button.btn', { type: 'button', onclick: () => { s.close(); findNewSlot(c); } }, 'Find a new time')))),
  });
}

function openRemindersSheet() {
  const s = sheet({
    title: 'Reminders',
    content: h('ul.rows', cache.dash.notifications.map(n => {
      const li = h('li.simple-row', h('p.grow', n.message),
        h('button.btn', {
          type: 'button',
          onclick: async () => {
            try { await post('/api/notifications/dismiss', { occurrence_key: n.occurrence_key, fire_at: n.fire_at }); li.remove(); changed('reminders'); if (!s.el.querySelector('li')) s.close(); }
            catch (e) { showError(e); }
          },
        }, 'Dismiss'));
      return li;
    })),
  });
}
export { pendingQuestion };

// ---------------------------------------------------------------- time band
function hoursOf(lo, hi) { return (hi - lo) / 60; }

// Place block elements; returns the band element (decorative, aria-hidden).
function band({ lo, hi, occs, base, blocked = [], now = null, mini = false, onBlock = null }) {
  const span = hi - lo;
  const pct = m => `${((m - lo) / span) * 100}%`;
  const wid = (a, b) => `${((b - a) / span) * 100}%`;
  const el = h('div.band', { class: mini ? 'mini' : '', 'aria-hidden': 'true', style: `--hours:${hoursOf(lo, hi)}` });
  for (const [a, b, label] of blocked) {
    const s = Math.max(lo, a), e = Math.min(hi, b);
    if (e > s) el.appendChild(h('div.hatch', { style: `left:${pct(s)};width:${wid(s, e)}`, title: label || 'Unavailable' }));
  }
  // lanes for overlaps
  const items = occs.map(o => ({ o, s: Math.max(lo, wallNum(o.start) - base), e: Math.min(hi, wallNum(o.end) - base) }))
    .filter(x => x.e > x.s).sort((a, b) => a.s - b.s);
  const laneEnds = [];
  for (const x of items) {
    let l = laneEnds.findIndex(end => end <= x.s);
    if (l < 0) { l = laneEnds.length; laneEnds.push(0); }
    laneEnds[l] = x.e; x.lane = l;
  }
  const lanes = Math.max(1, laneEnds.length);
  for (const x of items) {
    const { o } = x;
    const bb = o.buffer_before_min || 0, ba = o.buffer_after_min || 0;
    if (bb) { const s = Math.max(lo, x.s - bb); if (x.s > s) el.appendChild(h('div.buf', { style: `left:${pct(s)};width:${wid(s, x.s)}` })); }
    if (ba) { const e = Math.min(hi, x.e + ba); if (e > x.e) el.appendChild(h('div.buf', { style: `left:${pct(x.e)};width:${wid(x.e, e)}` })); }
    const blk = h('div.blk', {
      class: `lvl-${o.authority_level}${o.status === 'tentative' ? ' tentative' : ''}`,
      style: `left:${pct(x.s)};width:${wid(x.s, x.e)};top:${(x.lane / lanes) * 100}%;height:${100 / lanes}%`,
      dataset: { min: String(x.e - x.s) },
    }, mini ? null : h('span.blk-label', o.title));
    if (onBlock) blk.addEventListener('click', ev => { ev.stopPropagation(); onBlock(o); });
    el.appendChild(blk);
  }
  if (now != null && now >= lo && now <= hi) el.appendChild(h('div.now', { style: `left:${pct(now)}` }));
  el.dataset.lo = lo; el.dataset.hi = hi;
  return el;
}

function axis(lo, hi) {
  return h('div.band-axis', { 'aria-hidden': 'true', dataset: { lo, hi } });
}

// Width-dependent parts: hour labels every 1 to 3 hours, and block titles only where they fit.
function layoutBands() {
  if (!mounted()) return;
  for (const ax of els.stage.querySelectorAll('.band-axis')) {
    const lo = +ax.dataset.lo, hi = +ax.dataset.hi;
    const w = ax.clientWidth;
    if (!w) continue;
    const hours = hoursOf(lo, hi);
    const pxh = w / hours;
    const stepH = pxh >= 48 ? 1 : pxh >= 26 ? 2 : pxh >= 16 ? 3 : 4;
    const long = pxh * stepH >= 52;
    const labels = [];
    for (let m = lo; m <= hi; m += 60 * stepH) {
      const edge = m === lo ? 'first' : m + 60 * stepH > hi ? 'last' : '';
      labels.push(h('span.tick', { class: edge, style: `left:${((m - lo) / (hi - lo)) * 100}%` }, long ? fmtMin(m) : pad(Math.floor(m / 60) % 24)));
    }
    clear(ax, labels);
  }
  for (const b of els.stage.querySelectorAll('.band:not(.mini)')) {
    const w = b.clientWidth, span = +b.dataset.hi - +b.dataset.lo;
    for (const blk of b.querySelectorAll('.blk')) blk.classList.toggle('labeled', (w * +blk.dataset.min) / span >= 72);
  }
}
let ro = null;
function observe() {
  if (ro || typeof ResizeObserver === 'undefined') return;
  let raf = 0;
  ro = new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(layoutBands); });
  ro.observe(document.getElementById('main'));
}

let clock = null;
function startClock() {
  observe();
  if (clock) return;
  clock = setInterval(() => {
    if (!mounted() || view.mode !== 'day' || view.date !== state.today) return;
    const n = els.stage.querySelector('.band .now');
    const b = els.stage.querySelector('.band:not(.mini)');
    if (!b) return;
    const lo = +b.dataset.lo, hi = +b.dataset.hi;
    const m = nowNum() - wallNum(`${view.date}T00:00`);
    if (n && m >= lo && m <= hi) n.style.left = `${((m - lo) / (hi - lo)) * 100}%`;
  }, 60000);
}

function windowFor(occs, baseOf) {
  let { lo, hi } = searchWindow();
  for (const o of occs) {
    const base = baseOf(o);
    lo = Math.min(lo, Math.max(0, wallNum(o.start) - base));
    hi = Math.max(hi, Math.min(1440, wallNum(o.end) - base));
  }
  return { lo: Math.floor(lo / 60) * 60, hi: Math.ceil(hi / 60) * 60 };
}

// ---------------------------------------------------------------- DAY
async function buildDay() {
  const date = view.date;
  const [occs, day] = await Promise.all([range(date, addDays(date, 1)), dayBlocks(date)]);
  const base = wallNum(`${date}T00:00`);
  const { lo, hi } = windowFor(occs, () => base);
  const isToday = date === state.today;
  const now = isToday ? nowNum() - base : null;
  const blocked = day.blocks.filter(b => b.kind === 'blocked').map(b => [wallNum(b.start) - base, wallNum(b.end) - base, b.label]);

  const openOcc = o => {
    const row = els.stage.querySelector(`[data-key="${CSS.escape('occ:' + o.key)}"]`);
    if (row) row.focus({ preventScroll: true });
    openCommitment(o.commitment_id, o);
  };
  const bandEl = band({ lo, hi, occs, base, blocked, now, onBlock: openOcc });
  const bandWrap = h('div.band-wrap', axis(lo, hi), bandEl);

  // Agenda: commitments plus free gaps of 30 minutes or more.
  const rows = occs.map(o => ({ t: wallNum(o.start) - base, o }));
  for (const b of day.blocks) {
    if (b.kind !== 'free') continue;
    let s = wallNum(b.start) - base;
    const e = wallNum(b.end) - base;
    if (now != null && now > s) s = Math.ceil(now / 15) * 15;
    if (e - s >= 30) rows.push({ t: s, free: [s, e] });
  }
  rows.sort((a, b) => a.t - b.t || (a.free ? 1 : -1));

  if (!occs.length) {
    const add = h('button.link', { type: 'button', onclick: () => openAdd() }, 'Add something');
    return [bandWrap, emptyLine(isToday ? 'Nothing today.' : 'Nothing on this day.', add)];
  }
  const list = h('ol.agenda', { class: rows.length > 10 ? 'two-col' : '' }, rows.map(r => r.free ? freeRow(date, r.free) : agendaRow(r.o, base, now)));
  return [bandWrap, list];
}

function agendaRow(o, base, now) {
  const past = now != null && wallNum(o.end) - base <= now;
  return h('li', h('button.agenda-row', {
    type: 'button', class: `lvl-${o.authority_level}${o.status === 'tentative' ? ' tentative' : ''}${past ? ' past' : ''}`,
    dataset: { key: 'occ:' + o.key },
    onclick: () => openCommitment(o.commitment_id, o),
  },
    h('span.row-time.tnum', timeRange(o.start, o.end)),
    h('span.row-title', h('span.row-title-text', o.title),
      o.recurring ? [icon('repeat', { size: 16 }), h('span.vh', ', repeats')] : null),
    h('span.row-meta',
      o.status === 'tentative' ? h('span.row-tent', 'tentative') : null,
      o.people && o.people.length ? h('span.row-people', o.people.join(', ')) : null)));
}

function freeRow(date, [s, e]) {
  const start = `${date}T${fmtMin(s)}`;
  const len = Math.min(60, e - s);
  return h('li', h('button.free-row.tnum', {
    type: 'button', dataset: { key: `free:${s}` },
    onclick: () => openAdd({ prefill: { start, end: addMinutesWall(start, len) } }),
  }, h('span', `Free ${fmtMin(s)}–${fmtMin(e)}`), h('span.vh', ', add a commitment')));
}

// ---------------------------------------------------------------- WEEK
function blockedFor(ymdStr) {
  const wd = (parseDate(ymdStr).getDay() + 6) % 7;
  return (cache.rules || []).filter(r => r.kind === 'block' && (!r.weekdays.length || r.weekdays.includes(wd)))
    .map(r => [hm(r.start), r.end === '24:00' ? 1440 : hm(r.end), r.label]);
}

async function buildWeek() {
  const mon = mondayOf(view.date);
  const days = [...Array(7)].map((_, i) => addDays(mon, i));
  const occs = await range(mon, addDays(mon, 7));
  const byDay = new Map(days.map(d => [d, []]));
  for (const o of occs) {
    // an occurrence belongs to each day it touches
    for (const d of days) {
      const b = wallNum(`${d}T00:00`);
      if (wallNum(o.start) < b + 1440 && wallNum(o.end) > b) byDay.get(d).push(o);
    }
  }
  const { lo, hi } = windowFor(occs, o => wallNum(`${o.start.slice(0, 10)}T00:00`));
  const rows = days.map((d, i) => {
    const base = wallNum(`${d}T00:00`);
    const list = byDay.get(d);
    const date = parseDate(d);
    const isToday = d === state.today;
    const mins = list.reduce((a, o) => a + Math.max(0, Math.min(wallNum(o.end), base + 1440) - Math.max(wallNum(o.start), base)), 0);
    return h('li', h('button.week-row', {
      type: 'button', class: isToday ? 'is-today' : '', dataset: { key: 'wk:' + d },
      'aria-label': `${DAYS_LONG[date.getDay()]} ${date.getDate()} ${MONTHS_LONG[date.getMonth()]}${isToday ? ', today' : ''}, ${list.length ? `${list.length} ${list.length === 1 ? 'commitment' : 'commitments'}, ${fmtDuration(mins)}` : 'nothing booked'}`,
      onclick: () => openDay(d),
    },
      h('span.wk-label', h('span.wk-day', WEEKDAYS[i]), h('span.wk-num.tnum', String(date.getDate()))),
      band({ lo, hi, occs: list, base, blocked: blockedFor(d), now: isToday ? nowNum() - base : null, mini: true })));
  });
  return h('div.week', h('div.week-axis', h('span'), axis(lo, hi)), h('ol.week-rows', rows));
}

// ---------------------------------------------------------------- MONTH
function heatLevel(min) { return min <= 0 ? 0 : min < 60 ? 1 : min < 180 ? 2 : min < 300 ? 3 : 4; }

async function buildMonth() {
  const d0 = parseDate(view.date);
  const month = view.date.slice(0, 7);
  const first = `${month}-01`;
  const last = ymd(new Date(d0.getFullYear(), d0.getMonth() + 1, 0));
  const gridStart = mondayOf(first);
  const gridEnd = addDays(mondayOf(last), 7);
  const occs = await range(gridStart, gridEnd);
  const perDay = new Map();
  for (const o of occs) {
    let s = wallNum(o.start); const e = wallNum(o.end);
    while (s < e) {
      const dn = Math.floor(s / 1440) * 1440;
      const u = new Date(dn * 60000);
      const key = `${u.getUTCFullYear()}-${pad(u.getUTCMonth() + 1)}-${pad(u.getUTCDate())}`;
      const segEnd = Math.min(e, dn + 1440);
      const cur = perDay.get(key) || { min: 0, n: 0 };
      cur.min += segEnd - s; cur.n += 1;
      perDay.set(key, cur);
      s = segEnd;
    }
  }
  const focusDate = view.date >= gridStart && view.date < gridEnd ? view.date : first;
  const cells = [];
  for (let d = gridStart; d < gridEnd; d = addDays(d, 1)) {
    const info = perDay.get(d) || { min: 0, n: 0 };
    const inMonth = d.slice(0, 7) === month;
    const dt = parseDate(d);
    cells.push(h('button.mcell', {
      type: 'button',
      class: `heat-${inMonth ? heatLevel(info.min) : 'out'}${d === state.today ? ' is-today' : ''}`,
      tabindex: d === focusDate ? '0' : '-1',
      dataset: { key: 'mo:' + d, date: d },
      'aria-label': `${DAYS_LONG[dt.getDay()]} ${dt.getDate()} ${MONTHS_LONG[dt.getMonth()]}${d === state.today ? ', today' : ''}, ${info.min ? `${fmtDuration(info.min)} booked` : 'nothing booked'}`,
      onclick: () => openDay(d),
    }, h('span.tnum', String(dt.getDate()))));
  }
  const grid = h('div.month-grid', { role: 'group', 'aria-label': `${MONTHS_LONG[d0.getMonth()]} ${d0.getFullYear()}, use arrow keys to move between days` },
    cells);
  grid.addEventListener('keydown', e => monthKeys(e, grid, gridStart, gridEnd, month));
  return h('div.month',
    h('div.month-head', { 'aria-hidden': 'true' }, WEEKDAYS.map(w => h('span', w))),
    grid);
}

function monthKeys(e, grid, gridStart, gridEnd, month) {
  const cur = document.activeElement && document.activeElement.dataset.date;
  if (!cur) return;
  const delta = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
  let target = null;
  if (delta) target = addDays(cur, delta);
  else if (e.key === 'Home') target = mondayOf(cur);
  else if (e.key === 'End') target = addDays(mondayOf(cur), 6);
  else if (e.key === 'PageUp' || e.key === 'PageDown') {
    const d = parseDate(cur);
    target = ymd(new Date(d.getFullYear(), d.getMonth() + (e.key === 'PageUp' ? -1 : 1), Math.min(d.getDate(), 28)));
  } else return;
  e.preventDefault();
  e.stopPropagation();
  view.date = target;
  if (target >= gridStart && target < gridEnd && target.slice(0, 7) === month) {
    for (const c of grid.children) c.tabIndex = c.dataset.date === target ? 0 : -1;
    grid.querySelector(`[data-date="${target}"]`)?.focus();
  } else {
    els.title.textContent = headerText();
    renderStage().then(() => els.stage.querySelector(`[data-date="${target}"]`)?.focus());
  }
}

