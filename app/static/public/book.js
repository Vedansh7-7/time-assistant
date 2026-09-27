/* Public booking page. Vanilla JS; every piece of API text goes in through textContent. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December"];
  const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const STRIP_DAYS = 14;
  const IDLE_MS = 8000;

  const desk = window.matchMedia("(min-width: 760px)");

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  function localPrefers24h() {
    try { return !/h1[12]/.test(new Intl.DateTimeFormat(undefined, { hour: "numeric" }).resolvedOptions().hourCycle || ""); }
    catch { return true; }
  }
  const saved24 = store.get("bk-24h");

  const S = {
    profile: null, motive: "", duration: null,
    days: new Map(),          // date -> open slot count (only days with openings)
    month: null,              // {y, m} shown in the desktop calendar
    stripPage: 0,             // which two-week page the phone strip shows
    selected: null, dayData: null, dayToken: 0, slot: null,
    h24: saved24 == null ? localPrefers24h() : saved24 === "1",
    showBusy: false,
    step: "day",
  };

  // ------------------------------------------------------------------ api
  async function api(path, opts = {}) {
    const init = { method: opts.method || "GET", headers: {} };
    if (opts.body) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
    let res;
    try { res = await fetch(path, init); } catch {
      throw Object.assign(new Error("Can't reach the server. Check your connection and try again."), { code: "NETWORK" });
    }
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const e = new Error((data && data.message) || `Something went wrong (error ${res.status}). Try again.`);
      e.code = data && data.error; e.status = res.status;
      throw e;
    }
    return data;
  }

  // ------------------------------------------------------------------ mood, idle and art
  let idleTimer = 0;
  function wake() {
    document.body.classList.remove("still");
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => document.body.classList.add("still"), IDLE_MS);
  }
  function setMood(m) { document.body.dataset.mood = m; wake(); }
  ["pointerdown", "keydown", "pointermove", "wheel", "touchstart"].forEach((t) =>
    window.addEventListener(t, wake, { passive: true }));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(idleTimer); document.body.classList.add("still"); } else wake();
  });

  async function loadArt() {
    try {
      const res = await fetch("/book/assets/art.svg");
      if (!res.ok) return;
      const doc = new DOMParser().parseFromString(await res.text(), "image/svg+xml");
      const style = doc.getElementById("art-style");
      if (style) { const s = document.createElement("style"); s.textContent = style.textContent; document.head.append(s); }
      document.querySelectorAll("[data-art]").forEach((slot) => {
        const src = doc.getElementById(slot.dataset.art);
        if (!src) return;
        const node = document.importNode(src, true);
        node.removeAttribute("id");
        node.setAttribute("aria-hidden", "true");
        slot.replaceChildren(node);
      });
    } catch { /* the art is decorative */ }
  }

  // ------------------------------------------------------------------ date and time helpers (owner tz strings)
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = (iso) => iso.slice(11, 16);
  function fmtTime(t) {
    if (S.h24) return t;
    let [h, m] = t.split(":").map(Number);
    const ap = h >= 12 ? "pm" : "am";
    h = h % 12 || 12;
    return `${h}:${pad(m)} ${ap}`;
  }
  const range = (a, b) => `${fmtTime(hhmm(a))}–${fmtTime(hhmm(b))}`;
  function parseDay(s) { const [y, m, d] = s.split("-").map(Number); return { y, m: m - 1, d }; }
  const toUTC = (s) => { const { y, m, d } = parseDay(s); return Date.UTC(y, m, d); };
  const fromUTC = (t) => { const x = new Date(t); return `${x.getUTCFullYear()}-${pad(x.getUTCMonth() + 1)}-${pad(x.getUTCDate())}`; };
  const addDays = (s, n) => fromUTC(toUTC(s) + n * 86400000);
  const dayDiff = (a, b) => Math.round((toUTC(b) - toUTC(a)) / 86400000);
  const weekday = (s) => WEEKDAYS[new Date(toUTC(s)).getUTCDay()];
  function longDate(s) { const { m, d } = parseDay(s); return `${weekday(s)}, ${d} ${MONTHS[m]}`; }
  function durText(n) {
    if (n % 60 === 0) return n === 60 ? "1 hour" : `${n / 60} hours`;
    if (n < 120) return `${n} min`;
    return `${Math.floor(n / 60)} h ${n % 60} min`;
  }
  const isOpen = (d) => (S.days.get(d) || 0) > 0;
  const firstOpen = () => [...S.days.keys()].sort()[0] || null;
  const meetingTitle = () => {
    const p = S.profile;
    return p.owner_name ? `${durText(S.duration)} with ${p.owner_name}` : `${durText(S.duration)} meeting`;
  };

  // ------------------------------------------------------------------ views, focus and announcements
  const VIEWS = ["v-loading", "v-closed", "v-intro", "v-book", "v-done", "v-status"];
  function announce(text) {
    const live = $("announce");
    live.textContent = "";
    setTimeout(() => { live.textContent = text; }, 60);
  }
  function focusHeading(h) {
    if (!h) return;
    window.scrollTo(0, 0);
    h.focus({ preventScroll: true });
  }
  function show(id, mood, focus = true) {
    VIEWS.forEach((v) => { $(v).hidden = v !== id; });
    if (mood) setMood(mood);
    if (focus) {
      const h = $(id).querySelector("h1");
      focusHeading(h);
      if (h) announce(h.textContent.trim());
    } else window.scrollTo(0, 0);
  }
  function setBusy(btn, busy, label) {
    if (!btn) return;
    if (busy) {
      btn.dataset.label = btn.textContent;
      btn.textContent = label;
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
    } else {
      if (btn.dataset.label) btn.textContent = btn.dataset.label;
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
    }
  }
  function fieldError(input, errEl, msg) {
    errEl.textContent = msg;
    if (input) {
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", errEl.id);
      input.focus();
    }
  }
  function clearField(input) {
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
  }

  // ------------------------------------------------------------------ step 1: purpose and duration
  function renderDurations() {
    const box = $("i-durations");
    box.replaceChildren();
    S.profile.durations_min.forEach((d) => {
      const input = el("input", "pill-input");
      input.type = "radio"; input.name = "duration"; input.id = `dur-${d}`; input.value = String(d);
      input.checked = d === S.duration;
      input.addEventListener("change", () => { S.duration = d; });
      const label = el("label", "pill", durText(d));
      label.htmlFor = input.id;
      box.append(input, label);
    });
  }

  function startIntro(focus = true) {
    const p = S.profile;
    $("i-title").textContent = p.headline || "Let's find a time";
    $("i-intro").textContent = p.intro || "";
    $("motive").value = S.motive;
    if (!S.duration) S.duration = p.durations_min.includes(30) ? 30 : p.durations_min[0];
    renderDurations();
    show("v-intro", "idle", focus);
  }

  $("motive").addEventListener("input", () => { clearField($("motive")); $("i-err").textContent = ""; });
  $("f-intro").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const m = $("motive").value.trim();
    if (m.length < 3) { fieldError($("motive"), $("i-err"), "Add a few words about the purpose."); return; }
    $("i-err").textContent = "";
    S.motive = m;
    const btn = $("i-submit");
    setBusy(btn, true, "Finding times");
    setMood("think");
    try {
      await loadDays();
      enterBooker();
    } catch (e) {
      setMood("idle");
      $("i-err").textContent = e.message;
      if (e.status === 503) boot();
    } finally { setBusy(btn, false); }
  });

  async function loadDays() {
    const r = await api("/api/public/suggest", { method: "POST", body: { duration_min: S.duration } });
    S.days = new Map(r.days.filter((d) => d.open_slots > 0).map((d) => [d.date, d.open_slots]));
  }

  // ------------------------------------------------------------------ booker shell
  const STEPS = { day: [2, "Pick a day"], time: [3, "Pick a time"], details: [4, "Your details"] };

  function renderSummary() {
    const p = S.profile;
    $("b-meta").textContent = meetingTitle();
    $("b-purpose").textContent = S.motive;
    $("b-dur").textContent = durText(S.duration);
    $("b-tz").textContent = p.timezone;
    let local = "";
    try { local = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { /* ignore */ }
    const wall = (tz) => { try { return new Date().toLocaleString("en-US", { timeZone: tz }); } catch { return tz; } };
    const differs = local && wall(local) !== wall(p.timezone); // aliases like Calcutta and Kolkata match
    const tzLi = $("b-tz").parentElement;
    const old = tzLi.querySelector(".note");
    if (old) old.remove();
    if (differs) tzLi.append(el("span", "note", `Your device is on ${local}`));
    $("b-tz-foot").textContent = `Times in ${p.timezone}${differs ? `. Your device is on ${local}.` : ""}`;
  }

  function renderHeading() {
    $("b-title").textContent = desk.matches ? meetingTitle() : STEPS[S.step][1];
    const n = STEPS[S.step][0];
    const bar = $("b-progress");
    bar.setAttribute("aria-valuenow", String(n));
    bar.setAttribute("aria-valuetext", `Step ${n} of 4`);
    [...bar.children].forEach((s, i) => s.classList.toggle("on", i < n));
  }

  function go(step, focus = true) {
    S.step = step;
    $("v-book").dataset.step = step;
    if ($("v-book").hidden) show("v-book", null, false);
    renderHeading();
    if (step === "day" && !desk.matches) scrollStrip();
    if (!focus) return;
    if (desk.matches && step === "details") {
      focusHeading($("d-title"));
      announce(`Your details. ${$("d-when").textContent}`);
    } else if (desk.matches) {
      focusHeading($("b-title"));
      announce(`${meetingTitle()}. Pick a day and time.`);
    } else {
      focusHeading($("b-title"));
      announce(`${STEPS[step][1]}, step ${STEPS[step][0]} of 4`);
    }
  }

  function enterBooker() {
    $("b-alert").textContent = "";
    if (S.selected && !isOpen(S.selected)) S.selected = null;
    if (!S.selected && desk.matches) S.selected = firstOpen();
    const anchor = S.selected || firstOpen() || S.profile.first_day;
    const a = parseDay(anchor);
    S.month = { y: a.y, m: a.m };
    S.stripPage = pageOf(anchor);
    renderSummary();
    renderCalendar();
    renderStrip();
    setFormat();
    const none = !S.days.size;
    const msg = "No open times in the coming weeks. Try a shorter duration.";
    $("c-empty").hidden = $("s-empty").hidden = !none;
    $("c-empty").textContent = $("s-empty").textContent = none ? msg : "";
    if (S.selected) loadDay(S.selected); else clearTimes();
    setMood(none ? "sad" : "idle");
    go("day");
  }

  $("b-back").addEventListener("click", () => {
    $("b-alert").textContent = "";
    if (S.step === "details") go(desk.matches ? "day" : "time");
    else if (S.step === "time") go("day");
    else startIntro();
  });
  $("b-edit").addEventListener("click", () => startIntro());
  $("d-back").addEventListener("click", () => { $("b-alert").textContent = ""; go("day"); });

  // ------------------------------------------------------------------ roving arrow keys
  function roving(container, vertical) {
    container.addEventListener("keydown", (e) => {
      const all = [...container.querySelectorAll("button[data-date]")];
      const i = all.indexOf(document.activeElement);
      if (i < 0) return;
      const enabled = (j) => all[j] && !all[j].disabled;
      let j = -1;
      const walk = (from, step) => { let k = from + step; while (k >= 0 && k < all.length && !enabled(k)) k += step; return enabled(k) ? k : -1; };
      switch (e.key) {
        case "ArrowLeft": j = walk(i, -1); break;
        case "ArrowRight": j = walk(i, 1); break;
        case "ArrowUp": if (vertical) j = walk(i, -7); break;
        case "ArrowDown": if (vertical) j = walk(i, 7); break;
        case "Home": j = walk(-1, 1); break;
        case "End": j = walk(all.length, -1); break;
        default: return;
      }
      e.preventDefault();
      if (j < 0) return;
      all.forEach((b) => { b.tabIndex = -1; });
      all[j].tabIndex = 0;
      all[j].focus();
    });
  }
  function setRovingStop(container) {
    const all = [...container.querySelectorAll("button[data-date]")];
    const stop = all.find((b) => b.getAttribute("aria-pressed") === "true") || all.find((b) => !b.disabled);
    all.forEach((b) => { b.tabIndex = b === stop ? 0 : -1; });
  }

  function dayButton(key, cls) {
    const b = el("button", cls);
    b.type = "button";
    b.dataset.date = key;
    const open = isOpen(key);
    b.setAttribute("aria-label", open ? longDate(key) : `${longDate(key)}, no openings`);
    if (open) {
      b.setAttribute("aria-pressed", String(key === S.selected));
      b.addEventListener("click", () => pickDay(key));
    } else b.disabled = true;
    if (key === S.profile.today) b.setAttribute("aria-current", "date");
    return b;
  }

  // ------------------------------------------------------------------ desktop: month calendar
  function renderCalendar() {
    const p = S.profile, { y, m } = S.month;
    $("c-title").textContent = `${MONTHS[m]} ${y}`;
    const grid = $("c-grid");
    grid.replaceChildren();
    const firstDow = (new Date(Date.UTC(y, m, 1)).getUTCDay() + 6) % 7; // Monday first
    const daysIn = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    for (let i = 0; i < firstDow; i++) { const s = el("span", "blank"); s.setAttribute("aria-hidden", "true"); grid.append(s); }
    for (let d = 1; d <= daysIn; d++) {
      const b = dayButton(`${y}-${pad(m + 1)}-${pad(d)}`, "day");
      b.textContent = String(d);
      grid.append(b);
    }
    setRovingStop(grid);
    const f = parseDay(p.first_day), l = parseDay(p.last_day);
    $("c-prev").disabled = y * 12 + m <= f.y * 12 + f.m;
    $("c-next").disabled = y * 12 + m >= l.y * 12 + l.m;
  }
  function moveMonth(delta) {
    let { y, m } = S.month;
    m += delta;
    if (m < 0) { m = 11; y--; } else if (m > 11) { m = 0; y++; }
    S.month = { y, m };
    renderCalendar();
    const btn = delta < 0 ? $("c-prev") : $("c-next");
    if (btn.disabled) (delta < 0 ? $("c-next") : $("c-prev")).focus();
  }
  $("c-prev").addEventListener("click", () => moveMonth(-1));
  $("c-next").addEventListener("click", () => moveMonth(1));
  roving($("c-grid"), true);

  // ------------------------------------------------------------------ phone: two-week strip
  const pageCount = () => Math.max(1, Math.ceil((dayDiff(S.profile.first_day, S.profile.last_day) + 1) / STRIP_DAYS));
  const pageOf = (d) => Math.min(pageCount() - 1, Math.max(0, Math.floor(dayDiff(S.profile.first_day, d) / STRIP_DAYS)));

  function renderStrip() {
    const p = S.profile;
    const strip = $("s-strip");
    strip.replaceChildren();
    const start = addDays(p.first_day, S.stripPage * STRIP_DAYS);
    for (let i = 0; i < STRIP_DAYS; i++) {
      const key = addDays(start, i);
      if (dayDiff(key, p.last_day) < 0) break;
      const { m, d } = parseDay(key);
      const b = dayButton(key, "tile");
      b.append(el("span", "wd", key === p.today ? "Today" : weekday(key).slice(0, 3)),
        el("span", "dn", String(d)), el("span", "mo", MONTHS[m].slice(0, 3)));
      strip.append(b);
    }
    setRovingStop(strip);
    $("s-prev").hidden = S.stripPage === 0;
    $("s-next").hidden = S.stripPage >= pageCount() - 1;
    scrollStrip();
  }
  function scrollStrip() {
    // Always instant: no smooth scrolling, so reduced motion is respected by default.
    const strip = $("s-strip");
    const sel = strip.querySelector('[aria-pressed="true"]');
    strip.scrollLeft = sel ? Math.max(0, sel.offsetLeft - strip.offsetLeft - 16) : 0;
  }
  function movePage(delta) {
    S.stripPage = Math.min(pageCount() - 1, Math.max(0, S.stripPage + delta));
    renderStrip();
    const btn = delta < 0 ? $("s-prev") : $("s-next");
    if (btn.hidden) {
      const target = $("s-strip").querySelector('button[tabindex="0"]') || (delta < 0 ? $("s-next") : $("s-prev"));
      if (target) target.focus();
    }
    announce(`${longDate(addDays(S.profile.first_day, S.stripPage * STRIP_DAYS))} onwards`);
  }
  $("s-prev").addEventListener("click", () => movePage(-1));
  $("s-next").addEventListener("click", () => movePage(1));
  roving($("s-strip"), false);

  // ------------------------------------------------------------------ picking a day
  function pickDay(key) {
    S.selected = key;
    S.slot = null;
    $("b-alert").textContent = "";
    const hadFocus = !!(document.activeElement && document.activeElement.dataset && document.activeElement.dataset.date);
    renderCalendar();
    renderStrip();
    if (desk.matches) {
      loadDay(key, true);
      if (hadFocus) {
        const again = document.querySelector(`#c-grid [data-date="${key}"]`);
        if (again) again.focus();
      }
    } else {
      go("time");
      loadDay(key);
    }
  }

  function clearTimes() {
    S.dayData = null;
    $("t-title").textContent = "Pick a day";
    $("t-err").textContent = "";
    $("t-list").replaceChildren();
    $("t-busy-wrap").hidden = true;
  }

  async function loadDay(key, speak = false) {
    const token = ++S.dayToken;
    $("t-title").textContent = longDate(key);
    $("t-err").textContent = "";
    $("t-list").replaceChildren(el("li", "empty", "Loading times"));
    $("t-busy-wrap").hidden = true;
    setMood("think");
    try {
      const data = await api(`/api/public/day/${key}?duration_min=${S.duration}`);
      if (token !== S.dayToken) return;
      S.dayData = data;
      renderTimes();
      setMood("idle");
      if (speak) announce(`${longDate(key)}: ${data.slots.length} open time${data.slots.length === 1 ? "" : "s"}`);
    } catch (e) {
      if (token !== S.dayToken) return;
      setMood("sad");
      $("t-list").replaceChildren();
      $("t-err").textContent = e.message;
    }
  }

  function renderTimes() {
    const list = $("t-list");
    const data = S.dayData;
    if (!data) return;
    const rows = data.slots.map((s) => ({ at: s.start, slot: s }));
    if (S.showBusy) rows.push(...data.reserved.map((r) => ({ at: r.start, busy: r })));
    rows.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.busy ? -1 : 1));
    list.replaceChildren();
    if (!data.slots.length) list.append(el("li", "empty", "No open times left on this day. Pick another day."));
    rows.forEach((r) => {
      const li = el("li");
      if (r.busy) {
        li.className = "busy";
        li.append(el("span", null, range(r.busy.start, r.busy.end)), el("span", null, r.busy.label || S.profile.busy_label));
      } else {
        const b = el("button", "slot", fmtTime(r.slot.time || hhmm(r.slot.start)));
        b.type = "button";
        b.setAttribute("aria-pressed", String(!!S.slot && S.slot.start === r.slot.start));
        b.setAttribute("aria-label", `${range(r.slot.start, r.slot.end)}`);
        b.addEventListener("click", () => pickSlot(r.slot));
        li.append(b);
      }
      list.append(li);
    });
    $("t-busy-wrap").hidden = !data.reserved.length;
    $("t-busy").checked = S.showBusy;
  }

  $("t-busy").addEventListener("change", (e) => { S.showBusy = e.target.checked; renderTimes(); });

  function setFormat() {
    $("t-12").setAttribute("aria-pressed", String(!S.h24));
    $("t-24").setAttribute("aria-pressed", String(S.h24));
  }
  [["t-12", false], ["t-24", true]].forEach(([id, v]) => $(id).addEventListener("click", () => {
    S.h24 = v; store.set("bk-24h", v ? "1" : "0"); setFormat(); renderTimes();
    if (S.slot) $("d-when").textContent = whenText(S.slot);
  }));

  // ------------------------------------------------------------------ step 4: details
  const whenText = (slot) => `${longDate(slot.start.slice(0, 10))}, ${range(slot.start, slot.end)}`;

  function pickSlot(slot) {
    S.slot = slot;
    $("d-when").textContent = whenText(slot);
    $("d-err").textContent = "";
    $("b-alert").textContent = "";
    [$("d-name"), $("d-email")].forEach(clearField);
    renderTimes();
    go("details");
  }

  [$("d-name"), $("d-email")].forEach((i) => i.addEventListener("input", () => { clearField(i); $("d-err").textContent = ""; }));

  $("f-details").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const nameI = $("d-name"), emailI = $("d-email"), err = $("d-err");
    const name = nameI.value.trim(), email = emailI.value.trim();
    [nameI, emailI].forEach(clearField);
    if (!name) { fieldError(nameI, err, "Enter your name."); return; }
    if (!/^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(email)) {
      fieldError(emailI, err, "Enter a valid email, like name@example.com.");
      return;
    }
    err.textContent = "";
    const btn = $("d-submit");
    setBusy(btn, true, "Sending");
    $("d-back").disabled = true;
    setMood("think");
    try {
      const r = await api("/api/public/requests", { method: "POST", body: {
        name, email, motive: S.motive, duration_min: S.duration, start: S.slot.start, website: $("d-website").value,
      } });
      const url = `${location.origin}/book/status/${encodeURIComponent(r.ref)}`;
      $("s-when").textContent = `${whenText(S.slot)} (${S.profile.timezone})`;
      $("s-text").textContent = S.profile.owner_name
        ? `You'll get an email when ${S.profile.owner_name} replies.`
        : "You'll get an email when your request is answered.";
      const a = $("s-link"); a.href = url; a.textContent = url;
      show("v-done", "happy");
    } catch (e) {
      setMood("idle");
      if (e.code === "SLOT_TAKEN") { await slotTaken(); return; }
      if (e.status === 503) { boot(); return; }
      const msg = e.message;
      if (/email/i.test(msg)) fieldError(emailI, err, msg);
      else if (/name/i.test(msg)) fieldError(nameI, err, msg);
      else err.textContent = msg;
    } finally {
      setBusy(btn, false);
      $("d-back").disabled = false;
    }
  });

  async function slotTaken() {
    S.slot = null;
    const day = S.selected;
    try { await loadDays(); } catch { /* keep the old list */ }
    if (!isOpen(day)) S.selected = desk.matches ? firstOpen() : null;
    renderCalendar();
    renderStrip();
    const msg = "That time was just taken. Pick another.";
    if (S.selected) {
      go(desk.matches ? "day" : "time");
      await loadDay(S.selected);
      $("t-err").textContent = msg;
    } else {
      clearTimes();
      go("day");
      $("b-alert").textContent = `${msg} That day is now full.`;
    }
  }

  // ------------------------------------------------------------------ status page
  const STATUS = {
    requested: ["Waiting for a reply", "You'll get an email once it's answered.", "idle"],
    confirmed: ["Confirmed", "See you then. The details are in your email.", "happy"],
    waitlisted: ["Pencilled in", "Not final yet. You'll get an email when it is.", "idle"],
    declined: ["Not this time", "This time doesn't work, sorry.", "sad"],
    withdrawn: ["Withdrawn", "You withdrew this request.", "sleep"],
  };

  function statusRef() {
    const m = location.pathname.match(/\/status\/([^/?#]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  async function showStatus(ref, focus = false) {
    $("st-err").textContent = "";
    $("st-confirm").hidden = true;
    try {
      const r = await api(`/api/public/requests/${encodeURIComponent(ref)}`);
      const [title, text, mood] = STATUS[r.status] || [r.status, "", "idle"];
      $("st-title-text").textContent = title;
      $("st-check").hidden = r.status !== "confirmed";
      $("st-when").textContent = `${r.when} (${r.timezone}), ${durText(r.duration_min)}`;
      $("st-when-row").hidden = false;
      $("st-motive").textContent = r.motive || "";
      $("st-text").textContent = r.reason ? "" : text;
      $("st-reason").hidden = !r.reason;
      $("st-reason").textContent = r.reason || "";
      $("st-withdraw").hidden = !r.can_withdraw;
      document.title = `${title}: meeting request`;
      show("v-status", mood, focus);
    } catch (e) {
      $("st-check").hidden = true;
      $("st-title-text").textContent = e.status === 404 ? "We couldn't find that request" : "Something went wrong";
      $("st-when-row").hidden = true;
      $("st-motive").textContent = "";
      $("st-text").textContent = e.status === 404 ? "Check the link in your email and try again." : e.message;
      $("st-reason").hidden = true;
      $("st-withdraw").hidden = true;
      show("v-status", "sad", focus);
    }
  }

  $("st-withdraw").addEventListener("click", () => {
    $("st-withdraw").hidden = true;
    $("st-confirm").hidden = false;
    $("st-no").focus();
  });
  $("st-no").addEventListener("click", () => {
    $("st-confirm").hidden = true;
    $("st-withdraw").hidden = false;
    $("st-withdraw").focus();
  });
  $("st-yes").addEventListener("click", async () => {
    const ref = statusRef();
    const btn = $("st-yes");
    setBusy(btn, true, "Withdrawing");
    $("st-no").disabled = true;
    try {
      await api(`/api/public/requests/${encodeURIComponent(ref)}/withdraw`, { method: "POST" });
      await showStatus(ref, true);
    } catch (e) {
      $("st-err").textContent = e.message;
    } finally { setBusy(btn, false); $("st-no").disabled = false; }
  });

  // ------------------------------------------------------------------ layout changes
  desk.addEventListener("change", () => {
    if (!S.profile || !$("v-book") || $("v-book").hidden) return;
    if (desk.matches && S.step === "time") S.step = "day";
    if (desk.matches && !S.selected && S.days.size) {
      S.selected = firstOpen();
      const a = parseDay(S.selected);
      S.month = { y: a.y, m: a.m };
      loadDay(S.selected);
    }
    $("v-book").dataset.step = S.step;
    renderCalendar();
    renderStrip();
    renderHeading();
  });

  // ------------------------------------------------------------------ boot
  async function boot() {
    const ref = statusRef();
    if (ref) { await showStatus(ref); return; }
    try {
      S.profile = await api("/api/public/profile");
    } catch (e) {
      $("x-title").textContent = "Can't reach the booking page";
      $("x-text").textContent = e.message;
      show("v-closed", "sad", false);
      return;
    }
    if (!S.profile.enabled || !S.profile.durations_min.length) { show("v-closed", "sleep", false); return; }
    document.title = S.profile.owner_name ? `Book a time with ${S.profile.owner_name}` : "Book a time";
    startIntro(false);
  }

  loadArt();
  wake();
  boot();
})();
