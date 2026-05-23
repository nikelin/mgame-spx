from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class Suspect(BaseModel):
    id: str = Field(description="Stable short ID like s1, s2, s3")
    name: str
    role: str = Field(description="Their role in the setting, e.g. 'the butler', 'the heiress'")
    description: str = Field(description="One-sentence physical and behavioural sketch")
    alibi: str = Field(description="What they claim to have been doing during the crime")


class Scene(BaseModel):
    id: str = Field(description="Stable short ID like sc1, sc2")
    name: str
    description: str = Field(description="A vivid one-paragraph description of the scene")


class Clue(BaseModel):
    id: str = Field(description="Stable short ID like c1, c2")
    text: str = Field(description="What a player learns when they discover this clue")
    points: int = Field(ge=5, le=25, description="Point value, 5-25 based on how revealing it is")
    linked_suspect_id: str | None = Field(
        default=None, description="ID of the suspect this clue most incriminates, or null"
    )
    scene_id: str = Field(description="ID of the scene where this clue can be found")


class Mystery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    setting: str = Field(description="Time and place, 1-2 sentences setting the mood")
    victim: str = Field(description="Who was killed, and how")
    suspects: list[Suspect] = Field(min_length=4, max_length=7)
    scenes: list[Scene] = Field(min_length=3, max_length=5)
    clues: list[Clue] = Field(min_length=8, max_length=14)
    culprit_id: str = Field(description="ID of the suspect who did it. Must match one of suspects[].id")
    motive: str = Field(description="The culprit's motive, logically connected to the clues")


class Player(BaseModel):
    id: str
    name: str
    token: str
    points: int = 0
    discovered_clue_ids: set[str] = Field(default_factory=set)
    accusations_used: int = 0
    last_active: float = Field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "points": self.points,
            "accusations_used": self.accusations_used,
        }


class Event(BaseModel):
    seq: int
    ts: float
    kind: Literal["join", "start", "clue", "message", "accuse", "win", "story", "leave"]
    payload: dict
    private_to: str | None = None


class GameRoom(BaseModel):
    """In-memory state for a single mystery room.

    The asyncio.Lock and subscriber queues are excluded from serialization."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    code: str
    status: Literal["lobby", "playing", "over"] = "lobby"
    host_id: str
    mystery: Mystery | None = None
    players: dict[str, Player] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    next_seq: int = 1
    winner_id: str | None = None
    created_at: float = Field(default_factory=time.time)
    last_activity: float = Field(default_factory=time.time)

    def public_state(self) -> dict:
        return {
            "code": self.code,
            "status": self.status,
            "host_id": self.host_id,
            "winner_id": self.winner_id,
            "mystery": self._public_mystery(),
            "players": [p.public() for p in self.players.values()],
            "next_seq": self.next_seq,
        }

    def _public_mystery(self) -> dict | None:
        if self.mystery is None:
            return None
        # Never leak the culprit until the game is over
        m = self.mystery.model_dump()
        if self.status != "over":
            m.pop("culprit_id", None)
            m.pop("motive", None)
            # Also strip clue text — only revealed clues should be visible
            m["clues"] = [
                {"id": c["id"], "scene_id": c["scene_id"], "points": c["points"]}
                for c in m["clues"]
            ]
        return m


class CreateRoomReq(BaseModel):
    host_name: str = Field(min_length=1, max_length=40)


class JoinRoomReq(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class MessageReq(BaseModel):
    token: str
    text: str = Field(min_length=1, max_length=2000)


class AccuseReq(BaseModel):
    token: str
    suspect_id: str


class StartReq(BaseModel):
    token: str
    theme: str | None = Field(default=None, max_length=200)


class StorytellerResult(BaseModel):
    """Schema the storyteller LLM returns each turn."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(description="In-character storyteller reply addressed to the player. 1-4 sentences.")
    revealed_clue_ids: list[str] = Field(
        default_factory=list,
        description="IDs of clues newly revealed to this player this turn. Empty if none.",
    )
    story_progress_bonus: int = Field(
        default=0, ge=0, le=5,
        description="0-5 bonus points for genuinely insightful questions or deductions, otherwise 0.",
    )
