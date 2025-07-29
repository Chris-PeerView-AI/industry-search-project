# Combined pipeline for Enigma lookup + financial pull + Supabase insert

import requests
import json
import os
import traceback
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
import uuid

# --- Load environment variables ---
load_dotenv()
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not ENIGMA_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing environment credentials")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Input: business details ---
google_places_id = "ChIJ0T3iK4FEwokRkWrEbgJmhyE"

try:
    print("🔍 Checking Supabase for existing business record...")
    existing = supabase.table("enigma_businesses").select("id").eq("google_places_id", google_places_id).execute()
    if existing.data:
        print("✅ Enigma data already exists in Supabase. Skipping pull.")
        exit(0)

    print("🔎 Searching Enigma for matching business...")
    business_name = "GolfCave"
    city = "Clark"
    state = "NJ"
    zip_code = "07066"

    search_query = """
    query SearchLocation($searchInput: SearchInput!) {
      search(searchInput: $searchInput) {
        ... on OperatingLocation {
          id
          names(first: 1) { edges { node { name } } }
          addresses(first: 1) { edges { node { streetAddress1 city state zip } } }
          brands(first: 1) { edges { node { names(first: 1) { edges { node { name } } } } } }
        }
      }
    }
    """

    search_variables = {
        "searchInput": {
            "entityType": "OPERATING_LOCATION",
            "name": business_name,
            "address": {"city": city, "state": state, "postalCode": zip_code}
        }
    }

    search_headers = {"x-api-key": ENIGMA_API_KEY, "Content-Type": "application/json"}
    resp = requests.post("https://api.enigma.com/graphql", json={"query": search_query, "variables": search_variables}, headers=search_headers)

    if resp.status_code != 200:
        raise Exception(f"Enigma search failed with status {resp.status_code}: {resp.text}")

    data = resp.json()
    print("[DEBUG] Enigma response keys:", list(data.keys()))
    if "errors" in data:
        print("[ERROR] Enigma returned errors:", json.dumps(data["errors"], indent=2))
        raise Exception("GraphQL errors in Enigma response")

    if "data" not in data or "search" not in data["data"]:
        raise Exception("'data' or 'search' key missing in Enigma response")

    matched = None
    for loc in data["data"]["search"]:
        try:
            matched = loc
            break
        except Exception:
            continue

    if not matched:
        print("❌ No matching Enigma ID found.")
        exit(1)

    print("✅ Match found with Enigma ID:", matched["id"])

    enigma_id = matched["id"]
    print("📊 Pulling financial metrics for Enigma ID...")

    metrics_query = """
    query GetLocationWithAggregatePeriods($searchInput: SearchInput!, $cardTxConditions: ConnectionConditions!) {
      search(searchInput: $searchInput) {
        ... on OperatingLocation {
          id
          names(first: 1) { edges { node { name } } }
          addresses(first: 1) { edges { node { fullAddress city state zip } } }
          brands(first: 1) { edges { node { names(first: 1) { edges { node { name } } } } } }
          cardTransactions(first: 50, conditions: $cardTxConditions) {
            edges {
              node {
                quantityType rawQuantity projectedQuantity period periodStartDate periodEndDate
              }
            }
          }
        }
      }
    }
    """

    metrics_vars = {
        "searchInput": {"entityType": "OPERATING_LOCATION", "id": enigma_id},
        "cardTxConditions": {
            "filter": {
                "AND": [
                    {"IN": ["period", ["3m", "12m", "2023", "2024"]]},
                    {"IN": ["quantityType", [
                        "card_revenue_amount", "avg_transaction_size", "card_transactions_count",
                        "card_customers_average_daily_count", "refunds_amount",
                        "card_revenue_yoy_growth", "card_revenue_prior_period_growth"
                    ]]}
                ]
            }
        }
    }

    metrics_resp = requests.post("https://api.enigma.com/graphql", json={"query": metrics_query, "variables": metrics_vars}, headers=search_headers)
    if metrics_resp.status_code != 200:
        raise Exception(f"Enigma metrics pull failed with status {metrics_resp.status_code}: {metrics_resp.text}")

    metrics_json = metrics_resp.json()
    if "errors" in metrics_json:
        raise Exception("Errors in metrics query: " + json.dumps(metrics_json["errors"], indent=2))

    metrics_data = metrics_json["data"]["search"][0]
    print("✅ Metrics retrieved. Inserting into Supabase...")

    business_uuid = str(uuid.uuid4())
    addr = metrics_data["addresses"]["edges"][0]["node"]

    supabase.table("enigma_businesses").insert({
        "id": business_uuid,
        "enigma_id": enigma_id,
        "google_places_id": google_places_id,
        "business_name": metrics_data["names"]["edges"][0]["node"]["name"],
        "full_address": addr["fullAddress"],
        "city": addr["city"],
        "state": addr["state"],
        "zip": addr["zip"],
        "date_pulled": datetime.today().date().isoformat()
    }).execute()

    for tx in metrics_data["cardTransactions"]["edges"]:
        node = tx["node"]
        supabase.table("enigma_metrics").insert({
            "id": str(uuid.uuid4()),
            "business_id": business_uuid,
            "quantity_type": node["quantityType"],
            "raw_quantity": node["rawQuantity"],
            "projected_quantity": node["projectedQuantity"],
            "period": node["period"],
            "period_start_date": node["periodStartDate"],
            "period_end_date": node["periodEndDate"]
        }).execute()

    print("\n✅ Enigma data successfully stored in Supabase.")

except Exception as e:
    print("\n❌ ERROR:", str(e))
    traceback.print_exc()
