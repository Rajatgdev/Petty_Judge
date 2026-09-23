"""
Petty Judge — backend (Railway).

Pure API + WebSocket. Pairs two people, runs a turn-based dispute (plaintiff
opens, strict alternation, capped turns), re-scores the jury after each message,
and renders the final verdict when the argument is over.

CORS is restricted to the frontend origin(s) in FRONTEND_ORIGIN (comma-separated).
"""

import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from rooms import manager, MAX_MESSAGES
from jev import judge_case, score_round
from summary import summarise_complaint

app = FastAPI(title="Petty Judge API")

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
MAX_MSG = 800  # per message


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


async def _run_final_verdict(room):
    room.judging = True
    await _broadcast(room)
    try:
        verdict = await judge_case(
            {"name": room.seats["plaintiff"]["name"] or "The Plaintiff",
             "case": _first_text(room, "plaintiff")},
            {"name": room.seats["defendant"]["name"] or "The Defendant",
             "case": _first_text(room, "defendant")},
            transcript=room.transcript,
        )
    except Exception as e:
        room.judging = False
        await _broadcast(room)
        for s in ("plaintiff", "defendant"):
            sk = room.sockets[s]
            if sk:
                await sk.send_text(json.dumps(
                    {"t": "error", "code": "judge_failed", "detail": str(e)[:200]}))
        return
    room.verdict = verdict
    room.judging = False
    await _broadcast(room)


def _first_text(room, seat):
    for m in room.transcript:
        if m["seat"] == seat:
            return m["text"]
    return ""


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

            if t == "join":
                # register display name (does not consume a turn)
                name = str(msg.get("name", "")).strip()[:MAX_NAME]
                if name:
                    room.seats[seat]["name"] = name
                await _broadcast(room)

            elif t == "say":
                if not room.can_speak(seat):
                    await ws.send_text(json.dumps({"t": "error", "code": "not_your_turn"}))
                    continue
                text = str(msg.get("text", "")).strip()[:MAX_MSG]
                if not text:
                    await ws.send_text(json.dumps({"t": "error", "code": "empty"}))
                    continue

                # record the turn
                room.transcript.append({"seat": seat, "text": text})
                room.counts[seat] += 1

                # the plaintiff's first line generates the topic/accusation brief
                if seat == "plaintiff" and room.counts["plaintiff"] == 1 and not room.summary:
                    room.summary = await summarise_complaint(
                        room.seats["plaintiff"]["name"] or "The Plaintiff", text)

                # re-score the jury over the transcript so far (live bar)
                room.jury = await score_round(
                    room.transcript,
                    room.seats["plaintiff"]["name"] or "The Plaintiff",
                    room.seats["defendant"]["name"] or "The Defendant",
                )

                # hand off the turn, then check if the argument is over
                room.advance_turn()
                await _broadcast(room)

                if room.is_over() and not room.verdict and not room.judging:
                    await _run_final_verdict(room)

            elif t == "rest":
                # rest your case — you forfeit your remaining turns
                room.rested[seat] = True
                room.advance_turn()
                await _broadcast(room)
                if room.is_over() and not room.verdict and not room.judging:
                    await _run_final_verdict(room)

            elif t == "typing":
                other = room.other(seat)
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
