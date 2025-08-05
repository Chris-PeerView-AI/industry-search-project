import os
import json
import requests
from dotenv import load_dotenv

# Load API key
load_dotenv()
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

headers = {
    'x-api-key': ENIGMA_API_KEY,
    'Content-Type': 'application/json'
}

# Replace this with your known valid Enigma ID
ENIGMA_ID = "7468876754963750678"

query = """
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

variables = {
    "searchInput": {
        "entityType": "OPERATING_LOCATION",
        "id": ENIGMA_ID
    },
    "cardTxConditions": {
        "filter": {
            "AND": [
                {"IN": ["period", ["3m", "12m", "2023", "2024"]]},
                {"IN": ["quantityType", [
                    "card_revenue_amount",
                    "avg_transaction_size",
                    "card_transactions_count",
                    "card_customers_average_daily_count",
                    "refunds_amount",
                    "card_revenue_yoy_growth",
                    "card_revenue_prior_period_growth"
                ]]}
            ]
        }
    }
}

# Send request
response = requests.post("https://api.enigma.com/graphql", json={"query": query, "variables": variables}, headers=headers)

print("✅ Status:", response.status_code)

try:
    data = response.json()
    print("📄 Full Response:")
    print(json.dumps(data, indent=2))

    edges = data.get("data", {}).get("search", [])[0].get("cardTransactions", {}).get("edges", [])
    if edges:
        print(f"✅ Retrieved {len(edges)} metrics")
        for e in edges:
            node = e["node"]
            print(f"• {node['period']}: {node['quantityType']} = {node['rawQuantity']}")
    else:
        print("❌ No metrics found")

except Exception as e:
    print("❌ Error parsing response:", e)
    print("↩ Raw text:", response.text)
