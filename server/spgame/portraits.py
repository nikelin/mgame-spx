from __future__ import annotations

import json
import random
from pathlib import Path

from .models import Mystery, Suspect


PORTRAITS_DIR = Path(__file__).resolve().parent.parent / "portraits"
MANIFEST_PATH = PORTRAITS_DIR / "manifest.json"


class PortraitPool:
    """In-memory index of the static portrait pool. Loaded once at startup."""

    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries

    @classmethod
    def load(cls) -> "PortraitPool":
        if not MANIFEST_PATH.exists():
            return cls([])
        entries = json.loads(MANIFEST_PATH.read_text())
        return cls(entries)

    def _candidates(self, gender: str, age_range: str | None) -> list[dict]:
        gender_match = [e for e in self.entries if e["gender"] == gender]
        if not gender_match:
            # Fall back to any if the requested gender isn't in the pool
            gender_match = list(self.entries)
        if age_range:
            age_match = [e for e in gender_match if e["age_range"] == age_range]
            if age_match:
                return age_match
        return gender_match

    def assign(self, mystery: Mystery, rng: random.Random | None = None) -> None:
        """Mutate mystery.suspects in place: set each suspect.image_url to a /portraits/* path.
        Avoids reusing the same portrait twice within one mystery."""
        rng = rng or random.Random()
        used: set[str] = set()
        for s in mystery.suspects:
            cands = [e for e in self._candidates(s.gender, s.age_range) if e["id"] not in used]
            if not cands:
                # Pool exhausted of fresh portraits matching this suspect; allow reuse rather
                # than leaving them blank.
                cands = self._candidates(s.gender, s.age_range)
            if not cands:
                continue
            choice = rng.choice(cands)
            used.add(choice["id"])
            s.image_url = f"/portraits/{choice['filename']}"


pool = PortraitPool.load()
