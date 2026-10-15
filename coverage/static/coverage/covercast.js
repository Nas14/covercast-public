// CoverCast: the two actions a supervisor takes, both through the JSON API.
// No framework, no external files. The server decides everything; this only
// sends the request and shows the answer.
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

  function showError(box, err) {
    box.hidden = false;
    box.className = "result error";
    box.replaceChildren();
    const e = (err && err.error) || {};
    box.append(el("p", {}, e.message_ar || "تعذّر الاتصال بالخادم. حاول مرة أخرى."));
    if (e.code === "GAP_ALREADY_FILLED" || e.code === "CANDIDATE_NOT_ELIGIBLE") {
      const p = el("p");
      p.append("تغيّر الوضع منذ فتح الصفحة. ");
      const a = el("a", { href: location.pathname + location.search }, "اعرض الوضع الحالي");
      p.append(a);
      box.append(p);
    }
  }

  function showApproved(box, data, name) {
    box.hidden = false;
    box.className = "result";
    box.replaceChildren();
    box.append(el("p", {}, data.replayed
      ? "هذا الاعتماد مسجّل من قبل؛ لم يُنشأ تكليف جديد."
      : "تم اعتماد " + name + ". المقعد محجوز الآن بانتظار الرد."));
    if (data.moved_from_site) box.append(el("p", {}, "عند الاعتذار يعود الموظف إلى موقعه السابق تلقائيًا."));
    const row = el("div", { class: "offer-link" });
    const input = el("input", { type: "text", readonly: "", value: data.offer_url, "aria-label": "رابط الموظف" });
    const copy = el("button", { class: "btn small", type: "button" }, "نسخ رابط الموظف");
    copy.addEventListener("click", async () => {
      input.select();
      try { await navigator.clipboard.writeText(data.offer_url); copy.textContent = "نُسخ"; }
      catch (e) { document.execCommand && document.execCommand("copy"); copy.textContent = "نُسخ"; }
    });
    row.append(input, copy);
    box.append(el("p", {}, "أرسل هذا الرابط إلى " + name + " ليقبل أو يعتذر:"), row);
  }

  async function doApprove(btn) {
    const all = document.querySelectorAll("[data-approve]");
    all.forEach((b) => (b.disabled = true));
    const box = btn.closest(".person").querySelector(".result");
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
      all.forEach((b) => (b.disabled = false));
    }
    box.scrollIntoView({ block: "nearest" });
  }

  // The approval dialog (design p. 41-43): what changes, where from, where to,
  // and a warning near the hours cap. Without <dialog> support: the browser's
  // own confirm box, as before.
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

  // ---------------------------------------------------------------- live board
  // Every few seconds: "has anything changed since version V?" The server
  // answers 204 (no) after a read-only check, or 200 with the new fragment.
  // We swap the fragment in place, keep the reader's place on the page, and
  // never interrupt an action: while an absence menu is open, a request is
  // running or a dialog is shown, the swap waits for the next tick.
  const live = document.querySelector("[data-live]");
  if (live) {
    const status = document.querySelector("[data-live-status]");
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
      live.querySelector("details[open]") || document.querySelector("dialog[open]") || document.body.dataset.busy === "1";

    // The first element still on screen, and how far it is from the top.
    function anchor() {
      for (const el of live.querySelectorAll("[data-anchor]")) {
        const top = el.getBoundingClientRect().top;
        if (top >= 0 || el.getBoundingClientRect().bottom > 0) return { key: el.dataset.anchor, top: top };
      }
      return null;
    }
    // Something the user had focused (a link or a button) that we can find again.
    function focusKey() {
      const a = document.activeElement;
      if (!a || !live.contains(a)) return null;
      return a.getAttribute("href") || (a.dataset.absence ? a.dataset.absence + "|" + a.dataset.kind : null);
    }

    function swap(html) {
      const where = anchor();
      const fk = focusKey();
      live.innerHTML = html;
      if (where) {
        const el = live.querySelector('[data-anchor="' + CSS.escape(where.key) + '"]');
        if (el) window.scrollBy(0, el.getBoundingClientRect().top - where.top);
      }
      if (fk) {
        const el = [...live.querySelectorAll("a[href], [data-absence]")].find(
          (e) => (e.getAttribute("href") || e.dataset.absence + "|" + e.dataset.kind) === fk);
        if (el) el.focus({ preventScroll: true });
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
        if (status && status.dataset.state === "error") setStatus("تتحدّث اللوحة تلقائيًا");
      } else if (r && r.status === 200) {
        const html = await r.text();
        if (interacting()) { schedule(base); return; }   // the user started something meanwhile
        version = r.headers.get("X-Board-Version") || version;
        swap(html);
        delay = base;
        setStatus("تحدّثت اللوحة " + hm(), "updated");
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
