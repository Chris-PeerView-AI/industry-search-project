import os
import uuid
import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

import requests
from dotenv import load_dotenv
from supabase import create_client

# ---------------- Normalizers / scoring ----------------
PUNCT_RE = re.compile(r"[^\w\s]")
MULTISPACE_RE = re.compile(r"\s+")

def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

def normalize_unit_synonyms(s: str) -> str:
    if not s: return ""
    s = re.sub(r"#\s*([A-Za-z0-9\-]+)", r"suite \1", s, flags=re.I)
    s = re.sub(r"\b(ste\.?|suite|unit|apt|no\.?|number)\b\s*([A-Za-z0-9\-]+)", r"suite \2", s, flags=re.I)
    return s

def normalize_street(s: str) -> str:
    if not s: return ""
    s = _strip_diacritics(s).lower().strip()
    s = normalize_unit_synonyms(s)
    s = PUNCT_RE.sub(" ", s)
    s = MULTISPACE_RE.sub(" ", s)
    return s.strip()

def street_equal(g_street: str, e_street: str) -> bool:
    return normalize_street(g_street) == normalize_street(e_street) and bool(g_street and e_street)

def name_sim(a: str, b: str) -> float:
    if not a or not b: return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()

def score_confidence(*, g_name, g_street, g_city, g_state, g_zip, e_name, e_full, e_city, e_state, e_zip):
    # Extract Enigma "street" from full address (before city/state/zip if present)
    e_street = (e_full or "").strip()
    m = re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?$", e_street, re.I)
    if m: e_street = e_street[:m.start()].rstrip(", ")
    if g_city:
        e_street = re.sub(r"[, ]+\b" + re.escape(g_city) + r"\b\s*$", "", e_street, flags=re.I).strip(", ")

    s_equal = street_equal(g_street, e_street)
    city_equal = (g_city or "").strip().lower() == (e_city or "").strip().lower()
    state_equal = (g_state or "").strip().lower() == (e_state or "").strip().lower()
    zip_equal = (str(g_zip or "").strip() == str(e_zip or "").strip())
    n_sim = name_sim(g_name, e_name)

    # Tight confidence: require street + city/state for "high"
    reason_bits = []
    if not s_equal: reason_bits.append("street_mismatch")
    if not city_equal or not state_equal: reason_bits.append("cross_city_state")
    elif not zip_equal: reason_bits.append("cross_zip")
    if n_sim < 0.75: reason_bits.append("name_low_sim")

    if s_equal and city_equal and state_equal:
        conf = 1.00 if n_sim >= 0.85 else 0.95
        reason = "street_city_state_match" if conf == 1.00 else "street_match_name_close"
    elif s_equal and (city_equal or state_equal):
        conf = 0.80
        reason = "street_match_partial_city_state"
    elif n_sim >= 0.90 and city_equal and state_equal:
        conf = 0.70
        reason = "name_city_state_match"
    else:
        conf = 0.40
        reason = ",".join(reason_bits) or "weak_match"

    return conf, reason, {
        "s_equal": s_equal, "city_equal": city_equal, "state_equal": state_equal,
        "zip_equal": zip_equal, "name_sim": round(n_sim, 2)
    }

# ---------------- Supabase / Enigma setup ----------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json",
}

# ---------------- Main puller ----------------
def pull_enigma_data_for_business(business, force_repull: bool = False):
    # ---- Inputs from Google row ----
    place_id = business.get("place_id")
    if not place_id:
        print("⚠️ Missing place_id; skipping pull.")
        return

    gpid = business.get("google_places_id") or place_id
    g_name = business.get("name")
    g_city = business.get("city")
    g_state = business.get("state")
    g_zip = business.get("zip") or business.get("postal_code")   # postal_code fallback
    g_street = business.get("address")

    # ---- Global reuse by place_id ----
    existing = (
        supabase.table("enigma_businesses")
        .select("id,enigma_id,matched_full_address,matched_city,matched_state,matched_postal_code,match_confidence")
        .eq("place_id", place_id)
        .execute()
        .data
    )

    if existing and not force_repull:
        business_id = existing[0]["id"]
        have_metrics = (
            supabase.table("enigma_metrics")
            .select("id")
            .eq("business_id", business_id)
            .limit(1)
            .execute()
            .data
        )
        if have_metrics:
            print(f"♻️ Reusing existing mapping/metrics for place_id={place_id}")
            return business_id
        # else: proceed to score and decide about metrics

    # ---- Enigma search ----
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
            "name": g_name,
            "address": {"city": g_city, "state": g_state, "postalCode": g_zip},
        }
    }
    payload = {"query": query, "variables": variables}
    try:
        response = requests.post(
            "https://api.enigma.com/graphql",
            headers=headers,
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"🚨 Request error for {g_name}, {g_city}, {g_state}, {g_zip}: {e}")
        print("📄 Sent payload:", json.dumps(payload, indent=2))
        return
    except json.JSONDecodeError:
        print("🚨 Failed to parse JSON response from Enigma.")
        print("🔄 Raw response:", response.text)
        return

    if "errors" in data:
        print(f"⚠️ GraphQL errors for {g_name}, {g_city}, {g_state}, {g_zip}:")
        for error in data["errors"]:
            print("  →", error.get("message"))

    search_results = data.get("data", {}).get("search", [])
    if not search_results:
        print(f"❌ No match found for {g_name}, {g_city}, {g_state}, {g_zip}")
        print("📄 Sent payload:", json.dumps(payload, indent=2))
        return

    # ---- Pick first result, capture Enigma fields ----
    loc = search_results[0]
    enigma_id = loc.get("id")
    enigma_name = (loc.get("names", {}).get("edges") or [{}])[0].get("node", {}).get("name")
    addr_node = (loc.get("addresses", {}).get("edges") or [{}])[0].get("node", {}) or {}
    e_city = addr_node.get("city")
    e_state = addr_node.get("state")
    e_zip = addr_node.get("zip")
    e_full = addr_node.get("fullAddress")

    # ---- Confidence scoring ----
    match_confidence, match_reason, dbg = score_confidence(
        g_name=g_name, g_street=g_street, g_city=g_city, g_state=g_state, g_zip=g_zip,
        e_name=enigma_name, e_full=e_full, e_city=e_city, e_state=e_state, e_zip=e_zip
    )
    print(f"[match] conf={match_confidence:.2f} reason={match_reason} dbg={dbg}")

    # ---- Upsert mapping globally by place_id ----
    business_id = (existing and not force_repull) and existing[0]["id"] or str(uuid.uuid4())
    mapping_row = {
        "id": business_id,
        "enigma_id": enigma_id,
        "place_id": place_id,
        "google_places_id": gpid,
        # Google-side fields
        "business_name": g_name,
        "full_address": g_street,
        "city": g_city,
        "state": g_state,
        "zip": g_zip,
        # Enigma chosen fields
        "enigma_name": enigma_name,
        "matched_full_address": e_full,
        "matched_city": e_city,
        "matched_state": e_state,
        "matched_postal_code": e_zip,
        # Audit / scoring
        "date_pulled": datetime.now(timezone.utc).date().isoformat(),
        "pull_session_id": business.get("pull_session_id"),
        "pull_timestamp": datetime.now(timezone.utc).isoformat(),
        "match_method": "operating_location",
        "match_confidence": match_confidence,
        "match_reason": match_reason,
    }
    supabase.table("enigma_businesses").upsert(mapping_row, on_conflict="place_id").execute()
    print(f"✅ Upserted mapping for place_id={place_id} (conf={match_confidence:.2f})")

    # ---- Gate metrics on confidence (skip if low unless forced) ----
    if match_confidence < 0.90 and not force_repull:
        print(f"⏭️ Skipping metrics (confidence {match_confidence:.2f} < 0.90). Mapping cached for reuse.")
        return business_id

    # ---- Metrics query ----
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
        "card_revenue_prior_period_growth",
    ]
    variables = {
        "searchInput": {"entityType": "OPERATING_LOCATION", "id": enigma_id},
        "cardTxConditions": {
            "filter": {
                "AND": [
                    {"IN": ["period", periods]},
                    {"IN": ["quantityType", quantity_types]},
                ]
            }
        },
    }
    metric_resp = requests.post(
        "https://api.enigma.com/graphql",
        json={"query": metrics_query, "variables": variables},
        headers=headers,
        timeout=30,
    )
    metric_resp.raise_for_status()
    metric_data = metric_resp.json()
    metrics = (metric_data.get("data", {}).get("search") or [{}])[0].get("cardTransactions", {}).get("edges", [])

    # ---- Upsert metrics ----
    for edge in metrics:
        node = edge.get("node", {}) or {}
        metric_row = {
            "id": str(uuid.uuid4()),
            "business_id": business_id,
            "quantity_type": node.get("quantityType"),
            "raw_quantity": node.get("rawQuantity"),
            "projected_quantity": node.get("projectedQuantity"),
            "period": node.get("period"),
            "period_start_date": node.get("periodStartDate"),
            "period_end_date": node.get("periodEndDate"),
            "pull_session_id": business.get("pull_session_id"),
            "pull_timestamp": business.get("pull_timestamp").isoformat()
                if isinstance(business.get("pull_timestamp"), datetime)
                else business.get("pull_timestamp"),
        }
        supabase.table("enigma_metrics").upsert(
            metric_row,
            on_conflict="business_id,quantity_type,period,period_end_date",
        ).execute()

    print(f"✅ Upserted {len(metrics)} metrics for place_id={place_id}")
    return business_id
