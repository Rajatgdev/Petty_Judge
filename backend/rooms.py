"""
Room management for Petty Judge.

In-memory, single-process. Fine for one Render instance, which is what this
runs on. If you ever scale to multiple instances, this is the piece to move
into Redis (room state + a pub/sub relay); nothing else needs to change.

A room holds exactly two seats: 'plaintiff' (the creator) and 'defendant'
(whoever opens the link second). A third opener is rejected.
"""

import secrets
import time
from dataclasses import dataclass, field

ROOM_TTL_SECONDS = 60 * 60  # rooms expire after an hour of inactivity


@dataclass
class Room:
    room_id: str
    created_at: float = field(default_factory=time.time)
    # seat -> {"name": str|None, "case": str|None, "submitted": bool}
    seats: dict = field(default_factory=lambda: {
        "plaintiff": {"name": None, "case": None, "submitted": False},
        "defendant": {"name": None, "case": None, "submitted": False},
    })
    # seat -> live WebSocket (or None)
    sockets: dict = field(default_factory=lambda: {"plaintiff": None, "defendant": None})
    verdict: dict = field(default_factory=dict)  # filled once judged
    judging: bool = False

    def occupied_seats(self):
        return [s for s in ("plaintiff", "defendant") if self.sockets[s] is not None]

    def is_full(self):
        return all(self.sockets[s] is not None for s in ("plaintiff", "defendant"))

    def both_submitted(self):
        return self.seats["plaintiff"]["submitted"] and self.seats["defendant"]["submitted"]

    def public_state(self):
        """What both clients are allowed to see."""
        return {
            "room_id": self.room_id,
            "seats": {
                s: {
                    "name": self.seats[s]["name"],
                    "present": self.sockets[s] is not None,
                    "submitted": self.seats[s]["submitted"],
                }
                for s in ("plaintiff", "defendant")
            },
            "judging": self.judging,
            "verdict": self.verdict or None,
        }


class RoomManager:
    def __init__(self):
        self._rooms: dict[str, Room] = {}

    def create(self) -> Room:
        self._sweep()
        room_id = secrets.token_urlsafe(9)  # ~12 URL-safe chars, unguessable
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
        """Atomically give the socket the next free seat. Returns seat name,
        or None if the room is already full (the third-person reject)."""
        for seat in ("plaintiff", "defendant"):
            if room.sockets[seat] is None:
                room.sockets[seat] = socket
                room.created_at = time.time()  # activity bump
                return seat
        return None

    def release_seat(self, room: Room, seat: str, socket) -> None:
        # Only clear if this exact socket still owns the seat (avoids a stale
        # disconnect wiping a fresh reconnect).
        if room.sockets.get(seat) is socket:
            room.sockets[seat] = None

    def _sweep(self):
        now = time.time()
        dead = [rid for rid, r in self._rooms.items()
                if (now - r.created_at) > ROOM_TTL_SECONDS]
        for rid in dead:
            self._rooms.pop(rid, None)


manager = RoomManager()
