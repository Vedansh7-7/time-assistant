// People: a list and a detail / edit panel. Profiles are context for the assistant only.
import {
  h, clear, get, post, patch, del, field, busy, trackDirty, whenText, emptyLine, errorText, toast,
  confirmDialog, formError, setFieldError, clearErrors, changed,
} from './core.js';
import { invalidatePeople, openCommitment } from './commitment.js';

const FIELDS = [
  ['aliases', 'Also known as', 'input', 'Nicknames, separated by commas'],
  ['relationship', 'Relationship', 'input', null],
  ['importance', 'Context', 'input', null],
  ['communication_style', 'Communication style', 'textarea', null],
  ['preferences', 'Preferences', 'textarea', null],
  ['custom_instructions', 'Instructions for the assistant', 'textarea', null],
  ['notes', 'Private notes', 'textarea', 'Shared with the assistant only in full privacy mode.'],
];

let els = null;

export async function renderPeople(root, id) {
  if (id && /^\d+$/.test(id)) id = 'P' + id;
  const list = await get('/api/people');
  const selected = id === 'new' ? 'new' : id ? list.find(p => p.id === id) : null;
  const side = h('nav.people-list', { 'aria-label': 'People' },
    list.length ? h('ul', list.map(p => h('li', h('a', {
      href: `#/people/${p.id}`, 'aria-current': selected && selected.id === p.id ? 'page' : null, dataset: { key: 'person:' + p.id },
    }, h('span.row-strong', p.name), p.relationship ? h('span.faint', p.relationship) : null)))) : emptyLine('No people yet.'));
  const detail = h('div.person-detail');
  els = { detail };
  clear(root,
    h('div.page-head', h('h1', { tabindex: '-1' }, 'People'), h('a.btn.primary', { href: '#/people/new' }, 'New person')),
    h('div.split', { class: selected ? 'has-detail' : '' }, side, detail));
  if (selected === 'new') personForm(detail, null);
  else if (selected) await renderPerson(detail, selected.id);
  else if (id) clear(detail, h('p.empty', 'That person was not found.'));
}

async function renderPerson(host, pid) {
  let p;
  try { p = await get(`/api/people/${pid}`); } catch (e) { clear(host, h('p.form-error', { role: 'alert' }, errorText(e))); return; }
  const rows = FIELDS.map(([k, label]) => [label, p[k]]).filter(([, v]) => v);
  const upcoming = p.upcoming.slice(0, 5);
  clear(host,
    h('a.back.link', { href: '#/people' }, 'All people'),
    h('h2.person-name', p.name),
    rows.length ? h('dl.kv', rows.map(([k, v]) => [h('dt', k), h('dd', v)])) : h('p.faint', 'No profile details yet.'),
    h('h3.sub', 'Coming up'),
    upcoming.length ? h('ul.mini-list', upcoming.map(c => h('li', h('button.mini-row', {
      type: 'button', dataset: { key: 'pc:' + c.id }, onclick: () => openCommitment(c.id),
    }, h('span.tnum.faint', whenText(c.start, c.end)), h('span', c.title))))) : h('p.faint', 'Nothing scheduled.'),
    h('div.actions.start', h('button.btn', { type: 'button', onclick: () => personForm(host, p) }, 'Edit')));
}

function personForm(host, p) {
  const name = h('input', { name: 'name', required: true, value: p ? p.name : '', autocomplete: 'off' });
  const inputs = FIELDS.map(([k, , kind]) => kind === 'input'
    ? h('input', { name: k, value: p ? p[k] || '' : '' })
    : h('textarea', { name: k, rows: 2 }, p ? p[k] || '' : ''));
  const save = h('button.btn.primary', { type: 'submit' }, p ? 'Save' : 'Add person');
  const form = h('form.stack', { novalidate: true },
    field('Name', name),
    FIELDS.map(([, label, , hint], i) => field(label, inputs[i], { hint })),
    h('div.actions',
      p ? h('button.link.danger', {
        type: 'button', class: 'push-left',
        onclick: async () => {
          if (!await confirmDialog(`Delete ${p.name}? Their commitments stay, just no longer linked.`, { okText: 'Delete', danger: true, title: 'Delete person' })) return;
          try { await del(`/api/people/${p.id}`); invalidatePeople(); form.dataset.dirty = '0'; changed('people'); location.hash = '#/people'; }
          catch (e) { toast(errorText(e), 'error', 8000); }
        },
      }, 'Delete') : null,
      h('button.btn', { type: 'button', onclick: () => { form.dataset.dirty = '0'; if (p) renderPerson(host, p.id); else location.hash = '#/people'; } }, 'Cancel'),
      save));
  const dirty = trackDirty(form, null);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearErrors(form);
    if (!name.value.trim()) { setFieldError(name, 'Add a name.'); name.focus(); return; }
    const body = { name: name.value.trim() };
    FIELDS.forEach(([k], i) => { body[k] = inputs[i].value.trim() || null; });
    await busy(save, async () => {
      try {
        const saved = p ? await patch(`/api/people/${p.id}`, body) : await post('/api/people', body);
        dirty.reset();
        invalidatePeople();
        toast(`Saved ${saved.name}.`, 'success');
        changed('people');
        if (location.hash !== `#/people/${saved.id}`) location.hash = `#/people/${saved.id}`;
        else renderPerson(host, saved.id);
      } catch (err) { formError(form, errorText(err)); }
    });
  });
  clear(host, h('h2.person-name', p ? `Edit ${p.name}` : 'New person'), form);
  name.focus();
}
