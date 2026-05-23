from __future__ import annotations

import time

from .models import GameRoom, Player

WRONG_ACCUSATION_PENALTY = 10
CORRECT_ACCUSATION_BONUS = 50
MAX_ACCUSATIONS_PER_PLAYER = 3


def award_clue_points(room: GameRoom, player: Player, clue_ids: list[str]) -> tuple[int, list[dict]]:
    """Mark clues as discovered for this player and award points. Returns (points_awarded, clue_objs)."""
    if room.mystery is None or not clue_ids:
        return 0, []
    awarded = 0
    revealed: list[dict] = []
    by_id = {c.id: c for c in room.mystery.clues}
    for cid in clue_ids:
        clue = by_id.get(cid)
        if clue is None or cid in player.discovered_clue_ids:
            continue
        player.discovered_clue_ids.add(cid)
        player.points += clue.points
        awarded += clue.points
        revealed.append({
            "id": clue.id,
            "text": clue.text,
            "points": clue.points,
            "scene_id": clue.scene_id,
            "image_url": clue.image_url,
            "image_title": clue.image_title,
        })
    return awarded, revealed


def resolve_accusation(room: GameRoom, player: Player, suspect_id: str) -> dict:
    """Resolve an accusation. Mutates player + room. Returns a payload dict for events."""
    if room.mystery is None:
        return {"status": "no_mystery"}
    if player.accusations_used >= MAX_ACCUSATIONS_PER_PLAYER:
        return {"status": "limit_reached", "accusations_used": player.accusations_used}

    player.accusations_used += 1
    correct = (suspect_id == room.mystery.culprit_id)

    suspect_name = next((s.name for s in room.mystery.suspects if s.id == suspect_id), suspect_id)

    # Append to the room-level accusation log so the UI can render per-suspect attribution.
    room.accusation_log.append({
        "ts": time.time(),
        "player_id": player.id,
        "player_name": player.name,
        "suspect_id": suspect_id,
        "suspect_name": suspect_name,
        "correct": correct,
    })

    if correct:
        player.points += CORRECT_ACCUSATION_BONUS
        room.winner_id = player.id
        room.status = "over"
        return {
            "status": "correct",
            "suspect_id": suspect_id,
            "suspect_name": suspect_name,
            "player_id": player.id,
            "player_name": player.name,
            "bonus": CORRECT_ACCUSATION_BONUS,
            "motive": room.mystery.motive,
            "leaderboard": leaderboard(room),
        }
    else:
        player.points = max(0, player.points - WRONG_ACCUSATION_PENALTY)
        return {
            "status": "wrong",
            "suspect_id": suspect_id,
            "suspect_name": suspect_name,
            "player_id": player.id,
            "player_name": player.name,
            "penalty": WRONG_ACCUSATION_PENALTY,
            "accusations_used": player.accusations_used,
            "accusations_remaining": MAX_ACCUSATIONS_PER_PLAYER - player.accusations_used,
        }


def leaderboard(room: GameRoom) -> list[dict]:
    return sorted(
        ({"id": p.id, "name": p.name, "points": p.points} for p in room.players.values()),
        key=lambda x: x["points"],
        reverse=True,
    )
