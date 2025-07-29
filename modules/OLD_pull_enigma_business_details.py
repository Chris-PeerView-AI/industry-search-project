import requests
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# Load API key
env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

if not ENIGMA_API_KEY:
    raise ValueError("Missing ENIGMA_API_KEY in .env file")

print(f"[DEBUG] Loaded Enigma API Key - Length: {len(ENIGMA_API_KEY)}")

# Enigma business ID to test
enigma_id = "E002508fc200016294"

# GraphQL query for business details
query = """
query GetBusiness($enigmaId: ID!) {
  business(enigmaId: $enigmaId) {
    name
    website
    addresses {
      streetAddress1
      city
      state
      postalCode
    }
  }
}
"""

variables = {"enigmaId": enigma_id}
payload = {"query": query, "variables": variables}
headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json"
}

print("[DEBUG] Sending request with payload:")
print(json.dumps(payload, indent=2))

# Make the API call
response = requests.post("https://api.enigma.com/graphql", json=payload, headers=headers)

print(f"[DEBUG] HTTP Status: {response.status_code}")
print("[DEBUG] Raw Response:")
print(response.text)

# Try parsing response
try:
    data = response.json()
except json.JSONDecodeError as e:
    print("[ERROR] Failed to parse JSON response")
    raise e

# Optional: check for GraphQL errors
if "errors" in data:
    print("[ERROR] GraphQL errors found in response:")
    print(json.dumps(data["errors"], indent=2))
    raise Exception("GraphQL error encountered. Check output for details.")

# Save to file regardless
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
output_file = f"enigma_business_pull_{timestamp}.json"
with open(output_file, "w") as f:
    json.dump(data, f, indent=2)

print(f"✅ Enigma business details saved to {output_file}")
