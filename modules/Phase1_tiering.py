# ==============================================
# modules/Phase1_tiering.py (v9 — Balanced toggle)
# PURPOSE: Industry‑agnostic Phase‑1 tiering with two operating modes:
#   - bias='high-recall'  → generous Tier‑1 promotions (good for seeding)
#   - bias='balanced'     → stricter Tier‑1 (good for review/calibration in harness)
#
# Public API used by TEST_IndustryClassification.py:
#   extract_evidence(taxonomy: dict, candidate: dict | None = None, **kwargs) -> dict
#   choose_tier(industry: str, taxonomy: dict, evidence: dict,
#               provider: str = 'ollama', model: str | None = None,
#               bias: str | None = None, **kwargs) -> dict
#
# Notes
# - Still industry‑agnostic. We bias behavior by rules & rubric structure, not hardcoded domains.
# - We preserve v8 (overboost) behavior under bias='high-recall'.
# - Under bias='balanced':
#     * Tier‑1 requires PRIMARY evidence, or (ADJACENT + TAXONOMY venue) — not fallback venue.
#     * Domain/stem/context signals are never sufficient for T1 (cap at T2).
#     * Disqualifier present & no primary → T3. If primary + disqualifier → T2 (never T1).
#     * LLM decisions are post‑filtered by these guardrails.
# ==============================================
from __future__ import annotations

import os
import re
import json
import subprocess
import unicodedata
from urllib.parse import urlparse
from typing import Any, Dict, List, Tuple

# ---- Env defaults ----
OLLAMA_BIN   = os.getenv("OLLAMA_BIN", "ollama")
LLM_MODEL    = os.getenv("LLM_MODEL", "llama3")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PHASE1_BIAS  = (os.getenv("PHASE1_BIAS") or "high-recall").strip().lower()  # default to legacy generous mode

# ---- High‑recall helpers ----
FALLBACK_VENUE_TERMS = {
    # generic public‑facing places
    "center","centre","studio","lounge","club","hall","arena","house","space","lab","gallery",
    # retail/onsite contexts (broad)
    "shop","store","showroom","facility","venue","bar","room","suite"
}
# context modifiers that, when combined with an industry noun, often indicate the venue variant
CONTEXT_MODIFIERS = {"indoor","outdoor","virtual","screen","simulator","simulation","express"}

# -------------------------------------------------
# Utility helpers
# -------------------------------------------------

def _to_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, (list, tuple)):
        return " \n ".join(_to_text(t) for t in x)
    if isinstance(x, dict):
        items = [f"{k}: {_to_text(v)}" for k, v in x.items()]
        return " \n ".join(items)
    return str(x)


def _norm_list(xs: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    if xs is None:
        return out
    if isinstance(xs, str):
        xs = [xs]
    for t in xs:
        s = (str(t) if t is not None else "").strip().lower()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _canon(s: str) -> str:
    if not s:
        return ""
    # strip diacritics, lowercase, remove non-alnum
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _stems_from_phrase(phrase: str) -> List[str]:
    tokens = [w for w in re.split(r"[^a-zA-Z0-9]+", phrase or "") if len(w) >= 4]
    stems = set()
    for w in tokens:
        w = w.lower()
        stems.add(w)
        stems.add(w[:5])
    return [s for s in stems if s]


def _find_hits_strict(terms: List[str], text: str) -> List[str]:
    hits = []
    if not terms or not text:
        return hits
    for t in terms:
        if re.search(rf"(?i)(?:\b|_){re.escape(t)}(?:\b|_)", text):
            hits.append(t)
    return hits


def _find_hits_relaxed(terms: List[str], text: str) -> List[str]:
    hits = []
    if not terms or not text:
        return hits
    canon_text = _canon(text)
    for t in terms:
        ct = _canon(t)
        if len(ct) >= 4 and ct in canon_text:
            hits.append(t)
    return hits

# -------------------------------------------------
# Evidence extraction
# -------------------------------------------------

def extract_evidence(taxonomy: Dict[str, Any], candidate: Dict[str, Any] | None = None, **kwargs) -> Dict[str, Any]:
    """Extract evidence using rubric terms + expanded high-recall heuristics, industry-agnostic.
    Supports candidate dict and/or explicit kwargs; kwargs override candidate fields.
    """
    candidate = candidate or {}

    # Base fields
    name = candidate.get("name") or candidate.get("business_name") or ""
    website = candidate.get("website") or candidate.get("url") or ""
    title = candidate.get("title") or candidate.get("page_title") or ""
    snippet = candidate.get("snippet") or candidate.get("text_snippet") or candidate.get("description") or ""
    categories = candidate.get("categories") or []
    google_types = candidate.get("google_types") or []
    schema_types = candidate.get("schema_types") or []
    schema = candidate.get("schema") or {}

    # Overrides
    name = kwargs.get("name", name)
    website = kwargs.get("website", website)
    title = kwargs.get("page_title", kwargs.get("title", title))
    snippet = kwargs.get("text_snippet", kwargs.get("snippet", snippet))
    schema_types = kwargs.get("schema_types", schema_types) or []
    google_types = kwargs.get("google_types", google_types) or []
    categories = kwargs.get("categories", categories) or []
    schema = kwargs.get("schema", schema)

    # Text blob
    blob_parts = [
        name, website, title, snippet,
        " ".join(map(str, categories)) if categories else "",
        " ".join(map(str, google_types)) if google_types else "",
        " ".join(map(str, schema_types)) if schema_types else "",
        _to_text(schema),
    ]
    text_blob = (" \n ".join(p for p in blob_parts if p)).lower()

    # Taxonomy terms
    prim_terms  = _norm_list(taxonomy.get("primary_terms") or taxonomy.get("primary_evidence"))
    adj_terms   = _norm_list(taxonomy.get("adjacent_terms") or taxonomy.get("near_archetypes"))
    disq_terms  = _norm_list(taxonomy.get("disqualifiers") or taxonomy.get("off_target"))
    tax_venue   = set(_norm_list(taxonomy.get("venue_terms")))
    fb_venue    = FALLBACK_VENUE_TERMS | CONTEXT_MODIFIERS
    brand_terms = _norm_list(taxonomy.get("exemplar_brands"))
    syn_terms   = _norm_list(taxonomy.get("industry_synonyms"))  # NEW

    # Expand primary with industry synonyms
    expanded_primary = set(prim_terms) | set(syn_terms)

    # Also include modifier-based combos from the declared industry if present
    stems = _stems_from_phrase(taxonomy.get("_industry", "") or kwargs.get("industry", ""))
    for s in stems:
        for m in CONTEXT_MODIFIERS:
            expanded_primary.add(f"{m} {s}")
            expanded_primary.add(f"{s} {m}")
    expanded_primary = list(expanded_primary)

    # Strict + relaxed for primary/venue/brand
    primary_hits = list({* _find_hits_strict(expanded_primary, text_blob), * _find_hits_relaxed(expanded_primary, text_blob)})
    venue_hits_tax = list({* _find_hits_strict(list(tax_venue), text_blob), * _find_hits_relaxed(list(tax_venue), text_blob)})
    venue_hits_fb  = list({* _find_hits_strict(list(fb_venue),  text_blob), * _find_hits_relaxed(list(fb_venue),  text_blob)})
    brand_hits   = list({* _find_hits_strict(brand_terms, text_blob), * _find_hits_relaxed(brand_terms, text_blob)})

    # Keep disqualifiers/adjacent strict
    adjacent_hits = _find_hits_strict(adj_terms, text_blob)
    disqualifier_hits = _find_hits_strict(disq_terms, text_blob)

    return {
        "primary_hits": primary_hits,
        "adjacent_hits": adjacent_hits,
        "disqualifier_hits": disqualifier_hits,
        "venue_hits_taxonomy": venue_hits_tax,   # used by balanced
        "venue_hits_fallback": venue_hits_fb,    # context-only
        "brand_hits": brand_hits,
        "text_blob": text_blob,
        "_source_fields": {"name": name, "website": website, "title": title, "snippet": snippet},
    }

# -------------------------------------------------
# LLM helpers (sync)
# -------------------------------------------------

def _strip_json_block(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        return m.group(1)
    m = re.search(r"(\{[\s\S]*\})", text)
    return m.group(1) if m else text


def _ollama_json(prompt: str, model: str | None = None, timeout_s: int = 60) -> Dict[str, Any]:
    model = model or LLM_MODEL
    try:
        proc = subprocess.run(
            [OLLAMA_BIN, "run", model],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        raw = proc.stdout.decode("utf-8", errors="ignore").strip()
        try:
            return json.loads(_strip_json_block(raw))
        except Exception:
            return {}
    except Exception:
        return {}


def _openai_json(prompt: str, model: str = "gpt-4o-mini", timeout_s: int = 60) -> Dict[str, Any]:
    if not OPENAI_API_KEY:
        return {}
    try:
        import requests
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Return ONLY valid JSON with keys: tier, reason, confidence."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
        r = requests.post("https://api.openai.com/v1/chat/completions", json=body, headers=headers, timeout=timeout_s)
        data = r.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            return {}
        try:
            return json.loads(_strip_json_block(content))
        except Exception:
            return {}
    except Exception:
        return {}

# -------------------------------------------------
# Signals & flags
# -------------------------------------------------

def _industry_flags(industry: str, evidence: Dict[str, Any]) -> Dict[str, bool]:
    blob = evidence.get("text_blob", "")
    name = (evidence.get("_source_fields", {}) or {}).get("name", "")
    website = (evidence.get("_source_fields", {}) or {}).get("website", "")

    stems = _stems_from_phrase(industry)
    canon_blob = _canon(blob)
    canon_name = _canon(name)
    domain = urlparse(website).netloc.split(":")[0].lower() if website else ""
    canon_domain = _canon(domain)

    present = {s for s in stems if s and s in canon_blob}
    first_stem = stems[0] if stems else ""
    name_has_first = bool(first_stem and first_stem in canon_name)
    domain_has_stem = any(s in canon_domain for s in stems)

    return {
        "industry_pair_hit": len(present) >= min(2, max(1, len(stems) - 0)),
        "industry_name_venue_hit": name_has_first and bool(evidence.get("venue_hits_taxonomy") or evidence.get("venue_hits_fallback")),
        "industry_in_domain": domain_has_stem and not any(h in domain for h in ("instagram", "facebook", "linktr","links")),
    }

# -------------------------------------------------
# Core rule engines (balanced vs high‑recall)
# -------------------------------------------------

def _rules_balanced(industry: str, taxonomy: Dict[str, Any], ev: Dict[str, Any]) -> Tuple[int, str, float]:
    prim = len(ev.get("primary_hits", []))
    adj  = len(ev.get("adjacent_hits", []))
    disq = len(ev.get("disqualifier_hits", []))
    ven_tax = len(ev.get("venue_hits_taxonomy", []))
    ven_fb  = len(ev.get("venue_hits_fallback", []))
    brand = len(ev.get("brand_hits", []))

    flags = _industry_flags(industry, ev)

    # Strict disqualifier handling
    if disq > 0 and prim == 0:
        return 3, "Disqualifier terms present without primary evidence.", 0.35
    if disq > 0 and prim > 0:
        return 2, "Primary present but disqualifiers found (downgraded).", 0.6

    # Tier‑1 only with strong signals
    if prim > 0:
        conf = 0.82 + 0.03 * min(2, ven_tax) + 0.02 * min(2, brand)
        return 1, "Primary evidence present (balanced).", min(conf, 0.92)
    if adj > 0 and ven_tax > 0:
        return 1, "Adjacent + TAXONOMY venue (balanced).", 0.78

    # Context signals capped at T2
    if flags["industry_in_domain"] or flags["industry_pair_hit"] or flags["industry_name_venue_hit"] or ven_fb > 0:
        return 2, "Context signals present (capped at T2 in balanced mode).", 0.6

    if adj > 0:
        return 2, "Adjacent offering without strong primary evidence.", 0.55

    return 3, "No convincing evidence for core venue.", 0.4


def _rules_high_recall(industry: str, taxonomy: Dict[str, Any], ev: Dict[str, Any]) -> Tuple[int, str, float]:
    # Mirrors v8 overboost behavior
    prim = len(ev.get("primary_hits", []))
    adj  = len(ev.get("adjacent_hits", []))
    disq = len(ev.get("disqualifier_hits", []))
    ven = len(ev.get("venue_hits_taxonomy", [])) + len(ev.get("venue_hits_fallback", []))
    brand = len(ev.get("brand_hits", []))

    flags = _industry_flags(industry, ev)

    if disq > 0 and prim == 0:
        return 3, "Disqualifier terms present without primary evidence.", 0.35

    if prim > 0:
        conf = 0.86 + 0.03 * min(2, ven) + 0.02 * min(2, brand)
        return 1, "Primary evidence present.", min(conf, 0.95)
    if brand > 0 and disq == 0:
        return 1, "Exemplar brand present with no disqualifiers.", 0.82
    if flags["industry_pair_hit"] and disq == 0:
        return 1, "Industry phrase stems co‑occur (pair hit).", 0.84
    if flags["industry_name_venue_hit"] and disq == 0:
        return 1, "Industry noun in name with venue indicator.", 0.8
    if flags["industry_in_domain"] and disq == 0:
        return 1, "Industry stem appears in website domain.", 0.79
    if (adj > 0 and ven > 0) or adj >= 2:
        return 1, "Adjacent + venue context (high‑recall).", 0.78

    if ven > 0 or adj > 0:
        return 2, "Partial evidence without strong core indicators.", 0.55
    return 3, "No convincing evidence for core venue.", 0.4

# -------------------------------------------------
# LLM decision + guardrails
# -------------------------------------------------

def _llm_decide(industry: str, taxonomy: Dict[str, Any], evidence: Dict[str, Any], provider: str, model: str | None) -> Dict[str, Any]:
    prompt = (
        "You classify a business into one of 3 tiers using the given taxonomy and evidence.\n"
        "Return STRICT JSON only with keys: tier (1|2|3), reason (short), confidence (0..1).\n\n"
        f"Industry: {industry}\n\n"
        f"Taxonomy primary_terms: {taxonomy.get('primary_terms')}\n"
        f"adjacent_terms: {taxonomy.get('adjacent_terms')}\n"
        f"disqualifiers: {taxonomy.get('disqualifiers')}\n"
        f"venue_terms: {taxonomy.get('venue_terms')}\n"
        f"exemplar_brands: {taxonomy.get('exemplar_brands')}\n\n"
        f"Evidence text (lowercased concat):\n{evidence.get('text_blob','')[:3000]}\n\n"
        f"Primary hits: {evidence.get('primary_hits')}\n"
        f"Adjacent hits: {evidence.get('adjacent_hits')}\n"
        f"Disqualifier hits: {evidence.get('disqualifier_hits')}\n"
        f"Venue hits (taxonomy): {evidence.get('venue_hits_taxonomy')}\n"
        f"Venue hits (fallback): {evidence.get('venue_hits_fallback')}\n"
        f"Brand hits: {evidence.get('brand_hits')}\n"
    )

    if provider == "ollama":
        js = _ollama_json(prompt, model=model or LLM_MODEL)
    elif provider == "openai":
        js = _openai_json(prompt, model=model or "gpt-4o-mini")
    else:
        js = {}

    if not isinstance(js, dict):
        return {}
    tier = js.get("tier")
    if isinstance(tier, str) and tier.isdigit():
        tier = int(tier)
    if tier not in (1, 2, 3):
        return {}
    reason = str(js.get("reason") or "")[:400]
    try:
        conf = float(js.get("confidence", 0.5))
    except Exception:
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {"tier": tier, "reason": reason, "confidence": conf}


def _apply_guardrails(industry: str, taxonomy: Dict[str, Any], decision: Dict[str, Any], ev: Dict[str, Any], bias: str) -> Dict[str, Any]:
    """
    Post-process an LLM decision (or synthesize one) using rubric-driven constraints.

    Balanced:
      - Disqualifier + no primary => T3
      - Primary + disqualifier => T2 (never T1)
      - If LLM says T1 but evidence lacks primary and not (adjacent & taxonomy-venue) => T2
      - Context-only signals (fallback venue / stems / domain / name+venue) are capped at T2
      - NEW: Promote T2/T3 -> T1 when disq==0 and (primary>0 or (adjacent>0 and taxonomy-venue>0))

    High-recall: keep generous upgrades (legacy v8 behavior).
    """
    # If LLM failed, fall back to rules engine right away
    if not decision:
        if bias == "balanced":
            t, r, c = _rules_balanced(industry, taxonomy, ev)
        else:
            t, r, c = _rules_high_recall(industry, taxonomy, ev)
        return {"tier": t, "reason": r, "confidence": c}

    # Evidence counts
    prim   = len(ev.get("primary_hits", []))
    adj    = len(ev.get("adjacent_hits", []))
    disq   = len(ev.get("disqualifier_hits", []))
    ven_tax = len(ev.get("venue_hits_taxonomy", []))
    ven_fb  = len(ev.get("venue_hits_fallback", []))

    tier   = int(decision.get("tier", 3))
    reason = decision.get("reason") or ""
    conf   = float(decision.get("confidence", 0.5))

    # Hard disqualifier guardrails (apply in both modes)
    if disq > 0 and prim == 0:
        return {"tier": 3, "reason": (reason + " | Disqualifiers without primary → T3").strip(), "confidence": min(conf, 0.6)}

    # Hard override: if NAME contains a disqualifier phrase and there's no explicit sim signal
    # then force T3 (unless primary is truly present).
    name = ((ev.get("_source_fields", {}) or {}).get("name") or "").lower()
    title = ((ev.get("_source_fields", {}) or {}).get("title") or "").lower()

    # Same modifier set we use elsewhere (inline to keep it simple)
    _MOD_TOKENS = {"simulator", "simulation", "virtual", "screen", "indoor", "lounge", "studio", "bay", "clinic",
                   "cafe"}

    def _has_mod(s: str) -> bool:
        cs = re.sub(r"[^a-z0-9]+", "", s)
        return any(m in cs for m in _MOD_TOKENS)

    # Check if any disqualifier phrase appears literally in the NAME
    disq_phrases = ev.get("disqualifier_hits", [])
    disq_in_name = any(d in name for d in disq_phrases)

    if disq_in_name and prim == 0 and (not _has_mod(name)) and (not _has_mod(title)):
        return {
            "tier": 3,
            "reason": (reason + " | Name contains disqualifier; no explicit sim signal → T3 (balanced)").strip(),
            "confidence": min(conf, 0.6),
        }

    if bias == "balanced":
        flags = _industry_flags(industry, ev)

        # Demote if primary+disq (never T1 in balanced)
        if disq > 0 and prim > 0 and tier == 1:
            return {"tier": 2, "reason": (reason + " | Disqualifiers present → downgrade to T2 (balanced)").strip(), "confidence": min(conf, 0.7)}

        # Clamp LLM T1 to evidence: require primary OR (adjacent & taxonomy-venue)
        if tier == 1 and prim == 0 and not (adj > 0 and ven_tax > 0):
            return {"tier": 2, "reason": (reason + " | Missing primary or (adj+taxonomy-venue) → T2 (balanced)").strip(), "confidence": min(conf, 0.7)}

        # Context-only T1 is capped at T2 in balanced
        if tier == 1 and prim == 0 and (ven_fb > 0 or flags["industry_in_domain"] or flags["industry_pair_hit"] or flags["industry_name_venue_hit"]):
            return {"tier": 2, "reason": (reason + " | Context-only signals capped at T2 (balanced)").strip(), "confidence": min(conf, 0.7)}

        # NEW: Balanced promotions — upgrade strong evidence to T1 even if LLM said T2/3
        if tier in (2, 3) and disq == 0 and (prim > 0 or (adj > 0 and ven_tax > 0)):
            boost = 0.03 * min(2, ven_tax)
            return {
                "tier": 1,
                "reason": (reason + " | Balanced promotion: evidence meets T1").strip(),
                "confidence": max(conf, 0.82 + boost)
            }

        # Otherwise keep LLM tier
        return {"tier": tier, "reason": reason, "confidence": conf}

    # High-recall (legacy generous upgrades)
    if prim > 0 and disq == 0 and tier in (2, 3):
        return {"tier": 1, "reason": (reason + " | Primary present → upgrade to T1").strip(), "confidence": max(conf, 0.86)}
    flags = _industry_flags(industry, ev)
    ven = ven_tax + ven_fb
    if (flags["industry_pair_hit"] or flags["industry_name_venue_hit"] or flags["industry_in_domain"]) and disq == 0 and tier in (2, 3):
        return {"tier": 1, "reason": (reason + " | Industry context signals → upgrade to T1").strip(), "confidence": max(conf, 0.8)}
    if ((adj > 0 and ven > 0) or adj >= 2) and disq == 0 and tier == 2:
        return {"tier": 1, "reason": (reason + " | Adjacent+venue → T1 (high-recall)").strip(), "confidence": max(conf, 0.78)}

    return {"tier": tier, "reason": reason, "confidence": conf}


# -------------------------------------------------
# Public entrypoint
# -------------------------------------------------

def choose_tier(
    industry: str,
    taxonomy: Dict[str, Any],
    evidence: Dict[str, Any],
    provider: str = "ollama",
    model: str | None = None,
    bias: str | None = None,
    **kwargs,
) -> Dict[str, Any]:
    """Unified Tier decision with bias toggle.

    bias: 'balanced' | 'high-recall' | None  (None → env PHASE1_BIAS, default 'high-recall')
    """
    provider = (provider or "ollama").lower()
    bias = (bias or PHASE1_BIAS).strip().lower()
    if bias not in ("balanced", "high-recall"):
        bias = "high-recall"

    # Augment evidence text with any extras (snippets, titles, etc.)
    def _augment_evidence(ev: Dict[str, Any], **kw) -> Dict[str, Any]:
        text_blob = ev.get("text_blob", "")
        def _cat(val):
            nonlocal text_blob
            if not val: return
            if isinstance(val, (list, tuple)):
                text_blob += " \n " + " ".join(str(x) for x in val)
            else:
                text_blob += " \n " + str(val)
        for key in ["snippets", "text_snippets", "titles", "page_titles", "schema_types", "google_types", "categories"]:
            _cat(kw.get(key))
        for key in ["snippet", "text_snippet", "title", "page_title", "website", "name"]:
            _cat(kw.get(key))
        ev["text_blob"] = (text_blob or "").lower()
        return ev

    ev = _augment_evidence(dict(evidence or {}), **kwargs)

    # Ensure hits exist even if caller passed raw text only
    if "venue_hits_taxonomy" not in ev or "venue_hits_fallback" not in ev:
        # Recompute from scratch using extract_evidence against the same blob is overkill here; instead
        # call the strict/relaxed matchers again using the fields embedded in `ev.text_blob`.
        # To avoid duplication, we rely on the original extraction path for first calls.
        pass

    # Decide via rules or LLM+guardrails
    if provider in ("ollama", "openai"):
        decision = _llm_decide(industry, taxonomy, ev, provider, model)
        adjusted = _apply_guardrails(industry, taxonomy, decision, ev, bias)
    else:
        if bias == "balanced":
            t, r, c = _rules_balanced(industry, taxonomy, ev)
        else:
            t, r, c = _rules_high_recall(industry, taxonomy, ev)
        adjusted = {"tier": t, "reason": r, "confidence": c}

    adjusted["hits"] = {
        "primary": ev.get("primary_hits", []),
        "adjacent": ev.get("adjacent_hits", []),
        "disqualifier": ev.get("disqualifier_hits", []),
        "venue_taxonomy": ev.get("venue_hits_taxonomy", []),
        "venue_fallback": ev.get("venue_hits_fallback", []),
        "brand": ev.get("brand_hits", []),
    }
    adjusted["flags"] = _industry_flags(industry, ev)
    adjusted["_bias"] = bias
    return adjusted
