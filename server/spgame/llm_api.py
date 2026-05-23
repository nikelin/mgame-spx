from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import events as ev
from . import scoring, storyteller
from .models import GameRoom, Player
from .rooms import store


router = APIRouter(prefix="/llm", tags=["llm-direct"])


def _md(text: str) -> Response:
    return Response(content=text, media_type="text/markdown; charset=utf-8")


def _player_by_token(room: GameRoom, token: str) -> Player:
    p = store.player_by_token(room, token)
    if p is None:
        raise HTTPException(403, "invalid token for this room")
    return p


def _base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def _briefing(base: str, room_code: str | None = None) -> str:
    # If a room code was passed in the URL, bake it into the curl examples so the LLM doesn't
    # have to figure out where to put it. Otherwise show the generic placeholder.
    code_for_examples = room_code or "ABCD"
    invite_banner = (
        f"## You were invited to room `{code_for_examples}`\n\n"
        f"Use this exact code in the calls below. Pick any name you like for your player.\n\n"
        if room_code else ""
    )
    return f"""# spgame — LLM-direct play

You are reading the live API for a multiplayer mystery game. A human at the UI has created a room with a 4-character code (e.g. `AB9X`). You can join as a player and play entirely through this HTTP surface — no browser needed.

{invite_banner}## How to play

You investigate a procedurally generated whodunit. Each turn, you message an LLM **storyteller** that reveals clues based on what you ask. You score points for clues you uncover (5-25 each). When you think you know the culprit, **accuse** them. A correct accusation wins +50 points and ends the game; a wrong one costs you 10 points (max 3 accusations per player).

## Quickstart

1. **Join the room.** Pass the room code as `room=` (or `code=`).

   ```bash
   curl -s -X POST {base}/llm/join \\
     -H 'Content-Type: application/json' \\
     -d '{{"room":"{code_for_examples}","name":"Sherlock"}}'
   ```

   Or in one GET (handy when the host gives you a shareable link):

   ```bash
   curl -s '{base}/llm/join?room={code_for_examples}&name=Sherlock'
   ```

   Save the returned `token` — it authenticates every later call.

2. **Ask the storyteller something.** Be specific (about a scene, suspect, or object). Vague questions reveal little.

   ```bash
   curl -s -X POST {base}/llm/say \\
     -H 'Content-Type: application/json' \\
     -d '{{"token":"...","text":"Examine the desk in the study for anything unusual."}}'
   ```

3. **Check recent activity** (other players' clues and points). Use this between turns; it long-polls for up to ~20s.

   ```bash
   curl -s '{base}/llm/poll?token=...&since=0&wait=20'
   ```

4. **Accuse the culprit.** Use the suspect's exact name (case-insensitive).

   ```bash
   curl -s -X POST {base}/llm/accuse \\
     -H 'Content-Type: application/json' \\
     -d '{{"token":"...","suspect_name":"Vivien Marlowe"}}'
   ```

Every response from this API includes a **Next actions** footer with concrete next calls. You don't need to memorize anything.

## Endpoint reference

- `GET  /llm?room=ABCD`                  — this page (optionally room-aware)
- `GET  /llm/join?room=ABCD&name=Bot`    — one-GET join (convenience for chat sessions)
- `POST /llm/join`                       — JSON body `{{room, name}}`
- `POST /llm/say`                        — JSON body `{{token, text}}`
- `POST /llm/accuse`                     — JSON body `{{token, suspect_name}}` (or `suspect_id`)
- `GET  /llm/poll?token=...&since=N`     — long-poll for new public events

## Scoring rules

- Reveal a clue: **+points** (5-25, set per clue)
- Insightful question: **+0-5** bonus
- Wrong accusation: **-10** (max 3 wrong tries)
- Correct accusation: **+50** and you win

Good luck, detective."""


def _state_block(room: GameRoom, player: Player) -> str:
    m = room.mystery
    lines = [
        f"## Room `{room.code}` — status: {room.status}",
        f"You are **{player.name}** ({player.points} pts, {player.accusations_used}/3 accusations used).",
        "",
    ]
    if m is not None:
        lines += [
            f"### Mystery: {m.title}",
            f"_{m.setting}_",
            "",
            f"**Victim:** {m.victim}",
            "",
            "### Suspects",
        ]
        for s in m.suspects:
            lines.append(f"- **{s.name}** — {s.role}. {s.description} _Alibi:_ {s.alibi}")
        lines += ["", "### Scenes"]
        for sc in m.scenes:
            lines.append(f"- **{sc.name}** — {sc.description}")
        lines += ["", "### Your discovered clues"]
        if not player.discovered_clue_ids:
            lines.append("_(none yet — ask the storyteller about scenes, suspects, or objects)_")
        else:
            for c in m.clues:
                if c.id in player.discovered_clue_ids:
                    lines.append(f"- ({c.points} pts) {c.text}")
    lines += ["", "### Leaderboard"]
    for row in scoring.leaderboard(room):
        marker = " ← you" if row["id"] == player.id else ""
        lines.append(f"- {row['name']}: {row['points']} pts{marker}")
    return "\n".join(lines)


def _next_actions(base: str, token: str, code: str, status: str) -> str:
    if status == "over":
        return f"""## Next actions

The game is over. You can poll for any final events:

```bash
curl -s '{base}/llm/poll?token={token}&since=0&wait=5'
```
"""
    return f"""## Next actions

**Ask the storyteller** (specific scene/suspect/object = better):

```bash
curl -s -X POST {base}/llm/say -H 'Content-Type: application/json' \\
  -d '{{"token":"{token}","text":"Your in-character question here"}}'
```

**Accuse** when you're sure (use suspect's exact name):

```bash
curl -s -X POST {base}/llm/accuse -H 'Content-Type: application/json' \\
  -d '{{"token":"{token}","suspect_name":"NAME"}}'
```

**Long-poll for what other players are doing:**

```bash
curl -s '{base}/llm/poll?token={token}&since=0&wait=20'
```
"""


def _transcript(room: GameRoom, player_id: str) -> list[dict]:
    """Storyteller transcript shared with the human-UI path — lives on the room so it
    persists across reloads alongside the rest of the game state."""
    return room.transcripts.setdefault(player_id, [])


# -------- Endpoints --------


@router.get("")
async def llm_briefing(
    request: Request,
    room: str | None = Query(default=None),
    code: str | None = Query(default=None),
) -> Response:
    # Either ?room= or ?code= is accepted (we use both naming conventions in different places).
    room_code = (room or code or "").upper().strip() or None
    return _md(_briefing(_base_url(request), room_code))


@router.get("/join")
async def llm_join_get(
    request: Request,
    name: str,
    room: str | None = Query(default=None),
    code: str | None = Query(default=None),
) -> Response:
    room_code = room or code
    if not room_code:
        raise HTTPException(400, "missing ?room= (or ?code=) query parameter")
    return await _do_join(request, room_code, name)


@router.post("/join")
async def llm_join_post(request: Request, body: dict) -> Response:
    code = body.get("code") or body.get("room")
    name = body.get("name")
    if not code or not name:
        raise HTTPException(400, "body must include {code (or room), name}")
    return await _do_join(request, code, name)


async def _do_join(request: Request, code: str, name: str) -> Response:
    if store.get(code) is None:
        raise HTTPException(404, f"room {code!r} not found. Ask the host for the correct code.")
    room, player = await store.join_room(code, name)
    broadcaster = ev.broadcaster_for(room.code)
    async with store.lock_for(room.code):
        ev.append_and_publish(
            room, broadcaster, "join",
            {"player_id": player.id, "name": player.name, "via": "llm",
             "leaderboard": scoring.leaderboard(room)},
        )

    base = _base_url(request)
    body = (
        f"# Joined room `{room.code}` as **{player.name}**\n\n"
        f"**Your token:** `{player.token}` (use on every later call)\n"
        f"**Your player id:** `{player.id}`\n\n"
        f"{_state_block(room, player)}\n\n"
        f"{_next_actions(base, player.token, room.code, room.status)}"
    )
    return _md(body)


@router.post("/say")
async def llm_say(request: Request, body: dict) -> Response:
    token = body.get("token")
    text = body.get("text")
    if not token or not text:
        raise HTTPException(400, "body must include {token, text}")

    code = body.get("code")
    room = _find_room_for_token(code, token)
    player = _player_by_token(room, token)
    if room.status != "playing":
        raise HTTPException(409, f"game is {room.status}")

    broadcaster = ev.broadcaster_for(room.code)
    transcript = _transcript(room, player.id)
    last_seen = int(body.get("since") or 0)

    async with store.lock_for(room.code):
        ev.append_and_publish(
            room, broadcaster, "message",
            {"player_id": player.id, "name": player.name, "text": text, "role": "player"},
        )

    result = await storyteller.storyteller_turn(room, player, transcript, text)

    async with store.lock_for(room.code):
        transcript.append({"role": "user", "content": f"({player.name}) {text}"})
        transcript.append({"role": "assistant", "content": result.reply})

        clue_points, revealed = scoring.award_clue_points(room, player, result.revealed_clue_ids)
        bonus = max(0, min(5, result.story_progress_bonus))
        player.points += bonus

        ev.append_and_publish(
            room, broadcaster, "story",
            {"player_id": player.id, "name": player.name, "text": result.reply},
        )
        if revealed:
            ev.append_and_publish(
                room, broadcaster, "clue",
                {"clues": revealed, "points_awarded": clue_points},
                private_to=player.id,
            )
            public_summaries = [
                {
                    "clue_id": c["id"],
                    "image_url": c.get("image_url"),
                    "image_title": c.get("image_title"),
                    "scene_id": c.get("scene_id"),
                    "points": c.get("points"),
                }
                for c in revealed
            ]
            ev.append_and_publish(
                room, broadcaster, "clue_found",
                {
                    "player_id": player.id, "name": player.name,
                    "clues": public_summaries,
                    "points_awarded": clue_points,
                    "leaderboard": scoring.leaderboard(room),
                },
            )
        store.persist(room)

        new_events = _recent_public_events(room, last_seen, exclude_player=player.id)

    base = _base_url(request)
    body_md = [f"## Storyteller\n\n> {result.reply}\n"]
    if revealed:
        body_md.append("### Clues you uncovered")
        for c in revealed:
            body_md.append(f"- **(+{c['points']} pts)** {c['text']}")
        body_md.append("")
    if bonus:
        body_md.append(f"_+{bonus} bonus for a good question_\n")
    body_md.append(f"**Your score:** {player.points} pts")
    body_md.append("")
    if new_events:
        body_md.append("## While you were thinking")
        for e in new_events:
            body_md.append(_format_public_event(e))
        body_md.append("")
    body_md.append(_state_block(room, player))
    body_md.append("")
    body_md.append(_next_actions(base, player.token, room.code, room.status))
    return _md("\n".join(body_md))


@router.post("/accuse")
async def llm_accuse(request: Request, body: dict) -> Response:
    token = body.get("token")
    if not token:
        raise HTTPException(400, "body must include {token, suspect_name or suspect_id}")
    suspect_id = body.get("suspect_id")
    suspect_name = body.get("suspect_name")
    if not suspect_id and not suspect_name:
        raise HTTPException(400, "body must include suspect_name or suspect_id")

    code = body.get("code")
    room = _find_room_for_token(code, token)
    player = _player_by_token(room, token)
    if room.status != "playing":
        raise HTTPException(409, f"game is {room.status}")

    if not suspect_id:
        m = room.mystery
        if m is None:
            raise HTTPException(409, "mystery not yet generated")
        match = [s for s in m.suspects if s.name.lower() == str(suspect_name).strip().lower()]
        if not match:
            available = ", ".join(s.name for s in m.suspects)
            raise HTTPException(
                400,
                f"no suspect named {suspect_name!r}. Suspects: {available}",
            )
        suspect_id = match[0].id

    broadcaster = ev.broadcaster_for(room.code)
    async with store.lock_for(room.code):
        result = scoring.resolve_accusation(room, player, suspect_id)
        if result.get("status") == "correct":
            ev.append_and_publish(room, broadcaster, "win", result)
        else:
            ev.append_and_publish(room, broadcaster, "accuse", result)
        store.persist(room)

    base = _base_url(request)
    if result["status"] == "correct":
        winner_lines = [
            f"# 🎉 Correct! **{result['suspect_name']}** was the culprit.",
            "",
            f"**Motive:** {result['motive']}",
            "",
            f"**You won +{result['bonus']} bonus points. Final score: {player.points}.**",
            "",
            "### Final leaderboard",
        ]
        for row in result["leaderboard"]:
            winner_lines.append(f"- {row['name']}: {row['points']} pts")
        return _md("\n".join(winner_lines))
    elif result["status"] == "wrong":
        return _md(
            f"# Wrong accusation\n\n"
            f"**{result['suspect_name']}** is not the culprit. You lose {result['penalty']} points.\n\n"
            f"Accusations remaining: **{result['accusations_remaining']}/3**\n\n"
            f"Your score: {player.points} pts\n\n"
            f"{_next_actions(base, player.token, room.code, room.status)}"
        )
    else:
        return _md(f"# Cannot accuse\n\n{result}\n")


@router.get("/poll")
async def llm_poll(
    request: Request,
    token: str,
    since: int = Query(default=0),
    wait: float = Query(default=20.0, ge=0.0, le=25.0),
    code: str | None = Query(default=None),
) -> Response:
    room = _find_room_for_token(code, token)
    player = _player_by_token(room, token)

    events_now = _recent_public_events(room, since)
    if not events_now and room.status == "playing":
        broadcaster = ev.broadcaster_for(room.code)
        sid, q = await broadcaster.subscribe(player.id)
        try:
            await asyncio.wait_for(q.get(), timeout=wait)
        except asyncio.TimeoutError:
            pass
        finally:
            await broadcaster.unsubscribe(sid)
        events_now = _recent_public_events(room, since)

    base = _base_url(request)
    lines = [f"# Activity in room `{room.code}` since seq {since}", ""]
    if not events_now:
        lines.append("_(no new public activity)_")
    else:
        for e in events_now:
            lines.append(_format_public_event(e))
    lines.append("")
    lines.append(f"**Next `since`:** `{room.next_seq}` (use this on your next /llm/poll call)")
    lines.append("")
    lines.append(_state_block(room, player))
    lines.append("")
    lines.append(_next_actions(base, player.token, room.code, room.status))
    return _md("\n".join(lines))


# -------- helpers --------


def _find_room_for_token(code: str | None, token: str) -> GameRoom:
    if code:
        room = store.get(code)
        if room is None:
            raise HTTPException(404, f"room {code!r} not found")
        if store.player_by_token(room, token) is None:
            raise HTTPException(403, "invalid token for this room")
        return room
    # No code provided — scan rooms for a matching token. O(rooms*players), fine at hackathon scale.
    for room in store.all_rooms():
        if store.player_by_token(room, token) is not None:
            return room
    raise HTTPException(403, "no room matches this token")


def _recent_public_events(room: GameRoom, since: int, exclude_player: str | None = None) -> list[dict]:
    out: list[dict] = []
    for e in room.events:
        if e.seq <= since:
            continue
        if e.private_to is not None:
            continue
        if exclude_player and e.payload.get("player_id") == exclude_player and e.kind == "message":
            # Don't echo this player's own messages back to them as "while you were thinking"
            continue
        out.append({"seq": e.seq, "kind": e.kind, "payload": e.payload})
    return out


def _format_public_event(e: dict[str, Any]) -> str:
    kind = e["kind"]
    p = e["payload"]
    seq = e["seq"]
    name = p.get("name", "someone")
    if kind == "join":
        return f"- `[{seq}]` **{name}** joined the room."
    if kind == "message":
        role = p.get("role", "player")
        if role == "system":
            return f"- `[{seq}]` _{p.get('text', '')}_"
        return f"- `[{seq}]` **{name}**: {p.get('text', '')}"
    if kind == "story":
        return f"- `[{seq}]` _Storyteller (to {name})_: {p.get('text', '')}"
    if kind == "accuse":
        return (f"- `[{seq}]` **{name}** wrongly accused {p.get('suspect_name')} "
                f"(-{p.get('penalty')} pts).")
    if kind == "win":
        return (f"- `[{seq}]` 🏆 **{name}** correctly accused {p.get('suspect_name')} "
                f"and won the game (+{p.get('bonus')} pts).")
    if kind == "start":
        return f"- `[{seq}]` The mystery **{p.get('title')}** has begun."
    return f"- `[{seq}]` {kind}: {p}"
