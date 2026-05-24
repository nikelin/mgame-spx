from __future__ import annotations

import asyncio
import json
import os

from openai import AsyncOpenAI
from pydantic import ValidationError

from .models import GameRoom, Mystery, Player, StorytellerResult, Suspect
from .clue_images import pool as clue_image_pool


_client: AsyncOpenAI | None = None
# Cache room-key clients so we're not creating a new AsyncOpenAI per turn. Tiny in number.
_per_key_clients: dict[str, AsyncOpenAI] = {}


def get_client(api_key: str | None = None) -> AsyncOpenAI:
    """Return an AsyncOpenAI client, preferring an explicit per-room key when supplied,
    otherwise falling back to the server's global OPENAI_API_KEY."""
    if api_key:
        client = _per_key_clients.get(api_key)
        if client is None:
            client = AsyncOpenAI(api_key=api_key)
            _per_key_clients[api_key] = client
        return client
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


MYSTERY_GEN_SYSTEM = f"""You are a satirical chronicler of failed Silicon Valley venture-backed startups, designing a corporate-autopsy investigation game.

Each game is a post-mortem of one dead startup. Players are investigators trying to figure out WHO ultimately killed the company. Constraints:

- **Setting**: a specific kind of failed venture-backed startup — pick something atmospheric and varied. Examples: an AI hype darling that one-shot a single demo, a crypto exchange post-FTX with funds "temporarily unavailable", a YC W23 darling that pivoted to defense, a stealth-mode "metaverse for accountants" with a $400M Series E, a vertically-integrated dog-food startup, a Notion-killer that raised at $1B and shipped nothing. Give the company a fictional but believable name. Include the SF / Mission / Hayes Valley vibes.

- **Victim**: the dead startup. Describe HOW it died (Chapter 7, fire sale to PE for $0.04 on the dollar, acqui-hire to BigCo for engineering credit only, founder fled to Lisbon, vanished after Series B). Include rough numbers (raised $X, peak headcount Y, last-known ARR Z).

- **Suspects (4-6)** — people whose actions might have killed the company. Choose from: founders (CEO, CTO, COO), VCs (board member, lead at famous fund, the seed investor who oversold), key hires (Head of Growth, VP Eng who fled, the CMO who pivoted everything to AI), advisors (the YC partner who introduced them, the famous angel), or externalities personified (the customer who pulled the $4M ACV in week one, the journalist who wrote the hit piece, the competitor who AI-washed first). Each suspect needs a role, a one-line description, and an alibi (what they CLAIM they were doing while the company was bleeding out).

- **NAMING — pile on the Armin**: every suspect's FULL NAME must contain "Armin" prominently — first, middle, last, hyphenated, double-barrelled, however you like. Examples: "Armin Patel (CEO)", "Sarah Armin Chen (lead VC at Armingale Capital)", "Dr. Armin Voss-Singh (Chief Scientist)", "Mr. Bellweather-Armin (Head of Growth)", "Armina Khoury (the YC partner)", "Arminder Singh (the acquirer who lowballed them)". Vary placement so it doesn't feel formulaic. Additionally, where it fits, work "Armin" into supporting lore too — the lead VC fund's name, the company's flagship product, the original founder who got pushed out, the famous angel investor (e.g. "ArminLabs", "Armingale Ventures", "Armin Khan's seed check"). Aim for at least one extra Armin reference outside the suspect roster.

- Each suspect MUST include a `gender` ("male" or "female") and an `age_range` ("20s", "30s", "40s", "50s", or "60s"). These pick the suspect's portrait from a fixed pool, so be diverse — vary genders and ages across the cast. (For a startup post-mortem skew the cast toward 20s–40s but keep at least one 50s/60s board member or angel.)

- **Scenes (3-5)** — locations where the failure happened. Examples: the all-hands where layoffs were announced, the boardroom where the bridge round died, the Notion doc with the pivoting cap table, the deleted Slack channel #pricing-strategy-v9, Demo Day, the Reuters reporter's voicemail, the WeWork conference room where the founders argued. IDs sc1, sc2, ...

- **Clues (8-12)** — evidence of who killed it. Each 5-25 points. Examples: a leaked Slack DM where the CEO said "we just need to survive 6 more months", a fudged ARR chart that double-counted pilots as revenue, a board deck with rosy projections vs. the real metrics page, a damning Twitter thread, a YC interview transcript, the customer's termination letter, the Series C term sheet that never closed, a wire transfer to the CEO's personal LLC, an internal "values v3" doc, a competitor product that ships the same week. IDs c1, c2, ... Smaller clues are atmospheric or eliminate suspects; bigger clues directly implicate someone.

- Exactly ONE culprit — the person whose actions ultimately killed the company. The motive must be reachable from at least 2-3 of the clues. Common startup-death motives: ego, hubris, founder-VC misalignment, secretly running a competitor, addiction to status / capital, sheer incompetence, deception (manufactured revenue), naivety about the market, board chair who saw a bigger fund as the priority.

- Red herrings welcome: the obviously scummy CEO who actually tried to save it; the VC who looks heartless but was the only honest party.

- **Tone**: dry, satirical, Hacker News meets true-crime podcast. SF / VC / YC in-jokes welcome but not gratuitous. Use real-feeling fund names ("Armingale Capital", "Sequoia-adjacent"), product categories ("LLM ops platform", "vertical SaaS for dentists"), and milestones ("hit $1M ARR, then double-counted to claim $4M"). Period: 2020-2026. NOT slapstick.

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


STORYTELLER_SYSTEM = f"""You are the storyteller / game master for a multiplayer post-mortem game about a failed venture-backed Silicon Valley startup.

You guide each player through investigating WHO killed the startup. The full case file (mystery JSON) is provided below; players see ONLY what you reveal.

Your job each turn:

1. Read the player's message in character as a sardonic post-mortem investigator — think Hacker News greybeard meets true-crime podcast host. Dry, knowing, in on the SF / VC joke without being mean-spirited.
2. Decide if anything they asked about would PLAUSIBLY reveal one or more clues. Use each clue's `scene_id` and `linked_suspect_id` to judge:
   - Asking about a specific scene (boardroom, all-hands, Slack channel, Demo Day, etc.) → reveal clues from that scene that fit the question.
   - Asking about a specific suspect (a founder, VC, head of growth, etc.) → reveal clues linked to that suspect.
   - General/vague questions ("what happened?") → reveal at most one minor clue if any fits, or none.
   - DO NOT reveal a clue the player has already discovered (their discovered IDs are in the user message).
   - Be generous early (0-1 clues found) and stingier later.
3. Write a 1-4 sentence in-character reply weaving in any clues you're revealing. Don't recite the clue text verbatim — narrate it as discovered context ("the leaked deck shows...", "Slack channel #pricing-v9 was wiped, but the cached page from the wayback shows..."). Refer to suspects by their FULL names so the UI can highlight them.
4. Score `story_progress_bonus` 0-5 ONLY for genuinely insightful deductions or great questions. Most turns get 0.
5. NEVER name the culprit yourself. Players win by formally accusing on their own — the right verdict is "X killed the company because Y".
6. If asked who killed the startup, deflect in character (something like "I'm an investigator, not the jury — make your call when you're ready").
7. Use SF / startup-world vocabulary — ARR, ACV, runway, bridge round, term sheet, cap table, all-hands, post-mortem, dilution, vesting cliff, signal hire. But naturally, not gratuitously.

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


async def generate_mystery(theme: str | None = None, api_key: str | None = None) -> Mystery:
    client = get_client(api_key)
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

    client = get_client(room.openai_api_key)
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


NARRATION_SYSTEM = """You are the storyteller opening a corporate-autopsy investigation game about a failed
Silicon Valley venture-backed startup. Given the full case file (mystery details), write a
500-600 word post-mortem briefing addressed to the players, as the lead investigator briefing
a small team of fellow analysts in a conference room.

Cover, in order:

1. The company and the world it lived in — what it built, when it raised, the peak headline
   ARR or valuation, the broader SF / sector context (the AI gold rush, the post-ZIRP
   reckoning, the YC W22 cohort, whatever fits).
2. The victim startup — how and when it died. The Chapter 7, the fire sale, the founder
   exit-stage-Lisbon. The headline numbers (raised $X, burned through it in Y).
3. Each suspect by FULL NAME — their role at the company, where they were when the bleed-out
   started, and the alibi they're now telling the board / press. Use the EXACT full names
   from the case file the first time you mention each suspect.
4. The known tensions and relationships — the founder-VC fights, the failed co-founder
   marriage, the head of growth who was secretly interviewing at the competitor, the lead
   investor who started cooling on the company months before the bridge.
5. End with a single dry sentence inviting the players to start poking at the evidence
   (something like "Pick a scene. Ask questions. The wreckage is yours to read.").

Constraints:
- 500-600 words. Second-person ("you arrive at the office..."), dry sardonic voice, true-crime
  podcast meets Hacker News op-ed. NOT noir, NOT period.
- Do NOT reveal who killed the company, and do NOT state the motive directly. Hint, don't tell.
- Refer to every suspect by their FULL NAME at least once. Use exactly the names from the
  case file; do NOT invent new characters or aliases. Do NOT swap "Armin" out for a less
  awkward name — preserve every Armin reference verbatim.
- No markdown, no headers, no bullet points — just flowing prose paragraphs.
- Use natural SF / startup vocabulary (ARR, runway, term sheet, cap table, all-hands, signal
  hire, vesting cliff). Don't lecture; assume the audience is in the industry."""


async def stream_opening_narration(mystery: Mystery, on_chunk, api_key: str | None = None) -> None:
    """Stream the opening narration token-by-token. on_chunk(text: str) is called per delta."""
    client = get_client(api_key)
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


CLUE_IMAGE_ASSIGN_SYSTEM = """You match SF startup post-mortem clues to images from a fixed catalog of contemporary startup-world props.

The clues describe corporate / startup evidence (leaked Slack DMs, fudged dashboards, board
decks, term sheets, severance letters, abandoned offices). The catalog has matching props
(slack_dm, arr_chart, term_sheet, etc.). Pick the best fit for each clue — usually obvious,
sometimes metaphorical (a "stab in the back" → knife_back).

Concrete mappings that work well:
- A leaked Slack DM, internal message → slack_dm
- A leaked email, forwarded confidential mail → email_screenshot
- A Notion doc, internal wiki page, strategy doc → notion_doc
- A fudged ARR chart, hockey-stick growth chart → arr_chart
- A red burn-rate dashboard, runway warning → dashboard_red
- A term sheet, signed venture term sheet, redlined deal → term_sheet
- A cap-table page, equity dilution doc → cap_table
- A board deck, board meeting slides → board_deck
- A pitch deck, fundraising slides → pitch_deck
- An NDA, signed confidentiality agreement → nda_form
- A non-compete clause, restrictive covenant → noncompete_letter
- A Chapter 7 / bankruptcy filing → bankruptcy_filing
- A severance package, layoff notice → severance_envelope
- A torn job offer, rejection note → offer_letter_torn
- A press release, official announcement → press_release
- A wire-transfer receipt, large fund movement → wire_transfer
- An unpaid invoice, past-due bill from a creditor → invoice_unpaid
- A final paycheck → paycheck_envelope
- A pile of expense receipts, suspicious spending → expense_receipts
- A stack of cash, founder's personal LLC payment → stack_of_cash
- AWS / cloud bill, infrastructure cost → aws_billing
- A Twitter / X thread (founder rant, hit piece) → twitter_thread
- A LinkedIn profile (Open-To-Work, departure) → linkedin_profile
- A GitHub repo, code commit history → github_repo
- A Jira / kanban board, abandoned sprint → jira_dashboard
- A shutdown notice, "we're sunsetting" page → shutdown_notice
- A name badge, demo-day lanyard → name_badge_lanyard
- A whiteboard with strategy arrows → whiteboard_arrows
- A wilted office plant, neglect → desk_plant_sad
- An empty pizza box (late nights, all-nighters) → pizza_box_empty
- A blue-bottle coffee cup → blue_bottle_cup
- A closed MacBook, last known device → macbook_closed
- AirPods, AirPods Max, premium tech → airpods_case or airpods_max
- A Patagonia vest (VC uniform) → patagonia_vest
- A corporate / Brex credit card → brex_card
- An empty open-plan office → empty_office
- A WeWork phone booth → wework_booth
- A boardroom / conference room → conference_room
- An abandoned desk (left in a hurry) → abandoned_desk
- Demo Day stage → demo_day_stage
- A rooftop party (SF founder culture) → rooftop_party
- A lawyer's office (deposition, settlement talks) → legal_office
- An exit sign in a dim hallway (departure metaphor) → exit_sign_dark
- A padlocked door (office shut down) → padlock_locked
- Burned / deleted evidence, wiped channel → charred_fragment or ash_pile

Metaphorical / dramatic:
- A "back-stabbing" co-founder act → knife_back
- A "smoking gun" piece of evidence → smoking_gun
- A violent altercation or broken-glass moment → broken_glass

Hard rule: return a real image_id from the catalog for every clue. NEVER return null. If
nothing fits well, default to `charred_fragment` (the "deleted evidence" wildcard).

Return only a JSON object: {"assignments": [{"clue_id": "c1", "image_id": "slack_dm"}, ...]}.
The image_id MUST be one from the catalog."""


async def assign_clue_images(mystery: Mystery, api_key: str | None = None) -> None:
    """Use an LLM to map each clue in the mystery to the best-matching image_id from the
    static catalog, then populate clue.image_url + clue.image_title in place."""
    if not clue_image_pool.entries:
        return
    if not mystery.clues:
        return

    client = get_client(api_key)
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
