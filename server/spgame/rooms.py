from __future__ import annotations

import asyncio
import secrets
import string
import time
import uuid
from typing import Iterable

from . import persistence
from .models import GameRoom, Player


ROOM_CODE_ALPHABET = string.ascii_uppercase + string.digits
ROOM_CODE_LEN = 4
ROOM_IDLE_TIMEOUT_S = 30 * 60  # GC abandoned rooms after 30 minutes idle


class GameStartedError(Exception):
    """Raised by join_room when a new (non-resuming) player tries to join an in-progress game."""


class RoomStore:
    """In-memory store of GameRoom objects with per-room asyncio locks. Backed by JSON-per-room
    persistence on SPX local-fs so rooms survive container restarts."""

    def __init__(self) -> None:
        self._rooms: dict[str, GameRoom] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._store_lock = asyncio.Lock()

    def hydrate_from_disk(self) -> int:
        """Load all persisted rooms into memory. Call once at startup."""
        loaded = persistence.load_all_rooms()
        for code, room in loaded.items():
            self._rooms[code] = room
            self._locks[code] = asyncio.Lock()
        return len(loaded)

    def persist(self, room: GameRoom) -> None:
        """Atomic-write this room's JSON snapshot. Safe to call under the room lock."""
        persistence.save_room(room)

    @staticmethod
    def _new_code() -> str:
        return "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LEN))

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(24)

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    async def create_room(
        self, host_name: str, openai_api_key: str | None = None,
    ) -> tuple[GameRoom, Player]:
        async with self._store_lock:
            # Retry on the unlikely event of a code collision
            for _ in range(8):
                code = self._new_code()
                if code not in self._rooms:
                    break
            else:
                raise RuntimeError("could not allocate a unique room code; try again")

            host = Player(
                id=self._new_id("p"),
                name=host_name.strip(),
                token=self._new_token(),
            )
            room = GameRoom(
                code=code,
                host_id=host.id,
                players={host.id: host},
                openai_api_key=openai_api_key,
            )
            self._rooms[code] = room
            self._locks[code] = asyncio.Lock()
            persistence.save_room(room)
            return room, host

    async def join_room(self, code: str, name: str) -> tuple[GameRoom, Player]:
        """Join (or resume) a room. Name is the unique identifier.
        Raises GameStartedError if a new player tries to join after the game has started."""
        room = self.get(code)
        if room is None:
            raise KeyError(code)
        async with self.lock_for(code):
            # Reuse existing player slot if the same name rejoins (case-insensitive match).
            # This is the "resume" path — always allowed, regardless of game status.
            for existing in room.players.values():
                if existing.name.lower() == name.strip().lower():
                    existing.last_active = time.time()
                    return room, existing
            # New player — only allowed while the room is still in the lobby. Once the
            # game starts, the player roster is locked.
            if room.status != "lobby":
                raise GameStartedError(
                    f"game already {room.status} — no new players can join. "
                    "If you played earlier, re-enter your original name to resume."
                )
            player = Player(
                id=self._new_id("p"),
                name=name.strip(),
                token=self._new_token(),
            )
            room.players[player.id] = player
            room.last_activity = time.time()
            persistence.save_room(room)
            return room, player

    def get(self, code: str) -> GameRoom | None:
        return self._rooms.get(code.upper())

    def lock_for(self, code: str) -> asyncio.Lock:
        lock = self._locks.get(code.upper())
        if lock is None:
            raise KeyError(code)
        return lock

    def player_by_token(self, room: GameRoom, token: str) -> Player | None:
        for p in room.players.values():
            if secrets.compare_digest(p.token, token):
                return p
        return None

    def all_rooms(self) -> Iterable[GameRoom]:
        return list(self._rooms.values())

    async def gc_idle_rooms(self) -> int:
        """Drop rooms that have had no activity in ROOM_IDLE_TIMEOUT_S. Returns count dropped."""
        cutoff = time.time() - ROOM_IDLE_TIMEOUT_S
        async with self._store_lock:
            dead = [code for code, room in self._rooms.items() if room.last_activity < cutoff]
            for code in dead:
                self._rooms.pop(code, None)
                self._locks.pop(code, None)
                persistence.delete_room(code)
            return len(dead)


store = RoomStore()


async def gc_loop() -> None:
    while True:
        try:
            await asyncio.sleep(300)
            await store.gc_idle_rooms()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Don't kill the GC loop on transient errors
            await asyncio.sleep(60)
