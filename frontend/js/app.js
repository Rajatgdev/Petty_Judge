import { connectRoom } from "/js/socket.js";
import { animate } from "https://cdn.jsdelivr.net/npm/motion@11/+esm";

const API_BASE = import.meta.env.VITE_API_BASE || "";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const screens = {};
document.querySelectorAll("[data-screen]").forEach((el) => screens[el.dataset.screen] = el);
function show(name) {
  Object.values(screens).forEach((el) => (el.hidden = true));
  screens[name].hidden = false;
}
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function flash(btn, text) { const o = btn.textContent; btn.textContent = text; setTimeout(() => (btn.textContent = o), 1400); }

// ---------- state ----------
let room = null;
let mySeat = null;
let roomId = null;
let joined = false;         // sent our name
let verdictShown = false;
let renderedCount = 0;      // transcript messages already drawn
let lastJuryP = 50;
const restedShown = { plaintiff: false, defendant: false };
let lastVerdict = null;

const pathMatch = location.pathname.match(/^\/r\/([A-Za-z0-9_-]+)$/);

// ============ FLOW A: host files ============
$("file-case").addEventListener("click", async () => {
  const name = $("host-name").value.trim();
  const kase = $("host-case").value.trim();
  if (!kase) { $("host-case").focus(); return; }

  const res = await fetch(`${API_BASE}/api/rooms`, { method: "POST" });
  const { room_id } = await res.json();
  roomId = room_id;

  const link = `${location.origin}/r/${room_id}`;
  $("share-link").textContent = link;
  history.replaceState({}, "", `/r/${room_id}`);

  pendingName = name || "The Plaintiff";
  pendingOpening = kase;     // plaintiff's first line, sent once seated
  show("trial");
  joinRoom(room_id);
});

$("copy-link").addEventListener("click", async () => {
  const link = `${location.origin}/r/${roomId}`;
  try {
    if (navigator.share) await navigator.share({ title: "You've been summoned", text: "Petty Night Court is in session. Argue your case:", url: link });
    else { await navigator.clipboard.writeText(link); flash($("copy-link"), "Copied"); }
  } catch {}
});

// ============ FLOW B: defendant lands on /r/<id> ============
if (pathMatch) {
  roomId = pathMatch[1];
  show("join");
  joinRoom(roomId); // connect early to receive the accusation brief

  $("enter-court").addEventListener("click", () => {
    const name = $("guest-name").value.trim();
    pendingName = name || "The Defendant";
    if (room) room.send({ t: "join", name: pendingName });
    joined = true;
    show("trial");
  });
}

// ============ shared socket ============
let pendingName = null;
let pendingOpening = null;

function joinRoom(id) {
  room = connectRoom(id, API_BASE, {
    onOpen() {
      if (pendingName && !joined) { room.send({ t: "join", name: pendingName }); joined = true; }
      // plaintiff's opening line is their first turn
      if (pendingOpening) { room.send({ t: "say", text: pendingOpening }); pendingOpening = null; }
    },
    onMessage: handle,
    onReconnecting() {
      const tl = $("turn-line");
      if (tl && !verdictShown) tl.textContent = "Reconnecting to chambers…";
    },
  });
}

// ============ composer ============
$("say-btn").addEventListener("click", sendSay);
$("say-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendSay();
});
$("rest-btn").addEventListener("click", () => { if (room) room.send({ t: "rest" }); });

function sendSay() {
  const el = $("say-input");
  const text = el.value.trim();
  if (!text || !room) return;
  room.send({ t: "say", text });
  el.value = "";
  setComposerEnabled(false); // optimistic lock until server confirms next turn
}

function setComposerEnabled(on) {
  $("say-input").disabled = !on;
  $("say-btn").disabled = !on;
  $("rest-btn").disabled = !on;
}

// ============ message handling ============
function handle(msg) {
  if (msg.t === "seat") { mySeat = msg.seat; return; }

  if (msg.t === "error") {
    const map = {
      full: "This courtroom already has two parties. The gallery is closed.",
      no_room: "This case has expired or was never filed.",
      judge_failed: "The court could not reach a ruling. Try filing again.",
      not_your_turn: null, empty: null,
    };
    if (map[msg.code]) { show("dismissed"); $("dismissed-reason").textContent = map[msg.code]; }
    return;
  }

  if (msg.t !== "room_state") return;
  const s = msg.state;

  // defendant's accusation brief (join screen)
  if (s.summary && !joined) {
    $("accusation-topic").textContent = s.summary.topic || "";
    $("accusation-text").textContent = s.summary.accusation || "";
  }

  if (s.verdict && !verdictShown) { renderVerdict(s.verdict); return; }
  if (s.judging && !verdictShown) { show("deliberating"); animateGavel(); return; }

  updateTrial(s);
}

function updateTrial(s) {
  if (verdictShown) return;

  // names on the jury bar
  $("jb-p-name").textContent = s.seats.plaintiff.name || "Plaintiff";
  $("jb-d-name").textContent = s.seats.defendant.name || "Defendant";
  if (s.summary && s.summary.topic) $("trial-topic").textContent = s.summary.topic;

  // jury bar
  setJury(s.jury.plaintiff_pct, s.jury.reason);

  // transcript — append only new messages
  const tr = $("transcript");
  for (let i = renderedCount; i < s.transcript.length; i++) {
    const m = s.transcript[i];
    const who = m.seat === "plaintiff" ? (s.seats.plaintiff.name || "Plaintiff") : (s.seats.defendant.name || "Defendant");
    const el = document.createElement("div");
    el.className = "msg " + (m.seat === mySeat ? "msg--me" : "msg--them") + " msg--" + m.seat;
    el.innerHTML = `<span class="msg__who">${escapeHtml(who)}</span><p class="msg__text">${escapeHtml(m.text)}</p>`;
    tr.appendChild(el);
    if (!reduceMotion) { el.style.opacity = 0; animate(el, { opacity: [0, 1], y: [8, 0] }, { duration: 0.35 }); }
  }
  if (s.transcript.length !== renderedCount) { renderedCount = s.transcript.length; tr.scrollTop = tr.scrollHeight; }

  // "rested" banners — show each side that has rested, once
  ["plaintiff", "defendant"].forEach((seat) => {
    if (s.rested[seat] && !restedShown[seat]) {
      restedShown[seat] = true;
      const name = s.seats[seat].name || (seat === "plaintiff" ? "Plaintiff" : "Defendant");
      const who = seat === mySeat ? "You have" : `${name} has`;
      const el = document.createElement("div");
      el.className = "rested-banner";
      el.textContent = `⚖ ${who} rested their case.`;
      tr.appendChild(el);
      tr.scrollTop = tr.scrollHeight;
    }
  });

  // summons: show only for host, only until defendant present
  const summons = $("summons-box");
  if (summons) summons.style.display = (mySeat === "plaintiff" && !s.seats.defendant.present) ? "" : "none";

  // turn + composer
  const bothHere = s.seats.plaintiff.present && s.seats.defendant.present;
  const myTurn = s.turn === mySeat && !s.rested[mySeat] && s.remaining[mySeat] > 0;
  const rem = s.remaining[mySeat];

  if (!bothHere) {
    $("turn-line").textContent = mySeat === "plaintiff" ? "Awaiting the accused…" : "Awaiting the plaintiff…";
    setComposerEnabled(false);
  } else if (s.rested[mySeat] || s.remaining[mySeat] === 0) {
    $("turn-line").textContent = "Your case rests. Awaiting the other party…";
    setComposerEnabled(false);
  } else if (myTurn) {
    $("turn-line").textContent = "The floor is yours.";
    setComposerEnabled(true);
    $("say-input").focus();
  } else {
    const them = s.turn === "plaintiff" ? (s.seats.plaintiff.name || "Plaintiff") : (s.seats.defendant.name || "Defendant");
    const otherRested = s.rested[mySeat === "plaintiff" ? "defendant" : "plaintiff"];
    $("turn-line").textContent = otherRested ? `${them} rested. Awaiting the court…` : `${them} is speaking…`;
    setComposerEnabled(false);
  }
  $("remaining").textContent = bothHere ? `${rem} statement${rem === 1 ? "" : "s"} left` : "";
}

function setJury(pPct, reason) {
  pPct = Math.max(0, Math.min(100, pPct));
  const fp = $("jb-fill-p"), fd = $("jb-fill-d"), needle = $("jb-needle");
  $("jb-p-pct").textContent = pPct + "%";
  $("jb-d-pct").textContent = (100 - pPct) + "%";
  const reasonEl = $("jury-reason");
  if (reasonEl && reason) {
    if (reasonEl.textContent !== reason && !reduceMotion) animate(reasonEl, { opacity: [0.3, 1] }, { duration: 0.4 });
    reasonEl.textContent = reason;
  }
  if (reduceMotion) {
    fp.style.width = pPct + "%"; fd.style.width = (100 - pPct) + "%"; needle.style.left = pPct + "%";
    lastJuryP = pPct; return;
  }
  fp.style.width = pPct + "%"; fd.style.width = (100 - pPct) + "%";
  // needle swings with a spring from its last position
  animate(needle, { left: [lastJuryP + "%", pPct + "%"] }, { type: "spring", stiffness: 90, damping: 12 });
  lastJuryP = pPct;
}

// ============ animations ============
function animateGavel() {
  if (reduceMotion) return;
  const g = document.querySelector(".gavel");
  if (!g) return;
  animate(g, { rotate: [0, -18, 6, -18, 6, 0], y: [0, 0, 4, 0, 4, 0] }, { duration: 1.4, repeat: Infinity, ease: "easeInOut" });
}

// ============ verdict (unchanged core) ============
function renderVerdict(v) {
  verdictShown = true;
  lastVerdict = v;
  if (v.dismissed) { show("dismissed"); $("dismissed-reason").textContent = v.reason; return; }
  show("verdict");
  $("vc-verdict-line").textContent = v.verdict_line;
  $("p-name").textContent = v.plaintiff.name;
  $("d-name").textContent = v.defendant.name;
  $("vc-sentence").textContent = v.sentence;
  $("vc-damages").textContent = `$${v.damages} in emotional damages`;
  $("vc-case").textContent = `CASE ${roomId.slice(0, 6).toUpperCase()}-PETTY`;
  $("p-petty-label").textContent = v.plaintiff.pettiness.label;
  $("d-petty-label").textContent = v.defendant.pettiness.label;
  if (v.winner === "plaintiff" || v.winner === "both_equally") $("party-plaintiff").classList.add("party--guilty");
  if (v.winner === "defendant" || v.winner === "both_equally") $("party-defendant").classList.add("party--guilty");
  renderCharges($("p-charges"), v.plaintiff.charges);
  renderCharges($("d-charges"), v.defendant.charges);
  if (reduceMotion) {
    setFill($("jury-fill"), v.jury_confidence); setFill($("p-petty-fill"), v.plaintiff.pettiness.pct); setFill($("d-petty-fill"), v.defendant.pettiness.pct);
    $("jury-num").textContent = v.jury_confidence; $("p-petty-num").textContent = v.plaintiff.pettiness.pct; $("d-petty-num").textContent = v.defendant.pettiness.pct;
    return;
  }
  animate($("vc-stamp"), { scale: [2.4, 0.9, 1.06, 1], rotate: [-14, -4, -4, -4], opacity: [0, 1, 1, 1] }, { duration: 0.5, ease: "easeOut" });
  fillBar($("jury-fill"), $("jury-num"), v.jury_confidence, 0.5);
  fillBar($("p-petty-fill"), $("p-petty-num"), v.plaintiff.pettiness.pct, 0.7);
  fillBar($("d-petty-fill"), $("d-petty-num"), v.defendant.pettiness.pct, 0.85);
}
function renderCharges(ul, charges) {
  ul.innerHTML = "";
  if (!charges.length) { const li = document.createElement("li"); li.className = "charges--clean"; li.textContent = "No charges filed. Suspiciously clean."; ul.appendChild(li); return; }
  charges.forEach((c, i) => {
    const li = document.createElement("li"); li.className = "charge";
    li.innerHTML = `<span>${escapeHtml(c.label)}</span><span class="charge__prob">${Math.round(c.probability * 100)}%</span>`;
    ul.appendChild(li);
    if (!reduceMotion) { li.style.opacity = 0; animate(li, { opacity: [0, 1], x: [-8, 0] }, { duration: 0.3, delay: 1 + i * 0.12 }); }
  });
}
function setFill(el, pct) { el.style.width = pct + "%"; }
function fillBar(fillEl, numEl, pct, delay) {
  animate(fillEl, { width: ["0%", pct + "%"] }, { duration: 0.9, delay, ease: [0.22, 1, 0.36, 1] });
  const obj = { n: 0 };
  animate(obj, { n: pct }, { duration: 0.9, delay, ease: "easeOut", onUpdate: () => (numEl.textContent = Math.round(obj.n)) });
}

// ============ nav ============
["new-case", "dismissed-new"].forEach((id) => { const b = $(id); if (b) b.addEventListener("click", () => (location.href = "/")); });
$("share-verdict").addEventListener("click", async () => {
  if (!lastVerdict) return;
  const v = lastVerdict;
  const line = (name, p) => `${name}: ${p.pettiness.pct}% petty (${p.pettiness.label})`;
  const text =
    `⚖ THE PETTY NIGHT COURT RULES ⚖\n` +
    `${v.verdict_line}\n` +
    `Jury ${v.jury_confidence}% sure\n\n` +
    `${line(v.plaintiff.name, v.plaintiff)}\n` +
    `${line(v.defendant.name, v.defendant)}\n\n` +
    `SENTENCE: ${v.sentence}\n` +
    `$${v.damages} in emotional damages\n\n` +
    `Argue your own case: ${location.origin}`;
  try {
    if (navigator.share) await navigator.share({ title: "The verdict is in", text });
    else { await navigator.clipboard.writeText(text); flash($("share-verdict"), "Copied ruling!"); }
  } catch {}
});
