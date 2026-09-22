"""
Petty Judge — backend (Railway).

Pure API + WebSocket. The frontend is a separate static app on Vercel, so this
service only: creates rooms, pairs two people over a WebSocket, runs the batched
Jev call, and broadcasts the verdict. No HTML, no static files.

CORS is restricted to the frontend origin(s) in FRONTEND_ORIGIN (comma-separated).
"""

import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rooms import manager
from jev import judge_case

app = FastAPI(title="Petty Judge API")

# Which frontend origins may call this API. Set FRONTEND_ORIGIN on Railway to
# your Vercel URL, e.g. "https://petty-judge.vercel.app". Comma-separate to allow
# more than one (production + preview). Defaults to localhost for dev.
_origins = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_NAME = 40
MAX_CASE = 1500


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/rooms")
async def create_room():
    room = manager.create()
    return JSONResponse({"room_id": room.room_id})


async def _broadcast(room):
    msg = json.dumps({"t": "room_state", "state": room.public_state()})
    for seat in ("plaintiff", "defendant"):
        sock = room.sockets[seat]
        if sock is not None:
            try:
                await sock.send_text(msg)
            except Exception:
                pass


@app.websocket("/ws/rooms/{room_id}")
async def room_ws(ws: WebSocket, room_id: str):
    await ws.accept()
    room = manager.get(room_id)
    if room is None:
        await ws.send_text(json.dumps({"t": "error", "code": "no_room"}))
        await ws.close()
        return

    seat = manager.claim_seat(room, ws)
    if seat is None:
        await ws.send_text(json.dumps({"t": "error", "code": "full"}))
        await ws.close()
        return

    await ws.send_text(json.dumps({"t": "seat", "seat": seat}))
    await _broadcast(room)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("t")

            if t == "submit":
                name = str(msg.get("name", "")).strip()[:MAX_NAME] or (
                    "The Plaintiff" if seat == "plaintiff" else "The Defendant")
                case = str(msg.get("case", "")).strip()[:MAX_CASE]
                if not case:
                    await ws.send_text(json.dumps({"t": "error", "code": "empty_case"}))
                    continue
                room.seats[seat]["name"] = name
                room.seats[seat]["case"] = case
                room.seats[seat]["submitted"] = True
                await _broadcast(room)

                if room.both_submitted() and not room.judging and not room.verdict:
                    room.judging = True
                    await _broadcast(room)
                    try:
                        verdict = await judge_case(
                            room.seats["plaintiff"], room.seats["defendant"])
                    except Exception as e:
                        room.judging = False
                        await _broadcast(room)
                        for s in ("plaintiff", "defendant"):
                            sk = room.sockets[s]
                            if sk:
                                await sk.send_text(json.dumps(
                                    {"t": "error", "code": "judge_failed",
                                     "detail": str(e)[:200]}))
                        continue
                    room.verdict = verdict
                    room.judging = False
                    await _broadcast(room)

            elif t == "typing":
                other = "defendant" if seat == "plaintiff" else "plaintiff"
                sk = room.sockets[other]
                if sk:
                    await sk.send_text(json.dumps({"t": "opponent_typing"}))

            elif t == "ping":
                await ws.send_text(json.dumps({"t": "pong"}))

    except WebSocketDisconnect:
        pass
    finally:
        manager.release_seat(room, seat, ws)
        try:
            await _broadcast(room)
        except Exception:
            pass
