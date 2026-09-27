// Screenshot every page of a running instance with headless Chrome over the DevTools protocol.
//   node scripts/shots.mjs <base-url> <out-dir> [status-ref]
// No dependencies (Node 22+ has fetch and WebSocket built in). Use seeded demo data, not real data.
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [, , BASE = 'http://127.0.0.1:8790', OUT = 'shots', STATUS_REF = ''] = process.argv;
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync);
if (!CHROME) throw new Error('Chrome/Edge not found');
mkdirSync(OUT, { recursive: true });

const PORT = 9333;
const chrome = spawn(CHROME, [`--remote-debugging-port=${PORT}`, '--headless=new', '--hide-scrollbars',
  '--no-first-run', '--no-default-browser-check', `--user-data-dir=${mkdtempSync(join(tmpdir(), 'shots-'))}`,
  'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));

let targets;
for (let i = 0; i < 50 && !targets; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); } catch { await sleep(200); }
}
const page = targets.find(t => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0;
const waiting = new Map();
ws.addEventListener('message', ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg); waiting.delete(msg.id); }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  waiting.set(id, m => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
  ws.send(JSON.stringify({ id, method, params }));
});
const run = async js => {
  const r = await send('Runtime.evaluate', { expression: `(async () => { ${js} })()`, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || 'eval failed');
  return r.result.value;
};
// Helpers available to actions inside the page.
const HELPERS = `
  window.__w = ms => new Promise(r => setTimeout(r, ms));
  window.__q = async (sel, tries = 40) => { for (let i = 0; i < tries; i++) { const e = document.querySelector(sel); if (e && e.offsetParent !== null) return e; await __w(100); } throw new Error('not found: ' + sel); };
  window.__click = async sel => { (await __q(sel)).click(); await __w(500); };
  window.__text = async (text, scope = 'button, a, [role=tab], summary') => { for (let i = 0; i < 40; i++) { const e = [...document.querySelectorAll(scope)].find(x => x.offsetParent !== null && x.textContent.trim() === text); if (e) { e.click(); await __w(500); return; } await __w(100); } throw new Error('no ' + text); };
  window.__fill = async (sel, v) => { const e = await __q(sel); e.focus(); e.value = v; e.dispatchEvent(new Event('input', { bubbles: true })); e.dispatchEvent(new Event('change', { bubbles: true })); await __w(200); };
`;

await send('Page.enable');
await send('Runtime.enable');
await send('Page.addScriptToEvaluateOnNewDocument', { source: HELPERS });

const DEVICES = {
  laptop: { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false },
  phone: { width: 390, height: 844, deviceScaleFactor: 2, mobile: true },
};

async function shot(name, { url, device = 'laptop', scheme = 'light', mode, actions = '' }) {
  await send('Emulation.setDeviceMetricsOverride', DEVICES[device]);
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: scheme },
    { name: 'prefers-reduced-motion', value: 'reduce' }] });
  await send('Page.navigate', { url: BASE + '/health' });
  await sleep(300);
  await run(`localStorage.clear(); ${mode ? `localStorage.setItem('home.mode', '${mode}');` : ''}`);
  await send('Page.navigate', { url: BASE + url });
  await sleep(1800);
  if (actions) await run(actions);
  await sleep(700);
  const { data } = await send('Page.captureScreenshot', { format: 'png' });
  const file = `${name}.png`;
  writeFileSync(join(OUT, file), Buffer.from(data, 'base64'));
  console.log('ok', file);
  return file;
}

const bookFlow = {
  times: `await __fill('#motive', 'Quick chat about a design project'); await __click('#i-durations button, #i-durations label'); await __click('#i-submit');`,
};

const plan = [
  // Owner app, laptop
  ['01-schedule-day', { url: '/#/' }],
  ['02-schedule-week', { url: '/#/', mode: 'week' }],
  ['03-schedule-month', { url: '/#/', mode: 'month' }],
  ['04-commitment-detail', { url: '/#/', actions: `await __click('.agenda-row');` }],
  ['05-add-ask', { url: '/#/add' }],
  ['06-add-manual', { url: '/#/add', actions: `await __text('Manual');` }],
  ['07-requests', { url: '/#/requests' }],
  ['08-tasks', { url: '/#/tasks' }],
  ['09-people', { url: '/#/people' }],
  ['10-settings-schedule', { url: '/#/settings/schedule' }],
  ['11-settings-availability', { url: '/#/settings/availability' }],
  ['12-settings-assistant', { url: '/#/settings/assistant' }],
  ['13-settings-advanced', { url: '/#/settings/advanced' }],
  // Public booking, laptop
  ['20-book-intro', { url: '/book' }],
  ['21-book-pick-time', { url: '/book', actions: bookFlow.times }],
  ['22-book-details', { url: '/book', actions: bookFlow.times + `await __click('#t-list button');` }],
  ...(STATUS_REF ? [['23-book-status-declined', { url: `/book/status/${STATUS_REF}` }]] : []),
  // Phone
  ['30-phone-day', { url: '/#/', device: 'phone' }],
  ['31-phone-week', { url: '/#/', device: 'phone', mode: 'week' }],
  ['32-phone-add', { url: '/#/add', device: 'phone' }],
  ['33-phone-requests', { url: '/#/requests', device: 'phone' }],
  ['34-phone-book-intro', { url: '/book', device: 'phone' }],
  ['35-phone-book-day', { url: '/book', device: 'phone', actions: bookFlow.times }],
  ['36-phone-book-times', { url: '/book', device: 'phone', actions: bookFlow.times + `await __click('#s-strip button:not([disabled])');` }],
  // Dark
  ['40-dark-day', { url: '/#/', scheme: 'dark' }],
  ['41-dark-phone-day', { url: '/#/', device: 'phone', scheme: 'dark' }],
  ['42-dark-book', { url: '/book', scheme: 'dark', actions: bookFlow.times }],
  ['43-dark-phone-add', { url: '/#/add', device: 'phone', scheme: 'dark', actions: `await __text('Manual');` }],
];

// Warm the browser cache once so the first shot isn't a cold load.
await send('Page.navigate', { url: BASE + '/#/' });
await sleep(4000);
await send('Page.navigate', { url: BASE + '/book' });
await sleep(2000);

const done = [];
for (const [name, opts] of plan) {
  try { done.push({ name, file: await shot(name, opts), device: opts.device || 'laptop', scheme: opts.scheme || 'light' }); }
  catch (e) { console.log('FAIL', name, e.message); }
}
writeFileSync(join(OUT, 'manifest.json'), JSON.stringify(done, null, 2));
ws.close();
chrome.kill();
