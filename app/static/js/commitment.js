// Commitment form (manual add, edit, move one occurrence) with a one-sentence live preview,
// the detail sheet, and "find a new time" for displaced commitments.
import {
  h, clear, get, post, sheet, field, select, levelSelect, chipGroup, readChips, weekdayChips, busy,
  setFieldError, clearErrors, debounce, wall, wallDate, wallTime, addMinutesWall, minutesBetween,
  longDate, timeRange, whenText, today, state, showError, errorText, menu, nextId, fmtDuration, listText,
} from './core.js';
import { runAction, check, previewSentence } from './plan.js';
import { icon } from './icons.js';

let peopleCache = null;
export async function people(force = false) {
  if (!peopleCache || force) peopleCache = await get('/api/people');
  return peopleCache;
}
export function invalidatePeople() { peopleCache = null; }

function nextHour() {
  const src = state.now ? state.now.slice(11, 13) : String(new Date().getHours()).padStart(2, '0');
  const hh = Math.min(22, Number(src) + 1);
  return `${String(hh).padStart(2, '0')}:00`;
}

/**
 * Build the commitment form.
 * commitment: existing (edit). occurrence {start,end}: edit one occurrence of a series.
 * defaults: prefill for create {start, end, title, kind, task_id, person_ids}.
 * Returns {form, focus}.
 */
export async function commitmentForm({ commitment = null, occurrence = null, defaults = {}, onDone = null, submitLabel = null } = {}) {
  const editing = !!commitment;
  const occOnly = !!occurrence;
  const c = commitment || {};
  let ppl = [];
  try { ppl = await people(); } catch (e) { showError(e); }

  const start0 = occurrence ? wall(occurrence.start) : editing ? wall(c.start) : (defaults.start || `${today()}T${nextHour()}`);
  const end0 = occurrence ? wall(occurrence.end) : editing ? wall(c.end) : (defaults.end || addMinutesWall(start0, 60));
  const selected = (c.people || []).map(p => p.id).concat(defaults.person_ids || []);

  const title = h('input', { name: 'title', required: true, value: c.title || defaults.title || '', autocomplete: 'off' });
  const date = h('input', { type: 'date', name: 'date', required: true, value: start0.slice(0, 10) });
  const startT = h('input', { type: 'time', name: 'start', required: true, value: start0.slice(11, 16), class: 'tnum' });
  const endT = h('input', { type: 'time', name: 'end', required: true, value: end0.slice(11, 16), class: 'tnum' });
  // Moving the start keeps the duration.
  let lastStart = startT.value;
  startT.addEventListener('change', () => {
    if (startT.value && endT.value && lastStart) {
      const dur = minutesBetween(`2000-01-01T${lastStart}`, `2000-01-01T${endT.value}`);
      if (dur > 0) endT.value = addMinutesWall(`2000-01-01T${startT.value}`, dur).slice(11, 16);
    }
    lastStart = startT.value;
  });

  const peopleBox = occOnly ? null : ppl.length
    ? chipGroup('People', 'person', ppl.map(p => [p.id, p.name]), selected)
    : h('p.hint', 'No people yet. ', h('a', { href: '#/people' }, 'Add someone'));

  // "More options"
  const status = select('status', [['confirmed', 'Confirmed'], ['tentative', 'Tentative']],
    ['tentative', 'confirmed'].includes(c.status) ? c.status : (defaults.status || 'confirmed'));
  const level = levelSelect(c.authority_level || defaults.authority_level || 3);
  const kind = select('kind', [['meeting', 'Meeting'], ['personal', 'Personal'], ['task_block', 'Task block']], c.kind || defaults.kind || 'meeting');
  const bufBefore = h('input', { type: 'number', min: 0, step: 5, name: 'buffer_before_min', value: c.buffer_before_min || 0, class: 'tnum' });
  const bufAfter = h('input', { type: 'number', min: 0, step: 5, name: 'buffer_after_min', value: c.buffer_after_min || 0, class: 'tnum' });
  const location = h('input', { name: 'location', value: c.location || '' });
  const notes = h('textarea', { name: 'notes', rows: 3 }, c.notes || '');
  const rec = occOnly ? null : recurrenceEditor(c.recurrence);
  const rem = remindersEditor(c.reminders === undefined ? null : c.reminders);
  const moreOpen = !!(editing && (c.location || c.notes || c.buffer_before_min || c.buffer_after_min || c.recurrence || c.reminders || c.status === 'tentative' || c.authority_level !== 3));

  const previewText = h('p.preview', { 'aria-hidden': 'true' });
  const previewLive = h('p.vh', { 'aria-live': 'polite' });
  const planHost = h('div.plan');
  const submit = h('button.btn.primary', { type: 'submit' }, submitLabel || (editing ? 'Save' : 'Add'));

  const form = h('form.stack.cform', { autocomplete: 'off', novalidate: true },
    field('Title', title),
    h('div.when', field('Date', date), field('Start', startT), field('End', endT)),
    peopleBox,
    h('details.more', { open: moreOpen },
      h('summary', 'More options'),
      h('div.stack',
        h('div.grid2', field('Status', status), field('Priority', level, { hint: 'Higher priority can move lower ones.' })),
        h('div.grid2', field('Kind', kind), field('Location', location)),
        h('div.grid2', field('Buffer before (min)', bufBefore), field('Buffer after (min)', bufAfter)),
        rec ? rec.el : null,
        rem.el,
        field('Notes', notes))),
    h('div.preview-row', previewText, previewLive),
    planHost,
    h('div.actions', submit));

  function times() {
    if (!date.value || !startT.value || !endT.value) return null;
    return { start: `${date.value}T${startT.value}`, end: `${date.value}T${endT.value}` };
  }

  function fields() {
    const t = times();
    const out = {
      title: title.value.trim(), start: t.start, end: t.end, status: status.value,
      authority_level: Number(level.value), kind: kind.value,
      buffer_before_min: Number(bufBefore.value) || 0, buffer_after_min: Number(bufAfter.value) || 0,
      location: location.value.trim() || null, notes: notes.value.trim() || null,
      reminders: rem.read(),
    };
    if (!occOnly) {
      out.person_ids = readChips(form, 'person').map(id => id);
      out.recurrence = rec.read();
    }
    if (!editing && defaults.task_id) out.task_id = defaults.task_id;
    return out;
  }

  function request() {
    const f = fields();
    if (occOnly) return ['move_occurrence', { id: c.id, occurrence_start: occurrence.start, changes: f }];
    if (editing) return ['update_commitment', { id: c.id, changes: f }];
    return ['create_commitment', f];
  }

  let lastOutcome = null;
  let previewSeq = 0;
  const doPreview = debounce(async () => {
    const t = times();
    if (!t || occOnly) { previewText.textContent = ''; return; }
    if (t.end <= t.start) { previewText.textContent = ''; return; }
    const [k, payload] = request();
    if (k === 'create_commitment' && !payload.title) payload.title = 'Untitled';
    if (k === 'update_commitment' && !payload.changes.title) payload.changes.title = c.title || 'Untitled';
    const mySeq = ++previewSeq;
    try {
      const plan = await check(k, payload);
      if (mySeq !== previewSeq) return; // a newer check is on its way
      const s = previewSentence(plan);
      previewText.textContent = s.text;
      previewText.dataset.tone = s.tone;
      if (plan.outcome !== lastOutcome) { previewLive.textContent = s.text; lastOutcome = plan.outcome; }
    } catch (e) {
      if (mySeq !== previewSeq) return;
      previewText.textContent = errorText(e);
      previewText.dataset.tone = 'bad';
    }
  }, 400);
  form.addEventListener('input', e => { if (e.target !== notes && e.target !== location) doPreview(); });
  form.addEventListener('change', doPreview);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearErrors(form);
    let bad = null;
    if (!title.value.trim()) { setFieldError(title, 'Add a title.'); bad = bad || title; }
    const t = times();
    if (!t) { setFieldError(date, 'Pick a date and times.'); bad = bad || date; }
    else if (t.end <= t.start) { setFieldError(endT, 'End must be after the start.'); bad = bad || endT; }
    if (bad) { bad.focus(); return; }
    clear(planHost);
    const [k, payload] = request();
    await busy(submit, () => runAction(k, payload, { host: planHost, onDone }).catch(() => {}));
  });

  doPreview();
  return { form, focus: () => title.focus(), reset: () => { form.reset(); clear(planHost); previewText.textContent = ''; lastOutcome = null; } };
}

function recurrenceEditor(r) {
  const freq = select('rec_freq', [['', 'Does not repeat'], ['daily', 'Daily'], ['weekly', 'Weekly'], ['monthly', 'Monthly']], r ? r.freq : '');
  const interval = h('input', { type: 'number', name: 'rec_interval', min: 1, value: r ? r.interval : 1, class: 'tnum' });
  const until = h('input', { type: 'date', name: 'rec_until', value: r && r.until ? r.until.slice(0, 10) : '' });
  const count = h('input', { type: 'number', name: 'rec_count', min: 1, value: r && r.count ? r.count : '', class: 'tnum' });
  const days = weekdayChips('On', 'rec_wd', r ? r.by_weekday : [], { hint: 'None picked means the start date’s weekday.' });
  const details = h('div.stack',
    h('div.grid3', field('Every', interval, { hint: 'Days, weeks or months' }), field('Until', until), field('Times', count)),
    days);
  const sync = () => { details.hidden = !freq.value; days.hidden = freq.value !== 'weekly'; };
  freq.addEventListener('change', sync);
  sync();
  const el = h('div.stack', field('Repeat', freq), details);
  return {
    el,
    read() {
      if (!freq.value) return null;
      const out = { freq: freq.value, interval: Number(interval.value) || 1 };
      if (freq.value === 'weekly') out.by_weekday = [...days.querySelectorAll('input:checked')].map(i => Number(i.value));
      if (until.value) out.until = until.value + 'T23:59';
      if (count.value) out.count = Number(count.value);
      return out;
    },
  };
}

function remindersEditor(value) {
  const mode = value == null ? 'default' : value.length === 0 ? 'none' : 'custom';
  const offsets = (value || []).filter(r => r.offset_min != null).map(r => r.offset_min).join(', ');
  const modeSel = select('rem_mode', [['default', 'My defaults'], ['none', 'None'], ['custom', 'Custom']], mode);
  const input = h('input', { name: 'rem_offsets', value: offsets, inputmode: 'numeric' });
  const custom = field('Minutes before', input, { hint: 'For example 30, 1440' });
  custom.hidden = mode !== 'custom';
  modeSel.addEventListener('change', () => { custom.hidden = modeSel.value !== 'custom'; });
  return {
    el: h('div.grid2', field('Reminders', modeSel), custom),
    read() {
      if (modeSel.value === 'default') return null;
      if (modeSel.value === 'none') return [];
      return input.value.split(/[,\s]+/).filter(Boolean).map(Number)
        .filter(n => Number.isFinite(n) && n >= 0).map(n => ({ offset_min: n }));
    },
  };
}

// Edit / move in a sheet.
export async function openCommitmentForm({ commitment = null, occurrence = null, defaults = {}, onDone = null, returnTo = null } = {}) {
  const title = occurrence ? 'Move this occurrence' : commitment ? (commitment.recurrence ? 'Edit the series' : 'Move or edit') : 'Add';
  let s;
  const built = await commitmentForm({ commitment, occurrence, defaults, onDone: r => { s.close(); onDone && onDone(r); } });
  s = sheet({ title, content: built.form, returnTo });
  built.focus();
  return s;
}

// ---------------------------------------------------------------- detail sheet
export async function openCommitment(cid, occ = null) {
  const trigger = document.activeElement;
  const triggerKey = trigger && trigger.closest ? trigger.closest('[data-key]')?.dataset.key : null;
  const returnTo = { trigger, triggerKey };
  let c;
  try { c = await get(`/api/commitments/${encodeURIComponent(cid)}`); } catch (e) { showError(e); return; }
  const recurring = !!c.recurrence;
  const active = ['tentative', 'confirmed'].includes(c.status);
  const s0 = occ ? occ.start : c.start, e0 = occ ? occ.end : c.end;
  const planHost = h('div.plan');
  let s;
  const close = () => s.close();
  const act = (kind, payload, extra = {}) => runAction(kind, payload, { host: planHost, onDone: close, ...extra }).catch(() => {});
  const swap = fn => { s.close(); fn(); };

  const lines = [
    h('p.detail-when.tnum', `${longDate(wallDate(s0))}, ${timeRange(s0, e0)}`,
      c.status === 'tentative' ? h('span.faint', ' tentative') : null),
    c.status === 'needs_reschedule' ? h('p.detail-line.warn-ink', 'Needs a new time.') : null,
    recurring ? h('p.detail-line', icon('repeat', { size: 16 }), h('span', `Repeats ${c.recurrence.text}`)) : null,
    c.people.length ? h('p.detail-line', `With ${listText(c.people.map(p => p.name))}`) : null,
    c.location ? h('p.detail-line', c.location) : null,
    c.buffer_before_min || c.buffer_after_min
      ? h('p.detail-line.faint', [c.buffer_before_min ? `${c.buffer_before_min} min buffer before` : null, c.buffer_after_min ? `${c.buffer_after_min} min after` : null].filter(Boolean).join(', ')) : null,
    c.notes ? h('p.detail-notes', c.notes) : null,
  ];

  const buttons = [];
  const more = [];
  if (c.status === 'needs_reschedule') {
    buttons.push(h('button.btn.primary', { type: 'button', onclick: () => swap(() => findNewSlot(c, returnTo)) }, 'Find a new time'));
    buttons.push(h('button.btn', { type: 'button', onclick: () => swap(() => openCommitmentForm({ commitment: c, returnTo })) }, 'Move'));
  } else if (active) {
    if (recurring && occ) {
      buttons.push(h('button.btn', { type: 'button', onclick: () => swap(() => openCommitmentForm({ commitment: c, occurrence: { start: occ.start, end: occ.end }, returnTo })) }, 'Move'));
    } else {
      buttons.push(h('button.btn', { type: 'button', onclick: () => swap(() => openCommitmentForm({ commitment: c, returnTo })) }, 'Move'));
    }
    if (c.status === 'tentative') {
      buttons.push(h('button.btn.primary', { type: 'button', onclick: e => busy(e.currentTarget, () => act('update_commitment', { id: c.id, changes: { status: 'confirmed' } }, { statusChange: true })) }, 'Confirm'));
    } else if (!recurring) {
      buttons.push(h('button.btn', { type: 'button', onclick: e => busy(e.currentTarget, () => act('update_commitment', { id: c.id, changes: { status: 'completed' } })) }, 'Complete'));
    }
  }
  if (!['cancelled', 'completed'].includes(c.status)) {
    buttons.push(h('button.btn.danger-quiet', { type: 'button', onclick: e => busy(e.currentTarget, () => act('cancel_commitments', { ids: [c.id] })) }, recurring ? 'Cancel series' : 'Cancel'));
  }
  if (recurring && occ && active) {
    more.push({ label: 'Skip this occurrence', onSelect: () => act('skip_occurrence', { id: c.id, occurrence_start: occ.start }) });
    more.push({ label: 'Edit the series', onSelect: () => swap(() => openCommitmentForm({ commitment: c, returnTo })) });
  }
  const priorityBox = h('div.inline-row', { hidden: true });
  if (active || c.status === 'needs_reschedule') {
    more.push({
      label: 'Change priority',
      onSelect: () => {
        const sel = levelSelect(c.authority_level);
        clear(priorityBox, field('Priority', sel, { hint: 'Higher priority can move lower ones.' }),
          h('button.btn', { type: 'button', onclick: e => busy(e.currentTarget, () => act('update_commitment', { id: c.id, changes: { authority_level: Number(sel.value) } })) }, 'Save'));
        priorityBox.hidden = false;
        sel.focus();
      },
    });
  }

  s = sheet({
    title: c.title,
    returnTo,
    content: [
      h('div.detail', lines),
      h('div.actions.detail-actions', buttons, more.length ? menu('More actions', more) : null),
      priorityBox,
      planHost,
    ],
  });
  s.el.querySelector('.detail-actions button')?.focus();
}

// For displaced ("needs reschedule") commitments: search free slots and move it back.
export async function findNewSlot(c, returnTo = null) {
  const duration = minutesBetween(wall(c.start), wall(c.end));
  const host = h('div', h('p.loading', { role: 'status' }, 'Looking for free time'));
  const planHost = h('div.plan');
  const s = sheet({ title: `New time for ${c.title}`, content: [host, planHost], returnTo });
  try {
    const res = await post('/api/slots', {
      duration_min: duration, exclude_id: c.id,
      buffer_before_min: c.buffer_before_min, buffer_after_min: c.buffer_after_min, limit: 6,
    });
    if (!res.slots.length) { clear(host, h('p.empty', 'No free time in the next two weeks.')); return; }
    const tentative = h('input', { type: 'checkbox', id: nextId('tent') });
    clear(host,
      h('p.plan-sentence', `Pick a free ${fmtDuration(duration)} slot.`),
      h('ul.choices', res.slots.map(sl => h('li', h('button.choice', {
        type: 'button',
        onclick: e => busy(e.currentTarget, () => runAction('update_commitment',
          { id: c.id, changes: { start: wall(sl.start), end: wall(sl.end), status: tentative.checked ? 'tentative' : 'confirmed' } },
          { host: planHost, onDone: () => s.close() }).catch(() => {})),
      }, h('span.choice-main.tnum', whenText(sl.start, sl.end)),
        sl.reasons.length ? h('span.choice-why', sl.reasons.join(', ')) : null)))),
      h('label.check', tentative, h('span', 'Pencil in as tentative')));
    host.querySelector('.choice')?.focus();
  } catch (e) { clear(host, h('p.form-error', { role: 'alert' }, errorText(e))); }
}

// Used by other pages for a compact line: "Tue 16:00–17:00"
export function occWhen(o) { return whenText(o.start, o.end); }
export { wallTime };
