// Entry point: hash router, navigation, keyboard shortcuts, quiet refresh, reminder polling.
import { h, clear, get, toast, errorText, isTyping, dialogOpen, anyDirty, loadingLine } from './js/core.js';
import { icon } from './js/icons.js';
import { renderHome, refreshHome, homeKey } from './js/home.js';
import { openAdd } from './js/add.js';
import { renderTasks } from './js/tasks.js';
import { renderPeople } from './js/people.js';
import { renderSettings, updateSettings } from './js/settings.js';
import { renderRequests, refreshRequests } from './js/requests.js';

const ROUTES = [
  { path: '', label: 'Schedule', icon: 'calendar', render: renderHome, refresh: refreshHome },
  { path: 'requests', label: 'Requests', icon: 'inbox', render: renderRequests, refresh: refreshRequests },
  { path: 'tasks', label: 'Tasks', icon: 'list', render: renderTasks },
  { path: 'people', label: 'People', icon: 'users', render: renderPeople },
  { path: 'settings', label: 'Settings', icon: 'settings', render: renderSettings, update: updateSettings },
];
const ALIASES = { prefs: 'settings', system: 'settings/advanced' };

const main = document.getElementById('main');
const nav = document.getElementById('nav');
const countEl = h('span.nav-count', { hidden: true });
const links = new Map();

// Navigation is built once; route changes only move aria-current.
clear(nav, h('ul', ROUTES.map(r => {
  const a = h('a', { href: '#/' + r.path },
    icon(r.icon, { size: 22 }), h('span.nav-label', r.label), r.path === 'requests' ? countEl : null);
  links.set(r.path, a);
  return h('li', a);
})));

const fab = document.getElementById('fab');
fab.appendChild(icon('plus', { size: 24 }));
fab.addEventListener('click', () => openAdd());

let current = { page: null, arg: null };
let pendingHomeKey = null;

function parseHash() {
  let raw = location.hash.replace(/^#\/?/, '');
  const first = raw.split('/')[0];
  if (ALIASES[first]) { raw = ALIASES[first]; history.replaceState(null, '', '#/' + raw); }
  const parts = raw.split('/');
  return { page: parts[0] || '', arg: parts[1] ? decodeURIComponent(parts[1]) : null };
}

async function route(initial = false) {
  let { page, arg } = parseHash();
  if (page === 'add') {
    // #/add opens the Add sheet over the current page (or the schedule).
    const back = current.page != null ? current : { page: '', arg: null };
    history.replaceState(null, '', '#/' + back.page + (back.arg ? '/' + encodeURIComponent(back.arg) : ''));
    if (current.page == null) await show(back.page, back.arg, true);
    openAdd();
    return;
  }
  if (page === current.page && ROUTES.find(r => r.path === page)?.update) {
    if (await ROUTES.find(r => r.path === page).update(arg)) { current = { page, arg }; return; }
  }
  await show(page, arg, initial);
}

async function show(page, arg, initial) {
  const r = ROUTES.find(x => x.path === page) || ROUTES[0];
  current = { page: r.path, arg };
  document.body.dataset.page = r.path || 'home';
  for (const [p, a] of links) {
    if (p === r.path) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  document.title = r.path ? `${r.label} · Time Assistant` : 'Time Assistant';
  const slow = setTimeout(() => clear(main, loadingLine()), 200);
  try {
    await r.render(main, arg);
  } catch (e) {
    clear(main, h('div.page-error', h('h1', { tabindex: '-1' }, 'This page did not load'), h('p', errorText(e)),
      h('button.btn', { type: 'button', onclick: () => show(r.path, arg, false) }, 'Try again')));
  } finally { clearTimeout(slow); }
  if (r.path === '' && pendingHomeKey) { homeKey(pendingHomeKey); pendingHomeKey = null; }
  if (!initial) {
    const h1 = main.querySelector('h1');
    (h1 || main).focus({ preventScroll: true });
    window.scrollTo(0, 0);
  }
}

window.addEventListener('hashchange', () => route(false));
route(true);

// ---- keyboard shortcuts (ignored while typing or when a dialog is open)
document.addEventListener('keydown', e => {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || isTyping() || dialogOpen()) return;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (key === 'n') { e.preventDefault(); openAdd(); return; }
  if (['t', 'd', 'w', 'm', 'ArrowLeft', 'ArrowRight'].includes(key)) {
    if ((key === 'ArrowLeft' || key === 'ArrowRight') && document.activeElement && document.activeElement.closest('.month-grid, .seg, .menu')) return;
    if (current.page !== '') {
      if (key.length === 1) { pendingHomeKey = key; location.hash = '#/'; }
      return;
    }
    if (homeKey(key)) e.preventDefault();
  }
});

// ---- quiet refresh when the tab comes back: only Schedule and Requests, never over a dialog
// or unsaved edits, and never replacing content with a placeholder.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || dialogOpen() || anyDirty()) return;
  const r = ROUTES.find(x => x.path === current.page);
  if (r && r.refresh) r.refresh();
  updateRequestCount();
});

// ---- open request count in the nav
async function updateRequestCount() {
  try {
    const n = (await get('/api/requests/count')).open;
    countEl.textContent = n ? String(n) : '';
    countEl.hidden = !n;
    links.get('requests').setAttribute('aria-label', n ? `Requests, ${n} open` : 'Requests');
  } catch { /* offline: keep the last count */ }
}
updateRequestCount();
document.addEventListener('ta:changed', updateRequestCount);

// ---- reminders: poll and toast new ones (browser notifications need HTTPS; don't rely on them)
const seen = new Set();
let primed = false;
const offline = document.getElementById('offline');
async function pollNotifications() {
  try {
    const list = await get('/api/notifications');
    for (const n of list) {
      const k = n.occurrence_key + '|' + n.fire_at;
      if (!seen.has(k)) {
        seen.add(k);
        if (primed) toast(n.message, 'reminder', 15000);
      }
    }
    primed = true;
    offline.textContent = '';
    document.body.classList.remove('offline');
  } catch {
    offline.textContent = 'Offline';
    document.body.classList.add('offline');
  }
}
pollNotifications();
setInterval(pollNotifications, 60000);
