"""
Pull Enigma data for a single Google Place business (v2.2.2)

What changed since v2.2
-----------------------
• Fix: mapping insert sent a NULL id, but the DB column is NOT NULL without a default. We now assign a UUID on INSERT.
• Safer write path:
  - If mapping exists → UPDATE by id (no primary-key churn).
  - If mapping doesn’t exist → INSERT with generated id. If INSERT races, fall back to UPSERT on google_places_id.
• Kept per‑project metrics dedupe: ON CONFLICT (business_id, project_id, quantity_type, period, period_end_date).
• Added noisy debug lines so we can trace project_id end‑to‑end.
"""

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

# ---------------- Config: ON CONFLICT targets (must match DB unique indexes) ----------------
ON_CONFLICT_BUSINESS = "google_places_id"  # unique index exists in your DB
ON_CONFLICT_METRICS = "business_id,project_id,quantity_type,period,period_end_date"  # per‑project uniqueness

# ---------------- Normalizers / scoring ----------------
PUNCT_RE = re.compile(r"[^\w\s]")
MULTISPACE_RE = re.compile(r"\s+")
SUFFIX_RE = re.compile(r"\b(the|a|llc|pllc|inc|inc\.|co|co\.|corp|corp\.|ltd|ltd\.|spa|clinic|center)\b", re.I)
ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_unit_synonyms(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"#\s*([A-Za-z0-9\-]+)", r"suite \1", s, flags=re.I)
    s = re.sub(r"\b(ste\.?|suite|unit|apt|no\.?|number)\b\s*([A-Za-z0-9\-]+)", r"suite \2", s, flags=re.I)
    return s


def normalize_street(s: str) -> str:
    if not s:
        return ""
    s = _strip_diacritics(s).lower().strip()
    s = normalize_unit_synonyms(s)
    s = PUNCT_RE.sub(" ", s)
    s = MULTISPACE_RE.sub(" ", s)
    return s.strip()


def street_equal(g_street: str, e_full_address: str) -> bool:
    if not g_street or not e_full_address:
        return False
    e_street = e_full_address.strip()
    m = re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?$", e_street, re.I)
    if m:
        e_street = e_street[: m.start()].rstrip(", ")
    try:
        g_city_part = g_street.split(",")[1].strip()
        e_street = re.sub(r"[, ]+\b" + re.escape(g_city_part) + r"\b\s*$", "", e_street, flags=re.I).strip(", ")
    except Exception:
        pass
    return normalize_street(g_street) == normalize_street(e_street)


WHITESPACE_RE = re.compile(r"\s+")


def _clean_name(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = WHITESPACE_RE.sub(" ", s)
    s = SUFFIX_RE.sub(" ", s)
    s = WHITESPACE_RE.sub(" ", s)
    return s.strip()


def _name_sim(a: str, b: str) -> float:
    a = _clean_name(a)
    b = _clean_name(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_confidence(*, g_name, g_street, g_city, g_state, g_zip, e_name, e_full, e_city, e_state, e_zip):
    g_city_n = (g_city or "").strip().lower()
    g_state_n = (g_state or "").strip().lower()
    g_zip_n = (str(g_zip or "").strip())

    e_city_n = (e_city or "").strip().lower()
    e_state_n = (e_state or "").strip().lower()
    e_zip_n = (str(e_zip or "").strip())

    try:
        s_equal = street_equal(g_street, e_full)
    except Exception:
        s_equal = False

    city_equal = (g_city_n == e_city_n) if g_city_n and e_city_n else False
    state_equal = (g_state_n == e_state_n) if g_state_n and e_state_n else False
    zip_equal = (g_zip_n == e_zip_n) if g_zip_n and e_zip_n else False

    n_sim = _name_sim(g_name, e_name)

    if n_sim >= 0.93 and zip_equal and state_equal:
        return (1.00 if s_equal else 0.95), ("street_city_state_match" if s_equal else "name_zip_match"), {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "street_equal": s_equal,
            "boost": "name_zip_high"
        }
    if n_sim >= 0.88 and zip_equal and state_equal:
        return (0.95 if s_equal else 0.90), "name_zip_state_match", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "street_equal": s_equal,
            "boost": "name_zip_state"
        }
    if s_equal and city_equal and state_equal:
        conf = 1.00 if n_sim >= 0.85 else 0.95
        reason = "street_city_state_match" if conf == 1.00 else "street_match_name_close"
        return conf, reason, {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "street_equal": s_equal
        }
    if s_equal and (city_equal or state_equal):
        return 0.80, "street_match_partial_city_state", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "street_equal": s_equal
        }
    if n_sim >= 0.90 and city_equal and state_equal:
        return 0.70, "name_city_state_match", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "street_equal": s_equal
        }
    return 0.40, "weak_match", {
        "name_sim": round(n_sim, 2),
        "city_equal": city_equal, "state_equal": state_equal,
        "zip_equal": zip_equal, "street_equal": s_equal
    }


# ---------------- Supabase / Enigma setup ----------------
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
ENIGMA_API_KEY = os.getenv("ENIGMA_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing Supabase credentials in .env (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)")
if not ENIGMA_API_KEY:
    raise RuntimeError("Missing ENIGMA_API_KEY in .env")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
headers = {
    "x-api-key": ENIGMA_API_KEY,
    "Content-Type": "application/json",
}


# ---------------- Enigma search helpers ----------------

def _enigma_search(search_input: dict, *, timeout=20):
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
    payload = {"query": query, "variables": {"searchInput": search_input}}
    resp = requests.post("https://api.enigma.com/graphql", headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        for e in data["errors"]:
            print("  → GraphQL error:", e.get("message"))
    return data.get("data", {}).get("search", []), payload


def _find_best_enigma_match(*, g_name, g_city, g_state, g_zip, g_street, force_repull: bool):
    g_name_clean = _clean_name(g_name)
    g_zip_norm = (str(g_zip).strip() if g_zip else None)

    variants = [
        {"entityType": "OPERATING_LOCATION", "name": g_name,
         "address": {"city": g_city, "state": g_state, "postalCode": g_zip_norm}},
        {"entityType": "OPERATING_LOCATION", "name": g_name, "address": {"city": g_city, "state": g_state}},
        {"entityType": "OPERATING_LOCATION", "name": g_name_clean, "address": {"city": g_city, "state": g_state}},
        {"entityType": "OPERATING_LOCATION", "name": g_name, "address": {"state": g_state}},
        {"entityType": "OPERATING_LOCATION", "name": g_name_clean},
    ]

    if force_repull and g_name_clean:
        first_token = g_name_clean.split(" ")[0]
        if first_token:
            variants.append({"entityType": "OPERATING_LOCATION", "name": first_token})

    search_debug = []
    best = None

    for v in variants:
        try:
            results, payload = _enigma_search(v)
        except requests.exceptions.RequestException as e:
            search_debug.append({"variant": v, "error": str(e)})
            continue
        except json.JSONDecodeError:
            search_debug.append({"variant": v, "error": "json-decode-failed"})
            continue

        search_debug.append({"variant": v, "results": len(results)})
        if not results:
            continue

        for loc in results:
            enigma_name = (loc.get("names", {}).get("edges") or [{}])[0].get("node", {}).get("name")
            addr_node = (loc.get("addresses", {}).get("edges") or [{}])[0].get("node", {}) or {}
            e_city = addr_node.get("city")
            e_state = addr_node.get("state")
            e_zip = addr_node.get("zip")
            e_full = addr_node.get("fullAddress")

            conf, reason, _dbg = score_confidence(
                g_name=g_name, g_street=g_street, g_city=g_city, g_state=g_state, g_zip=g_zip_norm,
                e_name=enigma_name, e_full=e_full, e_city=e_city, e_state=e_state, e_zip=e_zip
            )
            if not best or conf > best[1]:
                best = (loc, conf, reason, {
                    "e_city": e_city, "e_state": e_state,
                    "e_zip": e_zip, "e_full": e_full, "e_name": enigma_name
                })
                if conf >= 1.00:
                    break
        if best and best[1] >= 0.95:
            break

    return best, search_debug


# ---------------- Main puller ----------------

def _to_iso(ts):
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return ts if ts is not None else None


def pull_enigma_data_for_business(business, force_repull: bool = False):
    place_id = business.get("place_id")
    if not place_id:
        print("⚠️ Missing place_id; skipping pull.")
        return

    project_id = business.get("project_id")
    if not project_id:
        raise ValueError(f"project_id missing for place_id={business.get('place_id')}")
    pull_session_id = business.get("pull_session_id")

    gpid = business.get("google_places_id") or place_id
    g_name = business.get("name")
    g_city = business.get("city")
    g_state = business.get("state")
    g_zip = business.get("zip") or business.get("postal_code")
    g_street = business.get("address")

    # Verbose debug so we can see project context on every call
    print(f"[pull] place_id={place_id} project_id={project_id} name={g_name}")

    # Check existence by google_places_id first (our conflict key), then by place_id
    existing = supabase.table("enigma_businesses").select("id").eq("google_places_id", gpid).limit(1).execute().data
    if not existing:
        existing = supabase.table("enigma_businesses").select("id").eq("place_id", place_id).limit(1).execute().data
    existing_ids = [row["id"] for row in (existing or [])]

    # If forcing, purge mapping+metrics FIRST
    if force_repull and existing_ids:
        supabase.table("enigma_metrics").delete().in_("business_id", existing_ids).execute()
        supabase.table("enigma_businesses").delete().eq("id", existing_ids[0]).execute()
        print(f"🧹 Purged mapping+metrics for place_id={place_id} (ids={existing_ids})")
        existing_ids = []

    # ♻️ Reuse mapping: only skip metrics when this project already has them
    if existing_ids and not force_repull:
        bid = existing_ids[0]
        have_metrics = (
            supabase.table("enigma_metrics").select("id").eq("business_id", bid).eq("project_id", project_id).limit(1).execute().data
        )
        if have_metrics:
            print(f"♻️ Reusing existing mapping/metrics for place_id={place_id} (this project already has metrics)")
            return bid

    best, sdbg = _find_best_enigma_match(
        g_name=g_name, g_city=g_city, g_state=g_state, g_zip=g_zip, g_street=g_street, force_repull=force_repull
    )
    if not best:
        print(f"❌ No match found for {g_name}, {g_city}, {g_state}, {g_zip}")
        print("🔎 Search attempts:", json.dumps(sdbg, indent=2, default=str))
        return

    loc, match_confidence, match_reason, addr = best
    enigma_id = loc.get("id")
    e_name = addr["e_name"]; e_full = addr["e_full"]; e_city = addr["e_city"]; e_state = addr["e_state"]; e_zip = addr["e_zip"]
    print(f"[match] conf={match_confidence:.2f} reason={match_reason} ({g_name} ⇄ {e_name})")

    # Build the row once; we assign id only on INSERT
    mapping_row_base = {
        "enigma_id": enigma_id,
        "place_id": place_id,
        "google_places_id": gpid,
        "business_name": g_name,
        "full_address": g_street,
        "city": g_city,
        "state": g_state,
        "zip": g_zip,
        "enigma_name": e_name,
        "matched_full_address": e_full,
        "matched_city": e_city,
        "matched_state": e_state,
        "matched_postal_code": e_zip,
        "date_pulled": datetime.now(timezone.utc).date().isoformat(),
        "pull_session_id": pull_session_id,
        "pull_timestamp": _to_iso(business.get("pull_timestamp")) or datetime.now(timezone.utc).isoformat(),
        "match_method": "operating_location",
        "match_confidence": match_confidence,
        "match_reason": match_reason,
    }

    if existing_ids and not force_repull:
        # Update existing row by primary key to avoid PK conflict during upsert
        business_id = existing_ids[0]
        print("[DB] update enigma_businesses by id (existing mapping)")
        supabase.table("enigma_businesses").update(mapping_row_base).eq("id", business_id).execute()
        print(f"✅ Updated mapping for place_id={place_id} (id={business_id}, conf={match_confidence:.2f})")
    else:
        # Fresh INSERT with generated id (DB column is NOT NULL and lacks a default)
        business_id = str(uuid.uuid4())
        insert_row = {"id": business_id, **mapping_row_base}
        print("[DB] insert enigma_businesses (new mapping)")
        try:
            res = supabase.table("enigma_businesses").insert(insert_row).execute()
            _ = getattr(res, "data", None)
        except Exception as e:
            print(f"[DB] insert failed, trying upsert on {ON_CONFLICT_BUSINESS}: {e}")
            # Fallback: UPSERT if insert raced with another writer
            supabase.table("enigma_businesses").upsert(insert_row, on_conflict=ON_CONFLICT_BUSINESS).execute()
        print(f"✅ Inserted/Upserted mapping for place_id={place_id} (id={business_id}, conf={match_confidence:.2f})")

    if match_confidence < 0.90 and not force_repull:
        print(f"⏭️ Skipping metrics (confidence {match_confidence:.2f} < 0.90). Mapping cached for reuse.")
        return business_id

    # ---- Metrics fetch ----
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
        "cardTxConditions": {"filter": {"AND": [
            {"IN": ["period", periods]},
            {"IN": ["quantityType", quantity_types]}
        ]}}
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

    print(f"[metrics] project_id={project_id} pull_session_id={pull_session_id} count={len(metrics)}")

    for edge in metrics:
        node = edge.get("node", {}) or {}
        metric_row = {
            "id": str(uuid.uuid4()),
            "business_id": business_id,
            "project_id": project_id,  # ✅ per‑project association
            "quantity_type": node.get("quantityType"),
            "raw_quantity": node.get("rawQuantity"),
            "projected_quantity": node.get("projectedQuantity"),
            "period": node.get("period"),
            "period_start_date": node.get("periodStartDate"),
            "period_end_date": node.get("periodEndDate"),
            "pull_session_id": pull_session_id,
            "pull_timestamp": _to_iso(business.get("pull_timestamp")) or datetime.now(timezone.utc).isoformat(),
        }
        supabase.table("enigma_metrics").upsert(
            metric_row,
            on_conflict=ON_CONFLICT_METRICS
        ).execute()

    print(f"✅ Upserted {len(metrics)} metrics for place_id={place_id}")
    return business_id
