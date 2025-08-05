import os
import json
import requests
from dotenv import load_dotenv

# Load env vars
load_dotenv()
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

headers = {
    'x-api-key': ENIGMA_API_KEY,
    'Content-Type': 'application/json'
}

query = """
query SearchLocation($searchInput: SearchInput!) {
  search(searchInput: $searchInput) {
    ... on OperatingLocation {
      id
      names(first: 1) { edges { node { name } } }
      addresses(first: 1) { edges { node { fullAddress city state zip } } }
    }
  }
}
"""

variables = {
    "searchInput": {
        "entityType": "OPERATING_LOCATION",
        "name": "GOLFCAVE",
        "address": {
            "city": "Clark",
            "state": "NJ",
            "postalCode": "07066"
        }
    }
}

payload = {"query": query, "variables": variables}
response = requests.post("https://api.enigma.com/graphql", headers=headers, json=payload)

print("✅ Status Code:", response.status_code)

try:
    data = response.json()
    print("📄 Raw Response:")
    print(json.dumps(data, indent=2))

    results = data.get("data", {}).get("search", [])
    if results:
        print("✅ Match found:")
        for res in results:
            addr = res["addresses"]["edges"][0]["node"]
            print(f"→ {addr['fullAddress']}")
    else:
        print("❌ No match found")

except Exception as e:
    print("❌ Failed to parse JSON:", str(e))
    print("↩ Raw Text:", response.text)
