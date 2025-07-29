import requests
import json
import os
from dotenv import load_dotenv
from datetime import datetime
import traceback

# Load API key
load_dotenv()
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")
if not ENIGMA_API_KEY:
    raise ValueError("Missing ENIGMA_API_KEY in .env file")
print(f"[DEBUG] Loaded API key length: {len(ENIGMA_API_KEY)}")

# Location ID for GolfCave Clark
location_id = "7468876754963750678"

# Periods to request (3m, 12m, and annuals)
periods = ["3m", "12m", "2023", "2024"]

# Quantity types you want
quantity_types = [
    "card_revenue_amount",
    "avg_transaction_size",
    "card_transactions_count",
    "card_customers_average_daily_count",
    "refunds_amount",
    "card_revenue_yoy_growth",
    "card_revenue_prior_period_growth"
]

# GraphQL query
query = """
query GetLocationWithAggregatePeriods($searchInput: SearchInput!, $cardTxConditions: ConnectionConditions!) {
  search(searchInput: $searchInput) {
    ... on OperatingLocation {
      id
      names(first: 1) { edges { node { name } } }
      addresses(first: 1) { edges { node { fullAddress city state zip } } }
      brands(first: 1) {
        edges {
          node {
            names(first: 1) { edges { node { name } } }
          }
        }
      }
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

# Set query variables
variables = {
    "searchInput": {
        "entityType": "OPERATING_LOCATION",
        "id": location_id
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

headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json"
}

# Send request
response = requests.post("https://api.enigma.com/graphql", json={"query": query, "variables": variables}, headers=headers)
print(f"[DEBUG] Status Code: {response.status_code}")

try:
    data = response.json()
except Exception:
    print("[ERROR] Failed to parse JSON")
    traceback.print_exc()
    exit(1)

if "errors" in data:
    print("[ERROR] GraphQL errors:")
    print(json.dumps(data["errors"], indent=2))
    exit(1)

# Display results
try:
    loc = data["data"]["search"][0]
    print("\n✅ GolfCave Clark Aggregated Metrics:")
    print(f"  Name: {loc['names']['edges'][0]['node']['name']}")
    print(f"  Brand: {loc['brands']['edges'][0]['node']['names']['edges'][0]['node']['name']}")
    addr = loc["addresses"]["edges"][0]["node"]
    print(f"  Address: {addr['fullAddress']}, {addr['city']}, {addr['state']} {addr['zip']}")

    print("\n📊 Aggregated Period Data:")
    for tx in sorted(loc["cardTransactions"]["edges"], key=lambda x: x["node"]["period"]):
        n = tx["node"]
        print(
            f"{n['period']:>6} | {n['quantityType']:30} | "
            f"Projected: {n['projectedQuantity'] if n['projectedQuantity'] else 'N/A':>10} | "
            f"Raw: {n['rawQuantity'] if n['rawQuantity'] else 'N/A':>10} | "
            f"{n['periodStartDate']} → {n['periodEndDate']}"
        )

except Exception:
    print("[WARN] Could not extract or print transaction details")
    traceback.print_exc()

# Save to file
filename = f"golfcave_clark_aggregate_metrics_{datetime.now():%Y%m%d_%H%M%S}.json"
with open(filename, "w") as f:
    json.dump(data, f, indent=2)
print(f"\n✅ Full response saved to {filename}")
