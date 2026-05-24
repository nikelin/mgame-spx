"""One-shot offline script: generate a pool of SF startup-themed portraits via gpt-image-1,
save them to server/portraits/ along with a manifest the runtime can match against.

Run from repo root with OPENAI_API_KEY set:

    OPENAI_API_KEY=... uv run --with openai python scripts/generate_portraits.py --force

Re-run is idempotent for files that already exist; pass --force to regenerate.
The filename convention {gender_initial}_{age_range}_{slot}.jpg is preserved so the
runtime portrait-matching code doesn't need to change when the pool is re-themed.
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
OUT_DIR = REPO_ROOT / "server" / "portraits"
MANIFEST = OUT_DIR / "manifest.json"

BASE_STYLE = (
    "Style: editorial portrait photograph, soft natural light, San Francisco tech "
    "industry vibe, contemporary 2020s wardrobe, neutral muted background (office "
    "wall, brick, blurred glass), head-and-shoulders framing, looking at or slightly "
    "off camera, confident but human expression. Sharp focus, shallow depth of field. "
    "Do not include any text, logos, signatures, or watermarks anywhere in the image. "
    "One person only."
)


def make_spec(gender: str, age_range: str, vibe: str, slot: int) -> dict:
    gid = {"male": "M", "female": "F", "any": "X"}[gender]
    pid = f"{gid}_{age_range}_{slot:02d}"
    prompt = (
        f"Portrait of a {gender} character in their {age_range}, San Francisco tech / "
        f"startup world. Vibe: {vibe}. {BASE_STYLE}"
    )
    return {
        "id": pid,
        "filename": f"{pid}.jpg",
        "gender": gender,
        "age_range": age_range,
        "vibe": vibe,
        "prompt": prompt,
    }


# 24 portraits skewed toward startup demographics: more 20s-40s founders/operators,
# a few 50s/60s VCs and board members.
SPECS: list[dict] = []

_MALE_VIBES = [
    ("20s", "scrappy CS grad turned solo founder, fleece zip-up over a faded sci-fi t-shirt, AirPods in"),
    ("20s", "ex-FAANG engineer in his first founder role, frameless glasses, hoodie, holding a MacBook"),
    ("30s", "polished YC partner in a perfectly fitted button-down and slacks, athletic build, confident smile"),
    ("30s", "jaded technical co-founder with a beanie and untrimmed beard, plaid flannel, stressed eyes"),
    ("40s", "venture capital partner wearing a charcoal Patagonia vest over a crisp dress shirt, salt-and-pepper hair"),
    ("40s", "South Asian enterprise SaaS CEO in a navy blazer, no tie, smirking confidently at the camera"),
    ("50s", "weathered serial founder in a wrinkled button-down, tired but sharp eyes, just-back-from-an-all-nighter look"),
    ("50s", "famous angel investor, lightly tanned, Henley shirt, friendly but appraising expression"),
    ("60s", "legacy venture capital managing partner, gray hair, expensive sweater, skeptical raised eyebrow"),
    ("60s", "Black enterprise board chair in a perfectly tailored suit and rimless glasses, gravitas"),
    ("30s", "Latino head of growth, fitted black t-shirt, designer stubble, confident gym-bro energy"),
    ("40s", "Asian COO with a quant background, polished button-down, careful expression behind glasses"),
]

_FEMALE_VIBES = [
    ("20s", "Asian ML engineer turned founder, oversized hoodie, casual ponytail, MacBook within reach"),
    ("20s", "design-savvy product co-founder, statement frame glasses, denim jacket, sharp gaze"),
    ("30s", "polished business-development VP in a tailored blazer, ambitious knowing smile"),
    ("30s", "Latina head of comms, fashionable mockneck, dark lipstick, slight smirk"),
    ("40s", "general partner at a female-led fund, sharp dark suit, intelligent eyes, brunette bob"),
    ("40s", "Indian customer success VP in a teal blouse, warm but probing expression"),
    ("50s", "famous female venture capital partner, blonde shoulder-length hair, blazer, calm authority"),
    ("50s", "Black enterprise software founder, weary but resolute expression, simple cashmere sweater"),
    ("60s", "legendary board chair with silver hair, pearl earrings, knowing half-smile"),
    ("60s", "angel investor with strong opinions, silver curls, statement glasses, button-down"),
    ("30s", "Asian CMO who pivoted everything to AI, hipster aesthetic, geometric earrings, slightly manic energy"),
    ("40s", "operations leader with hair pulled back tight, charcoal turtleneck, no-nonsense direct gaze"),
]

for i, (age, vibe) in enumerate(_MALE_VIBES):
    SPECS.append(make_spec("male", age, vibe, i + 1))
for i, (age, vibe) in enumerate(_FEMALE_VIBES):
    SPECS.append(make_spec("female", age, vibe, i + 1))


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
    parser.add_argument("--force", action="store_true", help="regenerate even if file exists")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--filter", type=str, default=None)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI()

    specs = [s for s in SPECS if (args.filter is None or args.filter in s["id"])]
    print(f"Generating {len(specs)} portraits with concurrency={args.concurrency} (force={args.force})")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[tuple[dict, str]] = []

    async def worker(spec: dict) -> None:
        async with sem:
            t0 = time.monotonic()
            spec, status = await generate_one(client, spec, args.force)
            elapsed = time.monotonic() - t0
            results.append((spec, status))
            marker = "✓" if status == "ok" else ("·" if status == "skipped" else "✗")
            print(f"  {marker} {spec['id']:<10} ({elapsed:5.1f}s) {status}")

    await asyncio.gather(*[worker(s) for s in specs])

    existing_ids = {p.stem for p in OUT_DIR.glob("*.jpg")}
    manifest = [
        {k: v for k, v in s.items() if k != "prompt"}
        for s in SPECS
        if s["id"] in existing_ids
    ]
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"\nManifest: {MANIFEST} ({len(manifest)} portraits)")
    ok = sum(1 for _, s in results if s == "ok")
    failed = [(spec["id"], s) for spec, s in results if s.startswith("error")]
    print(f"Generated {ok}, failed {len(failed)}, skipped {len(results) - ok - len(failed)}")
    if failed:
        for pid, err in failed:
            print(f"  FAIL {pid}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
