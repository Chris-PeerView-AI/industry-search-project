import os
import uuid
import requests
import json
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment
load_dotenv()
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

headers = {
    'x-api-key': ENIGMA_API_KEY,
    'Content-Type': 'application/json'
}

# Bark & Zoom test business
business = {
    "name": "Bark & Zoom",
    "city": "Sunset Valley",
    "state": "TX",
    "zip": "78745",
    "place_id": "test_bark_zoom_place_id",
    "address": "4900 US-290, Sunset Valley, TX 78745",
    "project_id": "test_project_id",
    "pull_session_id": "test_session_id",
    "pull_timestamp": datetime.now(timezone.utc).isoformat()
}

# Query to get Enigma ID
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
        "name": business["name"],
        "address": {
            "city": business["city"],
            "state": business["state"],
            "postalCode": business["zip"]
        }
    }
}
payload = {"query": query, "variables": variables}
print("🔍 Sending GraphQL query:")
print(json.dumps(payload, indent=2))

response = requests.post("https://api.enigma.com/graphql", headers=headers, json=payload)
print("🔁 Status code:", response.status_code)
response.raise_for_status()
data = response.json()

search_results = data.get("data", {}).get("search", [])
if not search_results:
    print("❌ No match found.")
    exit()

loc = search_results[0]
enigma_id = loc.get("id")
matched_city = loc["addresses"]["edges"][0]["node"].get("city")
matched_state = loc["addresses"]["edges"][0]["node"].get("state")
matched_postal_code = loc["addresses"]["edges"][0]["node"].get("zip")
matched_full_address = loc["addresses"]["edges"][0]["node"].get("fullAddress")

print(f"\n✅ Found Enigma ID: {enigma_id}")
print(f"Matched Address: {matched_full_address}, {matched_city}, {matched_state} {matched_postal_code}")

# Query to get metrics
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

metrics_resp = requests.post("https://api.enigma.com/graphql", json={"query": metrics_query, "variables": variables}, headers=headers)
print("📊 Metrics query status:", metrics_resp.status_code)
metrics_resp.raise_for_status()
metric_data = metrics_resp.json()
metrics = metric_data.get("data", {}).get("search", [])[0].get("cardTransactions", {}).get("edges", [])

print("\n📈 Metrics:")
for edge in metrics:
    node = edge["node"]
    print(f"{node['period']:>6} | {node['quantityType']:35} | Projected: {node['projectedQuantity']:>10} | Raw: {node['rawQuantity']:>10}")