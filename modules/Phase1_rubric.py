# =============================
# modules/Phase1_rubric.py (v3 — stricter primary prompt)
# PURPOSE: Build an INDUSTRY‑AGNOSTIC taxonomy for Phase‑1 tiering with clearer guidance
#          so LLM avoids generic primary terms like "experience"/"services".
# API: build_taxonomy(industry, focus=None, provider='ollama'|'openai'|'none', model=None)
# =============================
from __future__ import annotations

import os, re, json, subprocess
import unicodedata
from typing import Dict, Any, List

OLLAMA_BIN = os.getenv("OLLAMA_BIN", "ollama")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL  = os.getenv("LLM_MODEL", "llama3")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

PROMPT_TEMPLATE = (
    "You are designing a compact taxonomy to triage businesses for an arbitrary industry.\n"
    "Return STRICT JSON only (no prose) with keys: "
    "primary_terms, adjacent_terms, disqualifiers, venue_terms, exemplar_brands, industry_synonyms, notes, allow_t1_requires_primary.\n\n"
    "Definitions:\n"
    "- primary_terms: short, explicit phrases customers would search to find the core public-facing venue/offering "
    "(e.g., 'indoor golf simulator', 'virtual golf lounge', 'coffee cafe', 'medspa clinic').\n"
    "  • DO NOT use generic/abstract words alone like 'experience', 'services', 'solutions', 'equipment', 'supplies'.\n"
    "  • Avoid generic place nouns ('shop', 'store', 'club', 'center', 'academy') unless part of a distinctive venue phrase "
    "(e.g., 'simulator lounge', 'studio bays', 'clinic').\n"
    "- adjacent_terms: related offerings that alone are not the core venue (lessons/training, fittings, retail accessories, rentals).\n"
    "- disqualifiers: supply-chain or off-target archetypes (wholesale distributor, manufacturer, corporate HQ, B2B-only supplier, equipment brand).\n"
    "- venue_terms: distinctive words that signal a public venue/place (favor 'lounge', 'studio', 'bay', 'clinic', 'cafe').\n"
    "- exemplar_brands: a few well-known venue brands (if any).\n"
    "- industry_synonyms: 3–8 short phrases ordinary people use for this industry (e.g., "
    "for coffee: ['coffee shop','café','coffeehouse']; for indoor golf: ['indoor golf','golf simulator','screen golf']).\n"
    "- allow_t1_requires_primary: boolean; true if Tier-1 should require at least one primary_terms hit.\n\n"
    "Constraints:\n"
    "- Keep values short; lowercase where possible; 3–8 items per list when applicable.\n"
    "- Prefer explicit venue/offering phrases over broad marketing language.\n"
    "- Include common look-alike disqualifiers for this industry.\n\n"
    "Industry: {industry}\n"
    "Optional focus/subtype: {focus}\n\n"
    "Example shape (values are illustrative):\n"
    "{{\n"
    '  "primary_terms": ["core venue phrase 1","core venue phrase 2"],\n'
    '  "adjacent_terms": ["lessons","retail accessories"],\n'
    '  "disqualifiers": ["wholesale distributor","manufacturer","corporate office"],\n'
    '  "venue_terms": ["lounge","studio","clinic"],\n'
    '  "exemplar_brands": ["brand a","brand b"],\n'
    '  "industry_synonyms": ["industry term a","industry term b"],\n'
    '  "allow_t1_requires_primary": true,\n'
    '  "notes": "succinct rationale"\n'
    "}}\n"
)

def process_taxonomy(raw: Dict[str, Any], industry: str, focus: str | None = None) -> Dict[str, Any]:
    """
    Normalize and sanitize a raw taxonomy dict (from OpenAI or elsewhere)
    using the same post-processing as build_taxonomy.
    """
    industry = (industry or "").strip()
    focus = (focus or "").strip()

    # Pull lists safely
    primary_terms   = _norm_list(raw.get("primary_terms") or raw.get("primary_evidence"))
    adjacent_terms  = _norm_list(raw.get("adjacent_terms") or raw.get("near_archetypes") or raw.get("secondary_evidence"))
    disqualifiers   = _norm_list(raw.get("disqualifiers") or raw.get("off_target")) + _norm_list(raw.get("off_phrases"))
    venue_terms     = _norm_list(raw.get("venue_terms"))
    exemplar_brands = _norm_list(raw.get("exemplar_brands"))
    synonyms_llm    = _norm_list(raw.get("industry_synonyms"))
    allow_req       = bool(raw.get("allow_t1_requires_primary", True))
    notes           = (raw.get("notes") or "").strip()

    out = {
        "primary_terms": primary_terms,
        "adjacent_terms": adjacent_terms,
        "disqualifiers": disqualifiers,
        "venue_terms": venue_terms,
        "exemplar_brands": exemplar_brands,
        "industry_synonyms": synonyms_llm,
        "allow_t1_requires_primary": allow_req,
        "notes": notes,
        "primary_evidence": primary_terms,
        "near_archetypes": adjacent_terms,
        "off_target": disqualifiers,
        "off_phrases": [],
        "_provider": raw.get("_provider","external"),
        "_model": raw.get("_model",""),
    }

    # Ensure minimums
    fb = _fallback_taxonomy(industry, focus)
    if not out["primary_terms"] or any(p in {"experience","services","solutions"} for p in out["primary_terms"]):
        out["primary_terms"] = fb["primary_terms"]
        out["primary_evidence"] = out["primary_terms"]
    if not out["adjacent_terms"]:
        out["adjacent_terms"] = fb["adjacent_terms"]
        out["near_archetypes"] = out["adjacent_terms"]
    if not out["disqualifiers"]:
        out["disqualifiers"] = fb["disqualifiers"]
        out["off_target"] = out["disqualifiers"]
    if not out["venue_terms"]:
        out["venue_terms"] = fb["venue_terms"]

    # Expand synonyms with generic industry variants
    syn_variants = set(out.get("industry_synonyms") or [])
    syn_variants.update(_industry_variants(industry))
    out["industry_synonyms"] = _norm_list(list(syn_variants))

    # Demote overly-generic primaries
    MODIFIER_TOKENS = {"simulator","simulation","virtual","screen","indoor","lounge","studio","bay","clinic","cafe","café"}
    keep_primary, move_to_adj = [], []
    for p in out["primary_terms"]:
        canon = re.sub(r"[^a-z0-9]+", "", _strip_diacritics(p.lower()))
        if any(tok in canon for tok in MODIFIER_TOKENS):
            keep_primary.append(p)
        else:
            move_to_adj.append(p)
    if move_to_adj:
        out["primary_terms"] = _norm_list(keep_primary)
        out["primary_evidence"] = out["primary_terms"]
        out["adjacent_terms"] = _norm_list(out["adjacent_terms"] + move_to_adj)
        out["near_archetypes"] = out["adjacent_terms"]

    # Inject synonyms into primary candidates
    out["primary_terms"] = _norm_list(out["primary_terms"] + out["industry_synonyms"])
    out["primary_evidence"] = out["primary_terms"]

    return out


def _strip_diacritics(s: str) -> str:
    if not s:
        return s
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

def _industry_variants(industry: str) -> list[str]:
    """Generic, industry-agnostic variants for the industry phrase."""
    base = (industry or "").strip().lower()
    base = _strip_diacritics(base)
    if not base:
        return []
    v = {base}
    v.add(base.replace("-", " "))
    v.add(base.replace(" ", "-"))
    v.add(base.replace(" ", ""))  # coffeeshop, medspaclinic, etc.
    # crude plural/singular toggles (safe enough)
    if not base.endswith("s"):
        v.add(base + "s")
    else:
        v.add(base[:-1])
    # common cafe variant
    if "cafe" in base and "caf\u00e9" not in base:
        v.add(base.replace("cafe", "café"))
    if "café" in base:
        v.add(base.replace("café", "cafe"))
    return [x for x in v if x]

def build_taxonomy(industry: str, focus: str | None = None, provider: str = "ollama", model: str | None = None) -> Dict[str, Any]:
    industry = (industry or "").strip()
    focus = (focus or "").strip()

    prompt = PROMPT_TEMPLATE.format(industry=industry, focus=focus or "—")

    js: Dict[str, Any] = {}
    if provider == "ollama":
        js = _ollama_json(prompt, model=model or LLM_MODEL)
    elif provider == "openai":
        js = _openai_json(prompt, model=model or "gpt-4o-mini")
    else:
        js = {}

    if not isinstance(js, dict) or not js:
        out = _fallback_taxonomy(industry, focus)
        out["industry_synonyms"] = _industry_variants(industry)
        return out

    primary_terms     = _norm_list(js.get("primary_terms") or js.get("primary_evidence"))
    adjacent_terms    = _norm_list(js.get("adjacent_terms") or js.get("near_archetypes") or js.get("secondary_evidence"))
    disqualifiers     = _norm_list(js.get("disqualifiers") or js.get("off_target")) + _norm_list(js.get("off_phrases"))
    venue_terms       = _norm_list(js.get("venue_terms"))
    exemplar_brands   = _norm_list(js.get("exemplar_brands"))
    synonyms_llm      = _norm_list(js.get("industry_synonyms"))
    allow_primary_req = bool(js.get("allow_t1_requires_primary", True))
    notes             = (js.get("notes") or "").strip()

    out = {
        "primary_terms": primary_terms,
        "adjacent_terms": adjacent_terms,
        "disqualifiers": disqualifiers,
        "venue_terms": venue_terms,
        "exemplar_brands": exemplar_brands,
        "industry_synonyms": synonyms_llm,   # NEW
        "allow_t1_requires_primary": allow_primary_req,
        "notes": notes,
        # Back-compat mirrors
        "primary_evidence": primary_terms,
        "near_archetypes": adjacent_terms,
        "off_target": disqualifiers,
        "off_phrases": [],
        "_provider": provider,
        "_model": model or (LLM_MODEL if provider == "ollama" else "gpt-4o-mini"),
    }

    # Ensure minimums using fallback if LLM returns too sparse or too generic
    fb = _fallback_taxonomy(industry, focus)
    if not out["primary_terms"] or any(p in {"experience","services","solutions"} for p in out["primary_terms"]):
        out["primary_terms"] = fb["primary_terms"]
        out["primary_evidence"] = out["primary_terms"]
    if not out["adjacent_terms"]:
        out["adjacent_terms"] = fb["adjacent_terms"]
        out["near_archetypes"] = out["adjacent_terms"]
    if not out["disqualifiers"]:
        out["disqualifiers"] = fb["disqualifiers"]
        out["off_target"] = out["disqualifiers"]
    if not out["venue_terms"]:
        out["venue_terms"] = fb["venue_terms"]

    # NEW: Add robust industry variants to synonyms (LLM + generic variants)
    syn_variants = set(out.get("industry_synonyms") or [])
    syn_variants.update(_industry_variants(industry))
    out["industry_synonyms"] = _norm_list(list(syn_variants))

    # NEW: Demote overly-generic primaries (no venue/tech modifiers) into adjacent_terms
    MODIFIER_TOKENS = {"simulator","simulation","virtual","screen","indoor","lounge","studio","bay","clinic","cafe","café"}
    keep_primary, move_to_adj = [], []
    for p in out["primary_terms"]:
        canon = re.sub(r"[^a-z0-9]+", "", _strip_diacritics(p.lower()))
        if any(tok in canon for tok in MODIFIER_TOKENS):
            keep_primary.append(p)
        else:
            move_to_adj.append(p)
    if move_to_adj:
        out["primary_terms"] = _norm_list(keep_primary)
        out["primary_evidence"] = out["primary_terms"]
        out["adjacent_terms"] = _norm_list(out["adjacent_terms"] + move_to_adj)
        out["near_archetypes"] = out["adjacent_terms"]

    # NEW: Ensure industry synonyms are treated as primary candidates downstream by injecting into primary_terms list
    out["primary_terms"] = _norm_list(out["primary_terms"] + out["industry_synonyms"])
    out["primary_evidence"] = out["primary_terms"]

    return out

def _strip_json_block(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    m = re.search(r"(\{[\s\S]*\})", text)
    return m.group(1) if m else text


def _safe_json_loads(s: str) -> Dict[str, Any]:
    try:
        return json.loads(s)
    except Exception:
        return {}


def _ollama_json(prompt: str, model: str | None = None, timeout_s: int = 60) -> Dict[str, Any]:
    model = model or LLM_MODEL
    try:
        p = subprocess.run([OLLAMA_BIN, "run", model], input=prompt.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout_s)
        raw = p.stdout.decode("utf-8", errors="ignore").strip()
        js = _safe_json_loads(_strip_json_block(raw))
        return js if isinstance(js, dict) else {}
    except Exception:
        return {}


def _openai_json(prompt: str, model: str = "gpt-4o-mini", timeout_s: int = 60) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        return {}
    try:
        import requests
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        body = {"model": model, "messages": [{"role": "system", "content": "Return ONLY valid JSON. No prose."}, {"role": "user", "content": prompt}], "temperature": 0.2}
        r = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=timeout_s)
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return _safe_json_loads(_strip_json_block(content)) if content else {}
    except Exception:
        return {}


def _to_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, list):
        return x
    return []


def _norm_list(xs: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for t in _to_list(xs):
        s = (t or "").strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _fallback_taxonomy(industry: str, focus: str) -> Dict[str, Any]:
    return {
        "primary_terms": _norm_list(["core venue", "main service", "flagship experience"]),
        "adjacent_terms": _norm_list(["training", "lessons", "equipment", "retail accessories"]),
        "disqualifiers": _norm_list(["wholesale", "manufacturer", "distributor", "corporate office", "hq"]),
        "venue_terms": _norm_list(["lounge", "studio", "clinic", "cafe", "center"]),
        "exemplar_brands": [],
        "allow_t1_requires_primary": True,
        "notes": f"fallback: generic taxonomy for '{industry}'{(' / ' + focus) if focus else ''}",
        "primary_evidence": _norm_list(["core venue", "main service", "flagship experience"]),
        "near_archetypes": _norm_list(["training", "lessons", "equipment", "retail accessories"]),
        "off_target": _norm_list(["wholesale", "manufacturer", "distributor", "corporate office", "hq"]),
        "off_phrases": [],
    }

def build_taxonomy(industry: str, focus: str | None = None, provider: str = "ollama", model: str | None = None) -> Dict[str, Any]:
    industry = (industry or "").strip()
    focus = (focus or "").strip()

    prompt = PROMPT_TEMPLATE.format(industry=industry, focus=focus or "—")

    js: Dict[str, Any] = {}
    if provider == "ollama":
        js = _ollama_json(prompt, model=model or LLM_MODEL)
    elif provider == "openai":
        js = _openai_json(prompt, model=model or "gpt-4o-mini")
    else:
        js = {}

    if not isinstance(js, dict) or not js:
        return _fallback_taxonomy(industry, focus)

    primary_terms     = _norm_list(js.get("primary_terms") or js.get("primary_evidence"))
    adjacent_terms    = _norm_list(js.get("adjacent_terms") or js.get("near_archetypes") or js.get("secondary_evidence"))
    disqualifiers     = _norm_list(js.get("disqualifiers") or js.get("off_target")) + _norm_list(js.get("off_phrases"))
    venue_terms       = _norm_list(js.get("venue_terms"))
    exemplar_brands   = _norm_list(js.get("exemplar_brands"))
    allow_primary_req = bool(js.get("allow_t1_requires_primary", True))
    notes             = (js.get("notes") or "").strip()

    out = {
        "primary_terms": primary_terms,
        "adjacent_terms": adjacent_terms,
        "disqualifiers": disqualifiers,
        "venue_terms": venue_terms,
        "exemplar_brands": exemplar_brands,
        "allow_t1_requires_primary": allow_primary_req,
        "notes": notes,
        # Back-compat mirrors
        "primary_evidence": primary_terms,
        "near_archetypes": adjacent_terms,
        "off_target": disqualifiers,
        "off_phrases": [],
        "_provider": provider,
        "_model": model or (LLM_MODEL if provider == "ollama" else "gpt-4o-mini"),
    }

    # Ensure minimums using fallback if LLM returns too sparse or too generic
    fb = _fallback_taxonomy(industry, focus)
    if not out["primary_terms"] or any(p in {"experience","services","solutions"} for p in out["primary_terms"]):
        out["primary_terms"] = fb["primary_terms"]
        out["primary_evidence"] = out["primary_terms"]
    if not out["adjacent_terms"]:
        out["adjacent_terms"] = fb["adjacent_terms"]
        out["near_archetypes"] = out["adjacent_terms"]
    if not out["disqualifiers"]:
        out["disqualifiers"] = fb["disqualifiers"]
        out["off_target"] = out["disqualifiers"]
    if not out["venue_terms"]:
        out["venue_terms"] = fb["venue_terms"]

    # NEW: Demote overly generic primaries (no venue/tech modifiers) into adjacent_terms
    MODIFIER_TOKENS = {"simulator","simulation","virtual","screen","indoor","lounge","studio","bay","clinic","cafe"}
    keep_primary = []
    move_to_adj = []
    for p in out["primary_terms"]:
        canon = re.sub(r"[^a-z0-9]+", "", p.lower())
        if any(tok in canon for tok in MODIFIER_TOKENS):
            keep_primary.append(p)
        else:
            move_to_adj.append(p)

    if move_to_adj:
        # Update lists with de-dupe/normalize
        out["primary_terms"] = _norm_list(keep_primary)
        out["primary_evidence"] = out["primary_terms"]
        out["adjacent_terms"] = _norm_list(out["adjacent_terms"] + move_to_adj)
        out["near_archetypes"] = out["adjacent_terms"]

    return out




def build_industry_rubric(industry: str, focus: str | None = None, model: str | None = None) -> Dict[str, Any]:
    return build_taxonomy(industry, focus=focus, provider="ollama", model=model)
