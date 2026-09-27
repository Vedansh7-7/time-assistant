// Booking requests from the public page: confirm, waitlist or decline.
import {
  h, clear, get, post, sheet, segmented, field, busy, toast, errorText, fmtDuration, whenText, rerender,
  emptyLine, changed, nextId,
} from './core.js';
import { explainTaken } from './plan.js';

let filter = 'open';
let els = null;

export async function renderRequests(root) {
  const list = h('div.req-region');
  const seg = segmented('Show', [['open', 'Open'], ['all', 'All']], filter, v => { filter = v; refreshRequests(); }, { cls: 'seg-sm' });
  els = { list };
  clear(root,
    h('div.page-head', h('h1', { tabindex: '-1' }, 'Requests'), seg),
    list);
  await refreshRequests();
}

export async function refreshRequests() {
  if (!els || !els.list.isConnected) return;
  const q = filter === 'open' ? '?status=requested&status=waitlisted' : '';
  let rows;
  try { rows = await get('/api/requests' + q); }
  catch (e) { clear(els.list, h('p.form-error', { role: 'alert' }, errorText(e))); return; }
  rerender(els.list, () => clear(els.list, rows.length
    ? h('ul.rows', rows.map(item))
    : emptyLine(filter === 'open' ? 'No open requests.' : 'No requests yet.')));
}

function availability(r) {
  if (r.status === 'waitlisted') return ['Waitlisted, pencilled in', 'faint'];
  if (r.status !== 'requested') return [{ confirmed: 'Confirmed', declined: 'Declined', withdrawn: 'Withdrawn', expired: 'Expired' }[r.status] || r.status, 'faint'];
  if (!r.check) return ['', ''];
  if (r.check.outcome === 'FREE') return [(r.check.warnings || []).length ? 'Free, inside a blocked window' : 'Free then', 'ok-ink'];
  const c = (r.check.conflicts || [])[0];
  return [c ? `Clashes with ${c.title}` : 'Taken', 'warn-ink'];
}

function item(r) {
  const open = r.status === 'requested' || r.status === 'waitlisted';
  const [phrase, tone] = availability(r);
  return h('li.req', { dataset: { key: 'req:' + r.id } },
    h('div.req-main',
      h('p.req-name', h('span.row-strong', r.name), ' ', h('a.faint', { href: `mailto:${r.email}` }, r.email)),
      h('p.req-motive', r.motive),
      h('p.req-when', h('span.tnum', whenText(r.start, r.end)), h('span.faint', ` · ${fmtDuration(r.duration_min)}`),
        phrase ? [h('span.sep', { 'aria-hidden': 'true' }, ' · '), h('span', { class: tone }, phrase)] : null),
      r.reason ? h('p.faint', `Reason: ${r.reason}${r.share_reason ? ' (shared)' : ''}`) : null),
    open ? h('div.req-actions',
      h('button.btn.primary', { type: 'button', dataset: { key: 'req-confirm:' + r.id }, onclick: e => decide(r, 'confirm', {}, e.currentTarget) }, 'Confirm'),
      r.status !== 'waitlisted' ? h('button.btn', { type: 'button', onclick: e => decide(r, 'waitlist', {}, e.currentTarget) }, 'Waitlist') : null,
      h('button.btn.btn-quiet', { type: 'button', onclick: () => declineSheet(r) }, 'Decline')) : null);
}

async function decide(r, decision, extra, btn) {
  return busy(btn, async () => {
    try {
      const res = await post(`/api/requests/${encodeURIComponent(r.id)}/decide`, { decision, ...extra });
      if (res.status === 'not_possible') {
        sheet({ title: `Can't confirm ${r.name}`, content: h('p.plan-sentence', explainTaken(res.plan)) });
        refreshRequests();
        return false;
      }
      toast(decision === 'confirm' ? `Confirmed ${r.name}, ${whenText(r.start, r.end)}.` : decision === 'waitlist' ? `${r.name} is on the waitlist.` : `Declined ${r.name}.`, 'success');
      changed('requests');
      await refreshRequests();
      return true;
    } catch (e) { toast(errorText(e), 'error', 8000); return false; }
  });
}

function declineSheet(r) {
  const reason = h('textarea', { rows: 3, name: 'reason' });
  const shareId = nextId('share');
  const share = h('input', { type: 'checkbox', id: shareId });
  const ok = h('button.btn.danger', { type: 'submit' }, 'Decline');
  const form = h('form.stack',
    h('p', `Decline ${r.name}'s request for ${whenText(r.start, r.end)}?`),
    field('Reason (optional)', reason),
    h('label.check', { for: shareId }, share, h('span', 'Share the reason with them')),
    h('div.actions', h('button.btn', { type: 'button', onclick: () => s.close() }, 'Cancel'), ok));
  form.addEventListener('submit', async e => {
    e.preventDefault();
    if (await decide(r, 'decline', { reason: reason.value.trim() || null, share_reason: share.checked }, ok)) s.close();
  });
  const s = sheet({ title: 'Decline request', content: form, kind: 'modal' });
  reason.focus();
}
