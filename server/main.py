from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse

from spgame import events as ev
from spgame import scoring, storyteller
from spgame.llm_api import router as llm_router
from spgame.models import (
    AccuseReq,
    CreateRoomReq,
    JoinRoomReq,
    MessageReq,
    StartReq,
)
from spgame.rooms import gc_loop, store


@asynccontextmanager
async def lifespan(app: FastAPI):
    gc_task = asyncio.create_task(gc_loop())
    try:
        yield
    finally:
        gc_task.cancel()
        try:
            await gc_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="spgame backend", lifespan=lifespan)

ui_origin = os.environ.get("UI_ORIGIN", "")
cors_origins = [ui_origin] if ui_origin else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(llm_router)


def _require_room(code: str):
    room = store.get(code)
    if room is None:
        raise HTTPException(404, f"room {code!r} not found")
    return room


def _require_player(room, token: str):
    player = store.player_by_token(room, token)
    if player is None:
        raise HTTPException(403, "invalid token for this room")
    return player


# Per-player transcript cache (not in the GameRoom model so we don't pollute /state with it)
_transcripts: dict[str, dict[str, list[dict]]] = {}


def _get_transcript(room_code: str, player_id: str) -> list[dict]:
    return _transcripts.setdefault(room_code, {}).setdefault(player_id, [])


# -------- Endpoints --------


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/")
async def root():
    return {
        "name": "spgame backend",
        "endpoints": [
            "POST /rooms",
            "POST /rooms/{code}/join",
            "GET /rooms/{code}/state",
            "POST /rooms/{code}/start",
            "POST /rooms/{code}/message",
            "POST /rooms/{code}/accuse",
            "GET /rooms/{code}/events?token=&since=  (SSE)",
            "GET /rooms/{code}/events_poll?token=&since=&wait=  (long-poll fallback)",
            "GET /sse-test  (proxy buffering diagnostic)",
            "GET /llm  (LLM-direct play briefing)",
        ],
    }


@app.post("/rooms")
async def create_room(body: CreateRoomReq):
    room, host = await store.create_room(body.host_name)
    return {
        "code": room.code,
        "player_id": host.id,
        "token": host.token,
        "is_host": True,
    }


@app.post("/rooms/{code}/join")
async def join_room(code: str, body: JoinRoomReq):
    if store.get(code) is None:
        raise HTTPException(404, f"room {code!r} not found")
    room, player = await store.join_room(code, body.name)
    broadcaster = ev.broadcaster_for(room.code)
    async with store.lock_for(room.code):
        ev.append_and_publish(
            room, broadcaster, "join",
            {"player_id": player.id, "name": player.name, "leaderboard": scoring.leaderboard(room)},
        )
    return {
        "code": room.code,
        "player_id": player.id,
        "token": player.token,
        "is_host": player.id == room.host_id,
    }


@app.get("/rooms/{code}/state")
async def get_state(code: str, token: str | None = Query(default=None)):
    room = _require_room(code)
    state = room.public_state()
    # If a token is supplied, attach this player's private view
    if token:
        player = store.player_by_token(room, token)
        if player is not None:
            state["you"] = {
                "id": player.id,
                "name": player.name,
                "points": player.points,
                "accusations_used": player.accusations_used,
                "discovered_clue_ids": sorted(player.discovered_clue_ids),
                "discovered_clues": _player_clues(room, player),
                "is_host": player.id == room.host_id,
            }
    return state


def _player_clues(room, player) -> list[dict]:
    if room.mystery is None:
        return []
    return [
        {"id": c.id, "text": c.text, "points": c.points, "scene_id": c.scene_id}
        for c in room.mystery.clues
        if c.id in player.discovered_clue_ids
    ]


@app.post("/rooms/{code}/start")
async def start_game(code: str, body: StartReq):
    room = _require_room(code)
    player = _require_player(room, body.token)
    if player.id != room.host_id:
        raise HTTPException(403, "only the host can start the game")
    if room.status != "lobby":
        raise HTTPException(409, f"game already {room.status}")

    broadcaster = ev.broadcaster_for(room.code)
    # Generate outside the lock — it can take 30+ seconds — but guard against double-start
    async with store.lock_for(room.code):
        if room.status != "lobby":
            raise HTTPException(409, f"game already {room.status}")
        room.status = "playing"  # tentatively, to prevent races
        ev.append_and_publish(room, broadcaster, "story", {"text": "Generating the mystery..."})

    try:
        mystery = await storyteller.generate_mystery(theme=body.theme)
    except Exception as e:
        async with store.lock_for(room.code):
            room.status = "lobby"
        import traceback
        tb = traceback.format_exc()
        print(f"[mystery gen failure]\n{tb}", flush=True)
        raise HTTPException(500, f"mystery generation failed: {type(e).__name__}: {e}")

    async with store.lock_for(room.code):
        room.mystery = mystery
        ev.append_and_publish(
            room, broadcaster, "start",
            {
                "title": mystery.title,
                "setting": mystery.setting,
                "victim": mystery.victim,
                "suspects": [s.model_dump() for s in mystery.suspects],
                "scenes": [s.model_dump() for s in mystery.scenes],
                "clue_count": len(mystery.clues),
            },
        )
    return {"status": "playing", "title": mystery.title}


@app.post("/rooms/{code}/message")
async def send_message(code: str, body: MessageReq):
    room = _require_room(code)
    player = _require_player(room, body.token)
    if room.status != "playing":
        raise HTTPException(409, f"game is {room.status}")

    broadcaster = ev.broadcaster_for(room.code)
    transcript = _get_transcript(room.code, player.id)

    # Echo the player's message publicly so other players can see chatter (without the clue
    # reveal text).
    async with store.lock_for(room.code):
        ev.append_and_publish(
            room, broadcaster, "message",
            {"player_id": player.id, "name": player.name, "text": body.text, "role": "player"},
        )

    # Storyteller call happens outside the room lock; we serialize per-player via the transcript
    # only growing on this code path (single producer).
    result = await storyteller.storyteller_turn(room, player, transcript, body.text)

    async with store.lock_for(room.code):
        # Update player's transcript with what actually happened
        transcript.append({"role": "user", "content": f"({player.name}) {body.text}"})
        transcript.append({"role": "assistant", "content": result.reply})

        clue_points, revealed = scoring.award_clue_points(room, player, result.revealed_clue_ids)
        player.points += max(0, min(5, result.story_progress_bonus))

        # Public storyteller reply
        ev.append_and_publish(
            room, broadcaster, "story",
            {"player_id": player.id, "name": player.name, "text": result.reply},
        )

        # Private clue payload only to the discovering player
        if revealed:
            ev.append_and_publish(
                room, broadcaster, "clue",
                {"clues": revealed, "points_awarded": clue_points},
                private_to=player.id,
            )
            # Public notification — others see that someone got a clue, but not what
            ev.append_and_publish(
                room, broadcaster, "message",
                {
                    "player_id": player.id,
                    "name": player.name,
                    "text": f"{player.name} uncovered {len(revealed)} clue(s) (+{clue_points} pts).",
                    "role": "system",
                    "leaderboard": scoring.leaderboard(room),
                },
            )

    return {
        "reply": result.reply,
        "revealed_clues": revealed,
        "points": player.points,
        "story_progress_bonus": max(0, min(5, result.story_progress_bonus)),
    }


@app.post("/rooms/{code}/accuse")
async def accuse(code: str, body: AccuseReq):
    room = _require_room(code)
    player = _require_player(room, body.token)
    if room.status != "playing":
        raise HTTPException(409, f"game is {room.status}")

    broadcaster = ev.broadcaster_for(room.code)
    async with store.lock_for(room.code):
        result = scoring.resolve_accusation(room, player, body.suspect_id)
        if result.get("status") == "correct":
            ev.append_and_publish(room, broadcaster, "win", result)
        else:
            ev.append_and_publish(room, broadcaster, "accuse", result)
    return result


@app.get("/rooms/{code}/events")
async def stream_events(
    code: str,
    request: Request,
    token: str | None = Query(default=None),
    since: int = Query(default=0),
):
    room = _require_room(code)
    player_id = None
    if token:
        player = store.player_by_token(room, token)
        if player is not None:
            player_id = player.id

    # Honor Last-Event-ID if the browser sent one on reconnect
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            since = max(since, int(last_event_id))
        except ValueError:
            pass

    broadcaster = ev.broadcaster_for(room.code)

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        ev.sse_stream(room, broadcaster, player_id, since),
        media_type="text/event-stream",
        headers=headers,
    )


@app.get("/rooms/{code}/events_poll")
async def poll_events(
    code: str,
    token: str | None = Query(default=None),
    since: int = Query(default=0),
    wait: float = Query(default=20.0, ge=0.0, le=25.0),
):
    """Long-poll fallback. Returns immediately if there are events past `since`,
    otherwise waits up to `wait` seconds for the first new event then drains."""
    room = _require_room(code)
    player_id = None
    if token:
        player = store.player_by_token(room, token)
        if player is not None:
            player_id = player.id

    def filtered_since(seq: int) -> list[dict]:
        out = []
        for e in room.events:
            if e.seq <= seq:
                continue
            if e.private_to is not None and e.private_to != player_id:
                continue
            out.append(
                {"seq": e.seq, "ts": e.ts, "kind": e.kind, "payload": e.payload}
            )
        return out

    immediate = filtered_since(since)
    if immediate:
        return {"events": immediate, "next_seq": room.next_seq}

    broadcaster = ev.broadcaster_for(room.code)
    sid, queue = await broadcaster.subscribe(player_id)
    try:
        await asyncio.wait_for(queue.get(), timeout=wait)
    except asyncio.TimeoutError:
        return {"events": [], "next_seq": room.next_seq}
    finally:
        await broadcaster.unsubscribe(sid)

    return {"events": filtered_since(since), "next_seq": room.next_seq}


@app.get("/sse-test")
async def sse_test():
    """Proxy-buffering diagnostic. Emits one frame per second for 30s."""
    async def gen():
        yield b": connected\n\n"
        for i in range(30):
            yield f"event: tick\ndata: {{\"i\":{i}}}\n\n".encode()
            await asyncio.sleep(1.0)
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_request: Request, exc: RuntimeError):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
