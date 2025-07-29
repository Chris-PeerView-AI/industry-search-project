import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# Load API key from .env
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

if not ENIGMA_API_KEY:
    raise ValueError("Missing ENIGMA_API_KEY in .env file")

print(f"[DEBUG] Loaded Enigma API Key - Length: {len(ENIGMA_API_KEY)}")

# Define your search query
query_string = "GolfCave Clark NJ"
print(f"[DEBUG] Using businessSearch query: {query_string}")

# GraphQL query using businessSearch
query = """
query SearchBusinesses($query: String!) {
  businessSearch(query: $query) {
    totalCount
    businesses {
      enigmaId
      name
      website
    }
  }
}
"""

variables = {"query": query_string}
payload = {"query": query, "variables": variables}
headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json"
}

# Send request
response = requests.post("https://api.enigma.com/graphql", json=payload, headers=headers)

print(f"[DEBUG] HTTP Status: {response.status_code}")
print("[DEBUG] Raw response:")
print(response.text)

# Try parsing the response
try:
    data = response.json()
except json.JSONDecodeError:
    print("[ERROR] Failed to decode JSON")
    raise

# Check for GraphQL errors
if "errors" in data:
    print("[ERROR] GraphQL errors:")
    print(json.dumps(data["errors"], indent=2))
    raise Exception("GraphQL error encountered. Check output for details.")

# Save output to file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"enigma_search_output_{timestamp}.json"
with open(output_file, "w") as f:
    json.dump(data, f, indent=2)

print(f"✅ Enigma businessSearch results saved to {output_file}")
