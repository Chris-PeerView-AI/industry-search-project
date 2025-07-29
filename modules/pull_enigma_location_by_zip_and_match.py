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

# Target address details for filtering
expected_street = "1 Clarkton Dr".lower()
expected_city = "Clark"
expected_state = "NJ"
expected_zip = "07066"
expected_name = "GolfCave"

# GraphQL query
query = """
query SearchLocation($searchInput: SearchInput!, $cardTxConditions: ConnectionConditions!) {
  search(searchInput: $searchInput) {
    ... on OperatingLocation {
      id
      names(first: 1) {
        edges { node { name } }
      }
      addresses(first: 1) {
        edges { node { streetAddress1 city state zip } }
      }
      cardTransactions(first: 1, conditions: $cardTxConditions) {
        edges { node { projectedQuantity } }
      }
      brands(first: 1) {
        edges {
          node {
            names(first: 1) {
              edges { node { name } }
            }
          }
        }
      }
    }
  }
}
"""

# ✅ Include 'name' in searchInput per schema requirement
variables = {
    "searchInput": {
        "entityType": "OPERATING_LOCATION",
        "name": expected_name,
        "address": {
            "city": expected_city,
            "state": expected_state,
            "postalCode": expected_zip
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
response = requests.post("https://api.enigma.com/graphql", json=payload, headers=headers)
print(f"[DEBUG] Status Code: {response.status_code}")

try:
    data = response.json()
except Exception as e:
    print("[ERROR] Could not parse JSON response")
    traceback.print_exc()
    raise

if "errors" in data:
    print("[ERROR] GraphQL errors:")
    print(json.dumps(data["errors"], indent=2))
    raise Exception("GraphQL error in response")

# Find exact match on street address
matched = None
for loc in data["data"]["search"]:
    try:
        addr = loc["addresses"]["edges"][0]["node"]
        if addr["streetAddress1"].lower().strip() == expected_street:
            matched = {
                "id": loc["id"],
                "name": loc["names"]["edges"][0]["node"]["name"],
                "brand": loc["brands"]["edges"][0]["node"]["names"]["edges"][0]["node"]["name"],
                "address": addr,
                "revenue": loc["cardTransactions"]["edges"][0]["node"]["projectedQuantity"]
            }
            break
    except Exception:
        continue

if matched:
    print("\n✅ Match found:")
    print(json.dumps(matched, indent=2))
else:
    print("\n⚠️ No exact match found for street address.")

# Save full response
filename = f"enigma_location_results_{datetime.now():%Y%m%d_%H%M%S}.json"
with open(filename, "w") as f:
    json.dump(data, f, indent=2)
print(f"\n✅ Full response saved to {filename}")
