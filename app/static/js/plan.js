// The one plan/conflict component for every guarded action. It speaks in sentences and offers
// one primary action. runAction(kind, payload) submits through the guardrail layer and walks the
// owner through whatever the core decides: executed, rejected (choices), or needs confirmation.
import {
  h, clear, post, sheet, toast, errorText, wall, whenText, listText, levelSelect, field, busy,
  setFieldError, changed, nextId,
} from './core.js';

const TOP_LEVEL = new Set(['create_commitment', 'force_commitment']);
const url = kind => kind === 'create_commitment' ? '/api/commitments' : `/api/actions/${kind}`;

function withField(kind, payload, key, value) {
  const p = structuredClone(payload);
  if (TOP_LEVEL.has(kind)) p[key] = value;
  else { p.changes = p.changes || {}; p.changes[key] = value; }
  return p;
}
function getField(kind, payload, key) {
  return TOP_LEVEL.has(kind) ? payload[key] : (payload.changes || {})[key];
}

// ---------------------------------------------------------------- sentences
const names = list => listText(list.map(o => o.title));
const isAre = n => (n > 1 ? 'are' : 'is');

export function warningText(w) {
  let m = /^Inside '(.+)' window$/.exec(w);
  if (m) return `It falls during ${m[1]}.`;
  m = /^During (.+)$/.exec(w);
  if (m) return `It falls during ${m[1].replace(/\.$/, '')}.`;
  if (/in the past/i.test(w)) return 'This is in the past.';
  return w;
}

function conflictsOf(plan, rel) { return (plan.conflicts || []).filter(c => !rel || c.relation === rel); }

// One-sentence live preview for forms: "Free." / "Conflicts with X." / "Replaces X, which ..."
export function previewSentence(plan) {
  if (!plan) return { text: '', tone: '' };
  const warn = (plan.warnings || []).map(warningText);
  const inside = warn.find(w => w.startsWith('It falls during'));
  if (plan.outcome === 'FREE') {
    return inside ? { text: `Free, but ${inside[0].toLowerCase()}${inside.slice(1)}`, tone: 'warn' }
      : { text: 'Free.', tone: 'ok' };
  }
  if (plan.outcome === 'BLOCKED') {
    const b = conflictsOf(plan, 'blocked');
    return { text: `Conflicts with ${names(b.length ? b : plan.conflicts)}.`, tone: 'bad' };
  }
  if (plan.outcome === 'EQUAL_CONFLICT') {
    return { text: `Conflicts with ${names(conflictsOf(plan, 'equal'))}, at the same priority.`, tone: 'warn' };
  }
  if (plan.outcome === 'OVERRIDE_POSSIBLE') {
    const d = plan.displace || [];
    return { text: `Replaces ${names(d)}, which will need rescheduling.`, tone: 'warn' };
  }
  return { text: plan.message || '', tone: '' };
}

function confirmSentence(r, kind) {
  const plan = r.plan || {};
  const c = plan.candidate;
  const d = plan.displace || [];
  const warn = (plan.warnings || []).map(warningText).find(w => w.startsWith('It falls during'));
  if (kind === 'force_commitment') {
    return d.length ? `This places ${c.title} and moves ${names(d)} to "needs rescheduling". Continue?`
      : `Place ${c.title} anyway?`;
  }
  if (kind === 'cancel_commitments') {
    const cs = plan.cancel || [];
    return cs.length === 1 ? `Cancel ${cs[0].title}${cs[0].recurrence ? ' and all its repeats' : ''}?` : `Cancel ${cs.length} commitments?`;
  }
  if (kind === 'delete_tasks') return 'Delete this task and its history?';
  if (kind === 'skip_occurrence') return plan.message || 'Skip this occurrence?';
  if (d.length) return `This moves ${names(d)} to "needs rescheduling". Continue?`;
  if (warn && c) return `${c.title} ${warn.replace(/^It /, '').replace(/\.$/, '')}. Continue?`;
  if (c && kind === 'update_commitment' && plan.message && /^Mark as/.test(plan.message)) return `Mark ${c.title} as ${c.status === 'completed' ? 'done' : c.status}?`;
  if (c && (kind === 'update_commitment' || kind === 'move_occurrence')) {
    if (c.status === 'confirmed' && r.statusChange) return `Confirm ${c.title}?`;
    return `Save the change to ${c.title}, ${c.when}?`;
  }
  if (c) return `Add ${c.title}, ${c.when}?`;
  return 'Continue?';
}

// Phrase a pending action as a plain question from its structured fields.
export function pendingQuestion(p) {
  const c = p.candidate;
  const moved = (p.replaces || []).length ? ` and move ${names(p.replaces.map(title => ({ title })))} to "needs rescheduling"` : '';
  if (c && p.kind === 'create_commitment') return `Add ${c.title}, ${c.when}${moved}?`;
  if (c && p.kind === 'force_commitment') return `Place ${c.title}, ${c.when}${moved}?`;
  if (c && (p.kind === 'update_commitment' || p.kind === 'move_occurrence')) return `Change ${c.title} to ${c.when}${moved}?`;
  // Other actions: the server summary is already a short question.
  const s = String(p.summary || '').replace(/ Type ".+" to confirm\.$/, '').trim();
  return /\?$/.test(s) ? s : s.replace(/\.$/, '') + '?';
}

function successText(r, kind) {
  const res = r.result || {};
  const d = (r.plan && r.plan.displace) || [];
  let t = 'Done.';
  if (res.commitment) {
    const c = res.commitment;
    if (kind === 'create_commitment' || kind === 'force_commitment') t = `Added ${c.title}, ${c.when}.`;
    else if (kind === 'move_occurrence') t = `Moved this occurrence of ${c.title}.`;
    else if (c.status === 'completed') t = `Marked ${c.title} as done.`;
    else t = `Saved ${c.title}.`;
  } else if (res.cancelled) t = res.cancelled.length > 1 ? `Cancelled ${res.cancelled.length} commitments.` : 'Cancelled.';
  else if (res.skipped) t = 'Skipped this occurrence.';
  else if (res.deleted) t = 'Deleted.';
  else if (res.person) t = `Saved ${res.person.name}.`;
  if (d.length) t += ` ${names(d)} ${d.length > 1 ? 'need' : 'needs'} rescheduling.`;
  const warn = ((r.plan && r.plan.warnings) || []).map(warningText);
  if (warn.length) t += ' ' + warn.join(' ');
  return t;
}

// ---------------------------------------------------------------- flow
function finish(r, ctx) {
  // Close first so the toast lands in the page, not inside a dialog that is about to go away.
  ctx.close && ctx.close();
  ctx.onDone && ctx.onDone(r);
  toast(successText(r, ctx.kind), 'success');
  changed();
}

async function submit(ctx, kind, payload, btn) {
  ctx.kind = kind; ctx.payload = payload;
  return busy(btn, async () => {
    try {
      const r = await post(url(kind), payload);
      if (r.status === 'executed') finish(r, ctx);
      else render(ctx, r);
      return r;
    } catch (e) {
      clear(ctx.host, h('p.form-error', { role: 'alert' }, errorText(e)));
      return null;
    }
  });
}

/**
 * Submit a guarded action. opts.host renders the plan inline (e.g. under a form);
 * otherwise a sheet opens only when the owner needs to decide something.
 */
export async function runAction(kind, payload, { host = null, title = null, onDone = null, onClose = null, statusChange = false } = {}) {
  const ctx = { kind, payload, onDone, host, statusChange, close: null, dismiss: null };
  let r;
  try { r = await post(url(kind), payload); }
  catch (e) {
    if (host) clear(host, h('p.form-error', { role: 'alert' }, errorText(e)));
    else toast(errorText(e), 'error', 8000);
    throw e;
  }
  if (r.status === 'executed') { finish(r, ctx); return r; }
  if (host) {
    ctx.dismiss = () => clear(host);
  } else {
    ctx.host = h('div.plan');
    const s = sheet({ title: title || titleFor(kind, r), content: ctx.host, onClose });
    ctx.close = s.close;
    ctx.dismiss = s.close;
  }
  render(ctx, r);
  return r;
}

function titleFor(kind, r) {
  if (r.status === 'needs_confirmation') return kind === 'cancel_commitments' ? 'Cancel' : 'Please confirm';
  return 'This time is taken';
}

function render(ctx, r) {
  const plan = r.plan || {};
  const parts = [];
  if (r.status === 'superseded') parts.push(h('p.plan-note', 'The schedule changed, so nothing was done.'));
  if (r.status === 'needs_confirmation') {
    parts.push(confirmStep(ctx, {
      token: r.token, confirmation: r.confirmation, typed_phrase: r.typed_phrase,
      question: confirmSentence({ ...r, statusChange: ctx.statusChange }, ctx.kind),
      danger: ctx.kind === 'force_commitment' || ctx.kind === 'cancel_commitments' || ctx.kind === 'delete_tasks',
    }));
  } else if (plan.outcome === 'BLOCKED') {
    const b = conflictsOf(plan, 'blocked');
    const list = b.length ? b : plan.conflicts || [];
    parts.push(h('p.plan-sentence', `${names(list)} ${isAre(list.length)} booked then and ${list.length > 1 ? 'outrank' : 'outranks'} this.`));
    parts.push(...warnings(plan));
    parts.push(alternatives(ctx, plan));
    parts.push(h('div.plan-links', priorityAction(ctx, plan), overrideAction(ctx)));
  } else if (plan.outcome === 'EQUAL_CONFLICT') {
    const eq = conflictsOf(plan, 'equal');
    parts.push(h('p.plan-sentence', `${names(eq)} ${isAre(eq.length)} booked then, at the same priority.`));
    parts.push(...warnings(plan));
    parts.push(equalChoices(ctx, plan, eq));
  } else {
    parts.push(h('p.plan-sentence', r.status === 'superseded' ? previewSentence(plan).text : (plan.message || r.message || '')));
    parts.push(...warnings(plan));
  }
  if (r.status === 'superseded' && ctx.payload && plan.executable) {
    parts.push(h('div.actions', h('button.btn.primary', {
      type: 'button', onclick: e => submit(ctx, ctx.kind, ctx.payload, e.currentTarget),
    }, 'Check again')));
  }
  clear(ctx.host, parts);
  const first = ctx.host.querySelector('input, .choice, .btn.primary');
  if (first && ctx.host.closest('dialog')) first.focus();
}

function warnings(plan) {
  if (plan.outcome === 'FREE' || plan.outcome === 'OVERRIDE_POSSIBLE') return [];
  return (plan.warnings || []).map(w => h('p.plan-note', warningText(w)));
}

function alternatives(ctx, plan) {
  const alts = (plan.alternatives || []).slice(0, 3);
  if (!alts.length || !ctx.payload) return h('p.plan-note', 'No free time nearby.');
  return h('div.choices-wrap', h('p.plan-label', 'Free instead'), h('ul.choices', alts.map(a => h('li', h('button.choice', {
    type: 'button', onclick: e => submit(ctx, ctx.kind === 'force_commitment' ? 'create_commitment' : ctx.kind, moved(ctx, a), e.currentTarget),
  }, h('span.choice-main.tnum', whenText(a.start, a.end)),
    a.reasons && a.reasons.length ? h('span.choice-why', a.reasons.join(', ')) : null)))));
}

function moved(ctx, a) {
  let p = withField(ctx.kind, ctx.payload, 'start', wall(a.start));
  p = withField(ctx.kind, p, 'end', wall(a.end));
  delete p.displace;
  if (p.changes) delete p.changes.duration_min; else delete p.duration_min;
  return p;
}

function priorityAction(ctx, plan) {
  if (!ctx.payload) return null;
  const current = getField(ctx.kind, ctx.payload, 'authority_level') ?? plan.candidate?.authority_level ?? 3;
  const sel = levelSelect(current);
  const again = h('button.btn', {
    type: 'button', onclick: e => submit(ctx, ctx.kind, withField(ctx.kind, ctx.payload, 'authority_level', Number(sel.value)), e.currentTarget),
  }, 'Check again');
  const box = h('div.inline-row', { hidden: true }, field('Priority', sel, { hint: 'Higher priority can move lower ones.' }), again);
  const btn = h('button.link', {
    type: 'button', 'aria-expanded': 'false',
    onclick: () => { box.hidden = !box.hidden; btn.setAttribute('aria-expanded', String(!box.hidden)); if (!box.hidden) sel.focus(); },
  }, 'Change priority');
  return [btn, box];
}

function overrideAction(ctx) {
  if (!TOP_LEVEL.has(ctx.kind) || !ctx.payload) return null;
  return h('button.link.danger', {
    type: 'button',
    onclick: e => { const p = structuredClone(ctx.payload); delete p.displace; submit(ctx, 'force_commitment', p, e.currentTarget); },
  }, 'Override anyway');
}

function equalChoices(ctx, plan, eq) {
  const existing = names(eq);
  const keepNew = (plan.options || []).find(o => o.id === 'keep_new');
  const alt = (plan.alternatives || [])[0];
  const pr = priorityAction(ctx, plan);
  const items = [
    h('button.choice', { type: 'button', onclick: () => { ctx.dismiss(); toast('Kept as it was. Nothing changed.'); } }, `Keep ${existing} as it is?`),
    keepNew && ctx.payload ? h('button.choice', {
      type: 'button', onclick: e => submit(ctx, ctx.kind, { ...structuredClone(ctx.payload), displace: keepNew.displace }, e.currentTarget),
    }, `Keep this one and reschedule ${existing}?`) : null,
    alt && ctx.payload ? h('button.choice', {
      type: 'button', onclick: e => submit(ctx, ctx.kind, moved(ctx, alt), e.currentTarget),
    }, h('span.choice-main', 'Move this to ', h('span.tnum', whenText(alt.start, alt.end).replace(/^(Today|Tomorrow)/, x => x.toLowerCase())), '?')) : null,
  ].filter(Boolean);
  return [h('ul.choices', items.map(b => h('li', b))), pr ? h('div.plan-links', pr) : null];
}

// Confirm / cancel a pending action. Used by plans, the attention sheet and chat cards.
export function confirmStep(ctx, p) {
  const typed = p.confirmation === 'typed';
  const inputId = nextId('phrase');
  const input = typed ? h('input', { id: inputId, type: 'text', autocomplete: 'off', spellcheck: 'false' }) : null;
  const err = h('p.form-error', { role: 'alert', hidden: true });
  const ok = h('button.btn', { type: 'submit', class: p.danger ? 'danger' : 'primary' }, 'Confirm');
  const cancel = h('button.btn', {
    type: 'button',
    onclick: async () => {
      try { await post(`/api/pending/${encodeURIComponent(p.token)}/reject`); } catch { /* already resolved */ }
      ctx.dismiss && ctx.dismiss();
      ctx.onResolved && ctx.onResolved('rejected');
      toast('Nothing changed.');
    },
  }, 'Cancel');
  const form = h('form.confirm',
    h('p.plan-sentence', p.question),
    typed ? h('div.phrase',
      h('label', { for: inputId }, 'Type this phrase to confirm'),
      h('p.phrase-box', p.typed_phrase), input,
      h('p.field-error', { id: inputId + '-err', hidden: true })) : null,
    err,
    h('div.actions', cancel, ok));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    err.hidden = true;
    if (input) setFieldError(input, null);
    await busy(ok, async () => {
      try {
        const r = await post(`/api/pending/${encodeURIComponent(p.token)}/confirm`, typed ? { via: 'typed', text: input.value } : { via: 'button' });
        if (r.status === 'executed') { finish(r, ctx); ctx.onResolved && ctx.onResolved('executed'); }
        else if (r.status === 'superseded') {
          ctx.onResolved && ctx.onResolved('superseded');
          if (ctx.host) render(ctx, r); else { err.textContent = 'The schedule changed, so nothing was done.'; err.hidden = false; }
        } else { ctx.dismiss && ctx.dismiss(); ctx.onResolved && ctx.onResolved('rejected'); toast('Nothing changed.'); }
      } catch (ex) {
        if (input && ex.code === 'TYPED_CONFIRMATION_REQUIRED') setFieldError(input, 'That does not match the phrase.');
        else { err.textContent = errorText(ex); err.hidden = false; }
      }
    });
  });
  return form;
}

// A pending action from the server list (attention line, chat). Question comes from the summary.
export function pendingBlock(p, { onResolved = null, dismiss = null } = {}) {
  const host = h('div.pending');
  const resolved = st => {
    if (st === 'executed') clear(host, h('p.pending-done', 'Done.'));
    else if (st === 'rejected') clear(host, h('p.pending-done', 'Nothing changed.'));
    onResolved && onResolved(st);
  };
  const ctx = { kind: p.kind, payload: null, host, onResolved: resolved, dismiss: dismiss || (() => {}), close: null };
  host.append(confirmStep(ctx, {
    token: p.token, confirmation: p.confirmation, typed_phrase: p.typed_phrase,
    question: pendingQuestion(p), danger: p.kind === 'force_commitment',
  }));
  return host;
}

// Read-only explanation, e.g. a public request whose time is no longer free.
export function explainTaken(plan) {
  const list = (plan && plan.conflicts) || [];
  return list.length ? `${names(list)} ${isAre(list.length)} booked then. Decline the request, or free the time first.`
    : 'That time is no longer free. Decline the request, or free the time first.';
}

export async function check(kind, payload) {
  return post(`/api/check/${kind}`, payload);
}
