# ⚖ Petty Judge

A small-claims court for petty disputes. Two people, one shareable link. Each
states their side; **Jev** (TypeSafe's System One model) delivers the verdict —
who's guilty, how petty each party is, a sheet of charges, a sentence, and
emotional damages. One batched call of typed decisions, ~100ms, no text
generation.

## Layout

- `backend/` — FastAPI (rooms, two-person WebSocket, batched Jev call + safety guard), deployed to **Railway**
- `frontend/` — vanilla HTML/CSS/JS built with Vite, deployed to **Vercel**

## Stack

Vanilla JS + Motion (Vite) / Vercel · FastAPI / Railway · Jev (TypeSafe System One)

## How it works

1. Host opens the site, files a complaint, gets a summons link.
2. Host sends the link. The accused opens `/r/<room_id>`, states their defense.
3. Both statements land → one batched Jev call → the verdict card renders for
   both parties, ready to screenshot into the group chat.

The frontend and backend are separate origins, so the backend allows the
frontend via CORS (`FRONTEND_ORIGIN`) and the frontend targets the backend via
`VITE_API_BASE`. The WebSocket is built from that same base.

## Local dev

```
# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in TYPESAFE_API_KEY, or set MOCK=true
uvicorn main:app --reload     # http://localhost:8000

# frontend (new shell)
cd frontend && npm install
cp .env.example .env          # set VITE_API_BASE=http://localhost:8000
npm run dev                   # http://localhost:5173
```

To see real verdicts, put your key in `backend/.env`. For UI-only work with no
key, set `MOCK=true` in `backend/.env`.

> **Fonts:** drop your Gambarino + Satoshi `.woff2` files into
> `frontend/public/fonts/` (see that folder's README for exact names). Missing
> fonts fall back to a system serif/sans cleanly.

## Deploy

**Backend → Railway**
1. New project from the repo, root directory `backend/`.
2. Railway detects `requirements.txt` (or uses the `Dockerfile`). Start command
   is the `Procfile`: `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Set env vars: `TYPESAFE_API_KEY`, and `FRONTEND_ORIGIN` = your Vercel URL(s),
   comma-separated (production + preview), no trailing slash.
4. Railway's public edge supports WebSockets and exempts them from HTTP idle
   limits, so the two-person rooms hold.

**Frontend → Vercel**
1. New project from the repo, root directory `frontend/`.
2. Framework preset: Vite. Build `npm run build`, output `dist`.
3. Set env var `VITE_API_BASE` = your Railway backend URL, no trailing slash.
4. `vercel.json` rewrites `/r/:roomId` to the app so join links load.

After both are up: set `FRONTEND_ORIGIN` on Railway to the real Vercel domain,
redeploy the backend, and confirm a summons link both works and unfurls in a
chat.

## Notes / v1 tradeoffs

- **In-memory rooms** (`backend/rooms.py`) — fine for one Railway instance. For
  multiple instances, move room state + a pub/sub relay to Redis; nothing else
  changes.
- **Static OG preview** — every shared link unfurls with one generic lobby image
  (no per-room render). Per-room images would need a serverless function.
- **Screenshot = share** — the phone screenshot is the share path; no image export.
- The Jev judgment design follows TypeSafe's own guidance (Score levels describe
  situations not degrees; jury % comes from Choice confidence, not a Noul).
