# ================================
# FILE: modules/Phase1_scoring.py
# PURPOSE: Numeric score used for ranking *within* tiers
# ================================

from __future__ import annotations
from typing import Any, Dict, List, Set, Tuple

def _text_hits(text: str, tokens: Set[str]) -> int:
    tx = (text or "").lower()
    return sum(1 for t in tokens if t and t.lower() in tx)

def score_candidate(cand: Dict[str, Any], settings) -> Tuple[int, Any]:
    """
    Simple, transparent scoring used for intra-tier ranking.
    Returns (score 0..100, reasons (dict or str))
    """
    score = 0.0
    reasons: Dict[str, Any] = {}

    # allow / deny by Google types (light)
    types = set((cand.get("types") or []))
    allow_hit = types & getattr(settings, "allow_types", set())
    if allow_hit:
        w = settings.weights.get("allow_types", 20.0); score += w
        reasons[f"allow_types(+{int(w)})"] = sorted(allow_hit)

    deny_hit = types & getattr(settings, "soft_deny_types", set())
    if deny_hit:
        w = settings.weights.get("soft_deny_types", -15.0); score += w
        reasons[f"soft_deny({int(w)})"] = sorted(deny_hit)

    blob = " ".join([
        str(cand.get("page_title","")),
        str(cand.get("headers","")),
        str(cand.get("text","")),
        str(cand.get("name","")),
    ])
    pos_hits = _text_hits(blob, getattr(settings, "name_positive", set()))
    neg_hits = _text_hits(blob, getattr(settings, "name_negative", set()))
    if pos_hits:
        per = settings.weights.get("name_pos", 10.0)
        bonus = min(2, pos_hits) * per; score += bonus
        reasons[f"name_pos(+{int(bonus)})"] = pos_hits
    if neg_hits:
        per = abs(settings.weights.get("name_neg", -10.0))
        penalty = min(2, neg_hits) * per; score -= penalty
        reasons[f"name_neg(-{int(penalty)})"] = neg_hits

    # website + rating/reviews
    if cand.get("website"):
        w = settings.weights.get("website", 5.0); score += w
        reasons[f"website(+{int(w)})"] = True
    rating = cand.get("rating"); reviews = cand.get("user_ratings_total") or 0
    if isinstance(rating,(int,float)) and rating >= 3.8 and reviews >= 25:
        w = settings.weights.get("rating", 5.0); score += w
        reasons[f"rating(+{int(w)})"] = f"{rating} ({reviews})"

    # schema.org small nudge
    if (cand.get("schema_types") or []):
        w = settings.weights.get("schema_bonus", 4.0); score += w
        reasons[f"schema_bonus(+{int(w)})"] = cand.get("schema_types")

    return int(max(0, min(100, round(score)))), reasons
