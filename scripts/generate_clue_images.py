"""Generate the SF startup post-mortem prop image pool used at mystery time.

Output: server/clue_images/<id>.jpg + server/clue_images/manifest.json.

    OPENAI_API_KEY=... uv run --with openai python scripts/generate_clue_images.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "server" / "clue_images"
MANIFEST = OUT_DIR / "manifest.json"

BASE_STYLE = (
    "Style: contemporary editorial still-life photograph, single subject centered against "
    "a clean muted background (matte black, brushed concrete, light wood, or office "
    "fabric), shallow depth of field, soft directional light. The subject fills most of "
    "the frame. Do not include any readable text, logos, signatures, brand marks, or "
    "watermarks in the image. Realistic 2020s San Francisco tech-startup aesthetic."
)


def make(id_: str, title: str, prompt_subject: str, tags: list[str]) -> dict:
    return {
        "id": id_,
        "filename": f"{id_}.jpg",
        "title": title,
        "tags": tags,
        "prompt": f"{prompt_subject}. {BASE_STYLE}",
    }


# 50 startup post-mortem prop images. Categories: legal docs, dashboards & screens,
# physical office objects, money/financials, "death" markers (shutdown notices, layoff
# letters), plus a handful of metaphorical objects (knife, ash) that the LLM can lean
# on for dramatic clues ("a stab in the back from his co-founder").
SPECS: list[dict] = [
    # ===== Legal / corporate documents (10) =====
    make("term_sheet", "Term Sheet", "A printed venture-capital term sheet with red ink redlines and a coffee ring in the corner, lying on a black desk",
         ["document", "term sheet", "legal", "vc", "fundraising"]),
    make("cap_table", "Cap Table", "A printed cap table page showing dilution columns and percentages (numbers and headers blurred), on a wooden desk",
         ["document", "cap table", "equity", "ownership", "dilution"]),
    make("board_deck", "Board Deck", "A laptop open at an angle showing a board meeting slide deck with vague chart shapes, neutral office background",
         ["document", "board", "deck", "slides", "presentation"]),
    make("pitch_deck", "Pitch Deck", "A printed pitch deck cover page lying on a glass conference table, no readable text",
         ["document", "pitch", "deck", "fundraising", "slides"]),
    make("severance_envelope", "Severance Envelope", "A manila envelope on a desk, partially open, papers inside suggesting a legal document",
         ["document", "severance", "envelope", "layoff", "legal"]),
    make("nda_form", "Signed NDA", "A printed non-disclosure agreement with a fountain pen resting on top, signature line visible but illegible",
         ["document", "nda", "legal", "confidential", "signed"]),
    make("noncompete_letter", "Non-Compete Letter", "A formal-looking letter on letterhead with red highlighter marks, on a desk",
         ["document", "noncompete", "legal", "letter"]),
    make("bankruptcy_filing", "Chapter 7 Filing", "A thick stack of court-filing documents bound with a binder clip, ominous shadow",
         ["document", "bankruptcy", "chapter 7", "legal", "filing", "death"]),
    make("press_release", "Press Release", "A printed press release page on cream paper, professional formatting, on a wood desk",
         ["document", "press release", "announcement", "pr"]),
    make("offer_letter_torn", "Torn Offer Letter", "A formal job-offer letter ripped roughly in half, on a slate desk",
         ["document", "offer letter", "torn", "rejection", "departure"]),

    # ===== Dashboards & screens (10) =====
    make("slack_dm", "Slack DM Screenshot", "A laptop screen showing a Slack-like chat interface with messages, sidebar visible, text blurred, dark theme",
         ["screen", "slack", "messaging", "dm", "leaked"]),
    make("email_screenshot", "Email Window", "A laptop screen showing a generic email client with an inbox and an open message, text blurred",
         ["screen", "email", "inbox", "leaked"]),
    make("notion_doc", "Notion Doc", "A laptop screen showing a clean document interface with sidebar of pages, heading visible, text blurred",
         ["screen", "notion", "document", "wiki"]),
    make("jira_dashboard", "Sprint Board", "A laptop screen showing a kanban board with colored cards in columns, dark theme",
         ["screen", "jira", "kanban", "sprint", "project management"]),
    make("github_repo", "Code Editor", "A laptop screen showing a code editor with syntax-highlighted code, dark theme, sidebar of files",
         ["screen", "code", "github", "engineering"]),
    make("aws_billing", "Cloud Billing Dashboard", "A laptop screen showing a billing dashboard with bar charts of monthly costs, large dollar figure illegible, dark theme",
         ["screen", "aws", "cloud", "billing", "burn rate"]),
    make("twitter_thread", "Twitter Thread", "A smartphone in hand showing a social-media-style thread with multiple connected posts, text blurred",
         ["screen", "twitter", "x", "thread", "social"]),
    make("linkedin_profile", "LinkedIn Profile", "A smartphone screen showing a generic professional networking profile page, header photo and avatar visible, text blurred",
         ["screen", "linkedin", "profile", "departure"]),
    make("arr_chart", "Hockey-Stick ARR Chart", "A laptop screen showing a green line chart with an obvious hockey-stick growth curve, dark theme",
         ["screen", "chart", "arr", "metrics", "growth"]),
    make("shutdown_notice", "Shutdown Notice", "A laptop screen showing a stark website page with a sad-face icon, indicating a service is shutting down",
         ["screen", "shutdown", "death", "closure", "website"]),

    # ===== Physical office objects (10) =====
    make("macbook_closed", "Closed MacBook", "A closed silver laptop on a wood desk with a coffee cup beside it, dim office light",
         ["object", "laptop", "macbook", "desk"]),
    make("patagonia_vest", "Patagonia Vest", "A charcoal-gray puffer fleece vest folded neatly on the back of a designer office chair, blurred conference room behind",
         ["object", "vest", "patagonia", "vc", "uniform"]),
    make("airpods_case", "AirPods Case", "A small white wireless-earbud charging case sitting open on a marble desk, one earbud inside",
         ["object", "airpods", "earbuds", "tech"]),
    make("blue_bottle_cup", "Coffee Cup", "A simple white ceramic to-go coffee cup with a sleeve, sitting on a wooden cafe table",
         ["object", "coffee", "cafe", "morning meeting"]),
    make("pizza_box_empty", "Empty Pizza Box", "An open empty cardboard pizza box with only crumbs left, on a cluttered office desk at night",
         ["object", "pizza", "office", "all-nighter", "burn"]),
    make("name_badge_lanyard", "Conference Lanyard", "A blank-white plastic name badge dangling from a black lanyard on a glass desk, no readable text",
         ["object", "badge", "conference", "demo day"]),
    make("whiteboard_arrows", "Whiteboard with Arrows", "A glass whiteboard covered in scrawled arrows and boxes in dry-erase marker, blurred to be illegible",
         ["object", "whiteboard", "strategy", "diagram"]),
    make("desk_plant_sad", "Wilted Desk Plant", "A small wilted office desk plant in a white ceramic pot, leaves drooping, sad neglected look",
         ["object", "plant", "office", "neglect", "decay"]),
    make("brex_card", "Corporate Card", "A sleek matte-black metal credit card lying alone on a marble surface, no readable text or numbers",
         ["object", "credit card", "brex", "corporate", "money"]),
    make("airpods_max", "Premium Headphones", "Over-ear silver-gray wireless headphones resting on a desk, premium tech vibe",
         ["object", "headphones", "premium", "engineer"]),

    # ===== Money / financials (6) =====
    make("wire_transfer", "Wire Transfer Receipt", "A printed bank wire-transfer confirmation page with rows of figures (numbers blurred), on a wood desk",
         ["money", "wire transfer", "bank", "fundraising", "money out"]),
    make("invoice_unpaid", "Past-Due Invoice", "A printed invoice with a red 'PAST DUE' stamp across it, on a marble desk",
         ["money", "invoice", "unpaid", "creditor"]),
    make("paycheck_envelope", "Final Paycheck", "A plain business envelope with a clear-window check showing through, on a desk",
         ["money", "paycheck", "envelope", "layoff", "departure"]),
    make("stack_of_cash", "Stack of Cash", "A small neat stack of US one-hundred-dollar bills on a dark desk, dramatic lighting",
         ["money", "cash", "fundraise", "exit"]),
    make("expense_receipts", "Pile of Receipts", "A small pile of crumpled paper expense receipts on a desk, looking suspicious",
         ["money", "receipts", "expense", "fraud"]),
    make("dashboard_red", "Red Burn-Rate Dashboard", "A laptop screen showing a financial dashboard dominated by red downward arrows and negative numbers (numbers blurred)",
         ["money", "burn rate", "runway", "metrics", "death"]),

    # ===== Scene / location (8) =====
    make("empty_office", "Abandoned Open-Plan Office", "An empty modern open-plan startup office at dusk, rows of empty desks, a few chairs askew, no people",
         ["scene", "office", "empty", "abandoned", "shutdown"]),
    make("wework_booth", "Phone Booth", "A small glass phone-booth-style room inside a coworking space, single chair and desk, dim light",
         ["scene", "wework", "coworking", "phone booth", "meeting"]),
    make("conference_room", "Empty Conference Room", "An empty glass-walled conference room with a long table, the door slightly ajar, plant in the corner",
         ["scene", "conference room", "meeting", "boardroom"]),
    make("abandoned_desk", "Abandoned Desk", "A single office desk strewn with personal items left in a hurry — a half-finished coffee, a sweater, a key card",
         ["scene", "desk", "departure", "abandoned"]),
    make("demo_day_stage", "Stage at Demo Day", "A spotlit stage with a single microphone, screen behind glowing, no presenter, empty seats",
         ["scene", "demo day", "stage", "pitch"]),
    make("rooftop_party", "Rooftop Party", "A rooftop terrace at golden hour with abandoned wine glasses and string lights, distant city skyline",
         ["scene", "party", "rooftop", "social"]),
    make("legal_office", "Lawyer's Office", "A wood-paneled lawyer's office with a heavy oak desk, leather chair, stacked files, no people",
         ["scene", "lawyer", "office", "legal"]),
    make("exit_sign_dark", "Exit Sign in Dim Hallway", "A glowing red EXIT sign at the end of a dimly lit office hallway",
         ["scene", "exit", "hallway", "departure", "death"]),

    # ===== Death/failure markers (3) =====
    make("ash_pile", "Burned Document Pile", "A small pile of grey ash on a slate surface, with charred edges of paper fragments still visible",
         ["death", "ash", "burned", "evidence", "deleted"]),
    make("charred_fragment", "Charred Page Fragment", "A scorched piece of paper with burned edges, partial illegible handwriting barely visible, on a dark surface",
         ["death", "charred", "burned", "evidence", "fragment"]),
    make("padlock_locked", "Padlocked Door", "A heavy industrial padlock on a corrugated metal pulldown door, harsh light",
         ["death", "padlock", "shutdown", "locked"]),

    # ===== Dramatic metaphor (3) =====
    make("knife_back", "Knife in Wood", "A simple kitchen knife stuck blade-first into a wooden cutting board, dramatic shadow",
         ["metaphor", "knife", "betrayal", "back-stab"]),
    make("smoking_gun", "Smoking Gun", "A small ornate revolver lying alone on a dark surface with a thin wisp of smoke still rising",
         ["metaphor", "gun", "smoking gun", "evidence", "killer"]),
    make("broken_glass", "Broken Glass on Floor", "Shards of a broken drinking glass on a hardwood office floor, with spilled liquid",
         ["scene", "broken", "glass", "incident", "violence"]),
]


async def generate_one(client: AsyncOpenAI, spec: dict, force: bool) -> tuple[dict, str]:
    path = OUT_DIR / spec["filename"]
    if path.exists() and not force:
        return spec, "skipped"
    try:
        resp = await client.images.generate(
            model="gpt-image-1",
            prompt=spec["prompt"],
            size="1024x1024",
            quality="low",
            output_format="jpeg",
            output_compression=72,
            n=1,
        )
        b64 = resp.data[0].b64_json
        if not b64:
            return spec, "no b64 in response"
        path.write_bytes(base64.b64decode(b64))
        return spec, "ok"
    except Exception as e:
        return spec, f"error: {type(e).__name__}: {e}"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--filter", type=str, default=None)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI()
    specs = [s for s in SPECS if (args.filter is None or args.filter in s["id"])]
    print(f"Generating {len(specs)} clue images (concurrency={args.concurrency}, force={args.force})")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[tuple[dict, str]] = []

    async def worker(spec: dict) -> None:
        async with sem:
            t0 = time.monotonic()
            spec, status = await generate_one(client, spec, args.force)
            results.append((spec, status))
            marker = "✓" if status == "ok" else ("·" if status == "skipped" else "✗")
            print(f"  {marker} {spec['id']:<28} ({time.monotonic() - t0:5.1f}s) {status}")

    await asyncio.gather(*[worker(s) for s in specs])

    existing_ids = {p.stem for p in OUT_DIR.glob("*.jpg")}
    manifest = [
        {k: v for k, v in s.items() if k != "prompt"}
        for s in SPECS
        if s["id"] in existing_ids
    ]
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    ok = sum(1 for _, s in results if s == "ok")
    failed = [(spec["id"], s) for spec, s in results if s.startswith("error")]
    print(f"\nManifest: {MANIFEST} ({len(manifest)} entries)")
    print(f"Generated {ok}, failed {len(failed)}, skipped {len(results) - ok - len(failed)}")
    for pid, err in failed:
        print(f"  FAIL {pid}: {err}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
