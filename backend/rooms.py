"""
Room management for Petty Judge.

In-memory, single-process. Fine for one Railway instance. If you ever scale to
multiple instances, this is the piece to move into Redis (room state + a pub/sub
relay); nothing else needs to change.

A room holds exactly two seats: 'plaintiff' (the creator) and 'defendant'
(whoever opens the link second). A third opener is rejected.

The dispute is a TURN-BASED conversation: plaintiff speaks first, then strict
alternation. Each seat gets MAX_MESSAGES turns. After each message the jury is
re-scored (live bar). When both sides have used all their turns — or either
rests their case — the final verdict is rendered.
"""

import secrets
import time
from dataclasses import dataclass, field

ROOM_TTL_SECONDS = 60 * 60  # rooms expire after an hour of inactivity
MAX_MESSAGES = 5            # turns per side before the jury must decide


@dataclass
class Room:
    room_id: str
    created_at: float = field(default_factory=time.time)
    # seat -> {"name": str|None, "present": bool}
    seats: dict = field(default_factory=lambda: {
        "plaintiff": {"name": None},
        "defendant": {"name": None},
    })
    sockets: dict = field(default_factory=lambda: {"plaintiff": None, "defendant": None})

    # conversation state
    transcript: list = field(default_factory=list)   # [{"seat","text"}]
    turn: str = "plaintiff"                            # whose turn it is now
    counts: dict = field(default_factory=lambda: {"plaintiff": 0, "defendant": 0})
    rested: dict = field(default_factory=lambda: {"plaintiff": False, "defendant": False})

    # AI-derived
    summary: dict = field(default_factory=dict)        # {"topic","accusation"}
    jury: dict = field(default_factory=lambda: {"plaintiff_pct": 50, "defendant_pct": 50, "leaning": "even"})
    verdict: dict = field(default_factory=dict)
    judging: bool = False

    # ---- helpers ----
    def is_full(self):
        return all(self.sockets[s] is not None for s in ("plaintiff", "defendant"))

    def both_present(self):
        return self.sockets["plaintiff"] is not None and self.sockets["defendant"] is not None

    def has_opening(self):
        # plaintiff must have spoken at least once before the defendant can
        return self.counts["plaintiff"] >= 1

    def other(self, seat):
        return "defendant" if seat == "plaintiff" else "plaintiff"

    def can_speak(self, seat):
        """Is it this seat's turn, with turns left, and nobody's rested-out?"""
        if self.verdict or self.judging:
            return False
        if self.turn != seat:
            return False
        if self.counts[seat] >= MAX_MESSAGES:
            return False
        return True

    def remaining(self, seat):
        return max(0, MAX_MESSAGES - self.counts[seat])

    def is_over(self):
        """Conversation ends when both rested, or both used every turn."""
        both_rested = self.rested["plaintiff"] and self.rested["defendant"]
        both_maxed = (self.counts["plaintiff"] >= MAX_MESSAGES
                      and self.counts["defendant"] >= MAX_MESSAGES)
        return both_rested or both_maxed

    def advance_turn(self):
        """Hand the turn to the other side, skipping anyone who's done."""
        nxt = self.other(self.turn)
        # if the next side has rested or is maxed, keep it with whoever can still talk
        for _ in range(2):
            if not self.rested[nxt] and self.counts[nxt] < MAX_MESSAGES:
                self.turn = nxt
                return
            nxt = self.other(nxt)
        self.turn = nxt  # nobody can talk; is_over() will catch it

    def public_state(self):
        return {
            "room_id": self.room_id,
            "seats": {
                s: {"name": self.seats[s]["name"], "present": self.sockets[s] is not None}
                for s in ("plaintiff", "defendant")
            },
            "transcript": self.transcript,
            "turn": self.turn,
            "counts": self.counts,
            "rested": self.rested,
            "remaining": {"plaintiff": self.remaining("plaintiff"), "defendant": self.remaining("defendant")},
            "max_messages": MAX_MESSAGES,
            "summary": self.summary or None,
            "jury": self.jury,
            "judging": self.judging,
            "verdict": self.verdict or None,
        }


class RoomManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}

    def create(self) -> Room:
        self._sweep()
        room_id = secrets.token_urlsafe(9)
        room = Room(room_id=room_id)
        self._rooms[room_id] = room
        return room

    def get(self, room_id: str) -> Room | None:
        room = self._rooms.get(room_id)
        if room and (time.time() - room.created_at) > ROOM_TTL_SECONDS:
            self._rooms.pop(room_id, None)
            return None
        return room

    def claim_seat(self, room: Room, socket) -> str | None:
        for seat in ("plaintiff", "defendant"):
            if room.sockets[seat] is None:
                room.sockets[seat] = socket
                room.created_at = time.time()
                return seat
        return None

    def release_seat(self, room: Room, seat: str, socket) -> None:
        if room.sockets.get(seat) is socket:
            room.sockets[seat] = None

    def _sweep(self):
        now = time.time()
        dead = [rid for rid, r in self._rooms.items()
                if (now - r.created_at) > ROOM_TTL_SECONDS]
        for rid in dead:
            self._rooms.pop(rid, None)


manager = RoomManager()
