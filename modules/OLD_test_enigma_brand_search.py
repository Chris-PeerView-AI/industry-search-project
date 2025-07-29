import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
import traceback

# Load environment variables
try:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path)
    ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

    if not ENIGMA_API_KEY:
        raise ValueError("ENIGMA_API_KEY not found in .env file")

    print(f"[DEBUG] API key loaded. Length: {len(ENIGMA_API_KEY)}")
except Exception as e:
    print("[FATAL] Error loading environment variables")
    traceback.print_exc()
    exit(1)

# Setup query
brand_name = "GolfCave"
print(f"[DEBUG] Preparing search for brand: '{brand_name}'")

query = """
query SearchBrand($searchInput: SearchInput!, $cardTransactionConditions: ConnectionConditions!) {
  search(searchInput: $searchInput) {
    ... on Brand {
      id
      enigmaId
      names(first: 1) {
        edges {
          node {
            name
          }
        }
      }
      count(field: "operatingLocations")
      cardTransactions(first: 1, conditions: $cardTransactionConditions) {
        edges {
          node {
            projectedQuantity
          }
        }
      }
    }
  }
}
"""

variables = {
    "searchInput": {
        "entityType": "BRAND",
        "name": brand_name
    },
    "cardTransactionConditions": {
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

payload = {
    "query": query,
    "variables": variables
}

# Debug print payload
print("[DEBUG] Final payload being sent:")
print(json.dumps(payload, indent=2))

# Make the API request
try:
    response = requests.post("https://api.enigma.com/graphql", json=payload, headers=headers)
except Exception as e:
    print("[FATAL] Error making request to Enigma API:")
    traceback.print_exc()
    exit(1)

print(f"[DEBUG] HTTP status code: {response.status_code}")

# Raw response for inspection
print("[DEBUG] Raw response text:")
print(response.text)

# Try parsing JSON
try:
    data = response.json()
except json.JSONDecodeError as e:
    print("[ERROR] Failed to decode response as JSON")
    traceback.print_exc()
    exit(1)

# Check for GraphQL errors
if "errors" in data:
    print("[ERROR] GraphQL errors returned:")
    print(json.dumps(data["errors"], indent=2))
    exit(1)

# Print extracted fields for quick inspection
try:
    brand = data["data"]["search"][0]  # search returns a list
    name = brand["names"]["edges"][0]["node"]["name"]
    enigma_id = brand["enigmaId"]
    revenue = brand["cardTransactions"]["edges"][0]["node"]["projectedQuantity"]
    location_count = brand["count"]

    print(f"[RESULT] Brand Name: {name}")
    print(f"[RESULT] Enigma ID: {enigma_id}")
    print(f"[RESULT] 12M Revenue: ${revenue:,.2f}")
    print(f"[RESULT] Location Count: {location_count}")
except Exception as e:
    print("[WARN] Could not fully parse result fields:")
    traceback.print_exc()

# Save full output to file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"enigma_brand_search_output_{timestamp}.json"
try:
    with open(output_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Full JSON response saved to: {output_file}")
except Exception as e:
    print("[ERROR] Failed to save output to file")
    traceback.print_exc()
