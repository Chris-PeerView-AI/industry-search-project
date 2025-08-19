# modules/openai_evaluator.py
# Batched OpenAI evaluation for borderline Tier-1/Tier-2 results (no writes).
# Uses Responses API with `input=` (compatible with older client versions).
# Returns a list of suggestions keyed by id.

from __future__ import annotations

import os
import json
import re
from typing import List, Dict, Any, Iterable
from openai import OpenAI

DEFAULT_EVAL_MODEL = os.getenv("OPENAI_EVAL_MODEL", "gpt-5-mini")

EVAL_RULES = (
    "You are an industry-agnostic reviewer.\n"
    "Apply STRICT balanced rules:\n"
    "- Tier-1 ONLY IF primary_hits present, OR (adjacent_hits present AND venue_hits_taxonomy present). "
    "Do NOT count fallback venues alone.\n"
    "- Tier-3 if disqualifier_hits present AND primary_hits empty.\n"
    "- Else Tier-2.\n"
    "- Treat industry_synonyms as PRIMARY when found in category/page_title; in name they are weaker and need venue support.\n"
    "Only rely on provided evidence; do not invent content.\n"
    "Output MUST be a valid JSON array and nothing else.\n"
    "Each element MUST be: {id, suggested_tier (1|2|3), disposition ('keep'|'promote'|'demote'|'flag'), "
    "confidence (0..1), reason (<=25 words), evidence_tags (subset of provided hits only)}."
)

def _pack_batch_payload(taxonomy: Dict[str, Any], items: Iterable[Dict[str, Any]]) -> str:
    # Compact payload to minimize tokens.
    t = {
        "primary_terms": taxonomy.get("primary_terms", []),
        "venue_terms": taxonomy.get("venue_terms", []),
        "industry_synonyms": taxonomy.get("industry_synonyms", []),
        "adjacent_terms": taxonomy.get("adjacent_terms", []),
        "disqualifiers": taxonomy.get("disqualifiers", []),
    }
    rows = []
    for it in items:
        rows.append({
            "id": it.get("id"),
            "name": it.get("name"),
            "page_title": it.get("page_title"),
            "category": it.get("category"),
            "hits": {
                "primary": it.get("primary_hits", []),
                "adjacent": it.get("adjacent_hits", []),
                "disqualifier": it.get("disqualifier_hits", []),
                "venue_taxonomy": it.get("venue_hits_taxonomy", []),
                "venue_fallback": it.get("venue_hits_fallback", []),
            },
            "decision": {
                "tier": it.get("tier"),
                "confidence": it.get("confidence"),
                "reason": it.get("reason"),
                "bias": "balanced",
            },
        })
    payload = {"taxonomy": t, "items": rows}
    return json.dumps(payload, ensure_ascii=False)

def _parse_json_array(text: str) -> List[Dict[str, Any]]:
    """Strip code fences and parse JSON array safely."""
    if not text:
        return []
    # Remove ```json ... ``` or ``` ... ``` wrappers
    m = re.search(r"```json\s*(\[[\s\S]*?\])\s*```", text)
    if not m:
        m = re.search(r"```\s*(\[[\s\S]*?\])\s*```", text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def evaluate_tiers_openai(
    items: List[Dict[str, Any]],
    taxonomy: Dict[str, Any],
    model: str | None = None,
    batch_size: int = 40,
) -> List[Dict[str, Any]]:
    """
    items: list of dicts with keys: id, name, page_title, category, primary_hits, adjacent_hits, disqualifier_hits,
           venue_hits_taxonomy, venue_hits_fallback, tier, confidence, reason.
    returns: list of {id, suggested_tier, disposition, confidence, reason, evidence_tags}
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    project = (os.getenv("OPENAI_PROJECT") or "").strip() or None
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    client = OpenAI(api_key=api_key, project=project)
    mdl = model or DEFAULT_EVAL_MODEL

    out: List[Dict[str, Any]] = []

    for i in range(0, len(items), batch_size):
        chunk = items[i:i+batch_size]
        payload = _pack_batch_payload(taxonomy, chunk)

        prompt = (
            EVAL_RULES
            + "\n\n--- PAYLOAD START ---\n"
            + payload
            + "\n--- PAYLOAD END ---\n"
            + "Return ONLY the JSON array."
        )

        try:
            resp = client.responses.create(
                model=mdl,
                input=prompt,   # NOTE: use 'input' (compatible with your client)
            )
            text = getattr(resp, "output_text", None)
            if not text:
                # Some client versions expose fragmented outputs under resp.output
                parts = []
                try:
                    for p in getattr(resp, "output", []) or []:
                        for c in getattr(p, "content", []) or []:
                            if getattr(c, "type", "") == "output_text" and getattr(c, "text", None):
                                parts.append(c.text)
                except Exception:
                    pass
                text = "\n".join(parts) if parts else ""

            data = _parse_json_array(text)
            if isinstance(data, list) and data:
                out.extend([d for d in data if isinstance(d, dict) and d.get("id") is not None])
            else:
                raise ValueError("Empty/invalid JSON array from evaluator")

        except Exception as e:
            # On failure, mark as 'flag' and surface error in eval_reason for visibility
            err = str(e)[:180]
            for it in chunk:
                out.append({
                    "id": it.get("id"),
                    "suggested_tier": it.get("tier"),
                    "disposition": "flag",
                    "confidence": 0.5,
                    "reason": f"evaluator error: {err}",
                    "evidence_tags": [],
                })
    return out
