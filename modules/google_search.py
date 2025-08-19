# ================================
# FILE: modules/google_search.py
# PURPOSE:
# - LLM-assisted profile builder (+persist to search_projects)
# - Preview gate (no API calls until approved)
# - Google Nearby grid execution (fixed radius)
# - Numeric scoring for rank ordering (within tier)
# - Final tier via choose_tier (GPT/Ollama) with fallback to numeric if disabled
# - Web signals enrichment (Schema.org types) -> small scoring nudge in shim
# - Broad keyword planner (LLM or fallback) + token alignment to avoid wrong-vertical bias
# - Persist results to search_results (incl. score, reasons, web_signals, website, tier_reason)
# ================================

from __future__ import annotations

import os, re, json, time, asyncio, traceback
from uuid import uuid4
from math import cos, radians
from typing import Any, Dict, List, Optional, Tuple, Set

import streamlit as st
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client
from shutil import which as _which

load_dotenv()

# ---- Env ----
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
SEARCH_RADIUS_KM_DEFAULT = 5.0

if not SUPABASE_URL or not SUPABASE_KEY:
    st.warning("Supabase credentials not set. Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your .env")

supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# ---- HTTP session with retries/backoff ----
def _requests_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        read=4,
        connect=3,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

_HTTP = _requests_session()

# ---- Import core helpers from phase1_lib ----
try:
    # Prefer relative import (we're inside modules/)
    from .phase1_lib import (
        IndustrySettings,
        DiscoveryParams,
        KNOWN_TYPES,
        default_settings_for_industry,
        build_profile_prompt,
        ollama_generate_profile_json,
        validate_profile_json,
        merge_profile,
        plan_queries,
        explain_scoring_rules,
        compose_keyword,
    )
    _PHASE1_LIB_SRC = "modules.phase1_lib"
except ModuleNotFoundError:
    from phase1_lib import (
        IndustrySettings,
        DiscoveryParams,
        KNOWN_TYPES,
        default_settings_for_industry,
        build_profile_prompt,
        ollama_generate_profile_json,
        validate_profile_json,
        merge_profile,
        plan_queries,
        explain_scoring_rules,
        compose_keyword,
    )
    _PHASE1_LIB_SRC = "phase1_lib"
except Exception as e:
    raise ImportError("Error inside phase1_lib while importing core names:\n" + traceback.format_exc()) from e

# ---- Tiering: final decision comes from this function ----
try:
    from .Phase1_tiering import choose_tier  # (industry, audit_cand, predicted_tier) -> (final_tier, source, reason, conf, raw_json)
except Exception as e:
    raise ImportError("Phase1_tiering.choose_tier missing or failed to import:\n" + traceback.format_exc()) from e

# ---- Scoring (ranking within tier). Try Phase1_scoring, else phase1_lib, else fallback shim ----
try:
    from .Phase1_scoring import score_candidate, choose_tier1_threshold, assign_predicted_tier  # type: ignore
except Exception:
    try:
        from .phase1_lib import score_candidate, choose_tier1_threshold, assign_predicted_tier  # type: ignore
    except Exception:
        # ---- Fallback minimal scoring shim ----
        def _text_hits(text: str, tokens: Set[str]) -> int:
            tx = (text or "").lower()
            return sum(1 for t in tokens if t and t.lower() in tx)

        def _schema_match_bonus(schema_types: List[str], industry: str) -> float:
            if not schema_types:
                return 0.0
            ind = (industry or "").lower()
            tset = {t.lower() for t in schema_types}
            if any(s in tset for s in ["cafeorcoffeeshop", "cafe", "coffeeshop"]):
                if "coffee" in ind: return 1.0
            if any(s in tset for s in ["hairsalon", "barbershop", "beautysalon"]):
                if any(w in ind for w in ["hair", "salon", "barber"]): return 1.0
            if any(s in tset for s in ["carwash", "automotivebusiness"]):
                if "car wash" in ind or "carwash" in ind: return 1.0
            return 0.0

        def score_candidate(cand: Dict[str, Any], settings: "IndustrySettings") -> Tuple[float, Any]:
            score = 0.0
            reasons: Dict[str, Any] = {}

            # types allow / soft-deny
            types = set((cand.get("types") or []))
            allow_hit = types & settings.allow_types
            if allow_hit:
                w = settings.weights.get("allow_types", 30.0)
                score += w; reasons[f"allow_types(+{int(w)})"] = sorted(allow_hit)
            deny_hit = types & settings.soft_deny_types
            if deny_hit:
                w = settings.weights.get("soft_deny_types", -20.0)
                score += w; reasons[f"soft_deny({int(w)})"] = sorted(deny_hit)

            # tokens
            blob = " ".join([str(cand.get("page_title","")), str(cand.get("headers","")), str(cand.get("text","")), str(cand.get("name",""))])
            pos_hits = _text_hits(blob, settings.name_positive)
            neg_hits = _text_hits(blob, settings.name_negative)
            if pos_hits:
                per = settings.weights.get("name_pos", 10.0)
                bonus = min(2, pos_hits) * per
                score += bonus; reasons[f"name_pos(+{int(bonus)})"] = pos_hits
            if neg_hits:
                per = abs(settings.weights.get("name_neg", -10.0))
                penalty = min(2, neg_hits) * per
                score -= penalty; reasons[f"name_neg(-{int(penalty)})"] = neg_hits

            # phrase-level negatives from planner (soft)
            blob_l = blob.lower()
            phrase_hits = []
            for phrase in (cand.get("exclude_phrases") or [])[:12]:
                ph = (phrase or "").lower()
                if ph and ph in blob_l:
                    phrase_hits.append(phrase)
            if phrase_hits:
                penalty_each = 3.0; max_penalty = 6.0
                total_penalty = min(max_penalty, penalty_each * len(phrase_hits))
                score -= total_penalty
                reasons[f"name_neg_phrase(-{int(total_penalty)})"] = phrase_hits

            # website
            if cand.get("website"):
                w = settings.weights.get("website", 5.0)
                score += w; reasons[f"website(+{int(w)})"] = True

            # rating/reviews
            rating = cand.get("rating"); reviews = cand.get("user_ratings_total") or 0
            if isinstance(rating, (int, float)) and rating >= 3.8 and reviews >= 25:
                w = settings.weights.get("rating", 5.0)
                score += w; reasons[f"rating(+{int(w)})"] = f"{rating} ({reviews})"

            # focus bonus
            focus_detail = cand.get("focus_detail")
            if focus_detail and focus_detail.lower() in blob.lower():
                w = settings.weights.get("focus_bonus", 8.0)
                score += w; reasons[f"focus(+{int(w)})"] = focus_detail

            # schema bonus
            schema_bonus_w = settings.weights.get("schema_bonus", 4.0)
            if _schema_match_bonus(cand.get("schema_types") or [], cand.get("industry") or "") > 0:
                score += schema_bonus_w; reasons[f"schema_bonus(+{int(schema_bonus_w)})"] = cand.get("schema_types")

            return max(0.0, min(100.0, score)), reasons

        def choose_tier1_threshold(scores: List[int], threshold_candidates: List[int], floor_ratio: float, target: int) -> int:
            if not scores:
                return max(65, min(threshold_candidates) if threshold_candidates else 65)
            likely_eligible = sum(1 for s in scores if s >= 50)
            floor = max(1, min(target, int(round(floor_ratio * max(1, likely_eligible)))))
            for t in threshold_candidates:
                if sum(1 for s in scores if s >= t) >= floor:
                    return int(t)
            return int(min(threshold_candidates) if threshold_candidates else 55)

        def assign_predicted_tier(score: int, tier1_threshold: int) -> int:
            if score >= tier1_threshold: return 1
            if score >= 50: return 2
            return 3

# ---- small utils ----

def _tokens_from_phrases(phrases):
    bag = (phrases or [])
    toks = set()
    for p in bag:
        for t in re.split(r"[^a-z0-9]+", (p or "").lower()):
            if len(t) >= 3:
                toks.add(t)
    return toks

def _sanitize_keywords(kw_list, breadth):
    junk = {"near me", "best", "cheap", "price", "prices", "hours", "open", "now", "phone", "address", "reviews"}
    out = []
    for k in (kw_list or []):
        s = (k or "").strip()
        if not s: continue
        sl = s.lower()
        if any(j in sl for j in junk): continue
        if len(sl.split()) > 4: continue
        out.append(s)
    cap = {"narrow": 2, "normal": 4, "wide": 8}.get((breadth or "normal").lower(), 4)
    return out[:cap]

def _ensure_weight_floors(settings: "IndustrySettings"):
    floors = {
        "website": 5.0,
        "rating": 5.0,
        "name_pos": 10.0,
        "name_neg": -10.0,
        "soft_deny_types": settings.weights.get("soft_deny_types", -20.0) or -20.0,
    }
    for k, v in floors.items():
        if k not in settings.weights or abs(settings.weights.get(k, 0.0)) < 1e-9:
            settings.weights[k] = v

# ---------------- LLM discovery planner (broad, industry-agnostic) ----------------

def _sanitize_types_against_keywords(settings: "IndustrySettings", planned_keywords, industry: str):
    bag = (industry or "") + " " + " ".join(planned_keywords or [])
    bag = bag.lower()

    def _tokify(s: str):
        return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if t]

    kept = set()
    for t in list(getattr(settings, "allow_types", set()) or []):
        ttoks = _tokify(t)
        if any(tok in bag for tok in ttoks):
            kept.add(t)

    if not kept:
        settings.allow_types = set()
        settings.weights["allow_types"] = min(settings.weights.get("allow_types", 30.0), 10.0)
        return None
    else:
        settings.allow_types = kept
        return sorted(kept)[0]

def _llm_discovery_plan_prompt(industry: str, location: str, focus_detail: Optional[str], breadth: str) -> str:
    return (
        "Plan discovery queries for Google Places Nearby (keyword=...). "
        "Return STRICT JSON with keys:\n"
        "  keywords: array of 8-20 short, high-recall phrases for this industry\n"
        "  exclude_keywords: array of 3-10 negatives (e.g., 'golf course','driving range','mini golf','outdoor')\n"
        "  types_primary: array of 0-5 precise Google place types (optional)\n"
        "  types_secondary: array of 0-10 broad Google place types (optional)\n"
        "Rules:\n"
        "- Keywords MUST be short and specific to finding candidates (no city names).\n"
        "- Include venue phrases (e.g., 'golf lounge','simulator bar','golf studio').\n"
        "- Include 6-10 brand/product tokens commonly used in this vertical (e.g., vendor names, systems).\n"
        "- If focus_detail is given, include it and 1-3 close variants.\n"
        "- No boolean operators; just plain phrases.\n"
        f"Industry: {industry}\n"
        f"Location: {location}\n"
        f"Breadth: {breadth}\n"
        f"Focus: {focus_detail or ''}\n\n"
        '{ "keywords": ["..."], "exclude_keywords": ["..."], "types_primary": [], "types_secondary": [] }'
    )

async def _ollama_json(prompt: str) -> Dict[str, Any]:
    """Call local Ollama and parse JSON response."""
    proc = await asyncio.create_subprocess_exec(
        "ollama", "run", LLM_MODEL,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate(input=prompt.encode("utf-8"))
    raw = stdout.decode("utf-8").strip()
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw) or re.search(r"(\{[\s\S]*\})", raw)
    try:
        return json.loads(m.group(1)) if m else {"keywords": [], "exclude_keywords": []}
    except Exception:
        return {"keywords": [], "exclude_keywords": []}

def _fallback_keyword_plan(industry: str, focus_detail: Optional[str], breadth: str) -> Dict[str, Any]:
    ind = (industry or "").strip()
    tokens = [t for t in re.split(r"[\s/,&-]+", ind.lower()) if t and t.isalpha()]
    base = list(dict.fromkeys([
        ind.lower(),
        " ".join(tokens),
        *(f"{w} {s}" for w in tokens for s in ["shop", "center", "studio", "lounge", "club", "bar"]),
        *(f"indoor {w}" for w in tokens),
        *(f"{w} simulator" for w in tokens),
        *(f"{w} simulators" for w in tokens),
        *(f"{w} training" for w in tokens),
        *(f"{w} practice" for w in tokens),
    ]))
    if focus_detail:
        base = [focus_detail] + base

    excludes = {"for sale", "used", "wholesale", "market", "outdoor"}
    bag = " ".join(base)
    if "golf" in bag:
        excludes.update({"golf course", "driving range", "mini golf", "country club"})

    base = [k.strip() for k in base if len(k.strip()) >= 3]
    cap = {"narrow": 4, "normal": 8, "wide": 12}.get((breadth or "normal").lower(), 8)

    return {
        "keywords": base[:cap],
        "exclude_keywords": sorted(excludes),
        "types_primary": [],
        "types_secondary": [],
        "source": "fallback",
        "max_keywords": {"narrow": 2, "normal": 4, "wide": 8}.get(breadth, 4),
    }

def _plan_seed_keywords(project: Dict[str, Any], settings: "IndustrySettings") -> Dict[str, Any]:
    industry = project.get("industry", "")
    location = project.get("location", "")
    focus_detail = project.get("focus_detail")
    breadth = (project.get("breadth") or "normal").lower()

    plan: Dict[str, Any] = {}
    if _which("ollama") is not None and bool(project.get("use_llm_planner", True)):
        prompt = _llm_discovery_plan_prompt(industry, location, focus_detail, breadth)
        try:
            plan = asyncio.run(_ollama_json(prompt))
        except Exception:
            plan = {}
        plan = plan if isinstance(plan, dict) else {}

    if not plan or not plan.get("keywords"):
        plan = _fallback_keyword_plan(industry, focus_detail, breadth)

    max_kw = {"narrow": 2, "normal": 4, "wide": 8}.get(breadth, 4)
    plan["max_keywords"] = max_kw
    plan["source"] = plan.get("source") or ("llm" if _which("ollama") else "fallback")

    # soften ambiguous excludes (hybrid-friendly)
    ambiguous = {"golf lessons", "pro shop", "golf store", "golf equipment rental", "sporting goods store", "recreation center"}
    if plan.get("exclude_keywords"):
        plan["exclude_keywords"] = [p for p in plan["exclude_keywords"] if (p or "").lower() not in ambiguous]

    # Persist to project
    try:
        if supabase and project.get("id"):
            supabase.table("search_projects").update({"planner_json": plan}).eq("id", project["id"]).execute()
    except Exception as e:
        st.warning(f"Could not persist planner_json on project: {e}")

    return plan

# -------------------------------------------------------------
# Google helpers
# -------------------------------------------------------------

def geocode_location(location: str) -> Tuple[float, float]:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_API_KEY}
    r = _HTTP.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Unable to geocode location: {data.get('status')}")
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])

def generate_grid(center_lat: float, center_lng: float, max_radius_km: float, step_km: float = 2.5) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = [(center_lat, center_lng)]
    steps = int(max_radius_km / step_km)
    dlat = step_km / 110.574
    dlng = step_km / (111.320 * max(1e-9, cos(radians(center_lat))))
    seen = {(center_lat, center_lng)}
    for ring in range(1, steps + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if abs(dx) != ring and abs(dy) != ring: continue
                lat = center_lat + dy * dlat
                lng = center_lng + dx * dlng
                if (lat, lng) not in seen:
                    pts.append((lat, lng)); seen.add((lat, lng))
    return pts

def google_nearby_search(keyword: str, lat: float, lng: float, radius_km: float, type_hint: Optional[str] = None) -> List[Dict[str, Any]]:
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params: Dict[str, Any] = {"location": f"{lat},{lng}", "radius": int(radius_km * 1000), "keyword": keyword, "key": GOOGLE_API_KEY}
    if type_hint: params["type"] = type_hint
    all_results: List[Dict[str, Any]] = []
    page_count = 0
    while True:
        r = _HTTP.get(url, params=params, timeout=30)
        data = r.json()
        if "error_message" in data:
            st.warning(f"Google API warning: {data['error_message']}")
            break
        results = data.get("results", []) or []
        all_results.extend(results)
        token = data.get("next_page_token")
        page_count += 1
        if not token or page_count >= 3:
            break
        # Google needs a short delay before using next_page_token
        time.sleep(2.0)
        params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    return all_results

def get_place_details(place_id: str) -> Dict[str, Any]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id, "key": GOOGLE_API_KEY,
        "fields": "address_components,types,formatted_phone_number,opening_hours,editorial_summary,website,rating,user_ratings_total",
    }
    try:
        r = _HTTP.get(url, params=params, timeout=30)
        return r.json().get("result", {}) or {}
    except Exception:
        return {}

# -------------------------------------------------------------
# Web scraping (safe & bounded)
# -------------------------------------------------------------

def _extract_schema_types_ldjson(soup: BeautifulSoup) -> List[str]:
    types: List[str] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        def _collect(obj):
            if isinstance(obj, dict):
                t = obj.get("@type")
                if isinstance(t, str): types.append(t)
                elif isinstance(t, list): types.extend([str(x) for x in t])
                for v in obj.values(): _collect(v)
            elif isinstance(obj, list):
                for v in obj: _collect(v)
        _collect(data)
    norm, seen = [], set()
    for t in types:
        tnorm = re.sub(r"[^A-Za-z]", "", t).lower()
        if tnorm and tnorm not in seen:
            norm.append(tnorm); seen.add(tnorm)
    return norm[:12]

def scrape_site(url: str) -> Dict[str, Any]:
    if not url: return {}
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = _HTTP.get(url, headers=headers, timeout=8)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype: return {}
        soup = BeautifulSoup(r.text, "html.parser")
        page_title = soup.title.string if soup.title else ""
        meta_desc = (soup.find("meta", attrs={"name": "description"}) or {}).get("content", "") or ""
        headers_text = " ".join(h.get_text(strip=True) for h in soup.find_all(re.compile("h[1-3]")))[:2000]
        visible_text = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:2000]
        schema_types = _extract_schema_types_ldjson(soup)
        return {
            "page_title": page_title,
            "meta_description": meta_desc,
            "headers": headers_text,
            "visible_text_blocks": visible_text,
            "schema_types": schema_types,
        }
    except Exception:
        return {}

# -------------------------------------------------------------
# Profile builder + persistence + preview gate
# -------------------------------------------------------------

def _finalize_profile(project: Dict[str, Any]) -> Tuple["IndustrySettings", Dict[str, Any], Optional[str], Optional[str]]:
    industry = (project.get("industry") or "").strip()
    location = (project.get("location") or "").strip()
    use_llm_profile = bool(project.get("use_llm_profile", True))
    focus_detail = project.get("focus_detail")
    focus_strict = bool(project.get("focus_strict", False))

    settings = default_settings_for_industry(industry)

    if use_llm_profile:
        prompt = build_profile_prompt(industry, location, KNOWN_TYPES)
        prof_raw = ollama_generate_profile_json(LLM_MODEL, OLLAMA_URL, prompt, temperature=0.2)
        prof = validate_profile_json(prof_raw or {}, KNOWN_TYPES)
        if prof:
            settings = merge_profile(settings, prof)

    settings.weights.setdefault("schema_bonus", 4.0)
    _ensure_weight_floors(settings)

    type_hint, keyword = compose_keyword(settings, focus_detail, focus_strict)

    # Align scoring tokens to planned keywords (avoid wrong-vertical bias)
    kw_plan = _plan_seed_keywords(project, settings)
    kw_list = (kw_plan.get("keywords") or [])
    ex_list = kw_plan.get("exclude_keywords") or []
    topic_tokens = _tokens_from_phrases(kw_list + [industry, focus_detail or ""])
    base_pos = {t for t in (getattr(settings, "name_positive", set()) or set()) if (t or "").lower() in topic_tokens}
    derived_pos = {t for t in topic_tokens if t in {"simulator", "indoor", "studio", "lounge", "bay", "suite"}}
    settings.name_positive = base_pos | derived_pos
    settings.name_negative = set(getattr(settings, "name_negative", set()) or set())

    profile_json = {
        "type_hint": type_hint, "keyword": keyword,
        "allow_types": sorted(list(settings.allow_types)),
        "soft_deny_types": sorted(list(settings.soft_deny_types)),
        "name_positive": sorted(list(settings.name_positive)),
        "name_negative": sorted(list(settings.name_negative)),
        "include_keywords": sorted(list(settings.include_keywords)),
        "exclude_keywords": sorted(list(settings.exclude_keywords)),
        "early_open_hour": settings.early_open_hour,
        "weights": settings.weights,
        "threshold_candidates": settings.threshold_candidates,
        "floor_ratio": settings.floor_ratio,
        "profile_source": getattr(settings, "profile_source", "defaults"),
        "focus_detail": focus_detail, "focus_strict": focus_strict,
        "planned_keywords": kw_list,
        "planned_excludes": ex_list,
    }

    try:
        if supabase:
            supabase.table("search_projects").update({
                "profile_json": profile_json, "use_llm_profile": use_llm_profile,
            }).eq("id", project["id"]).execute()
    except Exception as e:
        st.warning(f"Could not persist profile_json on project: {e}")

    return settings, profile_json, type_hint, keyword


def _render_preview(center_lat: float, center_lng: float, project: Dict[str, Any],
                    settings: "IndustrySettings", type_hint: Optional[str], keyword: Optional[str]) -> bool:
    params = DiscoveryParams(
        breadth=project.get("breadth", "normal"),
        target_count=int(project.get("target_count", 20)),
        max_radius_km=float(project.get("max_radius_km", 25.0)),
        grid_step_km=float(project.get("grid_step_km", 2.5)),
    )
    plan = plan_queries((center_lat, center_lng), params, settings,
                        focus_detail=project.get("focus_detail"),
                        focus_strict=bool(project.get("focus_strict", False)))

    kw_plan = _plan_seed_keywords(project, settings)
    raw_kw_list = (kw_plan.get("keywords") or [])
    kw_list = _sanitize_keywords(raw_kw_list, (project.get("breadth") or "normal").lower())
    ex_list = kw_plan.get("exclude_keywords") or []
    src = kw_plan.get("source", "fallback")

    sanitized_type_hint = _sanitize_types_against_keywords(settings, kw_list, project.get("industry", ""))
    type_hint = sanitized_type_hint

    st.subheader("Preview: Discovery Plan")
    st.caption("No API calls will be made until you approve.")

    c1, c2 = st.columns(2)
    with c1:
        est_calls = int((plan.get("grid_nodes", 0) or 0) * len(kw_list))
        st.markdown("**Search knobs**")
        st.json({
            "type_hint (for scoring only)": type_hint,
            "keyword (legacy single)": (kw_list[0] if kw_list else None),
            "planned_keywords": kw_list,
            "exclude_keywords": ex_list,
            "excludes_used_for": "scoring_only",
            "planner_source": src,
            "search_radius_km": float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT)),
            "grid_step_km": float(project.get("grid_step_km", 2.5)),
            "max_radius_km": float(project.get("max_radius_km", 25.0)),
            "breadth": params.breadth,
            "est_nearby_calls_max": est_calls,
        })
    sr_m = int(float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT)) * 1000)
    with c2:
        st.markdown("**Grid (sample)**")
        preview = plan.get("grid_preview", [])[:5]
        st.write(
            f"Grid nodes planned: {plan.get('grid_nodes', 0)}; showing {len(preview) * len(kw_list)} sample queries")
        for q in preview:
            loc = q["location"]  # "lat,lng"
            for k in kw_list:
                st.code(
                    f"location={loc} radius={sr_m} keyword={k}\n"
                    f"e.g., https://maps.googleapis.com/maps/api/place/nearbysearch/json"
                    f"?location={loc}&radius={sr_m}&keyword={requests.utils.quote(k)}&key=<API_KEY>"
                )

    st.markdown("**Scoring rubric (used next step)**")
    for line in explain_scoring_rules(settings, project.get("focus_detail"), bool(project.get("focus_strict", False))):
        st.write("- ", line)

    return st.button("✅ Approve & Run", type="primary")

# -------------------------------------------------------------
# Persistence
# -------------------------------------------------------------

def _upsert_result(row: Dict[str, Any]) -> None:
    """UPSERT by (project_id, place_id) to avoid 23505 conflicts."""
    if not supabase:
        st.error("Supabase client not initialized.")
        return
    try:
        # Try native upsert with on_conflict if available (supabase-py v2)
        supabase.table("search_results").upsert(row, on_conflict="project_id,place_id").execute()
    except Exception as e:
        # Fallback: update if exists else insert
        try:
            supabase.table("search_results").update(row)\
                .match({"project_id": row["project_id"], "place_id": row["place_id"]}).execute()
        except Exception:
            try:
                supabase.table("search_results").insert(row).execute()
            except Exception as ee:
                st.error(f"Error saving result: {getattr(ee, 'message', str(ee)) or ee}")

# -------------------------------------------------------------
# Main entry used by the UI
# -------------------------------------------------------------

def search_and_expand(project: dict) -> bool:
    """
    Execute Google -> Score -> choose_tier (GPT/Ollama) -> Save results.
    Returns True when the run completes.
    """
    radius_km = float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT))
    grid_step_km = float(project.get("grid_step_km", 2.5))
    target = int(project.get("target_count", 20))
    max_radius_km = float(project.get("max_radius_km", 25.0))

    # center + profile
    center_lat, center_lng = geocode_location(project["location"])
    settings, profile_json, type_hint, keyword = _finalize_profile(project)

    # grid search (broad, keyword-only to maximize recall)
    kw_plan = _plan_seed_keywords(project, settings)
    raw_kw_list = (kw_plan.get("keywords") or [])
    kw_list = _sanitize_keywords(raw_kw_list, (project.get("breadth") or "normal").lower())
    exclude_kw = set(kw_plan.get("exclude_keywords") or [])

    # Sanitize types so scoring doesn't award wrong categories
    type_hint = _sanitize_types_against_keywords(settings, kw_list, project.get("industry", ""))

    oversample_factor = float(project.get("oversample_factor", 2.0))
    stop_after = int(max(target, 1) * oversample_factor)

    pts = generate_grid(center_lat, center_lng, max_radius_km, step_km=grid_step_km)
    found: Dict[str, Dict[str, Any]] = {}
    prog = st.progress(0.0, text="Collecting businesses from Google…")

    for i, (lat, lng) in enumerate(pts):
        prog.progress(i / max(1, len(pts)), text=f"Searching at ({lat:.4f}, {lng:.4f})")
        for k in kw_list:
            # Excludes are NOT used to filter queries (recall-first)
            try:
                results = google_nearby_search(k, lat, lng, radius_km, type_hint=None)  # no type filter
            except requests.exceptions.ReadTimeout:
                # continue on timeouts
                continue
            for r in results:
                pid = r.get("place_id")
                if pid and pid not in found:
                    found[pid] = r
            if len(found) >= stop_after:
                break
        if len(found) >= stop_after:
            break

    prog.empty()

    # scoring (ranking within tiers)
    st.write(f"Scoring + classifying {len(found)} unique businesses…")
    scored_list: List[Dict[str, Any]] = []
    for place in found.values():
        place_id = place.get("place_id")
        details = get_place_details(place_id)
        website = details.get("website") or place.get("website") or ""
        scraped = scrape_site(website) if website else {}

        cand = {
            "name": place.get("name", ""),
            "types": details.get("types", []) or place.get("types", []),
            "website": bool(website),
            "rating": details.get("rating"),
            "user_ratings_total": details.get("user_ratings_total"),
            "hour_open": (details.get("opening_hours", {}) or {}).get("periods", []),
            "page_title": scraped.get("page_title", ""),
            "headers": scraped.get("headers", ""),
            "text": scraped.get("visible_text_blocks", ""),
            "schema_types": scraped.get("schema_types", []),
            "focus_detail": profile_json.get("focus_detail"),
            "focus_strict": profile_json.get("focus_strict"),
            "industry": project.get("industry", ""),
            "exclude_phrases": list(exclude_kw),  # soft phrase-level negatives in shim
        }
        score, reasons = score_candidate(cand, settings)
        scored_list.append({
            "place": place, "details": details, "scraped": scraped,
            "score": int(round(score)), "reasons": reasons,
        })

    # dynamic threshold & predicted tier (used as context/fallback only)
    scores_only = [row["score"] for row in scored_list]
    threshold_candidates = settings.threshold_candidates or [80, 75, 70, 65, 60, 55]
    tier1_threshold = choose_tier1_threshold(
        scores_only, threshold_candidates=threshold_candidates,
        floor_ratio=settings.floor_ratio, target=target,
    )

    # choose_tier + persist
    async def _process():
        prog2 = st.progress(0.0, text="Choosing tiers and saving…")
        total = max(1, len(scored_list))
        for i, row in enumerate(scored_list):
            place = row["place"]; details = row["details"]; scraped = row["scraped"]
            score = row["score"]

            # base attributes
            lat = place.get("geometry", {}).get("location", {}).get("lat")
            lng = place.get("geometry", {}).get("location", {}).get("lng")
            place_id = place.get("place_id")
            name = place.get("name", "")
            address = place.get("vicinity", "")

            # predicted tier from numeric (context for LLM; not final)
            predicted_tier = assign_predicted_tier(score, tier1_threshold)

            # compact candidate for audit/tiering
            audit_cand = {
                "name": name,
                "types": details.get("types", []) or place.get("types", []),
                "rating": details.get("rating"),
                "reviews": details.get("user_ratings_total"),
                "website": details.get("website") or place.get("website") or "",
                "page_title": scraped.get("page_title", ""),
                "headers": scraped.get("headers", ""),
                "text": scraped.get("visible_text_blocks", ""),
                "numeric_score": score,
                "predicted_tier": predicted_tier,
                "tier1_threshold": tier1_threshold,
            }
            # NEW: pass the project toggle (default True if not present)
            audit_cand["_llm_tiering"] = bool(project.get("use_llm_tiering", True))

            # Final tier decision (GPT/Ollama when enabled; numeric otherwise inside choose_tier)
            final_tier, tier_source, tier_reason, audit_conf, _raw = choose_tier(
                project.get("industry", ""),
                audit_cand,
                predicted_tier,
            )

            # Address bits
            city = state = zipc = ""
            for comp in (details.get("address_components") or []):
                types = comp.get("types", [])
                if "locality" in types or "postal_town" in types: city = comp.get("long_name", "")
                elif "administrative_area_level_1" in types: state = comp.get("short_name", "")
                elif "postal_code" in types: zipc = comp.get("long_name", "")

            web_signals = {"schema_types": scraped.get("schema_types", [])}

            row_out = {
                "id": str(uuid4()),
                "project_id": project["id"],
                "place_id": place_id,
                "name": name,
                "address": address,
                "city": city, "state": state, "zip": zipc,
                "website": audit_cand["website"],  # persist website for manual inspection
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                "latitude": lat, "longitude": lng,
                "category": "",
                "page_title": scraped.get("page_title", ""),
                "eligibility_score": score,  # ranking within tier
                "score_reasons": json.dumps(row["reasons"]) if not isinstance(row["reasons"], str) else row["reasons"],
                "tier": final_tier,
                "tier_reason": tier_reason,          # short, human-readable reason
                "tier_source": tier_source,          # "llm_override" / "gpt4" / "numeric" depending on choose_tier impl
                "audit_confidence": audit_conf,      # 0..1 if LLM used

                "manual_override": False,
                "profile_source": getattr(settings, "profile_source", "defaults"),
                "web_signals": web_signals,
            }

            _upsert_result(row_out)
            prog2.progress((i + 1) / total, text=f"Processed {i + 1} of {total}")
        prog2.empty()

    asyncio.run(_process())
    st.success("All results scored, tiered, and saved.")
    return True

# Explicit exports
__all__ = [
    "geocode_location",
    "_finalize_profile",
    "_render_preview",
    "search_and_expand",
]
