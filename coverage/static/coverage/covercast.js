// CoverCast: the supervisor's actions through the JSON API, the live lists,
// and the view controls (shift tabs, filters). No framework, no external file.
// The server decides everything; this only sends requests and shows answers.
(function () {
  "use strict";

  function csrf() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function post(url, data) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(data),
    });
    let body = {};
    try { body = await r.json(); } catch (e) { /* non-JSON answer */ }
    return { ok: r.ok, status: r.status, body: body };
  }

  // One key per approve button, made when the page loads. A second click,
  // or a retry after a lost answer, sends the SAME key, so the server
  // returns the first approval instead of creating another (docs/06).
  function newKey() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => e.setAttribute(k, v));
    if (text) e.textContent = text;
    return e;
  }

  const me = document.currentScript;
  const links = {
    requests: (me && me.dataset.requestsUrl) || "/requests",
    board: (me && me.dataset.boardUrl) || "/",
  };

  function showError(box, err) {
    box.hidden = false;
    box.className = "result error";
    box.replaceChildren();
    const e = (err && err.error) || {};
    box.append(el("p", { class: "r-title" }, "لم يُعتمد البديل"));
    box.append(el("p", {}, e.message_ar || "تعذّر الاتصال بالخادم. حاول مرة أخرى."));
    if (e.code === "GAP_ALREADY_FILLED" || e.code === "CANDIDATE_NOT_ELIGIBLE") {
      const p = el("p");
      p.append("تغيّر الوضع منذ فتح الصفحة. ");
      p.append(el("a", { href: location.pathname + location.search }, "اعرض الوضع الحالي"));
      box.append(p);
    }
  }

  // The journey moves on: step 2 done, step 3 (the employee's answer) now.
  function advanceStepper() {
    const steps = document.querySelectorAll("[data-stepper] li");
    if (steps.length < 3) return;
    steps[1].className = "is-done"; steps[1].removeAttribute("aria-current");
    steps[2].className = "is-now"; steps[2].setAttribute("aria-current", "step");
  }

  function showApproved(box, data, name) {
    box.hidden = false;
    box.className = "result";
    box.replaceChildren();
    box.append(el("p", { class: "r-title" }, data.replayed
      ? "هذا الاعتماد مسجّل من قبل؛ لم يُنشأ تكليف جديد."
      : "تم اعتماد " + name + ". المقعد محجوز الآن بانتظار الرد."));
    box.append(el("p", {}, "أرسل هذا الرابط إلى " + name + " ليقبل أو يعتذر من جواله، بلا حساب:"));
    const row = el("div", { class: "offer-link" });
    const input = el("input", { type: "text", readonly: "", value: data.offer_url, "aria-label": "رابط الموظف" });
    const copy = el("button", { class: "btn small", type: "button" }, "نسخ رابط الموظف");
    copy.addEventListener("click", async () => {
      input.select();
      try { await navigator.clipboard.writeText(data.offer_url); copy.textContent = "نُسخ الرابط"; }
      catch (e) { document.execCommand && document.execCommand("copy"); copy.textContent = "نُسخ الرابط"; }
    });
    row.append(input, copy);
    box.append(row);
    box.append(el("p", { class: "small muted" }, data.moved_from_site
      ? "إن اعتذر، تعود الفجوة إلى «يحتاج قرارك» ويعود الموظف إلى موقعه السابق تلقائيًا."
      : "إن اعتذر، تعود الفجوة إلى «يحتاج قرارك» تلقائيًا."));
    const actions = el("div", { class: "r-actions" });
    actions.append(el("a", { class: "btn quiet small", href: links.board }, "العودة إلى اللوحة"),
                   el("a", { class: "btn quiet small", href: links.requests }, "متابعة الطلبات"));
    box.append(actions);
    advanceStepper();
  }

  async function doApprove(btn) {
    const all = document.querySelectorAll("[data-approve]");
    all.forEach((b) => (b.disabled = true));
    const box = btn.closest(".person").querySelector(".result");
    const original = btn.textContent;
    btn.textContent = "جارٍ الاعتماد…";
    const r = await post(btn.dataset.approve, {
      employee_code: btn.dataset.code,
      idempotency_key: btn.dataset.key,
      slot: Number(btn.dataset.slot),
    }).catch(() => ({ ok: false, body: {} }));
    if (r.ok) {
      showApproved(box, r.body, btn.dataset.name);
      btn.textContent = "معتمد";
    } else {
      showError(box, r.body);
      btn.textContent = original;
      all.forEach((b) => (b.disabled = false));
    }
    box.scrollIntoView({ block: "nearest" });
  }

  // The approval dialog: what changes, where from, where to, and a warning
  // near the hours cap. Without <dialog> support: the browser's confirm box.
  document.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.dataset.key = newKey();
    const dlg = btn.dataset.dialog && document.getElementById(btn.dataset.dialog);
    btn.addEventListener("click", () => {
      if (dlg && typeof dlg.showModal === "function") { dlg.showModal(); dlg.querySelector("[data-confirm-approve]").focus(); return; }
      if (window.confirm(btn.dataset.confirm)) doApprove(btn);
    });
    if (dlg) {
      dlg.querySelector("[data-cancel]").addEventListener("click", () => dlg.close());
      dlg.querySelector("[data-confirm-approve]").addEventListener("click", () => { dlg.close(); doApprove(btn); });
      dlg.addEventListener("close", () => btn.focus());
    }
  });

  // "Copy the employee's link" (board waiting card, requests page): delegated,
  // because both live in a part that polling replaces. If the clipboard is not
  // allowed, the link is shown so it can be copied by hand.
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    const label = btn.dataset.label || btn.textContent;
    btn.dataset.label = label;
    try {
      await navigator.clipboard.writeText(btn.dataset.copy);
      btn.textContent = "نُسخ الرابط ✓";
    } catch (e) {
      window.prompt("انسخ الرابط:", btn.dataset.copy);
    }
    setTimeout(() => { btn.textContent = btn.dataset.label; }, 2500);
  });

  // The absence menu is a small dropdown: one open at a time, closed by a click
  // elsewhere or by Escape (an open one holds the live update, see interacting()).
  document.addEventListener("toggle", (ev) => {
    const d = ev.target;
    if (!(d instanceof HTMLDetailsElement) || !d.hasAttribute("data-hold") || !d.open) return;
    document.querySelectorAll("details[data-hold][open]").forEach((o) => { if (o !== d) o.open = false; });
  }, true);
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("details[data-hold]")) return;
    document.querySelectorAll("details[data-hold][open]").forEach((o) => { o.open = false; });
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    const open = document.querySelector("details[data-hold][open]");
    if (open) { open.open = false; open.querySelector("summary").focus(); }
  });

  // Absence buttons live inside the part of the board that polling replaces,
  // so they are handled by delegation: new buttons work without re-binding.
  const KIND = { no_show: "عدم حضور", sick: "إجازة مرضية", leave: "إجازة" };
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-absence]");
    if (!btn) return;
    if (!window.confirm("تسجيل «" + KIND[btn.dataset.kind] + "» لـ" + btn.dataset.name + "؟ لن يُحسب ضمن الحاضرين في هذه المناوبة.")) return;
    btn.disabled = true;
    document.body.dataset.busy = "1";
    const r = await post(btn.dataset.absence, { kind: btn.dataset.kind }).catch(() => ({ ok: false, body: {} }));
    if (r.ok) { location.reload(); return; }
    btn.disabled = false;
    delete document.body.dataset.busy;
    window.alert(((r.body || {}).error || {}).message_ar || "تعذّر الاتصال بالخادم.");
  });

  // The day strip scrolls sideways on a phone: start with the open day in view.
  const today = document.querySelector('.days [aria-current="date"]');
  if (today && today.scrollIntoView) today.scrollIntoView({ block: "nearest", inline: "nearest" });

  // ------------------------------------------------------------ view controls
  // Shift tabs and filters are the reader's choice: they are kept in memory
  // and in this tab's sessionStorage, and re-applied after every live swap,
  // so a refresh never moves the reader to another shift or filter.
  const live = document.querySelector("[data-live]");
  const viewKey = live ? "covercast-view-" + (live.dataset.viewKey || "") + location.search : null;
  let view = {};
  try { view = JSON.parse(sessionStorage.getItem(viewKey) || "{}") || {}; } catch (e) { view = {}; }
  const saveView = () => { try { sessionStorage.setItem(viewKey, JSON.stringify(view)); } catch (e) { /* private mode */ } };

  function applyView() {
    if (!live) return;
    // tabs
    const tabs = [...live.querySelectorAll("[data-tab]")];
    if (tabs.length) {
      let want = view.tab && tabs.find((t) => t.dataset.tab === view.tab) ? view.tab : null;
      if (!want) want = (tabs.find((t) => t.getAttribute("aria-selected") === "true") || tabs[0]).dataset.tab;
      tabs.forEach((t) => {
        const on = t.dataset.tab === want;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      live.querySelectorAll("[data-tab-panel]").forEach((p) => p.classList.toggle("active", p.dataset.tabPanel === want));
    }
    // filter, inside the visible panel (board) or the whole list (requests)
    if (view.filter && !live.querySelector('[data-filter="' + view.filter + '"]')) view.filter = "all";   // that state is gone
    const filter = view.filter || "all";
    const panels = [...live.querySelectorAll("[data-tab-panel]")];
    const scope = panels.find((p) => p.classList.contains("active")) || live;
    const items = [...scope.querySelectorAll("[data-state]")];
    const count = { all: items.length, open: 0, waiting: 0, covered: 0, accepted: 0, declined: 0 };
    items.forEach((i) => { count[i.dataset.state] = (count[i.dataset.state] || 0) + 1; });
    let shown = 0;
    live.querySelectorAll("[data-state]").forEach((i) => {
      const on = filter === "all" || i.dataset.state === filter;
      i.hidden = !on;
      if (on && scope.contains(i)) shown += 1;
    });
    live.querySelectorAll("[data-count]").forEach((c) => { c.textContent = count[c.dataset.count] || 0; });
    live.querySelectorAll("[data-filter]").forEach((b) => {
      b.setAttribute("aria-pressed", b.dataset.filter === filter ? "true" : "false");
    });
    live.querySelectorAll("[data-empty]").forEach((e) => {
      e.hidden = !(scope.contains(e) || scope === live) || shown > 0 || filter === "all";
    });
  }

  if (live) {
    live.addEventListener("click", (ev) => {
      // «اعرض الفجوات»: the shift with a gap, only the gaps, then scroll there
      const jump = ev.target.closest("[data-jump-gaps]");
      if (jump) {
        ev.preventDefault();
        if (jump.dataset.jumpGaps) view.tab = jump.dataset.jumpGaps;
        view.filter = "open"; saveView(); applyView();
        const head = live.querySelector("#sites-h");
        if (head) { head.scrollIntoView({ behavior: "smooth", block: "start" }); head.setAttribute("tabindex", "-1"); head.focus({ preventScroll: true }); }
        return;
      }
      const tab = ev.target.closest("[data-tab]");
      if (tab) { view.tab = tab.dataset.tab; saveView(); applyView(); return; }
      const f = ev.target.closest("[data-filter]");
      if (f) { view.filter = f.dataset.filter; saveView(); applyView(); }
    });
    live.addEventListener("keydown", (ev) => {
      const tab = ev.target.closest("[data-tab]");
      if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) return;
      const tabs = [...live.querySelectorAll("[data-tab]")];
      let i = tabs.indexOf(tab);
      // right-to-left: ArrowLeft moves forward
      if (ev.key === "ArrowLeft") i = (i + 1) % tabs.length;
      if (ev.key === "ArrowRight") i = (i - 1 + tabs.length) % tabs.length;
      if (ev.key === "Home") i = 0;
      if (ev.key === "End") i = tabs.length - 1;
      view.tab = tabs[i].dataset.tab; saveView(); applyView(); tabs[i].focus();
      ev.preventDefault();
    });
    applyView();
  }

  // ------------------------------------------------------------- live lists
  // Every few seconds: "has anything changed since version V?" The server
  // answers 204 (no) after a read-only check, or 200 with the new fragment.
  // We swap the fragment in place, keep the reader's place on the page and
  // their tab and filter, and never interrupt an action: while an absence
  // menu is open, a request is running or a dialog is shown, the swap waits.
  if (live) {
    const status = document.querySelector("[data-live-status]");
    const idleText = (status && status.dataset.idle) || "تتحدّث اللوحة تلقائيًا";
    const updatedText = (status && status.dataset.updated) || "تحدّثت اللوحة";
    const base = Number(live.dataset.interval) || 5000;
    let version = live.dataset.version;
    let delay = base;
    let timer = null;
    let stopped = false;
    let inFlight = false;

    const setStatus = (text, state) => {
      if (!status) return;
      status.dataset.state = state || "";
      status.textContent = text;
    };
    const hm = () => new Date().toLocaleTimeString("ar-SA-u-nu-latn", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

    const interacting = () =>
      live.querySelector("details[data-hold][open]") || document.querySelector("dialog[open]") || document.body.dataset.busy === "1";

    // The first element still on screen, and how far it is from the top.
    function anchor() {
      for (const e of live.querySelectorAll("[data-anchor]")) {
        if (e.offsetParent === null) continue;            // hidden by a tab or a filter
        const box = e.getBoundingClientRect();
        if (box.top >= 0 || box.bottom > 0) return { key: e.dataset.anchor, top: box.top };
      }
      return null;
    }
    // Something the user had focused (a link or a button) that we can find again.
    function focusKey() {
      const a = document.activeElement;
      if (!a || !live.contains(a)) return null;
      return a.getAttribute("href") || (a.dataset.absence ? a.dataset.absence + "|" + a.dataset.kind : null)
        || (a.dataset.tab ? "tab|" + a.dataset.tab : null) || (a.dataset.filter ? "filter|" + a.dataset.filter : null);
    }
    function keyOf(e) {
      return e.getAttribute("href") || (e.dataset.absence ? e.dataset.absence + "|" + e.dataset.kind : null)
        || (e.dataset.tab ? "tab|" + e.dataset.tab : null) || (e.dataset.filter ? "filter|" + e.dataset.filter : null);
    }

    function swap(html) {
      const where = anchor();
      const fk = focusKey();
      live.innerHTML = html;
      applyView();
      if (where) {
        const e = live.querySelector('[data-anchor="' + CSS.escape(where.key) + '"]');
        if (e) window.scrollBy(0, e.getBoundingClientRect().top - where.top);
      }
      if (fk) {
        const e = [...live.querySelectorAll("a[href], [data-absence], [data-tab], [data-filter]")].find((x) => keyOf(x) === fk);
        if (e) e.focus({ preventScroll: true });
      }
    }

    function schedule(ms) {
      clearTimeout(timer);
      if (!stopped && !document.hidden) timer = setTimeout(tick, ms);
    }

    async function tick() {
      if (stopped || document.hidden || inFlight) return;
      if (interacting()) { schedule(base); return; }
      inFlight = true;
      let r;
      try {
        r = await fetch(live.dataset.live + "&v=" + encodeURIComponent(version), {
          credentials: "same-origin", cache: "no-store", redirect: "manual",
          headers: { "X-Requested-With": "fetch" },
        });
      } catch (e) {
        r = null;
      }
      inFlight = false;
      if (r && r.status === 204) {
        delay = base;
        if (status && status.dataset.state === "error") setStatus(idleText);
      } else if (r && r.status === 200) {
        const html = await r.text();
        if (interacting()) { schedule(base); return; }   // the user started something meanwhile
        version = r.headers.get("X-Board-Version") || version;
        swap(html);
        delay = base;
        setStatus(updatedText + " " + hm(), "updated");
      } else if (r && (r.status === 403 || r.type === "opaqueredirect")) {
        stopped = true;
        setStatus("", "stopped");
        status.replaceChildren("انتهت الجلسة، فتوقف التحديث التلقائي. ");
        const a = document.createElement("a");
        a.href = "/login/?next=" + encodeURIComponent(location.pathname + location.search);
        a.textContent = "سجّل الدخول من جديد";
        status.append(a);
        return;
      } else {
        delay = Math.min(delay * 2, 30000);             // network or server error: back off, keep trying
        setStatus("تعذّر التحديث، سنعيد المحاولة تلقائيًا", "error");
      }
      schedule(delay);
    }

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) clearTimeout(timer);
      else tick();                                      // back on the tab: check at once
    });
    window.addEventListener("online", () => { delay = base; tick(); });
    window.CoverCastLive = { tick: tick, version: () => version };
    schedule(base);
  }
})();
