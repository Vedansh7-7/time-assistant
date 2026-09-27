// Tasks: deadlines and estimates. They never take time on their own; "Find time" books work blocks.
import {
  h, clear, get, post, patch, sheet, field, busy, menu, wall, wallDate, wallTime, parseDate, today, addDays,
  DAYS, MONTHS, fmtDuration, whenText, rerender, emptyLine, errorText, toast, setFieldError, clearErrors,
  formError, changed, nextId, ensureToday,
} from './core.js';
import { runAction } from './plan.js';

let showClosed = false;
let els = null;

export async function renderTasks(root) {
  await ensureToday();
  try { showClosed = localStorage.getItem('tasks.closed') === '1'; } catch { /* private mode */ }
  const toggle = h('button.btn.btn-quiet', {
    type: 'button', 'aria-pressed': String(showClosed),
    onclick: () => {
      showClosed = !showClosed;
      toggle.setAttribute('aria-pressed', String(showClosed));
      try { localStorage.setItem('tasks.closed', showClosed ? '1' : '0'); } catch { /* private mode */ }
      refresh();
    },
  }, 'Show finished');
  const list = h('div');
  els = { list };
  clear(root,
    h('div.page-head', h('h1', { tabindex: '-1' }, 'Tasks'),
      h('div.head-actions', toggle, h('button.btn.primary', { type: 'button', onclick: () => taskSheet(null) }, 'New task'))),
    list);
  await refresh();
}

document.addEventListener('ta:changed', () => { if (els && els.list.isConnected) refresh(); });

async function refresh() {
  let list;
  try { list = await get(`/api/tasks${showClosed ? '?include_closed=true' : ''}`); }
  catch (e) { clear(els.list, h('p.form-error', { role: 'alert' }, errorText(e))); return; }
  rerender(els.list, () => clear(els.list, list.length
    ? h('ul.rows', list.map(row))
    : emptyLine(showClosed ? 'No tasks.' : 'No open tasks.', h('button.link', { type: 'button', onclick: () => taskSheet(null) }, 'Add one'))));
}

function dueText(iso) {
  const d = wallDate(iso);
  const diff = Math.round((parseDate(d) - parseDate(today())) / 86400000);
  const dt = parseDate(d);
  const day = diff === 0 ? 'today' : diff === 1 ? 'tomorrow' : diff > 1 && diff < 7 ? DAYS[dt.getDay()] : `${dt.getDate()} ${MONTHS[dt.getMonth()]}`;
  return `${day} ${wallTime(iso)}`;
}

function meta(t) {
  const facts = [`Due ${dueText(t.deadline)}`];
  if (t.status === 'done') facts.push('Done');
  else if (t.status === 'dropped') facts.push('Dropped');
  else if (t.remaining_min) facts.push(`${fmtDuration(t.remaining_min)} left`);
  else if (t.remaining_min === 0) facts.push('Scheduled');
  return facts.slice(0, 2).join(' · ');
}

function row(t) {
  const open = t.status === 'open';
  const risk = open && (t.overdue ? 'Overdue' : t.at_risk ? 'At risk' : null);
  return h('li.task', { class: open ? '' : 'closed', dataset: { key: 'task:' + t.id } },
    h('div.grow',
      h('p.row-strong', t.title),
      h('p.task-meta.tnum', meta(t), risk ? [h('span.sep', { 'aria-hidden': 'true' }, ' · '), h('span.warn-ink', risk)] : null)),
    h('div.row-actions',
      open && t.remaining_min ? h('button.btn', { type: 'button', dataset: { key: 'task-find:' + t.id }, onclick: () => openWorkBlocks(t.id) }, 'Find time') : null,
      open ? h('button.btn', { type: 'button', onclick: e => setStatus(t, 'done', e.currentTarget) }, 'Done')
        : h('button.btn.btn-quiet', { type: 'button', onclick: e => setStatus(t, 'open', e.currentTarget) }, 'Reopen'),
      menu(`More for ${t.title}`, [
        { label: 'Edit', onSelect: () => taskSheet(t) },
        open ? { label: 'Drop', onSelect: () => setStatus(t, 'dropped') } : null,
      ])));
}

async function setStatus(t, status, btn = null) {
  await busy(btn, async () => {
    try {
      await patch(`/api/tasks/${t.id}`, { status });
      toast(status === 'done' ? `${t.title} is done.` : status === 'dropped' ? `Dropped ${t.title}.` : `Reopened ${t.title}.`, 'success');
      changed('tasks');
    } catch (e) { toast(errorText(e), 'error', 8000); }
  });
}

function taskSheet(t) {
  const title = h('input', { name: 'title', required: true, value: t ? t.title : '', autocomplete: 'off' });
  const deadline = h('input', { type: 'datetime-local', name: 'deadline', required: true, value: t ? wall(t.deadline) : `${addDays(today(), 2)}T23:00`, class: 'tnum' });
  const est = h('input', { type: 'number', name: 'est', min: 5, step: 5, required: true, value: t ? t.estimated_duration_min : 60, class: 'tnum' });
  const notes = h('textarea', { name: 'notes', rows: 3 }, t && t.notes ? t.notes : '');
  const save = h('button.btn.primary', { type: 'submit' }, t ? 'Save' : 'Add task');
  const form = h('form.stack', { novalidate: true },
    field('Title', title),
    h('div.grid2', field('Deadline', deadline), field('Estimate (minutes)', est)),
    field('Notes', notes),
    h('div.actions',
      t ? h('button.link.danger', {
        type: 'button', class: 'push-left',
        onclick: () => { s.close(); runAction('delete_tasks', { ids: [t.id] }, { title: 'Delete task' }).catch(() => {}); },
      }, 'Delete task') : null,
      h('button.btn', { type: 'button', onclick: () => s.close() }, 'Cancel'), save));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearErrors(form);
    if (!title.value.trim()) { setFieldError(title, 'Add a title.'); title.focus(); return; }
    if (!deadline.value) { setFieldError(deadline, 'Pick a deadline.'); deadline.focus(); return; }
    if (!(Number(est.value) >= 5)) { setFieldError(est, 'At least 5 minutes.'); est.focus(); return; }
    const body = { title: title.value.trim(), deadline: deadline.value, estimated_duration_min: Number(est.value), notes: notes.value.trim() || null };
    await busy(save, async () => {
      try {
        if (t) await patch(`/api/tasks/${t.id}`, body); else await post('/api/tasks', body);
        s.close();
        toast(t ? 'Saved.' : `Added ${body.title}.`, 'success');
        changed('tasks');
      } catch (err) { formError(form, errorText(err)); }
    });
  });
  const s = sheet({ title: t ? 'Edit task' : 'New task', content: form });
  title.focus();
}

// Propose work blocks for a task, then book the chosen plan as task_block commitments.
export async function openWorkBlocks(tid) {
  const host = h('div', h('p.loading', { role: 'status' }, 'Looking for time before the deadline'));
  const planHost = h('div.plan');
  const s = sheet({ title: 'Find time', content: [host, planHost] });
  let res;
  try { res = await get(`/api/tasks/${encodeURIComponent(tid)}/work-blocks`); }
  catch (e) { clear(host, h('p.form-error', { role: 'alert' }, errorText(e))); return; }
  const t = res.task;
  if (!res.options.length) {
    clear(host, h('p.plan-sentence', t.remaining_min ? 'There is not enough free time before the deadline.' : 'Everything is already scheduled.'));
    return;
  }
  const tentId = nextId('tent');
  const tentative = h('input', { type: 'checkbox', id: tentId });
  clear(host,
    h('p.plan-sentence', `${t.title} needs ${fmtDuration(t.remaining_min)} before ${dueText(t.deadline)}.`),
    h('ul.choices', res.options.map(plan => h('li', h('button.choice', {
      type: 'button',
      onclick: e => busy(e.currentTarget, async () => {
        for (const sl of plan) {
          try {
            const r = await runAction('create_commitment', {
              title: t.title, start: wall(sl.start), end: wall(sl.end), kind: 'task_block',
              task_id: t.id, status: tentative.checked ? 'tentative' : 'confirmed', authority_level: 3,
            }, { host: planHost });
            if (r.status !== 'executed') return; // the owner resolves this block here
          } catch { return; }
        }
        s.close();
      }),
    }, h('span.choice-main.tnum', plan.map(sl => whenText(sl.start, sl.end)).join(' and ')),
      plan[0].reasons.length ? h('span.choice-why', plan[0].reasons.join(', ')) : null)))),
    h('label.check', { for: tentId }, tentative, h('span', 'Pencil in as tentative')));
  host.querySelector('.choice')?.focus();
}
