from __future__ import annotations

import asyncio
import json
import os

from openai import AsyncOpenAI
from pydantic import ValidationError

from .models import GameRoom, Mystery, Player, StorytellerResult, Suspect
from .clue_images import pool as clue_image_pool


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
# gpt-4o is more reliable than gpt-4o-mini at returning the StorytellerResult schema in
# json_object mode; mini has been observed dropping fields or returning singular forms.
STORYTELLER_MODEL = os.environ.get("SPGAME_STORYTELLER_MODEL", "gpt-4o")
# Tried in order; first successful response wins. Different OpenAI accounts have access
# to different image models, so we fall back rather than hard-pinning.
IMAGE_MODELS = [m for m in os.environ.get(
    "SPGAME_IMAGE_MODELS", "gpt-image-1,dall-e-2"
).split(",") if m.strip()]


# Schema printed into the prompt so the model knows the JSON shape to return.
# Kept in sync with models.Mystery / models.StorytellerResult by hand.
MYSTERY_JSON_SCHEMA = """{
  "title": "string",
  "setting": "string (1-2 sentences setting time + place)",
  "victim": "string (who was killed and how)",
  "suspects": [
    {
      "id": "s1",
      "name": "string",
      "role": "string",
      "description": "string",
      "alibi": "string",
      "gender": "male | female (required — picks the suspect's portrait)",
      "age_range": "20s | 30s | 40s | 50s | 60s (required — picks the suspect's portrait)"
    }
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
- Each suspect MUST include a `gender` ("male" or "female") and an `age_range` ("20s", "30s", "40s", "50s", or "60s"). These pick the suspect's portrait from a fixed pool, so be diverse — vary genders and ages across the cast.
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


def _mystery_for_llm(mystery: Mystery) -> dict:
    """Serialize a mystery for prompt inclusion, stripping bulky fields the LLM doesn't need.

    Critically: image_url holds a base64 JPEG (~100-200KB) which would blow the context window
    if we left it in the prompt.
    """
    d = mystery.model_dump()
    for s in d.get("suspects", []):
        s.pop("image_url", None)
    return d


def _storyteller_context_message(mystery: Mystery, player_discovered: list[str]) -> str:
    """The mystery payload is sent as a system message so it's eligible for caching across turns."""
    mystery_json = json.dumps(_mystery_for_llm(mystery), indent=2)
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
    last_raw: str = ""
    for i in range(attempts):
        raw = await call()
        last_raw = raw
        try:
            return model_cls.model_validate_json(raw)
        except ValidationError as e:
            last_err = e
            print(f"[parse-retry] {model_cls.__name__} validation failed attempt {i + 1}: {e}\nraw: {raw[:500]}", flush=True)
            continue
        except json.JSONDecodeError as e:
            last_err = e
            print(f"[parse-retry] {model_cls.__name__} json decode failed attempt {i + 1}: {e}\nraw: {raw[:500]}", flush=True)
            continue
    raise RuntimeError(f"LLM returned invalid {model_cls.__name__}: {last_err} (raw: {last_raw[:200]!r})")


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
            temperature=0.8,
            max_tokens=4000,
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
            temperature=0.7,
            max_tokens=800,
        )
        return resp.choices[0].message.content or "{}"

    try:
        return await asyncio.wait_for(
            _parse_with_retry(_call, StorytellerResult), timeout=30.0
        )
    except Exception as e:
        import traceback
        print(
            f"[storyteller turn failure for player={player.name}] {type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}",
            flush=True,
        )
        # Last-resort fallback so a flaky model call doesn't deadlock the game
        return StorytellerResult(
            reply=f"(The storyteller pauses, gathering their thoughts... [{type(e).__name__}]) Try asking again.",
            revealed_clue_ids=[],
            story_progress_bonus=0,
        )


NARRATION_SYSTEM = """You are the storyteller opening a noir mystery game. Given the full mystery details,
write a 500-600 word atmospheric opening narration addressed to the players, as the game master
addressing a group of detectives gathered at the scene.

Cover, in order:

1. The setting and mood (when and where, the weather, the ambient details).
2. The victim and how/when/where the body was found.
3. Each suspect by NAME — their role, their known whereabouts at the time of the crime, and the
   claimed alibi. Use their FULL NAME the first time you mention them in the narration.
4. The connections, tensions, or known relationships between characters.
5. End with a single haunting sentence inviting the players to begin investigating.

Constraints:
- 500-600 words. Tight prose, second-person address ("you find yourself..."), evocative noir voice.
- Do NOT reveal who the culprit is, and do NOT reveal the motive directly.
- Refer to every suspect by name at least once. Use exactly the names provided in the mystery JSON;
  do not invent new characters or aliases.
- No markdown, no headers, no bullet points — just flowing prose paragraphs.
- Period-appropriate language matching the setting."""


async def stream_opening_narration(mystery: Mystery, on_chunk) -> None:
    """Stream the opening narration token-by-token. on_chunk(text: str) is called per delta."""
    client = get_client()
    mystery_view = _mystery_for_llm(mystery)
    # Strip the secrets from what the model sees just in case (it knows better but defense in depth)
    mystery_view.pop("culprit_id", None)
    mystery_view.pop("motive", None)
    user_content = (
        "Open the case with a 500-600 word noir narration. Here is the full mystery context:\n\n"
        f"```json\n{json.dumps(mystery_view, indent=2)}\n```"
    )

    async def _run() -> None:
        stream = await client.chat.completions.create(
            model=STORYTELLER_MODEL,
            messages=[
                {"role": "system", "content": NARRATION_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            stream=True,
            max_tokens=1500,
            temperature=0.8,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                await on_chunk(delta)

    await asyncio.wait_for(_run(), timeout=90.0)


CLUE_IMAGE_ASSIGN_SYSTEM = """You match crime-scene clues to images from a fixed catalog.

Each clue is a short evidence description. Each catalog entry has an id, a title, and tags.
For EVERY clue, pick the single best matching image_id, even if the match is only thematic
or approximate. Examples of acceptable softer matches:
- "a torn page from the victim's diary" → charred_fragment or letter_torn (paper evidence)
- "a tarot card warning of betrayal" → photograph_old or theatre_ticket (any flat card prop)
- "a half-eaten meal abandoned on the bar" → whiskey_bottle (closest scene-of-evidence prop)
- "a strand of hair on the windowsill" → lock_of_hair
- "a peculiar smudge on the doorknob" → fingerprint smudge → use bloodstain_floor or ash_pile

Hard rule: return a real image_id from the catalog for every clue. NEVER return null.
If truly nothing fits, pick `charred_fragment` as the generic "mysterious evidence" fallback.

Return only a JSON object: {"assignments": [{"clue_id": "c1", "image_id": "knife_bloodied"}, ...]}.
The image_id MUST be one from the catalog."""


async def assign_clue_images(mystery: Mystery) -> None:
    """Use an LLM to map each clue in the mystery to the best-matching image_id from the
    static catalog, then populate clue.image_url + clue.image_title in place."""
    if not clue_image_pool.entries:
        return
    if not mystery.clues:
        return

    client = get_client()
    catalog = clue_image_pool.catalog()
    clues_payload = [
        {"clue_id": c.id, "text": c.text}
        for c in mystery.clues
    ]
    user_msg = (
        f"Catalog of available images:\n\n{catalog}\n\n"
        f"Clues to match:\n\n```json\n{json.dumps(clues_payload, indent=2)}\n```"
    )

    async def _call() -> str:
        resp = await client.chat.completions.create(
            model=STORYTELLER_MODEL,
            messages=[
                {"role": "system", "content": CLUE_IMAGE_ASSIGN_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2000,
        )
        return resp.choices[0].message.content or "{}"

    try:
        raw = await asyncio.wait_for(_call(), timeout=30.0)
        data = json.loads(raw)
    except Exception as e:
        print(f"[clue image assignment failed] {type(e).__name__}: {e}", flush=True)
        return

    assignments = data.get("assignments") or data.get("matches") or []
    by_clue_id = {a.get("clue_id"): a.get("image_id") for a in assignments if isinstance(a, dict)}
    fallback_id = "charred_fragment" if "charred_fragment" in clue_image_pool.by_id else (
        clue_image_pool.entries[0]["id"] if clue_image_pool.entries else None
    )
    for clue in mystery.clues:
        image_id = by_clue_id.get(clue.id)
        if not image_id or image_id not in clue_image_pool.by_id:
            image_id = fallback_id
        if image_id:
            clue.image_url = clue_image_pool.url_for(image_id)
            clue.image_title = clue_image_pool.title_for(image_id)


async def generate_suspect_portrait(suspect: Suspect, setting: str) -> str:
    """Generate a portrait, trying each model in IMAGE_MODELS in turn. Returns either a remote
    URL or a `data:image/...;base64,...` URL depending on which model responded."""
    client = get_client()
    prompt = (
        f"Period character portrait of {suspect.name}, {suspect.role}. "
        f"{suspect.description}. Setting context: {setting}. "
        f"Style: moody noir illustration, painterly, dramatic chiaroscuro lighting, "
        f"head-and-shoulders framing, period-accurate clothing, neutral background. "
        f"Do not include any text, letters, or signatures in the image."
    )

    last_err: Exception | None = None
    for model in IMAGE_MODELS:
        try:
            if model == "gpt-image-1":
                resp = await asyncio.wait_for(
                    client.images.generate(
                        model="gpt-image-1",
                        prompt=prompt,
                        size="1024x1024",
                        quality="low",
                        output_format="jpeg",
                        output_compression=70,
                        n=1,
                    ),
                    timeout=90.0,
                )
                b64 = resp.data[0].b64_json
                if b64:
                    return f"data:image/jpeg;base64,{b64}"
                raise RuntimeError("gpt-image-1 returned no b64_json")
            else:
                # dall-e-2 / dall-e-3 path — returns a temporary URL
                resp = await asyncio.wait_for(
                    client.images.generate(
                        model=model,
                        prompt=prompt,
                        size="512x512" if model == "dall-e-2" else "1024x1024",
                        n=1,
                    ),
                    timeout=90.0,
                )
                url = resp.data[0].url
                if url:
                    return url
                raise RuntimeError(f"{model} returned no URL")
        except Exception as e:
            last_err = e
            print(f"[portrait model {model} unavailable] {type(e).__name__}: {e}", flush=True)
            continue

    raise RuntimeError(f"all portrait models failed; last error: {last_err}")
