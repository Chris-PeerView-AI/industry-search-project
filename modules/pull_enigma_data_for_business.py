import os
import uuid
import requests
from datetime import datetime
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def pull_enigma_data_for_business(business):
    place_id = business["place_id"]
    name = business["name"]
    city = business.get("city")
    state = business.get("state")
    zip_code = business.get("zip")

    # Step 1: Search for Enigma ID
    search_query = {
        "query": {
            "name": name,
            "address": {
                "city": city,
                "state": state,
                "postal_code": zip_code
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {ENIGMA_API_KEY}",
        "Content-Type": "application/json"
    }

    search_resp = requests.post("https://api.enigma.com/v1/search", json=search_query, headers=headers)
    search_resp.raise_for_status()
    results = search_resp.json()

    if not results.get("results"):
        raise Exception("No match found in Enigma.")

    enigma_id = results["results"][0]["id"]

    # Step 2: Pull metrics for that ID
    metrics_url = f"https://api.enigma.com/v1/locations/{enigma_id}/metrics"
    metrics_resp = requests.get(metrics_url, headers=headers)
    metrics_resp.raise_for_status()
    metrics = metrics_resp.json()

    # Step 3: Insert into enigma_businesses
    business_id = str(uuid.uuid4())
    supabase.table("enigma_businesses").insert({
        "id": business_id,
        "enigma_id": enigma_id,
        "place_id": place_id,
        "business_name": name,
        "full_address": business.get("address"),
        "city": city,
        "state": state,
        "zip": zip_code,
        "date_pulled": datetime.utcnow().date().isoformat(),
        "project_id": business.get("project_id"),
        "pull_session_id": business.get("pull_session_id"),
        "pull_timestamp": business.get("pull_timestamp")
    }).execute()

    # Step 4: Insert metrics into enigma_metrics
    for m in metrics.get("metrics", []):
        supabase.table("enigma_metrics").insert({
            "id": str(uuid.uuid4()),
            "business_id": business_id,
            "quantity_type": m["type"],
            "raw_quantity": m.get("raw"),
            "projected_quantity": m.get("projected"),
            "period": m.get("period"),
            "period_start_date": m.get("start_date"),
            "period_end_date": m.get("end_date"),
            "project_id": business.get("project_id"),
            "pull_session_id": business.get("pull_session_id"),
            "pull_timestamp": business.get("pull_timestamp")
        }).execute()
