// Settings: one section at a time, each with its own Save (enabled once something changes).
import {
  h, clear, get, post, put, del, sheet, field, select, weekdayChips, readChips, busy, trackDirty,
  toast, errorText, formError, setFieldError, clearErrors, confirmDialog, menu, fmtBytes, emptyLine,
  loadingLine, changed, nextId, WEEKDAYS,
} from './core.js';
import { icon } from './icons.js';
import { qrMatrix, qrSvg, qrSvgBlob, qrPngBlob, saveBlob } from './qr.js';

const SECTIONS = [
  ['schedule', 'Schedule'], ['availability', 'Availability'], ['reminders', 'Reminders'],
  ['booking', 'Booking page'], ['email', 'Email'], ['assistant', 'Assistant'], ['advanced', 'Advanced'],
];
const RENDER = {
  schedule: renderSchedule, availability: renderAvailability, reminders: renderReminders,
  booking: renderBooking, email: renderEmail, assistant: renderAssistant, advanced: renderAdvanced,
};

let els = null;

export async function renderSettings(root, arg) {
  const current = SECTIONS.some(s => s[0] === arg) ? arg : 'schedule';
  const links = h('ul', SECTIONS.map(([id, label]) => h('li', h('a', { href: `#/settings/${id}`, dataset: { id } }, label))));
  const picker = select('section', SECTIONS, current, { id: 'settings-section' });
  picker.addEventListener('change', () => { location.hash = `#/settings/${picker.value}`; });
  const panel = h('section.settings-panel', { 'aria-live': 'off' });
  els = { links, picker, panel };
  clear(root,
    h('div.page-head', h('h1', { tabindex: '-1' }, 'Settings')),
    h('div.settings',
      h('nav.settings-nav', { 'aria-label': 'Settings sections' }, links),
      h('div.settings-picker', h('label', { for: 'settings-section' }, 'Section'), picker),
      panel));
  await showSection(current);
}

// Section changes re-render only the panel.
export async function updateSettings(arg) {
  if (!els || !els.panel.isConnected) return false;
  await showSection(SECTIONS.some(s => s[0] === arg) ? arg : 'schedule', true);
  return true;
}

async function showSection(id, focus = false) {
  for (const a of els.links.querySelectorAll('a')) {
    if (a.dataset.id === id) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
  els.picker.value = id;
  const label = SECTIONS.find(s => s[0] === id)[1];
  document.title = `${label} · Settings · Time Assistant`;
  clear(els.panel, loadingLine());
  try {
    const content = await RENDER[id]();
    clear(els.panel, h('h2.section-title', { tabindex: '-1' }, label), content);
  } catch (e) {
    clear(els.panel, h('h2.section-title', label), h('p.form-error', { role: 'alert' }, errorText(e)));
  }
  if (focus && !els.picker.matches(':focus')) els.panel.querySelector('h2')?.focus({ preventScroll: true });
}

// ---------------------------------------------------------------- helpers
function saveForm(build, onSave) {
  const save = h('button.btn.primary', { type: 'submit' }, 'Save');
  const form = h('form.stack.settings-form', { novalidate: true });
  build(form);
  form.append(h('div.actions.start', save));
  const dirty = trackDirty(form, save);
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearErrors(form);
    await busy(save, async () => {
      try {
        const ok = await onSave(form);
        if (ok === false) return;
        dirty.reset();
        toast('Saved.', 'success');
      } catch (err) { formError(form, errorText(err)); }
    });
    save.disabled = form.dataset.dirty !== '1';
  });
  form.markDirty = dirty.mark;
  return form;
}

function numList(v) { return v.split(/[,\s]+/).filter(Boolean).map(Number); }
function input(name, value, attrs = {}) { return h('input', { name, value: value ?? '', ...attrs }); }
const num = (name, value, attrs = {}) => input(name, value, { type: 'number', class: 'tnum', ...attrs });
const time = (name, value) => input(name, value, { type: 'time', class: 'tnum' });

async function allSettings() { return get('/api/settings'); }

// ---------------------------------------------------------------- schedule
async function renderSchedule() {
  const s = await allSettings();
  const zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  const listId = nextId('tz');
  const tz = input('timezone', s.timezone, { list: listId, autocomplete: 'off', spellcheck: 'false' });
  const start = time('day_start', s.search.day_start);
  const end = time('day_end', s.search.day_end);
  const stepMin = num('step_min', s.search.step_min, { min: 5, max: 120, step: 5 });
  return saveForm(form => form.append(
    field('Timezone', tz, { hint: 'For example Asia/Kolkata' }), h('datalist', { id: listId }, zones.map(z => h('option', { value: z }))),
    h('div.grid3', field('Day starts', start), field('Day ends', end), field('Slot step (minutes)', stepMin)),
  ), async () => {
    if (end.value <= start.value) { setFieldError(end, 'Must be after the start.'); end.focus(); return false; }
    if (tz.value.trim() !== s.timezone) await put('/api/settings/timezone', tz.value.trim());
    await put('/api/settings/search', { ...s.search, day_start: start.value, day_end: end.value, step_min: Number(stepMin.value) });
    s.timezone = tz.value.trim();
    s.search = { ...s.search, day_start: start.value, day_end: end.value, step_min: Number(stepMin.value) };
    changed('settings');
    return true;
  });
}

// ---------------------------------------------------------------- availability rules
const KIND = { block: 'Blocked', avoid: 'Avoid', prefer: 'Preferred' };

function daysText(wd) {
  const s = [...wd].sort().join(',');
  if (!wd.length || wd.length === 7) return 'Every day';
  if (s === '0,1,2,3,4') return 'Weekdays';
  if (s === '5,6') return 'Weekends';
  return wd.slice().sort().map(d => WEEKDAYS[d]).join(', ');
}

async function renderAvailability() {
  const rules = await get('/api/rules');
  const wrap = h('div.stack');
  const draw = list => clear(wrap,
    list.length ? h('ul.rows', list.map(r => h('li.simple-row', { dataset: { key: 'rule:' + r.id } },
      h('div.grow',
        h('p.row-strong', r.label || KIND[r.kind]),
        h('p.faint.tnum', `${KIND[r.kind]} · ${daysText(r.weekdays)} · ${r.start}–${r.end}`)),
      h('button.btn.btn-quiet', { type: 'button', onclick: () => ruleSheet(r, reload) }, 'Edit'),
      menu(`More for ${r.label || KIND[r.kind]}`, [{
        label: 'Delete', danger: true,
        onSelect: async () => {
          if (!await confirmDialog('Delete this rule?', { okText: 'Delete', danger: true, title: 'Delete rule' })) return;
          try { await del(`/api/rules/${r.id}`); changed('rules'); reload(); } catch (e) { toast(errorText(e), 'error', 8000); }
        },
      }])))) : emptyLine('No rules yet.'),
    h('div.actions.start', h('button.btn.primary', { type: 'button', onclick: () => ruleSheet(null, reload) }, 'New rule')));
  const reload = async () => { draw(await get('/api/rules')); };
  draw(rules);
  return wrap;
}

function ruleSheet(r, reload) {
  const kindName = nextId('kind');
  const kinds = [['block', 'Blocked', 'Nothing is scheduled here.'], ['avoid', 'Avoid', 'Used only when nothing else fits.'], ['prefer', 'Preferred', 'Suggested first.']];
  const kindSet = h('fieldset.radios', h('legend', 'Kind'), kinds.map(([v, t, hint]) => h('label.radio',
    h('input', { type: 'radio', name: kindName, value: v, checked: (r ? r.kind : 'block') === v }),
    h('span', h('span.row-strong', t), h('span.faint', ` ${hint}`)))));
  const from = time('start', r ? r.start : '19:00');
  const to = time('end', r ? (r.end === '24:00' ? '23:59' : r.end) : '20:00');
  const days = weekdayChips('Days', 'rule_wd', r ? r.weekdays : [], { hint: 'None picked means every day.' });
  const applies = select('applies_to', [['', 'Everything'], ['meeting', 'Meetings'], ['task_block', 'Task blocks'], ['personal', 'Personal']], r ? r.applies_to || '' : '');
  const label = input('label', r ? r.label : '');
  const weight = num('weight', r ? r.weight : 1, { min: 1, max: 10 });
  const weightField = field('Strength (1 to 10)', weight);
  const save = h('button.btn.primary', { type: 'submit' }, 'Save');
  const form = h('form.stack', { novalidate: true },
    kindSet, h('div.grid2', field('From', from), field('To', to)), days,
    h('div.grid2', field('Applies to', applies), field('Label', label)), weightField,
    h('div.actions', h('button.btn', { type: 'button', onclick: () => s.close() }, 'Cancel'), save));
  const kindVal = () => form.querySelector(`input[name="${kindName}"]:checked`).value;
  const sync = () => { weightField.hidden = kindVal() === 'block'; };
  kindSet.addEventListener('change', sync);
  sync();
  form.addEventListener('submit', async e => {
    e.preventDefault();
    clearErrors(form);
    if (!from.value || !to.value || to.value <= from.value) { setFieldError(to, 'Must be after the start.'); to.focus(); return; }
    const body = {
      kind: kindVal(), start: from.value, end: to.value === '23:59' ? '24:00' : to.value,
      weekdays: readChips(form, 'rule_wd').map(Number), applies_to: applies.value || null,
      label: label.value.trim(), weight: Number(weight.value) || 1,
    };
    await busy(save, async () => {
      try {
        if (r) await put(`/api/rules/${r.id}`, body); else await post('/api/rules', body);
        s.close(); toast('Saved.', 'success'); changed('rules'); reload();
      } catch (err) { formError(form, errorText(err)); }
    });
  });
  const s = sheet({ title: r ? 'Edit rule' : 'New rule', content: form });
  form.querySelector('input:checked')?.focus();
}

// ---------------------------------------------------------------- reminders
async function renderReminders() {
  const { reminders: v } = await allSettings();
  const offsets = input('offsets', v.default_offsets_min.join(', '), { inputmode: 'numeric' });
  const ntfy = input('ntfy_url', v.ntfy_url || '', { type: 'url', spellcheck: 'false' });
  return saveForm(form => form.append(
    field('Default reminders (minutes before)', offsets, { hint: 'Separate with commas, for example 15, 60' }),
    field('ntfy URL', ntfy, { hint: 'Optional. Sends reminders to your phone.' }),
  ), async () => {
    const list = numList(offsets.value);
    if (list.some(n => !Number.isFinite(n) || n < 0)) { setFieldError(offsets, 'Use whole minutes, separated by commas.'); offsets.focus(); return false; }
    await put('/api/settings/reminders', { ...v, default_offsets_min: list, ntfy_url: ntfy.value.trim() });
    return true;
  });
}

// ---------------------------------------------------------------- booking page
async function renderBooking() {
  const { public: v } = await allSettings();
  const f = {
    enabled: h('input', { type: 'checkbox', name: 'enabled', checked: !!v.enabled }),
    owner_name: input('owner_name', v.owner_name),
    headline: input('headline', v.headline),
    intro: h('textarea', { name: 'intro', rows: 2 }, v.intro || ''),
    durations_min: input('durations_min', (v.durations_min || []).join(', '), { inputmode: 'numeric' }),
    days_ahead: num('days_ahead', v.days_ahead, { min: 1 }),
    min_notice_hours: num('min_notice_hours', v.min_notice_hours, { min: 0 }),
    day_start: time('day_start', v.day_start),
    day_end: time('day_end', v.day_end),
    step_min: num('step_min', v.step_min, { min: 5, step: 5 }),
    busy_label: input('busy_label', v.busy_label),
    max_open_per_email: num('max_open_per_email', v.max_open_per_email, { min: 1 }),
    max_requests_per_ip_per_day: num('max_requests_per_ip_per_day', v.max_requests_per_ip_per_day, { min: 1 }),
    public_base_url: input('public_base_url', v.public_base_url, { type: 'url', spellcheck: 'false' }),
  };
  const enabledId = nextId('en');
  f.enabled.id = enabledId;
  const share = shareCard(v);
  return h('div.stack',
    share.el,
    saveForm(form => form.append(
      h('label.check', { for: enabledId }, f.enabled, h('span', 'Accept booking requests')),
      h('div.grid2', field('Your name', f.owner_name), field('Headline', f.headline)),
      field('Intro', f.intro),
      h('div.grid3', field('Lengths offered (minutes)', f.durations_min, { hint: 'For example 15, 30, 60' }), field('Days ahead', f.days_ahead), field('Notice (hours)', f.min_notice_hours)),
      h('div.grid3', field('Bookable from', f.day_start), field('Bookable until', f.day_end), field('Slot step (minutes)', f.step_min)),
      weekdayChips('Bookable days', 'pub_wd', v.weekdays || []),
      h('div.grid3', field('Busy label', f.busy_label, { hint: 'What visitors see for busy time' }), field('Open requests per email', f.max_open_per_email), field('Requests per visitor per day', f.max_requests_per_ip_per_day)),
      field('Public address', f.public_base_url, { hint: 'Used in email links, for example https://pi.example.ts.net' }),
    ), async form => {
      const durations = numList(f.durations_min.value);
      if (!durations.length || durations.some(n => !Number.isFinite(n) || n < 5 || n > 480)) { setFieldError(f.durations_min, 'Use minutes between 5 and 480, separated by commas.'); f.durations_min.focus(); return false; }
      if (f.day_end.value <= f.day_start.value) { setFieldError(f.day_end, 'Must be after the start.'); f.day_end.focus(); return false; }
      const out = {
        ...v, enabled: f.enabled.checked, owner_name: f.owner_name.value.trim(), headline: f.headline.value.trim(),
        intro: f.intro.value.trim(), durations_min: durations, days_ahead: Number(f.days_ahead.value),
        min_notice_hours: Number(f.min_notice_hours.value), day_start: f.day_start.value, day_end: f.day_end.value,
        step_min: Number(f.step_min.value), busy_label: f.busy_label.value.trim(),
        weekdays: readChips(form, 'pub_wd').map(Number),
        max_open_per_email: Number(f.max_open_per_email.value), max_requests_per_ip_per_day: Number(f.max_requests_per_ip_per_day.value),
        public_base_url: f.public_base_url.value.trim(),
      };
      await put('/api/settings/public', out);
      Object.assign(v, out);
      share.update(v);
      return true;
    }));
}

// The public booking link: "<public_base_url>/book", or null when it cannot be shared yet.
function bookingLink(v) {
  const base = (v.public_base_url || '').trim().replace(/\/+$/, '');
  if (!base) return null;
  try {
    const u = new URL(base + '/book');
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : null;
  } catch { return null; }
}

// "Share" card: link, copy, QR (SVG on screen, PNG/SVG downloads) and open. update(v) re-renders it.
function shareCard(v) {
  const el = h('section.share', { 'aria-labelledby': 'share-title' });
  function update(s) {
    const link = s.enabled ? bookingLink(s) : null;
    const head = h('h3.sub#share-title', 'Share');
    if (!link) {
      const why = !s.enabled && !(s.public_base_url || '').trim()
        ? 'Turn on booking requests and add your public address below to get a QR code.'
        : !s.enabled ? 'Turn on booking requests below to get a QR code.'
          : (s.public_base_url || '').trim() ? 'Your public address is not a valid link.'
            : 'Add your public address below to get a QR code.';
      clear(el, head, h('p.hint', why),
        h('p', h('a.ext', { href: '/book', target: '_blank', rel: 'noopener' }, 'Preview the booking page', icon('external', { size: 16 }), h('span.vh', ' (opens in a new tab)'))));
      return;
    }
    const m = qrMatrix(link);
    const text = h('input.share-link', { type: 'text', readonly: true, value: link, spellcheck: 'false', 'aria-label': 'Booking link' });
    text.addEventListener('focus', () => text.select());
    const copy = async () => {
      try {
        await navigator.clipboard.writeText(link);
        toast('Link copied.', 'success');
      } catch {
        text.focus();
        text.select();
        let ok = false;
        try { ok = document.execCommand('copy'); } catch { /* not supported */ }
        toast(ok ? 'Link copied.' : 'Link selected. Press Ctrl+C to copy.', ok ? 'success' : 'info');
      }
    };
    const png = async btn => busy(btn, async () => {
      try { saveBlob(await qrPngBlob(m, 1024), 'booking-qr.png'); } catch (e) { toast(errorText(e), 'error'); }
    });
    const pngBtn = h('button.btn', { type: 'button', onclick: () => png(pngBtn) }, icon('download', { size: 16 }), 'Download PNG');
    clear(el, head,
      h('div.share-body',
        h('div.share-qr', qrSvg(m, { label: `QR code for ${link}` })),
        h('div.share-side',
          text,
          h('div.actions.start',
            h('button.btn.primary', { type: 'button', onclick: copy }, icon('copy', { size: 16 }), 'Copy'),
            h('a.btn', { href: link, target: '_blank', rel: 'noopener' }, icon('external', { size: 16 }), 'Open', h('span.vh', ' (opens in a new tab)'))),
          h('div.actions.start',
            pngBtn,
            h('button.btn', { type: 'button', onclick: () => saveBlob(qrSvgBlob(m), 'booking-qr.svg') }, icon('download', { size: 16 }), 'Download SVG')))));
  }
  update(v);
  return { el, update };
}

// ---------------------------------------------------------------- email
async function renderEmail() {
  const { email: v } = await allSettings();
  const enabledId = nextId('en');
  const f = {
    enabled: h('input', { type: 'checkbox', id: enabledId, checked: !!v.enabled }),
    smtp_host: input('smtp_host', v.smtp_host, { spellcheck: 'false' }),
    smtp_port: num('smtp_port', v.smtp_port, { min: 1, max: 65535 }),
    username: input('username', v.username, { autocomplete: 'off', spellcheck: 'false' }),
    password_env: input('password_env', v.password_env, { spellcheck: 'false' }),
    from_name: input('from_name', v.from_name),
    reply_to: input('reply_to', v.reply_to, { type: 'email' }),
  };
  return saveForm(form => form.append(
    h('label.check', { for: enabledId }, f.enabled, h('span', 'Send email about requests')),
    h('div.grid2', field('SMTP host', f.smtp_host), field('SMTP port', f.smtp_port)),
    h('div.grid2', field('Username', f.username), field('Password variable', f.password_env, { hint: 'The password is read from this server environment variable.' })),
    h('div.grid2', field('From name', f.from_name), field('Reply-to', f.reply_to)),
  ), async () => {
    const out = { ...v, enabled: f.enabled.checked };
    for (const k of ['smtp_host', 'username', 'password_env', 'from_name', 'reply_to']) out[k] = f[k].value.trim();
    out.smtp_port = Number(f.smtp_port.value);
    await put('/api/settings/email', out);
    Object.assign(v, out);
    return true;
  });
}

// ---------------------------------------------------------------- assistant
async function renderAssistant() {
  const { ai } = await allSettings();
  let seq = 0;
  const providers = ai.providers.map(p => ({ ...p, _k: ++seq }));
  const enabledId = nextId('en');
  const enabled = h('input', { type: 'checkbox', id: enabledId, checked: !!ai.enabled });
  const privName = nextId('priv');
  const privacy = h('fieldset.radios', h('legend', 'Privacy'),
    [['minimal', 'Minimal', 'Only what it looks up, without locations or private notes.'], ['full', 'Full', 'Also the next 7 days and your people.']]
      .map(([v, t, hint]) => h('label.radio', h('input', { type: 'radio', name: privName, value: v, checked: ai.privacy_mode === v }),
        h('span', h('span.row-strong', t), h('span.faint', ` ${hint}`)))));
  const list = h('ol.providers');
  let form;
  const current = () => providers.map(({ _k, ...p }) => p);
  const KIND_TEXT = {
    model: 'This model is not available. Use Load models and pick another.',
    auth: 'The API key was refused. Check it on the Pi, then restart the app.',
    rate: 'Too many requests right now. Try again in a minute.',
    blocked: "The provider's firewall blocked the request. Update the app, or try again later.",
    unreachable: "Could not connect. Check the base URL and the Pi's internet.",
    config: null,
    bad_response: 'The provider answered with an error.',
  };
  const draw = focusKey => {
    clear(list, providers.map((p, i) => {
      const nm = `${p.name || 'Provider'}`;
      const upd = k => e => { p[k] = e.target.type === 'checkbox' ? e.target.checked : e.target.value; };
      const pe = h('input', { type: 'checkbox', checked: p.enabled, id: nextId('pe'), onchange: upd('enabled') });
      return h('li.provider',
        h('div.provider-row',
          h('span.provider-name', h('span.row-strong', nm), p.model ? h('span.faint', ` ${p.model}`) : null),
          h('label.check.compact', { for: pe.id }, pe, h('span', 'On', h('span.vh', ` (${nm})`))),
          h('button.icon-btn', { type: 'button', 'aria-label': `Move ${nm} up`, disabled: i === 0, dataset: { key: `up:${p._k}` }, onclick: () => move(i, -1, `up:${p._k}`) }, icon('arrow-up')),
          h('button.icon-btn', { type: 'button', 'aria-label': `Move ${nm} down`, disabled: i === providers.length - 1, dataset: { key: `down:${p._k}` }, onclick: () => move(i, 1, `down:${p._k}`) }, icon('arrow-down'))),
        h('details.provider-more',
          h('summary', 'Details'),
          h('div.stack',
            h('div.grid2',
              field('Name', h('input', { value: p.name, oninput: upd('name') })),
              modelField(p, upd)),
            field('Base URL', h('input', { value: p.base_url, type: 'url', spellcheck: 'false', oninput: upd('base_url') })),
            field('API key variable', h('input', { value: p.api_key_env || '', spellcheck: 'false', oninput: upd('api_key_env') }), { hint: 'Leave empty for a local model.' }),
            h('div.actions.start', h('button.link.danger', { type: 'button', onclick: () => { providers.splice(i, 1); form.markDirty(); draw(); } }, `Remove ${nm}`)))));
    }));
    if (focusKey) {
      const el = list.querySelector(`[data-key="${focusKey}"]`);
      if (el && !el.disabled) el.focus(); else list.querySelector(`[data-key^="${focusKey.split(':')[0] === 'up' ? 'down' : 'up'}:${focusKey.split(':')[1]}"]`)?.focus();
    }
  };
  // Model input with suggestions fetched from the provider (unsaved form values are used).
  function modelField(p, upd) {
    const dl = h('datalist', { id: nextId('models') });
    const note = h('p.hint', { 'aria-live': 'polite' });
    const input = h('input', { value: p.model, spellcheck: 'false', list: dl.id, oninput: upd('model') });
    const load = h('button.btn.btn-quiet.small', {
      type: 'button',
      onclick: e => busy(e.currentTarget, async () => {
        note.textContent = 'Loading';
        try {
          const r = await post('/api/ai/models', { name: p.name, providers: current() });
          clear(dl, r.models.map(m => h('option', { value: m })));
          note.textContent = r.models.length
            ? `${r.models.length} models available. Click the Model field to pick one.`
            : 'The provider returned no models.';
          input.focus();
        } catch (err) { note.textContent = errorText(err); }
      }),
    }, 'Load models');
    return h('div.stack', field('Model', input), dl, h('div.actions.start', load), note);
  }
  const results = h('ul.test-results', { 'aria-live': 'polite' });
  const testBtn = h('button.btn.btn-quiet', {
    type: 'button',
    onclick: e => busy(e.currentTarget, async () => {
      clear(results, h('li.faint', 'Testing'));
      try {
        const r = await post('/api/ai/test', { providers: current() });
        clear(results, r.results.map(x => h('li',
          h('span.row-strong', x.name), ' ',
          x.skipped ? h('span.faint', 'off')
            : x.ok ? h('span.ok-ink', `working, ${x.ms} ms`)
              : h('span.danger-ink', KIND_TEXT[x.kind] || x.error))));
      } catch (err) { clear(results, h('li.danger-ink', errorText(err))); }
    }),
  }, 'Test');
  const move = (i, d, key) => { [providers[i + d], providers[i]] = [providers[i], providers[i + d]]; form.markDirty(); draw(key); };
  draw();
  form = saveForm(fm => fm.append(
    h('label.check', { for: enabledId }, enabled, h('span', 'Use the assistant')),
    privacy,
    h('h3.sub', 'Providers, tried in order'),
    list,
    h('div.actions.start', testBtn, h('button.btn.btn-quiet', {
      type: 'button',
      onclick: () => { providers.push({ name: 'provider', enabled: true, base_url: '', model: '', api_key_env: '', _k: ++seq }); form.markDirty(); draw(); list.lastElementChild?.querySelector('details')?.setAttribute('open', ''); },
    }, 'Add provider')),
    results,
    h('p.hint', 'API keys live in the server environment, never in the app. Test uses the values above, even before you save.'),
  ), async fm => {
    const out = {
      ...ai, enabled: enabled.checked,
      privacy_mode: fm.querySelector(`input[name="${privName}"]:checked`).value,
      providers: providers.map(({ _k, ...p }) => p),
    };
    await put('/api/settings/ai', out);
    return true;
  });
  return form;
}

// ---------------------------------------------------------------- advanced
function uptime(s) {
  const d = Math.floor(s / 86400), hr = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}d ${hr}h` : hr ? `${hr}h ${m}m` : `${m}m`;
}

async function renderAdvanced() {
  const [s, outbox] = await Promise.all([get('/api/system'), get('/api/outbox').catch(() => null)]);
  const b = s.backup;
  const backupsHost = h('div');
  const runBtn = h('button.btn', {
    type: 'button',
    onclick: e => busy(e.currentTarget, async () => {
      try { const r = await post('/api/backup/run'); toast(`Backed up (${fmtBytes(r.size)}).`, 'success'); }
      catch (err) { toast(errorText(err), 'error', 8000); }
    }),
  }, 'Back up now');
  clear(backupsHost,
    h('p', b.last ? `Last backup ${String(b.last.at).replace('T', ' ').slice(0, 16)}` : 'No backups yet.'),
    b.last_error ? h('p.form-error', b.last_error) : null,
    b.files.length ? h('ul.mini-list.tnum', b.files.slice(0, 5).map(f => h('li.mini-line', h('span', f.file), h('span.faint', fmtBytes(f.size))))) : null,
    h('div.actions.start', runBtn));
  return h('div.stack',
    h('dl.kv.tnum',
      h('dt', 'Version'), h('dd', s.version),
      h('dt', 'Running for'), h('dd', uptime(s.uptime_s)),
      h('dt', 'Timezone'), h('dd', s.timezone),
      h('dt', 'Database'), h('dd', `${fmtBytes(s.db_bytes)}, schema ${s.schema_version}`)),
    h('details.quiet', h('summary', 'Database path'), h('p.mono', s.db_path), h('p.mono.faint', `Backups: ${b.dir}`)),
    h('h3.sub', 'Backups'), backupsHost,
    outbox ? [h('h3.sub', 'Outgoing email'), outbox.length ? h('ul.mini-list', outbox.slice(0, 8).map(m => h('li.mini-line',
      h('span', m.subject, h('span.faint', ` to ${m.to_addr}`)),
      h('span', { class: m.status === 'failed' ? 'warn-ink' : 'faint' }, m.status === 'sent' ? 'Sent' : m.status === 'failed' ? 'Failed' : 'Waiting')))) : h('p.faint', 'No email sent yet.')] : null,
    h('h3.sub', 'Keyboard'),
    h('p.keys', h('kbd', 'n'), ' new, ', h('kbd', 't'), ' today, ', h('kbd', 'd'), ' ', h('kbd', 'w'), ' ', h('kbd', 'm'), ' views, ',
      h('kbd', 'Left'), ' ', h('kbd', 'Right'), ' previous and next'));
}

