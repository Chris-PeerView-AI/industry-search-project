# modules/google_search.py

import os
import requests
import json
import asyncio
from uuid import uuid4
from typing import Dict, Any, List, Tuple
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client
import streamlit as st
import re
import time
from math import cos, radians

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
MODEL_NAME = os.getenv("LLM_MODEL", "llama3")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Constants for search density
GRID_STEP_KM = 5
SEARCH_RADIUS_KM = 5


def geocode_location(location: str) -> Tuple[float, float]:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    results = data.get("results", [])
    if not results:
        st.error(f"Geocoding failed for '{location}'. Full response: {json.dumps(data, indent=2)}")
        raise ValueError("Unable to geocode location.")
    loc = results[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]



def generate_grid(center_lat: float, center_lng: float, max_radius_km: int) -> List[Tuple[float, float]]:
    points = []
    steps = int(max_radius_km / GRID_STEP_KM)
    deg_step_lat = GRID_STEP_KM / 110.574  # km per degree latitude
    deg_step_lng = GRID_STEP_KM / (111.320 * cos(radians(center_lat)))

    for dx in range(-steps, steps + 1):
        for dy in range(-steps, steps + 1):
            dist = (dx**2 + dy**2)**0.5 * GRID_STEP_KM
            if dist <= max_radius_km:
                lat = center_lat + dx * deg_step_lat
                lng = center_lng + dy * deg_step_lng
                points.append((lat, lng))
    return points


def google_nearby_search(query: str, lat: float, lng: float, radius_km: int) -> List[Dict[str, Any]]:
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lng}",
        "radius": radius_km * 1000,
        "keyword": query,
        "key": GOOGLE_API_KEY,
    }
    all_results = []
    while True:
        r = requests.get(url, params=params)
        data = r.json()
        results = data.get("results", [])
        all_results.extend(results)
        token = data.get("next_page_token")
        if token:
            time.sleep(2)
            params["pagetoken"] = token
        else:
            break
    return all_results


def build_prompt(industry: str, business: Dict[str, Any], scraped: Dict[str, Any]) -> str:
    return f"""
You are an expert business analyst. Return ONLY valid JSON in your reply.

Your job is to classify how well this business matches the user's request: '{industry}'

Output format:
{{
  "tier": 1,  # 1 = strong match, 2 = partial match, 3 = unrelated
  "category": "string",
  "summary": "short 1-2 sentence summary"
}}

Business Name: {business.get("name", "")}
Page Title: {scraped.get("page_title", "")}
Meta Description: {scraped.get("meta_description", "")}
Headers: {scraped.get("headers", "")}
Visible Text Blocks: {scraped.get("visible_text_blocks", "")}
""".strip()


async def call_llm(prompt: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "ollama", "run", MODEL_NAME,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate(input=prompt.encode("utf-8"))
    raw_output = stdout.decode("utf-8").strip()
    match = re.search(r"```json\\n(.*?)```", raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()
    brace_match = re.search(r"\{.*\}", raw_output, re.DOTALL)
    return brace_match.group(0).strip() if brace_match else raw_output


def scrape_site(url: str) -> Dict[str, str]:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        return {
            "page_title": soup.title.string if soup.title else "",
            "meta_description": (soup.find("meta", attrs={"name": "description"}) or {}).get("content", ""),
            "headers": " ".join(h.get_text(strip=True) for h in soup.find_all(re.compile("h[1-3]"))),
            "visible_text_blocks": " ".join(p.get_text(strip=True) for p in soup.find_all("p"))[:2000],
        }
    except Exception:
        return {}


def insert_result(project_id: str, result: Dict[str, Any]):
    try:
        supabase.table("search_results").insert(result).execute()
    except Exception as e:
        st.error(f"Error saving result: {e}")


def search_and_expand(project: Dict[str, Any]) -> bool:
    st.write("Spiral search and categorization starting...")

    query = project["industry"]
    location = project["location"]
    max_radius_km = int(project["max_radius_km"])
    target = int(project["target_count"])
    center_lat, center_lng = geocode_location(location)
    points = generate_grid(center_lat, center_lng, max_radius_km)

    found = {}
    for lat, lng in points:
        st.write(f"Searching at ({lat:.4f}, {lng:.4f})")
        results = google_nearby_search(query, lat, lng, SEARCH_RADIUS_KM)
        for r in results:
            pid = r.get("place_id")
            if pid and pid not in found:
                found[pid] = r
        if len(found) >= target:
            break

    st.write(f"Classifying {len(found)} unique businesses...")

    async def process():
        for place in found.values():
            name = place.get("name")
            place_id = place.get("place_id")
            website = place.get("website") or ""
            address = place.get("vicinity", "")
            city = state = zip = ""

            scraped = scrape_site(website) if website else {}
            prompt = build_prompt(query, place, scraped)
            raw_response = await call_llm(prompt)

            try:
                parsed = json.loads(raw_response)
                tier = int(parsed.get("tier", 3))
                category = parsed.get("category", "")
                summary = parsed.get("summary", raw_response)
            except Exception:
                tier = 3
                category = "Unknown"
                summary = raw_response

            result = {
                "id": str(uuid4()),
                "project_id": project["id"],
                "name": name,
                "address": address,
                "city": city,
                "state": state,
                "zip": zip,
                "place_id": place_id,
                "website": website,
                "tier": tier,
                "tier_reason": summary,
                "manual_override": False,
            }
            insert_result(project["id"], result)

    asyncio.run(process())
    st.success("All results classified and saved.")
    return True
