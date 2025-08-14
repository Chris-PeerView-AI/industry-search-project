# ================================
# FILE: modules/google_search.py
# PURPOSE:
# - LLM-assisted profile builder (+persist to search_projects)
# - Preview gate (no API calls until approved)
# - Google Nearby grid execution (fixed radius)
# - Numeric scoring + dynamic threshold with LLM audit override
# - Persist results to search_results (incl. score & reasons)
# ================================

from __future__ import annotations

import os, re, json, time, asyncio
from uuid import uuid4
from math import cos, radians
from typing import Any, Dict, List, Optional, Tuple, Set

import streamlit as st
import requests
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
ENABLE_LLM_AUDIT = (_which("ollama") is not None) and (os.getenv("ENABLE_LLM_AUDIT", "1") == "1")

# ---- Import core helpers from phase1_lib with robust error surfacing ----
import traceback
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
    # Fallback to repo root version
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
    raise ImportError("Error inside modules.phase1_lib while importing core names:\n" + traceback.format_exc()) from e

# Try to import Step-2 scoring; if missing, define safe fallbacks
try:
    from .phase1_lib import score_candidate, choose_tier1_threshold, assign_predicted_tier  # type: ignore
except Exception:
    # ---- Fallback scoring implementations (shim) ----
    def _text_hits(text: str, tokens: Set[str]) -> int:
        tx = (text or "").lower()
        return sum(1 for t in tokens if t and t.lower() in tx)

    def score_candidate(cand: Dict[str, Any], settings: IndustrySettings) -> Tuple[float, Any]:
        score = 0.0
        reasons: Dict[str, Any] = {}

        types = set((cand.get("types") or []))
        allow_hit = types & settings.allow_types
        if allow_hit:
            w = settings.weights.get("allow_types", 30.0)
            score += w; reasons[f"allow_types(+{int(w)})"] = sorted(allow_hit)

        deny_hit = types & settings.soft_deny_types
        if deny_hit:
            w = settings.weights.get("soft_deny_types", -20.0)
            score += w; reasons[f"soft_deny({int(w)})"] = sorted(deny_hit)

        blob = " ".join([str(cand.get("page_title","")), str(cand.get("headers","")),
                         str(cand.get("text","")), str(cand.get("name",""))])
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

        if cand.get("website"):
            w = settings.weights.get("website", 5.0)
            score += w; reasons[f"website(+{int(w)})"] = True

        rating = cand.get("rating"); reviews = cand.get("user_ratings_total") or 0
        if isinstance(rating, (int, float)) and rating >= 3.8 and reviews >= 25:
            w = settings.weights.get("rating", 5.0)
            score += w; reasons[f"rating(+{int(w)})"] = f"{rating} ({reviews})"

        focus_detail = cand.get("focus_detail")
        if focus_detail and focus_detail.lower() in blob.lower():
            w = settings.weights.get("focus_bonus", 8.0)
            score += w; reasons[f"focus(+{int(w)})"] = focus_detail

        score = max(0.0, min(100.0, score))
        return score, reasons

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

# -------------------------------------------------------------
# Google helpers
# -------------------------------------------------------------

def geocode_location(location: str) -> Tuple[float, float]:
    """Geocode human-readable address to (lat, lng)."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_API_KEY}
    resp = requests.get(url, params=params, timeout=20)
    data = resp.json()
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
    while True:
        r = requests.get(url, params=params, timeout=30); data = r.json()
        if "error_message" in data: st.warning(f"Google API warning: {data['error_message']}"); break
        results = data.get("results", []) or []; all_results.extend(results)
        token = data.get("next_page_token")
        if not token: break
        time.sleep(2.0); params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    return all_results

def get_place_details(place_id: str) -> Dict[str, Any]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id, "key": GOOGLE_API_KEY,
        "fields": "address_components,types,formatted_phone_number,opening_hours,editorial_summary,website,rating,user_ratings_total",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        return r.json().get("result", {}) or {}
    except Exception:
        return {}

# -------------------------------------------------------------
# Web scraping (safe & bounded)
# -------------------------------------------------------------

def scrape_site(url: str) -> Dict[str, str]:
    if not url: return {}
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype: return {}
        soup = BeautifulSoup(r.text, "html.parser")
        page_title = soup.title.string if soup.title else ""
        meta_desc = (soup.find("meta", attrs={"name": "description"}) or {}).get("content", "") or ""
        headers_text = " ".join(h.get_text(strip=True) for h in soup.find_all(re.compile("h[1-3]")))[:2000]
        visible_text = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:2000]
        return {"page_title": page_title, "meta_description": meta_desc, "headers": headers_text, "visible_text_blocks": visible_text}
    except Exception:
        return {}

# -------------------------------------------------------------
# Profile builder + persistence + preview gate
# -------------------------------------------------------------

def _finalize_profile(project: Dict[str, Any]) -> Tuple[IndustrySettings, Dict[str, Any], Optional[str], Optional[str]]:
    """Build discovery profile (defaults -> optional LLM -> merge), persist it to the project, return knobs."""
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
        if prof: settings = merge_profile(settings, prof)

    type_hint, keyword = compose_keyword(settings, focus_detail, focus_strict)

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
                    settings: IndustrySettings, type_hint: Optional[str], keyword: Optional[str]) -> bool:
    """Render plan preview (no API calls). Return True if user clicks Approve & Run."""
    params = DiscoveryParams(
        breadth=project.get("breadth", "normal"),
        target_count=int(project.get("target_count", 20)),
        max_radius_km=float(project.get("max_radius_km", 25.0)),
        grid_step_km=float(project.get("grid_step_km", 2.5)),
    )
    plan = plan_queries((center_lat, center_lng), params, settings,
                        focus_detail=project.get("focus_detail"),
                        focus_strict=bool(project.get("focus_strict", False)))

    st.subheader("Preview: Discovery Plan")
    st.caption("No API calls will be made until you approve.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Search knobs**")
        st.json({
            "type_hint": type_hint, "keyword": keyword,
            "search_radius_km": float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT)),
            "grid_step_km": float(project.get("grid_step_km", 2.5)),
            "max_radius_km": float(project.get("max_radius_km", 25.0)),
        })
    with c2:
        st.markdown("**Grid (sample)**")
        preview = plan.get("grid_preview", [])
        st.write(f"Grid nodes planned: {plan.get('grid_nodes', 0)}; showing {len(preview)} sample queries")
        for i, q in enumerate(preview, start=1):
            st.code(f"{i:02d}. location={q['location']} radius={q['radius']} type={q.get('type')} keyword={q.get('keyword')}\n"
                    f"     e.g., {q['sample_url']}")

    st.markdown("**Scoring rubric (used next step)**")
    for line in explain_scoring_rules(settings, project.get("focus_detail"), bool(project.get("focus_strict", False))):
        st.write("- ", line)

    return st.button("✅ Approve & Run", type="primary")

# -------------------------------------------------------------
# LLM helpers
# -------------------------------------------------------------

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
        return json.loads(m.group(1)) if m else {"tier": 3, "reason": raw[:400]}
    except Exception:
        return {"tier": 3, "reason": raw[:400]}

def _llm_audit_prompt(industry: str, cand: Dict[str, Any]) -> str:
    return (
        "Return ONLY compact JSON with keys: tier (1|2|3) and reason (short string).\n\n"
        f"Industry: {industry}\n"
        f"Candidate: {json.dumps(cand, ensure_ascii=False)}\n\n"
        '{ "tier": 1, "reason": "..." }'
    )

# -------------------------------------------------------------
# Persistence
# -------------------------------------------------------------

def _insert_result(row: Dict[str, Any]) -> None:
    if not supabase:
        st.error("Supabase client not initialized.")
        return
    try:
        supabase.table("search_results").insert(row).execute()
    except Exception as e:
        st.error(f"Error saving result: {e}")

# -------------------------------------------------------------
# Main entry used by the UI
# -------------------------------------------------------------

def search_and_expand(project: dict) -> bool:
    """
    Execute Google -> Score -> (optional) LLM audit override -> Save results.
    Returns True when the run completes.
    """
    radius_km = float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT))
    grid_step_km = float(project.get("grid_step_km", 2.5))
    target = int(project.get("target_count", 20))
    max_radius_km = float(project.get("max_radius_km", 25.0))

    # center + profile
    center_lat, center_lng = geocode_location(project["location"])
    settings, profile_json, type_hint, keyword = _finalize_profile(project)

    # grid search
    pts = generate_grid(center_lat, center_lng, max_radius_km, step_km=grid_step_km)
    found: Dict[str, Dict[str, Any]] = {}
    prog = st.progress(0.0, text="Collecting businesses from Google…")
    search_keyword = (keyword or project.get("industry") or "").strip()

    for i, (lat, lng) in enumerate(pts):
        prog.progress(i / max(1, len(pts)), text=f"Searching at ({lat:.4f}, {lng:.4f})")
        results = google_nearby_search(search_keyword, lat, lng, radius_km, type_hint=type_hint)
        for r in results:
            pid = r.get("place_id")
            if pid and pid not in found:
                found[pid] = r
        if len(found) >= target:
            break
    prog.empty()

    # scoring
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
            "focus_detail": profile_json.get("focus_detail"),
            "focus_strict": profile_json.get("focus_strict"),
        }
        score, reasons = score_candidate(cand, settings)
        scored_list.append({
            "place": place, "details": details, "scraped": scraped,
            "score": int(round(score)), "reasons": reasons,
        })

    # dynamic threshold & predicted tier
    scores_only = [row["score"] for row in scored_list]
    threshold_candidates = settings.threshold_candidates or [80, 75, 70, 65, 60, 55]
    tier1_threshold = choose_tier1_threshold(
        scores_only, threshold_candidates=threshold_candidates,
        floor_ratio=settings.floor_ratio, target=target,
    )

    # audit + persist
    async def _process():
        prog2 = st.progress(0.0, text="Running LLM re-audit…" if ENABLE_LLM_AUDIT else "Saving results…")
        total = max(1, len(scored_list))
        for i, row in enumerate(scored_list):
            place = row["place"]; details = row["details"]; scraped = row["scraped"]
            score = row["score"]
            predicted_tier = assign_predicted_tier(score, tier1_threshold)

            audit_cand = {
                "name": place.get("name", ""),
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

            if ENABLE_LLM_AUDIT:
                try:
                    audit_json = await _ollama_json(_llm_audit_prompt(project["industry"], audit_cand))
                except Exception:
                    audit_json = {"tier": predicted_tier, "reason": f"Score-based tier {predicted_tier} (audit error)"}
            else:
                audit_json = {"tier": predicted_tier, "reason": f"Score-based tier {predicted_tier} (audit disabled)"}

            final_tier = int(audit_json.get("tier", predicted_tier)) if isinstance(audit_json, dict) else predicted_tier
            tier_reason = (audit_json.get("reason") if isinstance(audit_json, dict) else "") or f"Predicted {predicted_tier} from score {score}"

            city = state = zipc = ""
            for comp in (details.get("address_components") or []):
                types = comp.get("types", [])
                if "locality" in types or "postal_town" in types: city = comp.get("long_name", "")
                elif "administrative_area_level_1" in types: state = comp.get("short_name", "")
                elif "postal_code" in types: zipc = comp.get("long_name", "")

            lat = place.get("geometry", {}).get("location", {}).get("lat")
            lng = place.get("geometry", {}).get("location", {}).get("lng")
            place_id = place.get("place_id")

            row_out = {
                "id": str(uuid4()),
                "project_id": project["id"],
                "place_id": place_id,
                "name": place.get("name"),
                "address": place.get("vicinity", ""),
                "city": city, "state": state, "zip": zipc,
                "website": audit_cand["website"],
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                "latitude": lat, "longitude": lng,
                "category": "",
                "page_title": scraped.get("page_title", ""),
                "eligibility_score": score,
                "score_reasons": json.dumps(row["reasons"]) if not isinstance(row["reasons"], str) else row["reasons"],
                "tier": final_tier, "tier_reason": tier_reason,
                "manual_override": False,
                "profile_source": getattr(settings, "profile_source", "defaults"),
            }
            _insert_result(row_out)
            prog2.progress((i + 1) / total, text=f"Processed {i + 1} of {total}")
        prog2.empty()

    asyncio.run(_process())
    st.success("All results scored, audited, and saved.")
    return True

# Explicit exports to avoid name confusion
__all__ = [
    "geocode_location",
    "_finalize_profile",
    "_render_preview",
    "search_and_expand",
]
