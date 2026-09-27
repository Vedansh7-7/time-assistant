// Load each page in headless Chrome and report JS errors, failed requests, and whether "Loading" is still showing.
//   node scripts/diag.mjs <base-url> [cpu-slowdown]
// cpu-slowdown (e.g. 6) throttles the CPU to approximate a slow phone or old laptop.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [, , BASE = 'http://127.0.0.1:8792', SLOW = '1'] = process.argv;
const CHROME = ['C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium']
  .find(existsSync);
const PORT = 9334;
const chrome = spawn(CHROME, [`--remote-debugging-port=${PORT}`, '--headless=new', '--no-first-run',
  `--user-data-dir=${mkdtempSync(join(tmpdir(), 'diag-'))}`, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(r => setTimeout(r, ms));
let targets;
for (let i = 0; i < 50 && !targets; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); } catch { await sleep(200); }
}
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener('open', r, { once: true }));
let seq = 0;
const waiting = new Map();
const events = [];
ws.addEventListener('message', ev => {
  const m = JSON.parse(ev.data);
  if (m.id && waiting.has(m.id)) { waiting.get(m.id)(m); waiting.delete(m.id); return; }
  if (m.method === 'Runtime.exceptionThrown') events.push('JS ERROR: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text).split('\n').slice(0, 3).join(' | '));
  if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') events.push('console.error: ' + m.params.args.map(a => a.value ?? a.description).join(' '));
  if (m.method === 'Network.responseReceived' && m.params.response.status >= 400) events.push(`HTTP ${m.params.response.status} ${m.params.response.url}`);
  if (m.method === 'Network.loadingFailed' && !m.params.canceled) events.push(`FAILED ${m.params.errorText}`);
});
const send = (method, params = {}) => new Promise(res => { const id = ++seq; waiting.set(id, m => res(m.result)); ws.send(JSON.stringify({ id, method, params })); });
await send('Runtime.enable'); await send('Network.enable'); await send('Page.enable');
if (+SLOW > 1) await send('Emulation.setCPUThrottlingRate', { rate: +SLOW });

for (const path of ['/#/', '/#/requests', '/#/tasks', '/#/people', '/#/settings/schedule', '/#/add', '/book']) {
  events.length = 0;
  const t0 = Date.now();
  await send('Page.navigate', { url: BASE + path });
  let text = '';
  for (let i = 0; i < 60; i++) { // up to 12 s
    await sleep(200);
    const r = await send('Runtime.evaluate', { expression: 'document.body ? document.body.innerText : ""', returnByValue: true });
    text = r?.result?.value || '';
    if (text && !/^\s*$/.test(text) && !/\bLoading\b/.test(text)) break;
  }
  const ms = Date.now() - t0;
  const stuck = /\bLoading\b/.test(text);
  console.log(`${stuck ? 'STUCK' : 'ok   '} ${String(ms).padStart(5)}ms  ${path}`);
  for (const e of events) console.log('        ' + e);
}
ws.close(); chrome.kill();
