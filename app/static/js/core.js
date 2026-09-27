// Shared helpers: API client, safe DOM builder, wall-clock dates, toasts, sheets, form parts.
// All rendering goes through h(): text is always set via text nodes, never innerHTML.
import { icon } from './icons.js';

export class ApiError extends Error {
  constructor(status, body) {
    super((body && (body.message || body.detail)) || `Request failed (${status})`);
    this.status = status;
    this.body = body;
    this.code = body && body.error;
  }
}

export async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!r.ok) {
    if (data && Array.isArray(data.detail)) data.message = data.detail.map(d => d.msg).join('; ');
    throw new ApiError(r.status, data);
  }
  return data;
}

export const get = p => api(p);
export const post = (p, body = {}) => api(p, { method: 'POST', body });
export const patch = (p, body) => api(p, { method: 'PATCH', body });
export const put = (p, body) => api(p, { method: 'PUT', body });
export const del = p => api(p, { method: 'DELETE' });

// h('div.card#x', {onclick}, 'text', child, [children]): safe element builder.
export function h(sel, attrs, ...kids) {
  if (attrs == null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    if (attrs !== undefined) kids.unshift(attrs);
    attrs = {};
  }
  const m = sel.match(/^([a-z0-9-]*)((?:[.#][\w-]+)*)$/i);
  const el = document.createElement(m[1] || 'div');
  for (const part of m[2].match(/[.#][\w-]+/g) || []) {
    if (part[0] === '.') el.classList.add(part.slice(1)); else el.id = part.slice(1);
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') { for (const c of String(v).split(/\s+/)) if (c) el.classList.add(c); }
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'selected' || k === 'disabled' || k === 'hidden') el[k] = !!v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, kids);
  return el;
}

function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}

export function clear(el, ...kids) { el.replaceChildren(); append(el, kids); return el; }

let uid = 0;
export function nextId(prefix = 'f') { uid += 1; return `${prefix}-${uid}`; }

// ---------- wall-clock dates ----------
// The API returns local ISO with offset (2026-09-29T18:00:00+05:30), already in the user's
// timezone. We read the wall-clock part as-is and send naive local ISO back. No toISOString().
export function wall(iso) { return iso ? iso.slice(0, 16) : ''; }
export function wallDate(iso) { return iso ? iso.slice(0, 10) : ''; }
export function wallTime(iso) { return iso ? iso.slice(11, 16) : ''; }
export const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
export const DAYS_LONG = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function parseDate(ymdStr) { const [y, m, d] = ymdStr.split('-').map(Number); return new Date(y, m - 1, d); }
export function ymd(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
export function pad(n) { return String(n).padStart(2, '0'); }
export function addDays(ymdStr, n) { const d = parseDate(ymdStr); d.setDate(d.getDate() + n); return ymd(d); }
export function mondayOf(ymdStr) { const d = parseDate(ymdStr); d.setDate(d.getDate() - (d.getDay() + 6) % 7); return ymd(d); }
export function longDate(ymdStr) { const d = parseDate(ymdStr); return `${DAYS_LONG[d.getDay()]} ${d.getDate()} ${MONTHS_LONG[d.getMonth()]}`; }
export function shortDate(ymdStr) { const d = parseDate(ymdStr); return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`; }
// "14:00–15:00"
export function timeRange(startIso, endIso) { return `${wallTime(startIso)}–${wallTime(endIso)}`; }
// "Tue 16:00–17:00" (adds the date when it is more than a week away)
export function whenText(startIso, endIso) {
  const d = wallDate(startIso);
  const diff = Math.round((parseDate(d) - parseDate(today())) / 86400000);
  const dayPart = diff === 0 ? 'Today' : diff === 1 ? 'Tomorrow' : diff > 1 && diff < 7 ? DAYS[parseDate(d).getDay()] : shortDate(d);
  return `${dayPart} ${timeRange(startIso, endIso)}`;
}
export function minutesBetween(a, b) { return Math.round((wallNum(b) - wallNum(a))); }
// Wall-clock ISO to an absolute minute number (UTC arithmetic on wall parts; browser tz not involved).
export function wallNum(iso) {
  return Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10), +iso.slice(11, 13) || 0, +iso.slice(14, 16) || 0) / 60000;
}
export function hm(str) { const [a, b] = str.split(':').map(Number); return a * 60 + b; }
export function fmtMin(m) { m = ((m % 1440) + 1440) % 1440; return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`; }
export function addMinutesWall(wallStr, mins) {
  const [dp, tp] = wallStr.split('T');
  const d = parseDate(dp);
  const [hh, mm] = tp.split(':').map(Number);
  d.setHours(hh, mm + mins, 0, 0);
  return `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function fmtDuration(min) {
  if (min == null) return '';
  const hrs = Math.floor(min / 60), m = min % 60;
  return hrs ? (m ? `${hrs}h ${m}m` : `${hrs}h`) : `${m}m`;
}
export function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
export function listText(items) {
  const a = items.filter(Boolean);
  if (a.length <= 1) return a.join('');
  return `${a.slice(0, -1).join(', ')} and ${a[a.length - 1]}`;
}

// Server "today" and "now" in the user's configured timezone.
export const state = { today: null, now: null, tz: null };
export function today() { return state.today || ymd(new Date()); }
export async function ensureToday() {
  if (state.today) return;
  try {
    const r = await get('/health');
    state.now = r.time; state.today = r.time.slice(0, 10);
  } catch { /* fall back to the browser date */ }
}

// ---------- change events ----------
// Anything that mutates data calls changed(); visible regions re-fetch and re-render in place.
export function changed(what = 'schedule') { document.dispatchEvent(new CustomEvent('ta:changed', { detail: what })); }

// ---------- errors ----------
export function errorText(e) {
  if (e instanceof ApiError) return e.message;
  return e && e.message ? e.message : String(e);
}

// ---------- toasts ----------
function toastHost() {
  const open = [...document.querySelectorAll('dialog[open]')];
  const dlg = open[open.length - 1];
  if (!dlg) return document.getElementById('toasts');
  let host = dlg.querySelector(':scope > .toasts');
  if (!host) { host = h('div.toasts', { 'aria-live': 'polite' }); dlg.appendChild(host); }
  return host;
}

export function toast(msg, kind = 'info', ms = 5000) {
  const t = h('div.toast', { class: kind, role: kind === 'error' ? 'alert' : 'status' });
  let left = ms, started = Date.now(), timer = null;
  const remove = () => { clearTimeout(timer); t.remove(); };
  const start = () => { started = Date.now(); timer = setTimeout(remove, left); };
  const pause = () => { clearTimeout(timer); left = Math.max(1500, left - (Date.now() - started)); };
  t.append(h('span.toast-text', msg),
    h('button.icon-btn.toast-x', { type: 'button', 'aria-label': 'Dismiss', onclick: remove }, icon('close', { size: 16 })));
  t.addEventListener('mouseenter', pause);
  t.addEventListener('mouseleave', start);
  t.addEventListener('focusin', pause);
  t.addEventListener('focusout', start);
  toastHost().appendChild(t);
  start();
}
export function showError(e) { toast(errorText(e), 'error', 8000); }

// ---------- sheets (dialogs) ----------
// sheet({title, content, kind}) opens a <dialog>: 'sheet' is a right panel on laptop and a
// bottom sheet on phone; 'modal' is a small centred dialog. Focus returns to the trigger, or to
// its re-rendered equivalent (matched by data-key), when it closes.
export function sheet({ title, content, kind = 'sheet', head = null, onClose = null, fallbackFocus = null, returnTo = null }) {
  const trigger = returnTo ? returnTo.trigger : document.activeElement;
  const triggerKey = returnTo ? returnTo.triggerKey : trigger && trigger.closest ? trigger.closest('[data-key]')?.dataset.key : null;
  const titleId = nextId('dlg');
  const titleEl = h('h2.sheet-title', { id: titleId }, title);
  const body = h('div.sheet-body', content);
  const dlg = h('dialog', { class: kind, 'aria-labelledby': titleId },
    h('div.sheet-head', titleEl, head,
      h('button.icon-btn', { type: 'button', 'aria-label': 'Close', onclick: () => close() }, icon('close'))),
    body);
  document.body.appendChild(dlg);
  dlg.addEventListener('click', e => {
    if (e.target !== dlg) return;
    const r = dlg.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
  });
  dlg.addEventListener('close', () => {
    dlg.remove();
    onClose && onClose();
    requestAnimationFrame(() => restoreFocus(trigger, triggerKey, fallbackFocus));
  });
  dlg.showModal();
  function close() { if (dlg.open) dlg.close(); }
  return { close, el: dlg, body, trigger, triggerKey, setTitle: t => { titleEl.textContent = t; } };
}

export function restoreFocus(trigger, key, fallback) {
  if (document.querySelector('dialog[open]')) return;
  let target = trigger && trigger.isConnected && trigger !== document.body ? trigger : null;
  if (!target && key) target = document.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (!target && fallback) target = typeof fallback === 'function' ? fallback() : fallback;
  if (!target) target = document.querySelector('#main h1');
  if (target) {
    if (!target.hasAttribute('tabindex') && !/^(A|BUTTON|INPUT|SELECT|TEXTAREA)$/.test(target.tagName)) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: false });
  }
}

export function confirmDialog(message, { okText = 'Confirm', danger = false, title = 'Are you sure?' } = {}) {
  return new Promise(resolve => {
    let result = false;
    const ok = h('button.btn', { type: 'button', class: danger ? 'danger' : 'primary', onclick: () => { result = true; m.close(); } }, okText);
    const m = sheet({
      title, kind: 'modal',
      content: [h('p', message), h('div.actions', h('button.btn', { type: 'button', onclick: () => m.close() }, 'Cancel'), ok)],
      onClose: () => resolve(result),
    });
    ok.focus();
  });
}

export function isTyping(el = document.activeElement) {
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
}
export function dialogOpen() { return !!document.querySelector('dialog[open]'); }
export function anyDirty() { return !!document.querySelector('form[data-dirty="1"]'); }

// ---------- form parts ----------
// field(label, input, {hint}) builds label + control + optional one-line hint + error slot.
export function field(label, input, { hint = null, cls = '' } = {}) {
  if (!input.id) input.id = nextId('in');
  const hintId = hint ? input.id + '-hint' : null;
  if (hintId) input.setAttribute('aria-describedby', hintId);
  return h('div.field', { class: cls },
    h('label', { for: input.id }, label),
    input,
    hint ? h('p.hint', { id: hintId }, hint) : null,
    h('p.field-error', { id: input.id + '-err', hidden: true }));
}

export function setFieldError(input, msg) {
  const err = document.getElementById(input.id + '-err');
  const ids = (input.getAttribute('aria-describedby') || '').split(' ').filter(x => x && x !== input.id + '-err');
  if (msg) {
    input.setAttribute('aria-invalid', 'true');
    ids.push(input.id + '-err');
    if (err) { err.textContent = msg; err.hidden = false; }
  } else {
    input.removeAttribute('aria-invalid');
    if (err) { err.textContent = ''; err.hidden = true; }
  }
  if (ids.length) input.setAttribute('aria-describedby', ids.join(' ')); else input.removeAttribute('aria-describedby');
}

export function clearErrors(form) {
  for (const el of form.querySelectorAll('[aria-invalid="true"]')) setFieldError(el, null);
  const fe = form.querySelector('.form-error');
  if (fe) { fe.textContent = ''; fe.hidden = true; }
}

export function formError(form, msg) {
  let fe = form.querySelector('.form-error');
  if (!fe) { fe = h('p.form-error', { role: 'alert' }); form.querySelector('.actions')?.before(fe) || form.append(fe); }
  fe.textContent = msg; fe.hidden = !msg;
}

export function select(name, options, value, attrs = {}) {
  return h('select', { name, ...attrs },
    options.map(o => {
      const [v, t] = Array.isArray(o) ? o : [o, o];
      return h('option', { value: v, selected: String(v) === String(value) }, t);
    }));
}

export const LEVELS = [[1, '1 (lowest)'], [2, '2'], [3, '3 (default)'], [4, '4'], [5, '5 (highest)']];
export function levelSelect(value = 3, name = 'authority_level') { return select(name, LEVELS, value); }

// A group of checkbox chips inside <fieldset><legend>.
export function chipGroup(legend, name, options, selected = [], { hint = null } = {}) {
  const sel = new Set(selected.map(String));
  const hintId = hint ? nextId('hint') : null;
  return h('fieldset.chips', { 'aria-describedby': hintId },
    h('legend', legend),
    h('div.chip-row', options.map(([v, t]) => h('label.chip',
      h('input', { type: 'checkbox', name, value: String(v), checked: sel.has(String(v)) }),
      h('span', t)))),
    hint ? h('p.hint', { id: hintId }, hint) : null);
}
export function readChips(form, name) {
  return [...form.querySelectorAll(`input[name="${name}"]`)].filter(i => i.checked).map(i => i.value);
}
export function weekdayChips(legend, name, selected = [], opts) {
  return chipGroup(legend, name, WEEKDAYS.map((d, i) => [i, d]), selected, opts);
}

export function checkbox(name, label, checked, attrs = {}) {
  return h('label.check', h('input', { type: 'checkbox', name, checked, ...attrs }), h('span', label));
}

// Segmented control built from aria-pressed buttons.
export function segmented(label, options, value, onChange, { cls = '' } = {}) {
  const wrap = h('div.seg', { role: 'group', 'aria-label': label, class: cls });
  const set = v => { for (const b of wrap.children) b.setAttribute('aria-pressed', String(b.dataset.value === v)); };
  for (const [v, t] of options) {
    wrap.appendChild(h('button', {
      type: 'button', 'aria-pressed': String(v === value), dataset: { value: v, key: `seg:${label}:${v}` },
      onclick: () => { set(v); onChange(v); },
    }, t));
  }
  wrap.setValue = set;
  return wrap;
}

// Small "More" menu: a disclosure button plus a list of actions.
export function menu(label, items, { iconName = 'more', text = null } = {}) {
  const listId = nextId('menu');
  const btn = h('button', {
    type: 'button', class: text ? 'btn' : 'icon-btn', 'aria-expanded': 'false', 'aria-controls': listId,
    'aria-label': text ? null : label,
  }, text ? text : icon(iconName));
  const list = h('ul.menu-list', { id: listId, hidden: true },
    items.filter(Boolean).map(it => h('li', h('button', {
      type: 'button', class: it.danger ? 'danger' : '',
      onclick: () => { toggle(false); it.onSelect(); },
    }, it.label))));
  const wrap = h('div.menu', btn, list);
  function toggle(open) {
    list.hidden = !open;
    btn.setAttribute('aria-expanded', String(open));
    if (open) { list.querySelector('button')?.focus(); document.addEventListener('pointerdown', outside, true); }
    else document.removeEventListener('pointerdown', outside, true);
  }
  function outside(e) { if (!wrap.contains(e.target)) toggle(false); }
  btn.addEventListener('click', () => toggle(list.hidden));
  wrap.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !list.hidden) { e.stopPropagation(); e.preventDefault(); toggle(false); btn.focus(); }
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !list.hidden) {
      const bs = [...list.querySelectorAll('button')];
      const i = bs.indexOf(document.activeElement);
      e.preventDefault();
      bs[(i + (e.key === 'ArrowDown' ? 1 : -1) + bs.length) % bs.length].focus();
    }
  });
  wrap.addEventListener('focusout', e => { if (!list.hidden && !wrap.contains(e.relatedTarget)) toggle(false); });
  return wrap;
}

// Run an async submit with the button disabled and marked busy.
export async function busy(btn, fn) {
  if (!btn) return fn();
  if (btn.disabled && btn.getAttribute('aria-busy') === 'true') return undefined;
  btn.disabled = true;
  btn.setAttribute('aria-busy', 'true');
  try { return await fn(); }
  finally { if (btn.isConnected) { btn.disabled = false; btn.removeAttribute('aria-busy'); } }
}

// Track unsaved changes on a form; submit buttons stay disabled until something changes.
export function trackDirty(form, saveBtn) {
  const set = v => { form.dataset.dirty = v ? '1' : '0'; if (saveBtn) saveBtn.disabled = !v; };
  form.addEventListener('input', () => set(true));
  form.addEventListener('change', () => set(true));
  set(false);
  return { reset: () => set(false), mark: () => set(true) };
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function emptyLine(text, action = null) {
  return h('p.empty', text, action ? [' ', action] : null);
}
export function loadingLine() { return h('p.loading', { role: 'status' }, 'Loading'); }

// Keep focus on the "same" control across a re-render of `region`.
export function rerender(region, build) {
  const active = document.activeElement;
  const key = active && region.contains(active) ? active.closest('[data-key]')?.dataset.key : null;
  build();
  if (key) {
    const el = region.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (el) el.focus({ preventScroll: true });
  }
}
