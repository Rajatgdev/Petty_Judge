// Single socket owner. Builds the wss:// URL from the backend base (Railway),
// reconnects with backoff, gates sends on OPEN, and calls the app's handlers.

export function connectRoom(roomId, apiBase, handlers) {
  let ws = null;
  let closedByUs = false;
  let attempt = 0;
  let pingTimer = null;

  function url() {
    // Derive the WS origin from the backend base URL. If apiBase is empty
    // (single-origin dev), fall back to the page's own host.
    let host, secure;
    if (apiBase) {
      const u = new URL(apiBase);
      host = u.host;
      secure = u.protocol === "https:";
    } else {
      host = location.host;
      secure = location.protocol === "https:";
    }
    const proto = secure ? "wss" : "ws";
    return `${proto}://${host}/ws/rooms/${roomId}`;
  }

  function open() {
    ws = new WebSocket(url());

    ws.addEventListener("open", () => {
      attempt = 0;
      handlers.onOpen && handlers.onOpen();
      pingTimer = setInterval(() => send({ t: "ping" }), 25000);
    });

    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handlers.onMessage && handlers.onMessage(msg);
    });

    ws.addEventListener("close", () => {
      clearInterval(pingTimer);
      if (closedByUs) return;
      attempt++;
      handlers.onReconnecting && handlers.onReconnecting();
      const delay = Math.min(400 * 2 ** attempt, 8000) + Math.random() * 300;
      setTimeout(open, delay);
    });

    ws.addEventListener("error", () => { try { ws.close(); } catch {} });
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function close() {
    closedByUs = true;
    clearInterval(pingTimer);
    try { ws && ws.close(); } catch {}
  }

  window.addEventListener("pagehide", close);
  open();
  return { send, close };
}
