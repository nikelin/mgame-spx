"""JSON-per-room persistence on top of SPX local-fs.

SPX mounts a persistent directory at $SPX_LOCAL_FS (default /data/local-fs on the platform).
We store one file per room: $SPX_LOCAL_FS/rooms/<CODE>.json. The file holds the full GameRoom
state minus the live SSE event log (events are ephemeral — reconnecting clients re-subscribe
and replay state via /state).

For local development without SPX, falls back to ./.local-fs/rooms/.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import GameRoom


def _root() -> Path:
    # SPX sets SPX_LOCAL_FS=/data/local-fs in deployed containers per
    # https://runspx.com/docs/local-fs. Try the env var first, then the documented mount
    # path (in case the env var isn't propagated for some reason), then a local dev fallback.
    env = os.environ.get("SPX_LOCAL_FS")
    if env:
        return Path(env) / "rooms"
    spx_default = Path("/data/local-fs")
    if spx_default.exists():
        return spx_default / "rooms"
    return Path("./.local-fs/rooms")


def _path_for(code: str) -> Path:
    return _root() / f"{code.upper()}.json"


# Fields we drop before writing — we keep events now (so the chat panel hydrates on
# reload) but filter the bulky ones at save time below. EXCLUDED_FIELDS stays empty for
# now; if anything truly runtime-only is added later, exclude it here.
EXCLUDED_FIELDS: set[str] = set()

# Event kinds NOT worth persisting (chatty and redundant — narration is also stored on
# room.narration; suspect_image is also stored on room.mystery.suspects[].image_url).
PERSIST_SKIP_KINDS: set[str] = {"narration_chunk", "narration_end", "suspect_image"}

# Cap the persisted event log so very long games don't grow the JSON file unbounded.
MAX_PERSISTED_EVENTS = 500


def save_room(room: GameRoom) -> None:
    """Atomically persist one room. Safe to call from inside an asyncio task."""
    try:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        data = room.model_dump(mode="json", exclude=EXCLUDED_FIELDS)
        # Trim events: drop bulky kinds (narration_chunk etc.) and cap the tail length so
        # the JSON file stays bounded even across very long games.
        evs = data.get("events") or []
        evs = [e for e in evs if e.get("kind") not in PERSIST_SKIP_KINDS]
        if len(evs) > MAX_PERSISTED_EVENTS:
            evs = evs[-MAX_PERSISTED_EVENTS:]
        data["events"] = evs
        # Atomic write: tmp file in same dir, then os.replace
        fd, tmp_path = tempfile.mkstemp(prefix=f".{room.code}.", suffix=".json.tmp", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, _path_for(room.code))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # Diagnostic: log per-player points/clue counts so we can confirm persisted state
        player_summary = ",".join(
            f"{p['name']}({p['points']}/{len(p['discovered_clue_ids'])})"
            for p in data.get("players", {}).values()
        )
        print(
            f"[persistence] saved {room.code} status={data['status']} players=[{player_summary}] "
            f"narration={len(data.get('narration', ''))}chars done={data.get('narration_done')}",
            flush=True,
        )
    except Exception as e:
        # Persistence failures must not crash the game — log and continue
        print(f"[persistence] save_room({room.code}) failed: {type(e).__name__}: {e}", flush=True)


def delete_room(code: str) -> None:
    try:
        p = _path_for(code)
        if p.exists():
            p.unlink()
    except Exception as e:
        print(f"[persistence] delete_room({code}) failed: {e}", flush=True)


def load_all_rooms() -> dict[str, GameRoom]:
    """Read every room JSON in the persistence root. Skips malformed files."""
    out: dict[str, GameRoom] = {}
    root = _root()
    if not root.exists():
        return out
    for path in root.glob("*.json"):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            room = GameRoom.model_validate(data)
            # Migrate: rooms persisted before the turn-based feature have an empty
            # turn_order. If the game is already past lobby, initialize the rotation
            # from the current players so action endpoints don't reject everyone with
            # "not your turn".
            if room.status != "lobby" and not room.turn_order and room.players:
                ordered = [room.host_id] if room.host_id in room.players else []
                ordered += [pid for pid in room.players.keys() if pid != room.host_id]
                room.turn_order = ordered
                room.current_turn_index = 0
                print(
                    f"[persistence] migrated {room.code}: initialized turn_order from "
                    f"{len(room.players)} player(s)",
                    flush=True,
                )
            out[room.code] = room
            player_summary = ",".join(
                f"{p.name}({p.points}/{len(p.discovered_clue_ids)})"
                for p in room.players.values()
            )
            print(
                f"[persistence] loaded {room.code} status={room.status} players=[{player_summary}]",
                flush=True,
            )
        except Exception as e:
            print(f"[persistence] failed to load {path}: {type(e).__name__}: {e}", flush=True)
    return out


def root_path() -> str:
    return str(_root())
