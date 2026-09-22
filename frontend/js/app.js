import { connectRoom } from "/js/socket.js";
import { animate } from "https://cdn.jsdelivr.net/npm/motion@11/+esm";

// Backend base URL (Railway). Injected at build time by Vite from VITE_API_BASE.
// Falls back to same-origin for the single-origin dev case.
const API_BASE = import.meta.env.VITE_API_BASE || "";

// ---------- tiny helpers ----------
const $ = (id) => document.getElementById(id);
const screens = {};
document.querySelectorAll("[data-screen]").forEach((el) => screens[el.dataset.screen] = el);
function show(name) {
  Object.values(screens).forEach((el) => (el.hidden = true));
  screens[name].hidden = false;
}
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------- app state ----------
let room = null;      // socket handle
let mySeat = null;    // 'plaintiff' | 'defendant'
let roomId = null;
let submitted = false;
let verdictShown = false;

// Room id from the path? Then we're the defendant joining.
const pathMatch = location.pathname.match(/^\/r\/([A-Za-z0-9_-]+)$/);

// ============================================================
// FLOW A — host lands on "/" and files a case
// ============================================================
$("file-case").addEventListener("click", async () => {
  const name = $("host-name").value.trim();
  const kase = $("host-case").value.trim();
  if (!kase) { $("host-case").focus(); return; }

  const res = await fetch(`${API_BASE}/api/rooms`, { method: "POST" });
  const { room_id } = await res.json();
  roomId = room_id;

  // remember our statement to submit once the socket opens
  pendingSubmit = { name, case: kase };

  const link = `${location.origin}/r/${room_id}`;
  $("share-link").textContent = link;
  history.replaceState({}, "", `/r/${room_id}`);
  show("waiting");
  joinRoom(room_id);
});

// copy summons
$("copy-link").addEventListener("click", async () => {
  const link = `${location.origin}/r/${roomId}`;
  try {
    if (navigator.share) { await navigator.share({ title: "You've been summoned", text: "Petty Night Court is in session. State your case:", url: link }); }
    else { await navigator.clipboard.writeText(link); flash($("copy-link"), "Copied"); }
  } catch { /* user cancelled share — fine */ }
});

// ============================================================
// FLOW B — defendant lands on "/r/<id>"
// ============================================================
if (pathMatch) {
  roomId = pathMatch[1];
  show("join");
  $("enter-court").addEventListener("click", () => {
    const name = $("guest-name").value.trim();
    const kase = $("guest-case").value.trim();
    if (!kase) { $("guest-case").focus(); return; }
    pendingSubmit = { name, case: kase };
    joinRoom(roomId);
    show("waiting");
    $("waiting-sub").textContent = "Statement filed. Waiting on the court…";
    $("share-link").parentElement.style.display = "none"; // guest doesn't need the link box
  });
}

// ============================================================
// shared: join room over websocket
// ============================================================
let pendingSubmit = null;

function joinRoom(id) {
  room = connectRoom(id, API_BASE, {
    onOpen() {
      if (pendingSubmit && !submitted) {
        room.send({ t: "submit", ...pendingSubmit });
        submitted = true;
      }
    },
    onMessage(msg) { handle(msg); },
    onReconnecting() {
      const note = $("waiting-presence");
      if (note && !verdictShown) note.childNodes[note.childNodes.length - 1].textContent = " Reconnecting to chambers…";
    },
  });
}

function handle(msg) {
  if (msg.t === "seat") { mySeat = msg.seat; return; }

  if (msg.t === "error") {
    if (msg.code === "full") { show("dismissed"); $("dismissed-reason").textContent = "This courtroom already has two parties. The gallery is closed."; }
    else if (msg.code === "no_room") { show("dismissed"); $("dismissed-reason").textContent = "This case has expired or was never filed."; }
    else if (msg.code === "judge_failed") { show("dismissed"); $("dismissed-reason").textContent = "The court could not reach a ruling. Try filing again."; }
    return;
  }

  if (msg.t === "room_state") {
    const s = msg.state;

    if (s.verdict && !verdictShown) { renderVerdict(s.verdict); return; }
    if (s.judging && !verdictShown) { show("deliberating"); animateGavel(); return; }

    // still waiting — update presence text
    if (!verdictShown && submitted) {
      const other = mySeat === "plaintiff" ? "defendant" : "plaintiff";
      const present = s.seats[other] && s.seats[other].present;
      const theirSubmitted = s.seats[other] && s.seats[other].submitted;
      const note = $("waiting-presence");
      if (note) {
        const txt = present
          ? (theirSubmitted ? " Both statements are in. Approaching the bench…" : " The other party has entered. Awaiting their statement…")
          : " The bailiff is watching the door…";
        note.childNodes[note.childNodes.length - 1].textContent = txt;
      }
    }
  }
}

// ============================================================
// animations
// ============================================================
function animateGavel() {
  if (reduceMotion) return;
  const g = document.querySelector(".gavel");
  if (!g) return;
  animate(g, { rotate: [0, -18, 6, -18, 6, 0], y: [0, 0, 4, 0, 4, 0] },
    { duration: 1.4, repeat: Infinity, ease: "easeInOut" });
}

function flash(btn, text) {
  const old = btn.textContent;
  btn.textContent = text;
  setTimeout(() => (btn.textContent = old), 1400);
}

// ============================================================
// verdict rendering
// ============================================================
function renderVerdict(v) {
  verdictShown = true;

  if (v.dismissed) {
    show("dismissed");
    $("dismissed-reason").textContent = v.reason;
    return;
  }

  show("verdict");

  $("vc-verdict-line").textContent = v.verdict_line;
  $("p-name").textContent = v.plaintiff.name;
  $("d-name").textContent = v.defendant.name;
  $("vc-sentence").textContent = v.sentence;
  $("vc-damages").textContent = `$${v.damages} in emotional damages`;
  $("vc-case").textContent = `CASE ${roomId.slice(0, 6).toUpperCase()}-PETTY`;

  $("p-petty-label").textContent = v.plaintiff.pettiness.label;
  $("d-petty-label").textContent = v.defendant.pettiness.label;

  // guilty highlight
  if (v.winner === "plaintiff" || v.winner === "both_equally") $("party-plaintiff").classList.add("party--guilty");
  if (v.winner === "defendant" || v.winner === "both_equally") $("party-defendant").classList.add("party--guilty");

  renderCharges($("p-charges"), v.plaintiff.charges);
  renderCharges($("d-charges"), v.defendant.charges);

  // --- orchestrated reveal ---
  if (reduceMotion) {
    setFill($("jury-fill"), v.jury_confidence);
    setFill($("p-petty-fill"), v.plaintiff.pettiness.pct);
    setFill($("d-petty-fill"), v.defendant.pettiness.pct);
    $("jury-num").textContent = v.jury_confidence;
    $("p-petty-num").textContent = v.plaintiff.pettiness.pct;
    $("d-petty-num").textContent = v.defendant.pettiness.pct;
    return;
  }

  // stamp thwack
  const stamp = $("vc-stamp");
  animate(stamp, { scale: [2.4, 0.9, 1.06, 1], rotate: [-14, -4, -4, -4], opacity: [0, 1, 1, 1] },
    { duration: 0.5, ease: "easeOut" });

  // bars fill with a slight settle, staggered
  fillBar($("jury-fill"), $("jury-num"), v.jury_confidence, 0.5);
  fillBar($("p-petty-fill"), $("p-petty-num"), v.plaintiff.pettiness.pct, 0.7);
  fillBar($("d-petty-fill"), $("d-petty-num"), v.defendant.pettiness.pct, 0.85);
}

function renderCharges(ul, charges) {
  ul.innerHTML = "";
  if (!charges.length) {
    const li = document.createElement("li");
    li.className = "charges--clean";
    li.textContent = "No charges filed. Suspiciously clean.";
    ul.appendChild(li);
    return;
  }
  charges.forEach((c, i) => {
    const li = document.createElement("li");
    li.className = "charge";
    li.innerHTML = `<span>${escapeHtml(c.label)}</span><span class="charge__prob">${Math.round(c.probability * 100)}%</span>`;
    ul.appendChild(li);
    if (!reduceMotion) {
      li.style.opacity = 0;
      animate(li, { opacity: [0, 1], x: [-8, 0] }, { duration: 0.3, delay: 1 + i * 0.12 });
    }
  });
}

function setFill(el, pct) { el.style.width = pct + "%"; }

function fillBar(fillEl, numEl, pct, delay) {
  // animate width via scaleX for smoothness, then lock width
  animate(fillEl, { width: ["0%", pct + "%"] }, { duration: 0.9, delay, ease: [0.22, 1, 0.36, 1] });
  // count the number up
  const obj = { n: 0 };
  animate(obj, { n: pct }, {
    duration: 0.9, delay, ease: "easeOut",
    onUpdate: () => (numEl.textContent = Math.round(obj.n)),
  });
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ============================================================
// new case buttons
// ============================================================
["new-case", "dismissed-new"].forEach((id) => {
  const btn = $(id);
  if (btn) btn.addEventListener("click", () => { location.href = "/"; });
});

// share verdict = screenshot hint (native share of the URL)
$("share-verdict").addEventListener("click", async () => {
  try {
    if (navigator.share) await navigator.share({ title: "The verdict is in", text: "The Petty Night Court has ruled.", url: location.origin });
    else flash($("share-verdict"), "Screenshot it!");
  } catch {}
});
