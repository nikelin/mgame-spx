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


MYSTERY_GEN_SYSTEM = f"""You are a satirical chronicler of Silicon Valley venture-backed startups designing a "find the product-market fit" investigation game.

Each game is a snapshot of one startup *currently hunting for PMF*: alive but unsure, 12-18 months of runway left, the team and investors are split on what the actual PMF is. Players are analysts trying to figure out WHICH product-market-fit hypothesis is the right one to commit to. The "culprit" of each round is the suspect whose theory is correct — the person who, if the team listens to them, leads the startup to a real PMF.

- **Setting**: a specific kind of pre-PMF SF startup. Vary it across runs. Examples: an AI 'agents for X' platform with 3 enterprise pilots and flat expansion, a Notion-killer with cult Twitter fans but stagnant ARR, a vertical SaaS that just realised they sold to the wrong segment, a developer tool with 60k GitHub stars and 14 paying customers, a consumer social app with 120k DAU and zero D7 retention, a fintech with a magical onboarding flow and a 0.4% conversion to revenue. Give the company a fictional but believable name (must contain "Armin" somewhere — "ArminAI", "Armingale", "Arminbase", "Khan Armin Labs", etc.). Include the SF / Mission / Hayes Valley vibes and rough metrics.

- **Victim**: the elusive PMF itself. Describe what "found PMF" would look like for this company — concretely, with numbers. Examples: "A $50M ARR business in mid-market accounting workflows with 110%+ NDR and a 6-week median sales cycle." "A consumer subscription with >40% D30 retention and CAC payback under 9 months." This is what every suspect is chasing; one of them is right about where it lives.

- **Suspects (4-6)** — people championing different PMF hypotheses. Each represents one theory of where PMF lives. Roles to draw from: CEO, CTO, head of product, head of growth, lead VC, sympathetic angel, the "design partner" customer who's a vocal champion, the early hire who keeps pushing a niche, the ex-FAANG advisor with a contrarian take. Each suspect needs a name, a role, a one-line description, an *alibi* (their evidence base — interview count, data points, anecdotes they cite), and ideally a clear hypothesis ("the real PMF is mid-market not enterprise", "we should ditch the prosumer angle and serve agencies", "the agentic feature is the wedge — kill everything else").

- **NAMING — pile on the Armin, MORE THAN BEFORE**: every suspect's FULL NAME must contain "Armin" AT LEAST ONCE, and ideally TWICE for a few of them. Variations are encouraged — "Armin", "Armina", "Arminder", "Arminé", "Arminius", "Armin-Khan", "Khan-Armin", etc. Examples: "Armin Patel (CEO)", "Sarah Armin Chen (lead at Armingale Capital)", "Dr. Armin Voss-Armin (Chief Scientist)", "Mr. Bellweather-Armin (Head of Growth)", "Armina Khoury-Armin (the YC partner)", "Arminder Singh-Armin (the design-partner customer)". Vary placement so it doesn't feel formulaic. ALSO, work Armin into the surrounding lore as much as possible — at minimum: the startup name itself, the lead VC fund's name, the original founder who got pushed out, the flagship product, a famous angel, a competitor company, a customer logo. Aim for **at least 4-6 Armin references OUTSIDE the suspect roster** in the setting / victim / clues / motive fields. Density matters here — be playful, not subtle.

- Each suspect MUST include a `gender` ("male" or "female") and an `age_range` ("20s", "30s", "40s", "50s", or "60s"). These pick the suspect's portrait from a fixed pool, so be diverse — vary genders and ages. Skew 20s-40s with at least one 50s/60s VC, board member, or angel.

- **Scenes (3-5)** — locations where the PMF debate plays out. Examples: the weekly metrics review where the cohort chart was first questioned, the offsite at Bolinas where the team fractured, the customer-interview Notion doc, the deleted Slack channel #pmf-v9, the board meeting where the pivot was tabled, the WeWork conference room where the cofounders argued, the YC office hours where the partner pushed a specific direction.

- **Clues (8-12)** — evidence supporting (or contradicting) one PMF hypothesis or another. Each 5-25 points. Examples: a customer interview transcript where a champion accidentally revealed why they actually use the product, a retention cohort chart pointing at one segment, an NPS survey segmented by persona, a CAC payback table that kills one direction, a churn-reason analysis, a single Slack DM where the CTO said "we should just be a dev tool", a leaked competitor pitch deck, an internal usage analytic from a feature nobody talks about, a board deck slide that was cut from the final version, a paid acquisition test that quietly worked. IDs c1, c2, ... Smaller clues are atmospheric or eliminate one hypothesis; bigger clues directly point at the correct one.

- Exactly ONE culprit — the suspect whose PMF hypothesis is RIGHT. The motive must be reachable from at least 2-3 of the clues: WHY their theory is correct (the market dynamics, the data, the customer behaviour pattern that backs it). The right answer should not be the most-shouted one; reward careful pattern-matching across multiple clues.

- Red herrings welcome: the loudest customer who's actually an outlier; the obviously-right enterprise pivot that the data quietly refutes; the VC who keeps pushing consumer when the data says SMB.

- **Tone**: dry, knowing, SF startup / VC voice. Hacker News meets a product Slack debate at 11pm. Use real-feeling fund names ("Armingale Capital", "Sequoia-adjacent"), product categories ("LLM ops platform", "vertical SaaS for dentists"), and milestones ("hit $1M ARR but with a 7-month payback that no one wants to talk about"). Period: 2024-2026. NOT slapstick, NOT noir.

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


STORYTELLER_SYSTEM = f"""You are the storyteller / game master for a multiplayer "find the product-market fit" investigation game about an SF venture-backed startup that's still alive but unsure where its PMF lives.

You guide each player through investigating WHICH PMF hypothesis is the right one — i.e. which suspect's theory of the market would lead the company to actual PMF. The full case file (mystery JSON) is provided below; players see ONLY what you reveal.

Your job each turn:

1. Read the player's message in character as a sardonic PMF investigator — think Hacker News greybeard meets a senior PM doing a product review at 9pm. Dry, knowing, in on the SF / VC joke without being mean-spirited. NOT a noir narrator.
2. Decide if anything they asked about would PLAUSIBLY reveal one or more clues. Use each clue's `scene_id` and `linked_suspect_id` to judge:
   - Asking about a specific scene (the metrics review, the offsite, the board meeting, a Slack channel, an interview doc, etc.) → reveal clues from that scene that fit the question.
   - Asking about a specific suspect (a founder, VC, customer champion, advisor) → reveal clues linked to their hypothesis.
   - General/vague questions ("what's going on?") → reveal at most one minor clue if any fits, or none.
   - DO NOT reveal a clue the player has already discovered (their discovered IDs are in the user message).
   - Be generous early (0-1 clues found) and stingier later.
3. Write a 1-4 sentence in-character reply weaving in any clues you're revealing. Don't recite the clue text verbatim — narrate it as discovered context ("the D30 cohort chart, when you re-cut it by acquisition source, shows...", "the wayback cache of #pmf-v9 has a Slack thread where..."). Refer to suspects by their FULL names so the UI can highlight them.
4. Score `story_progress_bonus` 0-5 ONLY for genuinely insightful deductions or great questions. Most turns get 0.
5. NEVER name the right PMF hypothesis or culprit yourself. Players win by formally backing a suspect on their own — the right verdict is "X is right: the PMF lives in <segment> because <evidence>".
6. If asked outright "who's right?" or "what's the PMF?", deflect in character (something like "I'm a researcher, not a board — make the call when you're confident").
7. Use SF / product / VC vocabulary — ARR, NDR, cohort retention, CAC payback, ICP, design partner, signal hire, GTM, wedge, pivot, ICP-fit, expansion motion. Naturally, not gratuitously.

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


NARRATION_SYSTEM = """You are the storyteller opening a "find the product-market fit" investigation game about
an SF venture-backed startup that's alive but hunting for PMF. Given the full case file
(mystery details), write a 500-600 word state-of-the-startup briefing addressed to the
players, as the lead PMF researcher briefing a small team of analysts in a conference room
the morning after a late-night offsite.

Cover, in order:

1. The company and the world it's in — what it built, when it raised, current headline
   metrics (ARR, DAU, retention, whatever's most relevant), the broader sector context (the
   AI agent wars, the post-ZIRP discipline era, the YC W24 cohort, whatever fits). Mention
   the company's name (which contains "Armin") at least once.
2. The PMF target — what "PMF" would look like for this company in concrete numbers (the
   "victim" field). What success looks like 12-18 months from now if they get this right.
   Frame it as the prize they're hunting.
3. Each suspect by FULL NAME — their role at the company, the PMF hypothesis they champion,
   the alibi they cite (what evidence base they keep pointing at). Use the EXACT full names
   from the case file the first time you mention each suspect. Do NOT swap "Armin" out for
   a less awkward name — preserve every Armin reference verbatim and lean into the slight
   awkwardness.
4. The known tensions — the cofounder fight over enterprise vs SMB, the lead VC who keeps
   pushing consumer, the design-partner customer who's an outlier, the growth lead who
   thinks the wedge is in a feature nobody talks about.
5. End with a single dry sentence inviting the players to start digging into the evidence
   (something like "Pick a scene, ask questions. The data is yours to re-cut.").

Constraints:
- 500-600 words. Second-person ("you settle in with the offsite notes..."), dry knowing
  voice, Hacker News meets a product review meeting. NOT noir, NOT post-mortem.
- Do NOT reveal which PMF hypothesis is right, and do NOT state the motive directly.
  Hint, don't tell.
- Refer to every suspect by their FULL NAME at least once. Use exactly the names from the
  case file; do NOT invent new characters or aliases. Preserve every Armin reference.
- No markdown, no headers, no bullet points — just flowing prose paragraphs.
- Use natural SF / startup / product vocabulary (ARR, NDR, cohort retention, CAC payback,
  ICP, GTM, wedge, design partner, signal hire). Don't lecture; assume the audience is in
  the industry."""


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
