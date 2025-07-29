import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
import traceback

# 🔐 Load API key
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")
if not ENIGMA_API_KEY:
    raise ValueError("Missing ENIGMA_API_KEY in .env file")
print(f"[DEBUG] Loaded API key length: {len(ENIGMA_API_KEY)}")

# 🧭 Configure full address string
brand_name = "GolfCave"
full_address = "1 Clarkton Dr, Clark, NJ 07066"

# GraphQL query for operating location search
query = """
query SearchLocation($searchInput: SearchInput!, $cardTxConditions: ConnectionConditions!) {
  search(searchInput: $searchInput) {
    ... on OperatingLocation {
      id
      names(first: 1) {
        edges {
          node {
            name
          }
        }
      }
      addresses(first: 1) {
        edges {
          node {
            streetAddress1
            city
            state
            zip
          }
        }
      }
      cardTransactions(first: 1, conditions: $cardTxConditions) {
        edges {
          node {
            projectedQuantity
          }
        }
      }
      brands(first: 1) {
        edges {
          node {
            names(first: 1) {
              edges {
                node {
                  name
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

# ✅ Final corrected input format — using fullAddress
variables = {
    "searchInput": {
        "entityType": "OPERATING_LOCATION",
        "address": {
            "fullAddress": full_address
        }
    },
    "cardTxConditions": {
        "filter": {
            "AND": [
                {"EQ": ["period", "12m"]},
                {"EQ": ["quantityType", "card_revenue_amount"]},
                {"EQ": ["rank", 0]}
            ]
        }
    }
}

headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json"
}

payload = {"query": query, "variables": variables}
print("[DEBUG] Payload:\n", json.dumps(payload, indent=2))

# Send request
response = requests.post("https://api.enigma.com/graphql", json=payload, headers=headers)
print(f"[DEBUG] Status Code: {response.status_code}")
print("[DEBUG] Raw response:\n", response.text)

# Parse and validate
try:
    data = response.json()
except json.JSONDecodeError:
    print("[ERROR] Invalid JSON")
    traceback.print_exc()
    exit(1)

if "errors" in data:
    print("[ERROR] GraphQL errors:")
    print(json.dumps(data["errors"], indent=2))
    exit(1)

# Extract and display
try:
    loc = data["data"]["search"][0]
    loc_id = loc["id"]
    loc_name = loc["names"]["edges"][0]["node"]["name"]
    addr = loc["addresses"]["edges"][0]["node"]
    revenue = loc["cardTransactions"]["edges"][0]["node"]["projectedQuantity"]
    brand = loc["brands"]["edges"][0]["node"]["names"]["edges"][0]["node"]["name"]

    print("\n✅ Found Operating Location:")
    print(f"  ID: {loc_id}")
    print(f"  Name: {loc_name}")
    print(f"  Brand: {brand}")
    print(f"  Address: {addr['streetAddress1']}, {addr['city']}, {addr['state']} {addr['zip']}")
    print(f"  12‑month Revenue: ${revenue:,.2f}")
except Exception:
    print("[WARN] Unable to parse location result")
    traceback.print_exc()

# Save full JSON
filename = f"enigma_location_{datetime.now():%Y%m%d_%H%M%S}.json"
with open(filename, "w") as f:
    json.dump(data, f, indent=2)
print(f"\n✅ Full response saved to {filename}")
