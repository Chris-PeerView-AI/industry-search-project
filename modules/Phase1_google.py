# ================================
# FILE: modules/Phase1_google.py
# PURPOSE: Google Geocode, grid generation, Nearby Search with retry/backoff
# ================================

from __future__ import annotations
import os, time, requests
from math import cos, radians
from typing import Any, Dict, List, Optional, Tuple

GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")

def geocode_location(location: str) -> Tuple[float, float]:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params, timeout=20)
    data = r.json()
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Unable to geocode: {data.get('status')}")
    loc = results[0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])

def generate_grid(center_lat: float, center_lng: float, max_radius_km: float, step_km: float = 2.5) -> List[Tuple[float,float]]:
    pts = [(center_lat, center_lng)]
    steps = int(max_radius_km / step_km)
    dlat = step_km / 110.574
    dlng = step_km / (111.320 * max(1e-9, cos(radians(center_lat))))
    seen = {(center_lat, center_lng)}
    for ring in range(1, steps + 1):
        for dx in range(-ring, ring + 1):
            for dy in range(-ring, ring + 1):
                if abs(dx) != ring and abs(dy) != ring:
                    continue
                lat = center_lat + dy * dlat
                lng = center_lng + dx * dlng
                if (lat, lng) not in seen:
                    pts.append((lat, lng)); seen.add((lat, lng))
    return pts

def _nearby_once(params: Dict[str, Any], timeout=30):
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    return requests.get(url, params=params, timeout=timeout)

def google_nearby_search(keyword: str, lat: float, lng: float, radius_km: float, type_hint: Optional[str]=None) -> List[Dict[str,Any]]:
    """Recall-first: keyword only. Robust retry/backoff on timeouts & OVER_QUERY_LIMIT."""
    params: Dict[str, Any] = {
        "location": f"{lat},{lng}",
        "radius": int(radius_km * 1000),
        "keyword": keyword,
        "key": GOOGLE_API_KEY,
    }
    if type_hint:
        params["type"] = type_hint

    all_results: List[Dict[str, Any]] = []
    page_params = params.copy()
    attempts = 0
    while True:
        try:
            r = _nearby_once(page_params, timeout=30)
        except requests.ReadTimeout:
            attempts += 1
            if attempts > 3:
                break
            time.sleep(1.5 * attempts)
            continue

        data = r.json()
        if data.get("status") == "OVER_QUERY_LIMIT":
            time.sleep(2.0)
            attempts += 1
            if attempts > 5:
                break
            continue

        if "error_message" in data:
            # soft-warn and stop this chain
            break

        results = data.get("results", []) or []
        all_results.extend(results)

        token = data.get("next_page_token")
        if not token:
            break

        # Google requires a short delay before next_page_token is valid
        time.sleep(2.0)
        page_params = {"pagetoken": token, "key": GOOGLE_API_KEY}
    return all_results

def get_place_details(place_id: str) -> Dict[str, Any]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {
        "place_id": place_id,
        "key": GOOGLE_API_KEY,
        "fields": "address_components,types,formatted_phone_number,opening_hours,editorial_summary,website,rating,user_ratings_total",
    }
    try:
        r = requests.get(url, params=params, timeout=30)
        return r.json().get("result", {}) or {}
    except Exception:
        return {}
