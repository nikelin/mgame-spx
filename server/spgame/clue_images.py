from __future__ import annotations

import json
from pathlib import Path


CLUE_IMAGES_DIR = Path(__file__).resolve().parent.parent / "clue_images"
MANIFEST_PATH = CLUE_IMAGES_DIR / "manifest.json"


class CluePool:
    """In-memory index of the static clue/prop image pool. Loaded once at startup."""

    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries
        self.by_id = {e["id"]: e for e in entries}

    @classmethod
    def load(cls) -> "CluePool":
        if not MANIFEST_PATH.exists():
            return cls([])
        return cls(json.loads(MANIFEST_PATH.read_text()))

    def catalog(self) -> str:
        """Compact text catalog suitable for inclusion in an LLM prompt: one line per image."""
        lines = []
        for e in self.entries:
            tags = ", ".join(e.get("tags", []))
            lines.append(f"- {e['id']}: {e['title']} [{tags}]")
        return "\n".join(lines)

    def url_for(self, image_id: str) -> str | None:
        entry = self.by_id.get(image_id)
        if not entry:
            return None
        return f"/clue_images/{entry['filename']}"

    def title_for(self, image_id: str) -> str | None:
        entry = self.by_id.get(image_id)
        return entry["title"] if entry else None


pool = CluePool.load()
