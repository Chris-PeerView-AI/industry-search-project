import os
import uuid
import requests
import json
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

headers = {
    'x-api-key': ENIGMA_API_KEY,
    'Content-Type': 'application/json'
}

def pull_enigma_data_for_business(business):
    # Check if business already exists in Supabase
    existing = supabase.table("enigma_businesses")\
        .select("id")\
        .eq("place_id", business.get("place_id"))\
        .execute()

    if existing.data and len(existing.data) > 0:
        print(f"⚠️ Business {business['name']} with place_id {business['place_id']} already exists in enigma_businesses. Skipping.")
        return
    name = business["name"]
    city = business.get("city")
    state = business.get("state")
    zip_code = business.get("zip")
    place_id = business.get("place_id")
    google_places_id = business.get("google_places_id")  # required by Supabase

    # Step 1: Get Enigma ID
    query = """
    query SearchLocation($searchInput: SearchInput!) {
      search(searchInput: $searchInput) {
        ... on OperatingLocation {
          id
          names(first: 1) { edges { node { name } } }
          addresses(first: 1) { edges { node { city state zip fullAddress } } }
        }
      }
    }
    """
    variables = {
        "searchInput": {
            "entityType": "OPERATING_LOCATION",
            "name": name,
            "address": {
                "city": city,
                "state": state,
                "postalCode": zip_code
            }
        }
    }
    payload = {"query": query, "variables": variables}
    response = requests.post("https://api.enigma.com/graphql", headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    search_results = data.get("data", {}).get("search", [])
    if not search_results:
        print(f"❌ No match found for {name}, {city}, {state}, {zip_code}")
        return

    loc = search_results[0]
    enigma_id = loc.get("id")
    matched_city = loc["addresses"]["edges"][0]["node"].get("city")
    matched_state = loc["addresses"]["edges"][0]["node"].get("state")
    matched_postal_code = loc["addresses"]["edges"][0]["node"].get("zip")
    matched_full_address = loc["addresses"]["edges"][0]["node"].get("fullAddress")

    # Step 2: Pull metrics
    metrics_query = """
    query GetLocationMetrics($searchInput: SearchInput!, $cardTxConditions: ConnectionConditions!) {
      search(searchInput: $searchInput) {
        ... on OperatingLocation {
          cardTransactions(first: 50, conditions: $cardTxConditions) {
            edges {
              node {
                quantityType
                rawQuantity
                projectedQuantity
                period
                periodStartDate
                periodEndDate
              }
            }
          }
        }
      }
    }
    """
    periods = ["3m", "12m", "2023", "2024"]
    quantity_types = [
        "card_revenue_amount",
        "avg_transaction_size",
        "card_transactions_count",
        "card_customers_average_daily_count",
        "refunds_amount",
        "card_revenue_yoy_growth",
        "card_revenue_prior_period_growth"
    ]
    variables = {
        "searchInput": {
            "entityType": "OPERATING_LOCATION",
            "id": enigma_id
        },
        "cardTxConditions": {
            "filter": {
                "AND": [
                    {"IN": ["period", periods]},
                    {"IN": ["quantityType", quantity_types]}
                ]
            }
        }
    }
    metric_resp = requests.post("https://api.enigma.com/graphql", json={"query": metrics_query, "variables": variables}, headers=headers)
    metric_resp.raise_for_status()
    metric_data = metric_resp.json()
    metrics = metric_data.get("data", {}).get("search", [])[0].get("cardTransactions", {}).get("edges", [])

    # Step 3: Insert business
    business_id = str(uuid.uuid4())
    business_insert = supabase.table("enigma_businesses").insert({
        "id": business_id,
        "enigma_id": enigma_id,
        "place_id": place_id,
        "google_places_id": google_places_id,
        "business_name": name,
        "full_address": business.get("address"),
        "city": city,
        "state": state,
        "zip": zip_code,
        "date_pulled": datetime.now(timezone.utc).date().isoformat(),
        "project_id": business.get("project_id"),
        "pull_session_id": business.get("pull_session_id"),
        "pull_timestamp": datetime.now(timezone.utc).isoformat(),
        "match_method": "operating_location",
        "match_confidence": 1.0,
        "matched_city": matched_city,
        "matched_state": matched_state,
        "matched_postal_code": matched_postal_code,
        "matched_full_address": matched_full_address
    }).execute()

    if hasattr(business_insert, 'error') and business_insert.error:
        print(f"❌ Failed to insert into enigma_businesses: {business_insert.error}")
    else:
        print(f"✅ Inserted business {name} with ID {business_id}")

    # Step 4: Insert metrics
    for edge in metrics:
        node = edge.get("node", {})
        metric_insert = supabase.table("enigma_metrics").insert({
            "id": str(uuid.uuid4()),
            "business_id": business_id,
            "quantity_type": node.get("quantityType"),
            "raw_quantity": node.get("rawQuantity"),
            "projected_quantity": node.get("projectedQuantity"),
            "period": node.get("period"),
            "period_start_date": node.get("periodStartDate"),
            "period_end_date": node.get("periodEndDate"),
            "project_id": business.get("project_id"),
            "pull_session_id": business.get("pull_session_id"),
            "pull_timestamp": business.get("pull_timestamp").isoformat() if isinstance(business.get("pull_timestamp"), datetime) else business.get("pull_timestamp")
        }).execute()

        if hasattr(metric_insert, 'error') and metric_insert.error:
            print(f"❌ Failed to insert metric: {metric_insert.error}")
