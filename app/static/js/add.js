// The Add sheet: "Ask" the assistant, or fill in the form yourself ("Manual").
// Opened by the + button, the n key, free-time lines on the schedule, and the #/add route.
import { h, clear, get, post, del, sheet, segmented, errorText, confirmDialog, busy, dialogOpen } from './core.js';
import { commitmentForm } from './commitment.js';
import { pendingBlock } from './plan.js';
import { icon } from './icons.js';

let mode = null;
let current = null;

function savedMode() {
  if (mode) return mode;
  try { mode = localStorage.getItem('add.mode') || 'ask'; } catch { mode = 'ask'; }
  return mode;
}

export async function openAdd({ prefill = null, mode: want = null } = {}) {
  if (current) { current.close(); current = null; }
  if (dialogOpen()) return;
  const m = want || (prefill ? 'manual' : savedMode());
  const askPanel = h('div.add-panel', { hidden: m !== 'ask' });
  const manualPanel = h('div.add-panel', { hidden: m !== 'manual' });
  let manualBuilt = null;
  let askBuilt = false;

  const show = async v => {
    mode = v;
    if (!prefill) { try { localStorage.setItem('add.mode', v); } catch { /* private mode */ } }
    askPanel.hidden = v !== 'ask';
    manualPanel.hidden = v !== 'manual';
    if (v === 'ask' && !askBuilt) { askBuilt = true; await renderChat(askPanel, () => { seg.setValue('manual'); show('manual'); }); }
    if (v === 'manual' && !manualBuilt) {
      manualBuilt = await commitmentForm({ defaults: prefill || {}, onDone: () => s.close() });
      clear(manualPanel, manualBuilt.form);
    }
    if (v === 'manual') manualBuilt.focus(); else askPanel.querySelector('textarea')?.focus();
  };

  const seg = segmented('Add mode', [['ask', 'Ask'], ['manual', 'Manual']], m, show, { cls: 'seg-sm' });
  const s = sheet({
    title: 'Add', head: seg, content: [askPanel, manualPanel],
    onClose: () => { current = null; },
  });
  s.el.classList.add('add-sheet');
  current = s;
  await show(m);
  return s;
}

// ---------------------------------------------------------------- chat
async function renderChat(host, toManual) {
  const log = h('div.chat-log', { role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' });
  const input = h('textarea', { rows: 2, 'aria-label': 'Message', placeholder: 'Rahul wants to meet Tuesday at six' });
  const send = h('button.icon-btn.send', { type: 'submit', 'aria-label': 'Send' }, icon('send'));
  const form = h('form.composer', input, send);
  const clearBtn = h('button.link', {
    type: 'button',
    onclick: async () => {
      if (!await confirmDialog('Clear this conversation? Anything waiting for confirmation stays on the schedule.', { okText: 'Clear', title: 'Clear conversation' })) return;
      try { await del('/api/chat'); clear(log, msg('assistant', GREETING)); } catch (e) { log.append(msg('assistant', errorText(e))); }
    },
  }, 'Clear conversation');

  clear(host, log, form, h('div.chat-foot', clearBtn));

  try {
    const hist = await get('/api/chat/history');
    if (!hist.length) log.append(msg('assistant', GREETING));
    for (const m of hist) log.append(msg(m.role, m.content, m.pending));
  } catch (e) { log.append(msg('assistant', errorText(e))); }
  scrollEnd(log);

  const go = async () => {
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    log.append(msg('user', text));
    const typing = h('p.chat-typing', 'Checking the schedule');
    log.append(typing);
    scrollEnd(log);
    await busy(send, async () => {
      try {
        const r = await post('/api/chat', { message: text });
        typing.remove();
        log.append(msg('assistant', r.reply, r.pending));
        if (r.ai_available === false) {
          log.append(h('p.chat-aside', h('button.link', { type: 'button', onclick: toManual }, 'Add it manually instead')));
        }
      } catch (e) {
        typing.remove();
        log.append(msg('assistant', `That did not work: ${errorText(e)}`));
      }
    });
    scrollEnd(log);
    input.focus();
  };
  form.addEventListener('submit', e => { e.preventDefault(); go(); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); } });
}

const GREETING = 'Tell me what came up: a meeting, a task with a deadline, or a question about your week. I check the schedule and ask before changing anything.';

function scrollEnd(el) { requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; }); }

function msg(role, text, pending = []) {
  return h('div.msg', { class: role === 'user' ? 'from-user' : 'from-assistant' },
    String(text || '').split(/\n{2,}/).map(p => h('p', p)),
    (pending || []).map(p => pendingBlock(p, { dismiss: () => {} })));
}
