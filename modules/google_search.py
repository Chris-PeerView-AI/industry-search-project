import os
import json
import asyncio
import requests
from uuid import uuid4
from typing import Dict, Any, List, Tuple
import streamlit as st
import re
from supabase import create_client
from dotenv import load_dotenv
from math import cos, radians

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
SEARCH_RADIUS_KM = 5

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def geocode_location(location: str) -> Tuple[float, float]:
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {"address": location, "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params)
    results = r.json().get("results", [])
    if not results:
        raise ValueError("Unable to geocode location.")
    loc = results[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]

def generate_grid(center_lat: float, center_lng: float, max_radius_km: int) -> List[Tuple[float, float]]:
    points = []
    steps = int(max_radius_km / 5)
    deg_step_lat = 5 / 110.574
    deg_step_lng = 5 / (111.320 * cos(radians(center_lat)))

    for dx in range(-steps, steps + 1):
        for dy in range(-steps, steps + 1):
            dist = (dx**2 + dy**2)**0.5 * 5
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
            import time
            time.sleep(2)
            params["pagetoken"] = token
        else:
            break
    return all_results

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

def get_place_details(place_id: str) -> Dict[str, Any]:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {"place_id": place_id, "key": GOOGLE_API_KEY, "fields": "type,formatted_phone_number,opening_hours,editorial_summary"}
    try:
        r = requests.get(url, params=params)
        return r.json().get("result", {})
    except Exception:
        return {}

def build_prompt(industry: str, business: Dict[str, Any], scraped: Dict[str, Any], place_details: Dict[str, Any]) -> str:
    return f"""
You are an expert business analyst. Return ONLY valid JSON in your reply.

Your job is to classify how well this business matches the user's request: '{industry}'

Use the following logic:
- Tier 1: The business exclusively or primarily offers this service. It should be the main reason someone visits the business.
- Tier 2: The service is offered but not the primary focus. It may be one of many services or part of a larger complex.
- Tier 3: Irrelevant or unrelated. A simple mention is not sufficient.

Output format:
{{
  "tier": 1,
  "category": "string",
  "summary": "short 1-2 sentence summary"
}}

Business Name: {business.get("name", "")}
Page Title: {scraped.get("page_title", "")}
Meta Description: {scraped.get("meta_description", "")}
Headers: {scraped.get("headers", "")}
Visible Text Blocks: {scraped.get("visible_text_blocks", "")}
Google Place Types: {place_details.get("types", [])}
Phone: {place_details.get("formatted_phone_number", "")}
Hours: {place_details.get("opening_hours", {}).get("weekday_text", [])}
Editorial Summary: {place_details.get("editorial_summary", {}).get("overview", "")}
""".strip()

async def call_llm(prompt: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "ollama", "run", os.getenv("LLM_MODEL", "llama3"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate(input=prompt.encode("utf-8"))
    raw_output = stdout.decode("utf-8").strip()
    match = re.search(r"```json\n(.*?)```", raw_output, re.DOTALL)
    if match:
        return match.group(1).strip()
    brace_match = re.search(r"\{.*\}", raw_output, re.DOTALL)
    return brace_match.group(0).strip() if brace_match else raw_output

def insert_result(project_id: str, result: Dict[str, Any]):
    try:
        supabase.table("search_results").insert(result).execute()
    except Exception as e:
        st.error(f"Error saving result: {e}")

def search_and_expand(project: Dict[str, Any]) -> bool:
    audit_toggle = project.get("use_gpt_audit", False)
    st.caption("🔒 GPT-4 audit is {} for this project.".format("enabled" if audit_toggle else "disabled"))
    st.write("Spiral search and categorization starting...")

    query = project["industry"]
    location = project["location"]
    max_radius_km = int(project["max_radius_km"])
    target = int(project["target_count"])
    center_lat, center_lng = geocode_location(location)
    points = generate_grid(center_lat, center_lng, max_radius_km)

    found = {}
    progress = st.progress(0, text="Collecting businesses from Google...")
    for i, (lat, lng) in enumerate(points):
        progress.progress(i / len(points), text=f"Searching at ({lat:.4f}, {lng:.4f})")
        results = google_nearby_search(query, lat, lng, SEARCH_RADIUS_KM)
        for r in results:
            pid = r.get("place_id")
            if pid and pid not in found:
                found[pid] = r
        if len(found) >= target:
            break
    progress.empty()

    st.write(f"Classifying {len(found)} unique businesses...")

    async def process():
        classify_progress = st.progress(0, text="Classifying with LLM...")
        total = len(found)
        for i, place in enumerate(found.values()):
            name = place.get("name")
            place_id = place.get("place_id")
            website = place.get("website") or ""
            address = place.get("vicinity", "")
            city = state = zip = ""

            scraped = scrape_site(website) if website else {}
            place_details = get_place_details(place_id)
            prompt = build_prompt(query, place, scraped, place_details)
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
                "category": category,
                "page_title": scraped.get("page_title", ""),
                "google_maps_url": f"https://www.google.com/maps/place/?q=place_id:{place_id}",
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
            classify_progress.progress((i + 1) / total, text=f"Processed {i + 1} of {total}")
        classify_progress.empty()

    asyncio.run(process())
    st.caption("🔒 GPT-4 audit is disabled by default. Enable only for final checks.")
    audit_toggle = st.checkbox("Use GPT-4 to recheck Tier 1 results", value=False)

    if audit_toggle and OPENAI_API_KEY:
        async def call_gpt4(prompt: str) -> str:
            import openai
            openai.api_key = OPENAI_API_KEY
            try:
                response = await openai.ChatCompletion.acreate(
                    model="gpt-4-turbo",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                return response["choices"][0]["message"]["content"].strip()
            except Exception as e:
                return json.dumps({"tier": 1, "summary": f"GPT-4 error: {e}"})

        async def audit_process():
            st.write("Rechecking Tier 1 businesses with GPT-4...")
            result = supabase.table("search_results").select("*").eq("project_id", project["id"]).eq("tier", 1).execute()
            downgraded = []
            if result.data:
                audit_progress = st.progress(0, text="Auditing with GPT-4...")
                for i, row in enumerate(result.data):
                    place = {"name": row["name"]}
                    scraped = {"page_title": row.get("page_title", ""), "meta_description": "", "headers": "", "visible_text_blocks": ""}
                    place_details = {"types": [], "formatted_phone_number": "", "opening_hours": {}, "editorial_summary": {}}
                    prompt = build_prompt(query, place, scraped, place_details)
                    response = await call_gpt4(prompt)
                    try:
                        parsed = json.loads(response)
                        new_tier = int(parsed.get("tier", 1))
                        if new_tier != 1:
                            supabase.table("search_results").update({"tier": new_tier}).eq("id", row["id"]).execute()
                            downgraded.append(row["name"])
                    except:
                        pass
                    audit_progress.progress((i + 1) / len(result.data), text=f"Checked {i + 1} of {len(result.data)}")
                audit_progress.empty()
                st.success("GPT-4 audit complete.")
                if downgraded:
                    st.info(f"GPT-4 downgraded the following: {', '.join(downgraded)}")

        asyncio.run(audit_process())

    elif audit_toggle:
        st.warning("OPENAI_API_KEY not set in .env file. GPT-4 audit skipped.")

    st.success("All results classified and saved.")
    return True
