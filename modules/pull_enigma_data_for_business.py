"""
Pull Enigma data for a single Google Place business (v2.2.4)

Delta vs v2.2.3
----------------
• **Street matcher fix:** compare street-only cores symmetrically (both sides drop city/STATE/ZIP) so
  "2601 Cardinal Loop, Del Valle" ≡ "2601 CARDINAL LOOP DEL VALLE TX 78617".
• **Confidence gate tightened:** skip metrics when confidence < 0.90 **even on force_repull**.
• Minor: clearer debug and comments.
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
WHITESPACE_RE = re.compile(r"\s+")


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


def _zip5(z: str | None) -> str | None:
    if not z:
        return None
    z = str(z).strip()
    m = re.match(r"(\d{5})", z)
    return m.group(1) if m else None


def _zip3(z: str | None) -> str | None:
    z5 = _zip5(z)
    return z5[:3] if z5 else None


def street_equal(g_street: str, e_full_address: str) -> bool:
    """Return True if the street line is the same, ignoring city/state/ZIP and unit synonyms.
    This is symmetric: both sides are reduced to a street-only *core* before comparison.
    """
    if not g_street or not e_full_address:
        return False

    # Normalize inputs
    g_raw = normalize_street(g_street)
    e_raw = normalize_street(e_full_address)

    # Derive a city hint from the Google address if it contains a comma
    city_hint = None
    if "," in g_raw:
        parts = [p.strip() for p in g_raw.split(",") if p.strip()]
        if len(parts) >= 2:
            city_hint = parts[1]
    # Street-only core from Google: take text before first comma
    g_core = g_raw.split(",")[0].strip()

    # Strip STATE + ZIP from Enigma side, then optional trailing city hint
    e_core = re.sub(r"\b[a-z]{2}\s+\d{5}(?:-\d{4})?$", "", e_raw).strip()
    if city_hint:
        e_core = re.sub(r"[, ]+\b" + re.escape(city_hint) + r"\b$", "", e_core, flags=re.I).strip(", ")

    return g_core == e_core


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
    g_zip_n = _zip5(g_zip)
    g_zip3 = _zip3(g_zip)

    e_city_n = (e_city or "").strip().lower()
    e_state_n = (e_state or "").strip().lower()
    e_zip_n = _zip5(e_zip)
    e_zip3 = _zip3(e_zip)

    try:
        s_equal = street_equal(g_street, e_full)
    except Exception:
        s_equal = False

    city_equal = (g_city_n == e_city_n) if g_city_n and e_city_n else False
    state_equal = (g_state_n == e_state_n) if g_state_n and e_state_n else False
    zip_equal = (g_zip_n == e_zip_n) if g_zip_n and e_zip_n else False
    zip3_equal = (g_zip3 == e_zip3) if g_zip3 and e_zip3 else False

    n_sim = _name_sim(g_name, e_name)

    # Strong street+state (+zip or zip3) rule
    if s_equal and state_equal and (zip_equal or zip3_equal):
        return 0.97, "street_zip_state_match", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
        }
    # Promote exact street+state even if zip differs, with decent name
    if s_equal and state_equal and n_sim >= 0.85:
        return 0.95, "street_state_match_name_close", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
        }

    # Existing strong rules
    if n_sim >= 0.93 and zip_equal and state_equal:
        return (1.00 if s_equal else 0.95), ("street_city_state_match" if s_equal else "name_zip_match"), {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
            "boost": "name_zip_high",
        }
    if n_sim >= 0.88 and zip_equal and state_equal:
        return (0.95 if s_equal else 0.90), "name_zip_state_match", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
            "boost": "name_zip_state",
        }

    if s_equal and city_equal and state_equal:
        conf = 1.00 if n_sim >= 0.85 else 0.95
        reason = "street_city_state_match" if conf == 1.00 else "street_match_name_close"
        return conf, reason, {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
        }

    if s_equal and (city_equal or state_equal):
        return 0.80, "street_match_partial_city_state", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
        }

    if n_sim >= 0.90 and city_equal and state_equal:
        return 0.70, "name_city_state_match", {
            "name_sim": round(n_sim, 2),
            "city_equal": city_equal, "state_equal": state_equal,
            "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
        }

    return 0.40, "weak_match", {
        "name_sim": round(n_sim, 2),
        "city_equal": city_equal, "state_equal": state_equal,
        "zip_equal": zip_equal, "zip3_equal": zip3_equal, "street_equal": s_equal,
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


def _prefer_place_component(business: dict, *keys):
    """Return the first non‑empty among possible Google Place fields for a component."""
    for k in keys:
        v = business.get(k)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _find_best_enigma_match(*, g_name, g_city, g_state, g_zip, g_street, force_repull: bool):
    g_name_clean = _clean_name(g_name)
    g_zip_norm = _zip5(g_zip)
    g_zip3 = _zip3(g_zip)

    variants = [
        {"entityType": "OPERATING_LOCATION", "name": g_name, "address": {"city": g_city, "state": g_state, "postalCode": g_zip_norm}},
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

            # Hard filter on state (when we have one)
            if g_state and e_state and str(g_state).strip().upper() != str(e_state).strip().upper():
                continue

            # Soft prune on far ZIP3 when name is weak and street doesn't match
            n_sim_tmp = _name_sim(g_name, enigma_name)
            if g_zip3 and _zip3(e_zip) and g_zip3 != _zip3(e_zip):
                if not street_equal(g_street, e_full) and n_sim_tmp < 0.80:
                    continue

            conf, reason, _dbg = score_confidence(
                g_name=g_name, g_street=g_street, g_city=g_city, g_state=g_state, g_zip=g_zip_norm,
                e_name=enigma_name, e_full=e_full, e_city=e_city, e_state=e_state, e_zip=e_zip,
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


def pull_enigma_data_for_business(business: dict, force_repull: bool = False):
    place_id = business.get("place_id")
    if not place_id:
        print("⚠️ Missing place_id; skipping pull.")
        return

    project_id = business.get("project_id")
    if not project_id:
        raise ValueError(f"project_id missing for place_id={business.get('place_id')}")
    pull_session_id = business.get("pull_session_id")

    # Prefer precise per‑place components when available
    gpid = business.get("google_places_id") or place_id
    g_name = business.get("name")
    g_city = _prefer_place_component(business, "place_city", "google_city", "city")
    g_state = _prefer_place_component(business, "place_state", "google_state", "state")
    g_zip = _prefer_place_component(business, "place_zip", "google_zip", "zip", "postal_code")
    g_street = _prefer_place_component(business, "place_address", "google_address", "address")

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
        # Fresh INSERT with generated id (DB column is NOT NULL and may lack a default)
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

    # ---- Confidence gate for metrics (strict) ----
    if match_confidence < 0.90:
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
