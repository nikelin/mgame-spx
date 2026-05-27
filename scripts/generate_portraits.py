"""One-shot offline script: generate a pool of noir portraits via gpt-image-1,
save them to server/portraits/ along with a manifest the runtime can match against.

Run from repo root with OPENAI_API_KEY set:

    OPENAI_API_KEY=... uv run --with openai python scripts/generate_portraits.py

Re-run is idempotent for files that already exist; pass --force to regenerate.
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
    "Style: moody noir illustration, painterly, dramatic chiaroscuro lighting, "
    "head-and-shoulders framing, period-accurate 1920s-1940s clothing, neutral muted "
    "background. Do not include any text, letters, signatures, or watermarks in the image. "
    "One person only, gazing slightly off-camera."
)


def make_spec(gender: str, age_range: str, vibe: str, slot: int) -> dict:
    """Build one portrait spec — id, filename, prompt, attributes."""
    gid = {"male": "M", "female": "F", "any": "X"}[gender]
    aid = age_range.replace("s", "s")  # e.g. 30s
    pid = f"{gid}_{aid}_{slot:02d}"
    prompt = (
        f"Period noir portrait of a {gender} character in their {age_range}, vibe: {vibe}. "
        f"{BASE_STYLE}"
    )
    return {
        "id": pid,
        "filename": f"{pid}.jpg",
        "gender": gender,
        "age_range": age_range,
        "vibe": vibe,
        "prompt": prompt,
    }


# 24 portraits: 12 men + 12 women, spread across age ranges and vibes.
SPECS: list[dict] = []
_MALE_VIBES = [
    ("20s", "earnest reporter with rolled-up sleeves and ink-stained fingers"),
    ("20s", "sharply dressed jazz musician, slicked hair, sly smile"),
    ("30s", "world-weary detective, trench coat collar, three days of stubble"),
    ("30s", "Black bandleader in a tuxedo, charismatic, gold cufflinks"),
    ("40s", "stern shipping magnate, monocle, formal three-piece suit"),
    ("40s", "tired city doctor, wire-frame glasses, loosened tie"),
    ("50s", "weathered fisherman with a thick beard and pipe"),
    ("50s", "dignified Indian academic in a tweed jacket, holding a pocket watch"),
    ("60s", "elderly judge with white hair and a heavy gold chain"),
    ("60s", "Eastern European butler with severe expression, perfect posture"),
    ("30s", "Latin American croupier in a velvet vest, dealer's visor"),
    ("40s", "Japanese restaurateur, immaculate apron, watchful eyes"),
]
_FEMALE_VIBES = [
    ("20s", "glamorous chanteuse in a sequined dress, marcel waves, bold lipstick"),
    ("20s", "Black flapper in pearls and a feathered headband, mischievous grin"),
    ("30s", "stern librarian with cat-eye glasses and a tight bun"),
    ("30s", "Latina chemist in a lab coat, intelligent and skeptical"),
    ("40s", "society hostess in fox stole and pearls, looking faintly amused"),
    ("40s", "Indian heiress in an opulent sari, jeweled tiara, regal bearing"),
    ("50s", "matronly housekeeper in a starched apron, kind but knowing eyes"),
    ("50s", "Eastern European widow in heavy mourning veil and dark velvet"),
    ("60s", "elderly Black matriarch with silver hair, embroidered shawl"),
    ("60s", "elderly aristocrat in faded grandeur, antique cameo brooch"),
    ("30s", "Asian aviatrix in leather jacket and goggles pushed back on her head"),
    ("40s", "Eastern European concert pianist, severe black gown, long fingers"),
]

for i, (age, vibe) in enumerate(_MALE_VIBES):
    SPECS.append(make_spec("male", age, vibe, i + 1))
for i, (age, vibe) in enumerate(_FEMALE_VIBES):
    SPECS.append(make_spec("female", age, vibe, i + 1))


async def generate_one(client: AsyncOpenAI, spec: dict, force: bool) -> tuple[dict, str]:
    """Generate (or skip) one portrait. Returns (spec, status)."""
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
    parser.add_argument("--filter", type=str, default=None, help="only run portraits whose id contains this string")
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

    # Write/update manifest from successful + already-existing entries
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
