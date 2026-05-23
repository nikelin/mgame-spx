from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator

from .models import Event, GameRoom


class Broadcaster:
    """Per-room SSE broadcaster. One queue per subscriber (player_id or anonymous viewer)."""

    def __init__(self) -> None:
        # subscribers keyed by a unique id (player_id + serial) so the same player can have
        # multiple tabs without losing events to one another.
        self._subs: dict[str, tuple[str | None, asyncio.Queue[Event]]] = {}
        self._lock = asyncio.Lock()
        self._next_sub_serial = 0

    async def subscribe(self, player_id: str | None) -> tuple[str, asyncio.Queue[Event]]:
        async with self._lock:
            self._next_sub_serial += 1
            sid = f"{player_id or 'anon'}:{self._next_sub_serial}"
            q: asyncio.Queue[Event] = asyncio.Queue(maxsize=512)
            self._subs[sid] = (player_id, q)
            return sid, q

    async def unsubscribe(self, sid: str) -> None:
        async with self._lock:
            self._subs.pop(sid, None)

    async def publish(self, event: Event) -> None:
        # Snapshot subs under the lock, deliver outside to avoid holding the lock through awaits
        async with self._lock:
            subs = list(self._subs.items())
        for sid, (player_id, q) in subs:
            if event.private_to is not None and event.private_to != player_id:
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Slow consumer — drop. They can resync via /state if needed.
                pass


def append_and_publish(
    room: GameRoom,
    broadcaster: Broadcaster,
    kind: str,
    payload: dict,
    private_to: str | None = None,
) -> Event:
    """Append an event to the room's log, assign seq, and fan out. Caller must hold the room lock."""
    event = Event(
        seq=room.next_seq,
        ts=time.time(),
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
        private_to=private_to,
    )
    room.next_seq += 1
    room.events.append(event)
    room.last_activity = event.ts
    # Schedule the publish; we hold the room lock here, but publish itself uses a separate lock
    asyncio.create_task(broadcaster.publish(event))
    return event


def event_to_sse(event: Event) -> str:
    """Serialize an Event to a Server-Sent Events frame."""
    data = json.dumps(
        {
            "seq": event.seq,
            "ts": event.ts,
            "kind": event.kind,
            "payload": event.payload,
        },
        separators=(",", ":"),
    )
    return f"id: {event.seq}\nevent: {event.kind}\ndata: {data}\n\n"


async def sse_stream(
    room: GameRoom,
    broadcaster: Broadcaster,
    player_id: str | None,
    since: int = 0,
) -> AsyncIterator[bytes]:
    """SSE generator: replay missed events (filtered), then stream live events with keepalive."""
    sid, queue = await broadcaster.subscribe(player_id)
    try:
        # Replay anything since the requested seq, filtered for private_to
        for ev in room.events:
            if ev.seq <= since:
                continue
            if ev.private_to is not None and ev.private_to != player_id:
                continue
            yield event_to_sse(ev).encode("utf-8")

        # Initial flush comment to defeat any reverse-proxy buffering
        yield b": connected\n\n"

        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield event_to_sse(ev).encode("utf-8")
            except asyncio.TimeoutError:
                yield b": ping\n\n"
    finally:
        await broadcaster.unsubscribe(sid)


_room_broadcasters: dict[str, Broadcaster] = {}


def broadcaster_for(code: str) -> Broadcaster:
    code = code.upper()
    b = _room_broadcasters.get(code)
    if b is None:
        b = Broadcaster()
        _room_broadcasters[code] = b
    return b
