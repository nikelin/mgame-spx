"""Generate the pool of clue / prop images used at mystery time. Run once.

Output: server/clue_images/<id>.jpg + server/clue_images/manifest.json.

    OPENAI_API_KEY=... uv run --with openai python scripts/generate_clue_images.py
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
    "Style: moody noir still life, single object centered against a neutral dark background, "
    "dramatic chiaroscuro lighting from one side, painterly illustration, period-accurate "
    "1920s-1940s details. The object fills most of the frame. Do not include any text, "
    "letters, signatures, or watermarks anywhere in the image."
)


def make(id_: str, title: str, prompt_subject: str, tags: list[str]) -> dict:
    return {
        "id": id_,
        "filename": f"{id_}.jpg",
        "title": title,
        "tags": tags,
        "prompt": f"{prompt_subject}. {BASE_STYLE}",
    }


# 50 prop images covering common mystery-game evidence categories.
SPECS: list[dict] = [
    # ===== Weapons (10) =====
    make("knife_kitchen", "Kitchen Knife", "An ordinary kitchen knife with a wooden handle and a long steel blade, lying on a dark wood surface",
         ["weapon", "knife", "blade", "sharp", "kitchen"]),
    make("knife_bloodied", "Bloodied Dagger", "A small antique dagger with dried blood on the blade, resting on a stone surface",
         ["weapon", "knife", "dagger", "blood", "blade"]),
    make("revolver", "Silver Revolver", "A polished silver revolver from the 1930s with a pearl grip, on dark velvet",
         ["weapon", "gun", "pistol", "revolver", "firearm"]),
    make("derringer", "Pocket Derringer", "A small two-shot derringer pistol, ladies' style, ornate engraving",
         ["weapon", "gun", "pistol", "derringer", "concealed"]),
    make("candlestick_heavy", "Heavy Candlestick", "A heavy ornate brass candlestick, slightly bent, with wax drippings",
         ["weapon", "blunt", "candlestick", "brass", "heavy"]),
    make("rope_coiled", "Coiled Rope", "A coiled length of thick rope with frayed ends, on rough wooden boards",
         ["weapon", "rope", "strangle", "garotte"]),
    make("poison_vial", "Poison Vial", "A small dark glass apothecary bottle with a skull-and-crossbones glass stopper, half full of murky liquid",
         ["weapon", "poison", "vial", "bottle", "chemical"]),
    make("syringe", "Hypodermic Syringe", "A vintage glass-and-metal hypodermic syringe with a long needle, on a metal tray",
         ["weapon", "syringe", "needle", "injection", "medical"]),
    make("ice_pick", "Ice Pick", "A slim metal ice pick with a wooden handle, point gleaming under a single light",
         ["weapon", "ice pick", "puncture", "sharp"]),
    make("crowbar_iron", "Iron Crowbar", "A short iron crowbar with one end darkened by something",
         ["weapon", "blunt", "crowbar", "iron", "tool"]),

    # ===== Documents (8) =====
    make("letter_torn", "Torn Letter", "A handwritten letter torn into pieces and reassembled on a desk, pen and inkwell beside",
         ["document", "letter", "paper", "torn", "writing"]),
    make("letter_love", "Love Letter", "A scented pink envelope with elegant handwriting, lipstick-stained corner",
         ["document", "letter", "love", "romance", "envelope", "pink"]),
    make("telegram", "Yellow Telegram", "A folded yellow Western Union telegram on a wooden desk",
         ["document", "telegram", "telegraph", "message", "urgent"]),
    make("photograph_old", "Old Photograph", "A black-and-white sepia photograph with a torn corner, half hidden under other papers",
         ["document", "photograph", "photo", "picture", "memory"]),
    make("ledger_book", "Accounts Ledger", "A leather-bound accounts ledger open to a page of figures, pen across it",
         ["document", "ledger", "book", "accounts", "money"]),
    make("will_document", "Legal Will", "A folded legal document with a red wax seal, on a desk",
         ["document", "will", "legal", "wax seal", "inheritance"]),
    make("train_ticket", "Train Ticket", "A printed train ticket from the 1930s, slightly crumpled",
         ["document", "ticket", "train", "travel", "stub"]),
    make("theatre_ticket", "Theatre Ticket", "An ornate theatre ticket stub for an evening show, on red velvet",
         ["document", "ticket", "theatre", "show", "stub"]),

    # ===== Clothing / accessories (10) =====
    make("glove_bloodied", "Bloodied Glove", "A single white silk evening glove with dark blood stains, on hardwood",
         ["clothing", "glove", "silk", "blood", "evening"]),
    make("glove_leather", "Leather Glove", "A man's brown leather driving glove, slightly worn, on a car seat",
         ["clothing", "glove", "leather", "driving", "man"]),
    make("scarf_silk", "Silk Scarf", "A long emerald-green silk scarf, partially crumpled, on a marble surface",
         ["clothing", "scarf", "silk", "green", "ladies"]),
    make("hat_fedora", "Felt Fedora", "A man's grey felt fedora hat on a coat rack, light shadows",
         ["clothing", "hat", "fedora", "felt", "man"]),
    make("ring_signet", "Gold Signet Ring", "An ornate gold signet ring with engraved crest, on velvet",
         ["clothing", "ring", "jewelry", "signet", "gold"]),
    make("brooch_diamond", "Diamond Brooch", "A sparkling diamond brooch in art deco style, on dark silk",
         ["clothing", "brooch", "jewelry", "diamond", "art deco"]),
    make("earring_pearl", "Pearl Earring", "A single pearl drop earring, the other missing, on a vanity",
         ["clothing", "earring", "jewelry", "pearl", "lost"]),
    make("cufflinks_gold", "Gold Cufflinks", "A pair of monogrammed gold cufflinks on a silk shirt cuff",
         ["clothing", "cufflinks", "jewelry", "gold", "monogram"]),
    make("handkerchief_monogrammed", "Monogrammed Handkerchief", "A white linen handkerchief with embroidered monogram, slightly crumpled",
         ["clothing", "handkerchief", "linen", "monogram"]),
    make("fur_stole", "Fur Stole", "A luxurious fox fur stole draped on the back of a chair",
         ["clothing", "fur", "stole", "ladies", "luxury"]),

    # ===== Personal items (6) =====
    make("lipstick_gold", "Gold Lipstick", "A gold lipstick tube uncapped, vivid red bullet visible, on a vanity",
         ["personal", "lipstick", "makeup", "red", "ladies"]),
    make("perfume_bottle", "Perfume Bottle", "An ornate cut-glass perfume bottle with an atomizer bulb",
         ["personal", "perfume", "bottle", "ladies", "fragrance"]),
    make("lock_of_hair", "Lock of Hair", "A delicate lock of auburn hair tied with a black ribbon, on velvet",
         ["personal", "hair", "lock", "ribbon", "keepsake"]),
    make("locket_silver", "Silver Locket", "A silver locket open to reveal a tiny portrait, on a chain",
         ["personal", "locket", "jewelry", "silver", "portrait"]),
    make("pocket_watch", "Gold Pocket Watch", "A gold pocket watch on a chain, lid open showing roman numerals, slightly damaged",
         ["personal", "watch", "pocket watch", "gold", "timepiece"]),
    make("key_brass", "Brass Key", "An ornate antique brass key with intricate bow, on dark wood",
         ["personal", "key", "brass", "antique", "lock"]),

    # ===== Scene evidence (8) =====
    make("broken_glass", "Broken Glass", "Shards of a broken crystal tumbler on a hardwood floor, amber liquid pooled",
         ["scene", "glass", "broken", "tumbler", "drink"]),
    make("footprint_muddy", "Muddy Footprint", "A single muddy boot footprint on a polished wooden floor",
         ["scene", "footprint", "mud", "boot", "trace"]),
    make("bloodstain_floor", "Bloodstain", "A dark bloodstain on a patterned oriental rug, partially covered",
         ["scene", "blood", "stain", "rug", "trace"]),
    make("cigar_butt", "Cigar Stub", "A half-smoked cigar resting in a crystal ashtray, ash trail",
         ["scene", "cigar", "smoke", "ashtray", "tobacco"]),
    make("cigarette_lipstick", "Lipstick-stained Cigarette", "A cigarette butt with red lipstick smudge in a tin ashtray",
         ["scene", "cigarette", "smoke", "lipstick", "ashtray"]),
    make("matchbook", "Matchbook", "A matchbook from a speakeasy, half the matches gone, on a dark bar",
         ["scene", "matchbook", "matches", "speakeasy", "bar"]),
    make("ash_pile", "Pile of Ash", "A small pile of grey ash on a desk near burned paper fragments",
         ["scene", "ash", "burned", "fire", "paper"]),
    make("mud_caked_shoe", "Mud-caked Shoe", "A single black leather oxford shoe caked with dried mud",
         ["scene", "shoe", "mud", "footprint", "leather"]),

    # ===== Containers / locks (4) =====
    make("safe_open", "Open Safe", "A small wall safe with the door swung open, empty inside, dial visible",
         ["container", "safe", "lock", "empty", "vault"]),
    make("suitcase_battered", "Battered Suitcase", "A battered leather suitcase with brass clasps, travel stickers, on a station bench",
         ["container", "suitcase", "luggage", "travel", "leather"]),
    make("jewelry_box", "Jewelry Box", "An open velvet-lined jewelry box, contents disturbed, one compartment empty",
         ["container", "jewelry box", "velvet", "empty", "robbery"]),
    make("envelope_sealed", "Sealed Envelope", "A cream envelope sealed with red wax, on a desk",
         ["container", "envelope", "wax seal", "letter"]),

    # ===== Other (4) =====
    make("coin_silver", "Silver Coin", "A single tarnished silver coin from the 1920s, on dark felt",
         ["other", "coin", "silver", "money", "currency"]),
    make("whiskey_bottle", "Whiskey Bottle", "A half-empty bottle of bourbon on a polished bar, two glasses beside",
         ["other", "whiskey", "bourbon", "bottle", "drink", "alcohol"]),
    make("broken_necklace", "Broken Pearl Necklace", "Scattered pearls from a broken necklace on a marble floor",
         ["other", "pearls", "necklace", "broken", "jewelry", "ladies"]),
    make("charred_fragment", "Charred Paper Fragment", "A scorched piece of paper, edges burned, partial writing barely visible",
         ["other", "burned", "paper", "fragment", "evidence"]),
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
