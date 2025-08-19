# ================================
# FILE: modules/Phase1_planner.py
# PURPOSE: Industry-agnostic keyword planner (LLM or fallback)
# ================================

from __future__ import annotations
import os, re, json, asyncio
from shutil import which as _which
from typing import Any, Dict, List, Optional

LLM_MODEL = os.getenv("LLM_MODEL", "llama3")

def _sanitize_keywords(kw_list: List[str], breadth: str) -> List[str]:
    junk = {"near me","best","cheap","price","prices","hours","open","now","phone","address","reviews"}
    out = []
    for k in kw_list or []:
        s = (k or "").strip()
        if not s:
            continue
        sl = s.lower()
        if any(j in sl for j in junk):
            continue
        if len(sl.split()) > 4:  # keep short phrases
            continue
        out.append(s)
    cap = {"narrow": 2, "normal": 4, "wide": 8}.get((breadth or "normal").lower(), 4)
    return out[:cap]

def _llm_discovery_plan_prompt(industry: str, location: str, focus_detail: Optional[str], breadth: str) -> str:
    return (
        "Plan discovery queries for Google Places Nearby (keyword=...). "
        "Return STRICT JSON with keys:\n"
        "  keywords: array of 8-20 short, high-recall phrases for this industry\n"
        "  exclude_keywords: array of 3-10 negatives (optional)\n"
        "Rules:\n"
        "- Keywords MUST be short and specific to finding candidates (no city names).\n"
        "- Include venue phrases (e.g., 'indoor X', 'studio', 'lounge', 'simulator').\n"
        "- Include 4-8 brand/product tokens if they exist in the vertical.\n"
        "- If focus_detail is given, include it and 1-3 close variants.\n"
        "- No boolean operators; just plain phrases.\n"
        f"Industry: {industry}\n"
        f"Location: {location}\n"
        f"Breadth: {breadth}\n"
        f"Focus: {focus_detail or ''}\n\n"
        '{ "keywords": ["..."], "exclude_keywords": ["..."] }'
    )

async def _ollama_json(prompt: str, model: str) -> Dict[str, Any]:
    # lightweight subprocess call; avoids extra deps
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "ollama", "run", model,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate(input=prompt.encode("utf-8"))
    raw = stdout.decode("utf-8").strip()
    import re, json
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw) or re.search(r"(\{[\s\S]*\})", raw)
    try:
        return json.loads(m.group(1)) if m else {}
    except Exception:
        return {}

def _fallback_keyword_plan(industry: str, focus_detail: Optional[str], breadth: str) -> Dict[str, Any]:
    ind = (industry or "").strip()
    tokens = [t for t in re.split(r"[\s/,&-]+", ind.lower()) if t and t.isalpha()]
    base = list(dict.fromkeys([
        ind.lower(),
        " ".join(tokens),
        *(f"{w} {s}" for w in tokens for s in ["shop","center","studio","lounge","club","bar"]),
        *(f"indoor {w}" for w in tokens),
        *(f"{w} simulator" for w in tokens),
        *(f"{w} simulators" for w in tokens),
        *(f"{w} training" for w in tokens),
        *(f"{w} practice" for w in tokens),
    ]))
    if focus_detail:
        base = [focus_detail] + base
    excludes = {"for sale","used","wholesale","market","outdoor"}  # soft guidance; used only in scoring
    cap = {"narrow": 4, "normal": 8, "wide": 12}.get((breadth or "normal").lower(), 8)
    return {
        "keywords": base[:cap],
        "exclude_keywords": sorted(excludes),
        "source": "fallback",
        "max_keywords": {"narrow": 2, "normal": 4, "wide": 8}.get(breadth, 4),
    }

def plan_seed_keywords(project: Dict[str, Any]) -> Dict[str, Any]:
    """Returns planner_json; sanitize + cap; excludes are advisory (scoring only)."""
    industry = project.get("industry","")
    location = project.get("location","")
    focus_detail = project.get("focus_detail")
    breadth = (project.get("breadth") or "normal").lower()

    plan: Dict[str, Any] = {}
    if _which("ollama") is not None and bool(project.get("use_llm_planner", True)):
        prompt = _llm_discovery_plan_prompt(industry, location, focus_detail, breadth)
        try:
            plan = asyncio.run(_ollama_json(prompt, LLM_MODEL)) or {}
        except Exception:
            plan = {}
        plan["source"] = plan.get("source") or "llm"

    if not plan or not plan.get("keywords"):
        plan = _fallback_keyword_plan(industry, focus_detail, breadth)

    # sanitize & cap keywords to breadth
    plan["keywords"] = _sanitize_keywords(plan.get("keywords") or [], breadth)
    plan["max_keywords"] = {"narrow": 2, "normal": 4, "wide": 8}.get(breadth, 4)
    plan["source"] = plan.get("source") or ("llm" if _which("ollama") else "fallback")
    return plan
