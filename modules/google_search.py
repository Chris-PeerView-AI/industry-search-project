# ================================
# FILE: modules/google_search.py
# PURPOSE (Phase-1, Step 1):
# - Build an LLM-assisted discovery profile (defaults -> optional LLM -> merge)
# - Persist profile_json to search_projects
# - Provide a Preview gate (plan + rubric) that requires approval before running
# - Use profile-derived type_hint/keyword for Google Nearby (fixed radius)
# - Keep existing classification approach (light LLM pass) for now
# ================================

from __future__ import annotations

import os
import re
import json
import time
import asyncio
import requests
from uuid import uuid4
from math import cos, radians
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client

# ---- Borrow core planning/profile helpers from phase1_lib ----
try:
    from modules.phase1_lib import (
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
except Exception:
    # fallback if your project keeps phase1_lib at repo root
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

# ---- Environment ----
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")

SEARCH_RADIUS_KM_DEFAULT = 5.0  # fixed radius strategy per your decision

if not SUPABASE_URL or not SUPABASE_KEY:
    st.warning("Supabase credentials not set. Check SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in your .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

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
    """Simple square spiral-ish grid. Always includes center point."""
    pts: List[Tuple[float, float]] = [(center_lat, center_lng)]
    steps = int(max_radius_km / step_km)
    dlat = step_km / 110.574
    dlng = step_km / (111.320 * max(1e-9, cos(radians(center_lat))))

    seen = {(center_lat, center_lng)}
    for ring in range(1, steps + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                # edges of the ring only
                if abs(dx) != ring and abs(dy) != ring:
                    continue
                lat = center_lat + dy * dlat
                lng = center_lng + dx * dlng
                if (lat, lng) not in seen:
                    pts.append((lat, lng))
                    seen.add((lat, lng))
    return pts


def google_nearby_search(keyword: str, lat: float, lng: float, radius_km: float, type_hint: Optional[str] = None) -> List[Dict[str, Any]]:
    """Nearby Search with optional `type` and required compact `keyword`.
    Uses a fixed radius (km) per Phase-1 decision.
    """
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params: Dict[str, Any] = {
        "location": f"{lat},{lng}",
        "radius": int(radius_km * 1000),
        "keyword": keyword,
        "key": GOOGLE_API_KEY,
    }
    if type_hint:
        params["type"] = type_hint

    all_results: List[Dict[str, Any]] = []
    while True:
        r = requests.get(url, params=params, timeout=30)
        data = r.json()

        if "error_message" in data:
            st.warning(f"Google API warning: {data['error_message']}")
            break

        results = data.get("results", []) or []
        all_results.extend(results)

        token = data.get("next_page_token")
        if not token:
            break

        # next_page_token requires delay + replacements of parameters
        time.sleep(2.0)
        params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    return all_results


def get_place_details(place_id: str) -> Dict[str, Any]:
    """Fetch details for a place_id (safe fields only)."""
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key": GOOGLE_API_KEY,
        "fields": "address_components,types,formatted_phone_number,opening_hours,editorial_summary,website",
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
    """Lightweight scrape: title, meta description, h1-h3, short body sample."""
    if not url:
        return {}
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=8)
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" not in ctype:
            return {}
        soup = BeautifulSoup(r.text, "html.parser")
        page_title = soup.title.string if soup.title else ""
        meta_desc = (soup.find("meta", attrs={"name": "description"}) or {}).get("content", "") or ""
        headers_text = " ".join(h.get_text(strip=True) for h in soup.find_all(re.compile("h[1-3]")))[:2000]
        visible_text = " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:2000]
        return {
            "page_title": page_title,
            "meta_description": meta_desc,
            "headers": headers_text,
            "visible_text_blocks": visible_text,
        }
    except Exception:
        return {}

# -------------------------------------------------------------
# Profile builder + persistence + preview gate
# -------------------------------------------------------------

def _finalize_profile(project: Dict[str, Any]) -> Tuple[IndustrySettings, Dict[str, Any], Optional[str], Optional[str]]:
    """Build a discovery profile from defaults and optional LLM, then persist on project.
    Returns (settings, profile_json, type_hint, keyword).
    """
    industry = (project.get("industry") or "").strip()
    location = (project.get("location") or "").strip()
    use_llm_profile = bool(project.get("use_llm_profile", True))
    focus_detail = project.get("focus_detail")
    focus_strict = bool(project.get("focus_strict", False))

    # 1) Defaults by industry
    settings = default_settings_for_industry(industry)

    # 2) Optional LLM profile → validate → merge
    if use_llm_profile:
        prompt = build_profile_prompt(industry, location, KNOWN_TYPES)
        prof_raw = ollama_generate_profile_json(LLM_MODEL, OLLAMA_URL, prompt, temperature=0.2)
        prof = validate_profile_json(prof_raw or {}, KNOWN_TYPES)
        if prof:
            settings = merge_profile(settings, prof)

    # 3) Compose query knobs for Google
    type_hint, keyword = compose_keyword(settings, focus_detail, focus_strict)

    # 4) Persist concise profile JSON onto project
    profile_json = {
        "type_hint": type_hint,
        "keyword": keyword,
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
        "focus_detail": focus_detail,
        "focus_strict": focus_strict,
    }

    try:
        if supabase:
            supabase.table("search_projects").update({
                "profile_json": profile_json,
                "use_llm_profile": use_llm_profile,
            }).eq("id", project["id"]).execute()
    except Exception as e:
        st.warning(f"Could not persist profile_json on project: {e}")

    return settings, profile_json, type_hint, keyword


def _render_preview(
    center_lat: float,
    center_lng: float,
    project: Dict[str, Any],
    settings: IndustrySettings,
    type_hint: Optional[str],
    keyword: Optional[str],
) -> bool:
    """Render a preview of the plan (grid + sample Nearby URLs) and scoring rubric.
    Returns True if the user clicked Approve & Run.
    """
    params = DiscoveryParams(
        breadth=project.get("breadth", "normal"),
        target_count=int(project.get("target_count", 20)),
        max_radius_km=float(project.get("max_radius_km", 25.0)),
        grid_step_km=float(project.get("grid_step_km", 2.5)),
    )
    plan = plan_queries(
        (center_lat, center_lng),
        params,
        settings,
        focus_detail=project.get("focus_detail"),
        focus_strict=bool(project.get("focus_strict", False)),
    )

    st.subheader("Preview: Discovery Plan")
    st.caption("No API calls will be made until you approve.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Search knobs**")
        st.json({
            "type_hint": type_hint,
            "keyword": keyword,
            "search_radius_km": float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT)),
            "grid_step_km": float(project.get("grid_step_km", 2.5)),
            "max_radius_km": float(project.get("max_radius_km", 25.0)),
        })
    with c2:
        st.markdown("**Grid (sample)**")
        preview = plan.get("grid_preview", [])
        st.write(f"Grid nodes planned: {plan.get('grid_nodes', 0)}; showing {len(preview)} sample queries")
        for i, q in enumerate(preview, start=1):
            st.code(
                f"{i:02d}. location={q['location']} radius={q['radius']} type={q.get('type')} keyword={q.get('keyword')}\n"
                f"     e.g., {q['sample_url']}"
            )

    st.markdown("**Scoring rubric (used in the next step)**")
    for line in explain_scoring_rules(settings, project.get("focus_detail"), bool(project.get("focus_strict", False))):
        st.write("- ", line)

    return st.button("✅ Approve & Run", type="primary")

# -------------------------------------------------------------
# Light classification (kept similar to existing, Step 1 scope)
# -------------------------------------------------------------

async def _ollama_json(prompt: str) -> Dict[str, Any]:
    """Call local Ollama and parse JSON from response."""
    proc = await asyncio.create_subprocess_exec(
        "ollama", "run", LLM_MODEL,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate(input=prompt.encode("utf-8"))
    raw = stdout.decode("utf-8").strip()

    # Try to extract a JSON block
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", raw)
    if not m:
        m = re.search(r"(\{[\s\S]*\})", raw)
    try:
        return json.loads(m.group(1)) if m else {"tier": 3, "summary": raw[:400]}
    except Exception:
        return {"tier": 3, "summary": raw[:400]}


def _llm_classify_prompt(industry: str, biz: Dict[str, Any], scraped: Dict[str, Any], details: Dict[str, Any]) -> str:
    return f"""
You are an expert business analyst. Return ONLY valid JSON in your reply.

Your job is to classify how well this business matches the user's request: "{industry}"

Definitions:
- Tier 1: The business primarily offers this service (main reason to visit).
- Tier 2: The service is offered but not primary.
- Tier 3: Irrelevant or off-target.

Output JSON:
{{ "tier": 1, "category": "string", "summary": "short 1-2 sentence reason" }}

Name: {biz.get("name","")}
Google Types: {details.get("types",[])}
Website: {details.get("website","") or biz.get("website","")}
Phone: {details.get("formatted_phone_number","")}
Hours: {details.get("opening_hours",{}).get("weekday_text",[])}
Editorial: {details.get("editorial_summary",{}).get("overview","")}

Page Title: {scraped.get("page_title","")}
Meta Description: {scraped.get("meta_description","")}
Headers: {scraped.get("headers","")}
Text: {scraped.get("visible_text_blocks","")}
""".strip()

# -------------------------------------------------------------
# Persistence helper
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

def search_and_expand(project: Dict[str, Any]) -> bool:
    """Plan/Preview (handled in UI) -> Execute Google -> Classify (light) -> Save results."""
    radius_km = float(project.get("search_radius_km", SEARCH_RADIUS_KM_DEFAULT))
    grid_step_km = float(project.get("grid_step_km", 2.5))
    target = int(project.get("target_count", 20))
    max_radius_km = float(project.get("max_radius_km", 25.0))

    # Resolve center once here (Preview did this already; this re-checks to avoid state mismatch)
    center_lat, center_lng = geocode_location(project["location"])

    # Build + persist profile; compute query knobs (type_hint/keyword)
    settings, profile_json, type_hint, keyword = _finalize_profile(project)

    # Execute grid (fixed radius strategy)
    pts = generate_grid(center_lat, center_lng, max_radius_km, step_km=grid_step_km)

    found: Dict[str, Dict[str, Any]] = {}
    prog = st.progress(0.0, text="Collecting businesses from Google…")

    search_keyword = keyword or project.get("industry") or ""  # compact fallback

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
    st.write(f"Classifying {len(found)} unique businesses…")

    async def _process():
        prog2 = st.progress(0.0, text="Classifying with LLM…")
        total = max(1, len(found))
        for i, place in enumerate(found.values()):
            place_id = place.get("place_id")
            details = get_place_details(place_id)
            website = details.get("website") or place.get("website") or ""
            scraped = scrape_site(website) if website else {}

            prompt = _llm_classify_prompt(project["industry"], place, scraped, details)
            llm = await _ollama_json(prompt)

            # safe address components
            city = state = zipc = ""
            for comp in details.get("address_components", []) or []:
                types = comp.get("types", [])
                if "locality" in types or "postal_town" in types:
                    city = comp.get("long_name", "")
                elif "administrative_area_level_1" in types:
                    state = comp.get("short_name", "")
                elif "postal_code" in types:
                    zipc = comp.get("long_name", "")

            lat = place.get("geometry", {}).get("location", {}).get("lat", None)
            lng = place.get("geometry", {}).get("location", {}).get("lng", None)

            tier = int(llm.get("tier", 3)) if isinstance(llm, dict) else 3
            category = (llm.get("category") if isinstance(llm, dict) else "") or ""
            summary = (llm.get("summary") if isinstance(llm, dict) else "") or ""

            row = {
                "id": str(uuid4()),
                "project_id": project["id"],
                "place_id": place_id,
                "name": place.get("name"),
                "address": place.get("vicinity", ""),
                "city": city,
                "state": state,
                "zip": zipc,
                "website": website,
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
                "latitude": lat,
                "longitude": lng,
                "category": category,
                "page_title": scraped.get("page_title", ""),
                "tier": tier,
                "tier_reason": summary,
                "manual_override": False,
                # provenance
                "profile_source": getattr(settings, "profile_source", "defaults"),
            }
            _insert_result(row)
            prog2.progress((i + 1) / total, text=f"Processed {i + 1} of {total}")
        prog2.empty()

    asyncio.run(_process())

    st.success("All results classified and saved.")
    return True
