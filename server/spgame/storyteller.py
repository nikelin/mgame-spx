from __future__ import annotations

import asyncio
import json
import os

from openai import AsyncOpenAI
from pydantic import ValidationError

from .models import GameRoom, Mystery, Player, StorytellerResult


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Run `spx env set OPENAI_API_KEY=...` "
                "before deploying, or export it locally for development."
            )
        _client = AsyncOpenAI()
    return _client


MYSTERY_MODEL = os.environ.get("SPGAME_MYSTERY_MODEL", "gpt-4o")
STORYTELLER_MODEL = os.environ.get("SPGAME_STORYTELLER_MODEL", "gpt-4o-mini")


# Schema printed into the prompt so the model knows the JSON shape to return.
# Kept in sync with models.Mystery / models.StorytellerResult by hand.
MYSTERY_JSON_SCHEMA = """{
  "title": "string",
  "setting": "string (1-2 sentences setting time + place)",
  "victim": "string (who was killed and how)",
  "suspects": [
    {"id": "s1", "name": "string", "role": "string", "description": "string", "alibi": "string"}
  ],
  "scenes": [
    {"id": "sc1", "name": "string", "description": "string"}
  ],
  "clues": [
    {"id": "c1", "text": "string", "points": 15, "linked_suspect_id": "s1 or null", "scene_id": "sc1"}
  ],
  "culprit_id": "s1 (MUST match one of the suspect ids)",
  "motive": "string (must be derivable from at least 2 clues)"
}"""


MYSTERY_GEN_SYSTEM = f"""You are a master mystery game designer creating a noir whodunit for a small group of players.

Design a tight, internally consistent murder mystery. Constraints:

- Setting: pick something atmospheric and varied (Art Deco hotel, transatlantic liner, rural manor, 1920s newsroom, etc.). Avoid stale tropes.
- 4-6 suspects with distinct roles, personalities, and alibis. Give each a memorable name. IDs s1, s2, s3, ...
- 3-5 scenes (locations). IDs sc1, sc2, sc3, ...
- 8-12 total clues, each worth 5-25 points based on how revealing they are. IDs c1, c2, c3, ... Smaller clues are atmospheric or eliminate suspects; bigger clues directly implicate someone.
- Exactly ONE culprit. The motive must be logically reachable from at least 2-3 of the clues.
- Red herrings are welcome: some clues should implicate the wrong suspect, but the culprit_id field must be correct.
- Tone: noir, suspenseful, slightly playful. Period-appropriate language.

Return ONLY valid JSON matching this shape:

```json
{MYSTERY_JSON_SCHEMA}
```

Do not include any prose outside the JSON object."""


STORYTELLER_RESULT_SCHEMA = """{
  "reply": "string (in-character storyteller reply, 1-4 sentences)",
  "revealed_clue_ids": ["c1", "c2"],
  "story_progress_bonus": 0
}"""


STORYTELLER_SYSTEM = f"""You are the storyteller / game master for a multiplayer mystery game.

You guide each player through investigating a pre-generated mystery. The full mystery JSON is provided below; the player sees ONLY what you reveal.

Your job each turn:

1. Read the player's message in character as a noir narrator.
2. Decide if anything they asked about would PLAUSIBLY reveal one or more clues. Use the clue's scene_id and linked_suspect_id to judge relevance:
   - Asking about a specific scene → reveal clues from that scene that fit the question.
   - Asking about a specific suspect → reveal clues linked to that suspect.
   - General/vague questions → reveal at most one minor clue, if any fits, or none.
   - DO NOT reveal a clue the player has already discovered (their discovered IDs are in the user message).
   - Be generous early (when they have 0-1 clues) and stingier later.
3. Write a 1-4 sentence in-character reply weaving in any clues you're revealing. Don't recite clue text verbatim — narrate it.
4. Score story_progress_bonus 0-5 ONLY for genuinely insightful deductions or great questions. Most turns get 0.
5. NEVER name the culprit yourself. Players win by accusing on their own.
6. If asked who the killer is, deflect in character.

Return ONLY valid JSON matching this shape:

```json
{STORYTELLER_RESULT_SCHEMA}
```

Do not include any prose outside the JSON object."""


def _storyteller_context_message(mystery: Mystery, player_discovered: list[str]) -> str:
    """The mystery payload is sent as a system message so it's eligible for caching across turns."""
    mystery_json = mystery.model_dump_json(indent=2)
    return (
        "Here is the full mystery definition. The culprit_id and motive are SECRET — "
        "you know them but must not reveal them.\n\n"
        f"```json\n{mystery_json}\n```\n\n"
        f"This player has already discovered these clue IDs: {player_discovered or '(none yet)'}. "
        "Do not reveal any of these again."
    )


async def _parse_with_retry(call, model_cls, attempts: int = 2):
    """Call the LLM and validate against a Pydantic model, retrying once on validation failure."""
    last_err: Exception | None = None
    for i in range(attempts):
        raw = await call()
        try:
            return model_cls.model_validate_json(raw)
        except ValidationError as e:
            last_err = e
            # On retry, the prompt already asked for the shape; only retry once
            continue
        except json.JSONDecodeError as e:
            last_err = e
            continue
    raise RuntimeError(f"LLM returned invalid {model_cls.__name__}: {last_err}")


async def generate_mystery(theme: str | None = None) -> Mystery:
    client = get_client()
    user_content = "Design a brand new mystery now."
    if theme:
        user_content += f" Theme hint from the host: {theme.strip()}"

    async def _call() -> str:
        resp = await client.chat.completions.create(
            model=MYSTERY_MODEL,
            messages=[
                {"role": "system", "content": MYSTERY_GEN_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.9,
        )
        return resp.choices[0].message.content or "{}"

    return await asyncio.wait_for(_parse_with_retry(_call, Mystery), timeout=90.0)


async def storyteller_turn(
    room: GameRoom,
    player: Player,
    transcript_for_player: list[dict],
    user_text: str,
) -> StorytellerResult:
    if room.mystery is None:
        raise RuntimeError("storyteller_turn called before mystery generation")

    client = get_client()
    context_msg = _storyteller_context_message(room.mystery, sorted(player.discovered_clue_ids))

    messages: list[dict] = [
        {"role": "system", "content": STORYTELLER_SYSTEM},
        {"role": "system", "content": context_msg},
        *transcript_for_player,
        {"role": "user", "content": f"({player.name}) {user_text}"},
    ]

    async def _call() -> str:
        resp = await client.chat.completions.create(
            model=STORYTELLER_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.8,
            max_tokens=600,
        )
        return resp.choices[0].message.content or "{}"

    try:
        return await asyncio.wait_for(
            _parse_with_retry(_call, StorytellerResult), timeout=30.0
        )
    except Exception:
        # Last-resort fallback so a flaky model call doesn't deadlock the game
        return StorytellerResult(
            reply="(The storyteller pauses, gathering their thoughts...) Try asking again.",
            revealed_clue_ids=[],
            story_progress_bonus=0,
        )
