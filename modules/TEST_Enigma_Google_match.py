
"""
PeerView AI — Enigma vs Google Places Address Checker

EXECUTIVE SUMMARY (WHAT WE LEARNED)
- Google-side data comes from `search_results` (address, city/state/zip, place_id, project_id).
- Enigma-side mapping comes from `enigma_businesses` (enigma_id, matched_full_address, matched_city/state/zip), saved at pull time.
- Metrics come from `enigma_summaries` (annual_revenue, yoy_growth, ticket_size) linked via `search_result_id`.
- The dominant problem is cross_city_state: chain brands often get matched to a different city’s Enigma location (e.g., Dallas vs Austin).
- Formatting (STE/#/Suite/case) isn’t the core issue—our normalizer handles that.
- Some Google places have no row in `enigma_businesses` (pull skipped/failed) → reported as `missing_enigma_business`.
- Server-side `eq(project_id, <uuid>)` occasionally raised 22P02 in our env; this script now falls back to unfiltered paging + client-side filter.

HOW THIS TEST WORKS (HIGH LEVEL)
1) Pull a pool of `enigma_summaries` rows (server-side filter if possible; else fallback to paging).
2) Join to `search_results` to get the Google address and project_id.
3) Lookup `enigma_businesses` by `place_id` to get `matched_full_address` and `enigma_id`.
4) Normalize both addresses (strip diacritics, remove punctuation, collapse spaces) and compare with `--min-addr-sim` (default 1.0 exact after normalization).
5) Print mismatches (and matches with `--verbose`) and write an optional CSV.
6) Emit a diagnostics block so we can see where rows were lost.

TERMS & CATEGORIES (seen in enhanced builds)
- street_equal: street-line equality with unit synonyms normalized ("#101", "STE 101", "Suite 101").
- category:
  - cross_street: same city/state (and usually zip), different street line.
  - cross_zip: same street & city/state, different zip.
  - cross_city_state: city/state differ (dominant in your latest run).

WHY WE GET FALSE POSITIVES
- Enigma search by name + city can return another branch of the same brand. Without a guardrail, we save that wrong enigma_id + address.
- Later charts show the correct Google location but Enigma metrics for a different store.

TOMORROW’S FIX PLAN (INGESTION)
1) Guardrail at pull time: only insert/update `enigma_businesses` if Enigma street equals Google street (after normalization). Otherwise, skip or mark low-confidence.
2) Repair harness: read the mismatch CSV, re-query Enigma with stricter street scoring, update only the broken mappings; optionally refresh metrics for those.

CLI QUICK START
Strict equality + CSV of mismatches:
  python modules/test_enigma_google_match.py \
    --project 27e72d0e-a4bc-4a7f-9b37-feea1561d2a \
    --limit 50 \
    --min-addr-sim 1.0 \
    --csv addr_mismatches.csv

See everything + debug paging logs:
  python modules/test_enigma_google_match.py \
    --project 27e72d0e-a4bc-4a7f-9b37-feea1561d2a \
    --limit 50 \
    --min-addr-sim 1.0 \
    --verbose --debug

OUTPUT INTERPRETATION
- Mismatch blocks: Google full address vs Enigma `matched_full_address` + revenue/growth/ticket and a `reason` when available.
- Diagnostics: counters across each stage. If server-side filter failed, you’ll see `fallback=True` and client-side filtering kept results correct.
- CSV: includes normalized forms to make spreadsheet triage easy.

SCHEMA NOTES & PITFALLS
- `search_results`: may have `postal_code` or `zip`; this script tries both.
- `enigma_businesses`: `matched_full_address`, `matched_city`, `matched_state`, `matched_postal_code`, `enigma_id` confirmed present.
- `enigma_summaries`: linked by `search_result_id`; server-side project filter may throw 22P02 in some environments.

HANDY SQL (optional)
-- Side-by-side addresses for this project
select
  es.id as enigma_summary_id,
  sr.id as search_result_id,
  sr.name,
  sr.address as google_address,
  sr.city as google_city,
  sr.state as google_state,
  coalesce(sr.postal_code, sr.zip) as google_zip,
  eb.enigma_id,
  eb.matched_full_address,
  eb.matched_city,
  eb.matched_state,
  eb.matched_postal_code
from enigma_summaries es
join search_results sr on sr.id = es.search_result_id
left join enigma_businesses eb on eb.place_id = sr.place_id
where es.project_id = '27e72d0e-a4bc-4a7f-9b37-feea1561d2a';

-- Enigma IDs mapped to multiple place_ids (brand cross-wires)
select enigma_id, count(distinct place_id) as place_ids
from enigma_businesses
where project_id = '27e72d0e-a4bc-4a7f-9b37-feea1561d2a'
group by enigma_id
having count(distinct place_id) > 1
order by place_ids desc;

This file is our measurement tool. Once we tighten the ingestion guardrails and repair existing rows,
re-run this test; cross-city mismatches should drop dramatically.
"""



#!/usr/bin/env python3
"""
PeerView AI — Enigma vs Google Places Address Checker (Street-aware v2.1)

What this does
--------------
Given a project UUID, compare the Google Places **street address** (from `search_results.address`)
vs the Enigma **matched street** extracted from `enigma_businesses.matched_full_address`.
We normalize case, punctuation, and unit synonyms ("#", "STE", "SUITE", "APT", "UNIT").
City/State/ZIP differences alone no longer trigger a mismatch. We also show **triage fields**
for mismatches (matched_city/state/zip vs google city/state/zip) and bucket each mismatch
into a category: `cross_city_state`, `cross_zip`, or `cross_street`.

You can still set `--min-addr-sim` < 1.0 to allow fuzzy *full* comparisons when street-only
fails; but the primary decision is now **street-line equality**.

Usage
-----
python modules/test_enigma_google_match.py \
  --project 27e72d0e-a4bc-4a7f-9b37-feea156e1d2a \
  --limit 50 \
  --min-addr-sim 1.0 \
  --verbose \
  --debug

Notable changes
---------------
- New street-aware comparison avoids false mismatches like:
  "222 West Ave #120" vs "222 WEST AVE STE 120 AUSTIN TX 78701" ✅
- Unit synonyms unified to "suite <num>" before normalization.
- Extracts **Enigma street** by trimming `STATE ZIP` (and trailing `CITY`) from `matched_full_address`.
- Prints `enigma_id` so we can spot cross-wiring (same Enigma mapped to multiple Places).
- Keeps the gpid fallback join and robust paging + diagnostics.
- **v2.1** Adds mismatch categories + shows matched_city/state/zip and google city/state/zip.
"""

import argparse
import csv
import os
import re
import sys
import uuid
import unicodedata
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Tuple
from difflib import SequenceMatcher
from collections import Counter

from dotenv import load_dotenv
from supabase import create_client, Client
from postgrest.exceptions import APIError

# ----------------------------
# Normalization helpers
# ----------------------------
PUNCT_RE = re.compile(r"[^\w\s]")
MULTISPACE_RE = re.compile(r"\s+")
STATE_ZIP_TAIL_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?$", re.I)


def _strip_diacritics(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


def normalize_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = _strip_diacritics(s)
    s = s.lower().strip()
    s = PUNCT_RE.sub(" ", s)
    s = MULTISPACE_RE.sub(" ", s)
    return s.strip()


def normalize_unit_synonyms(s: Optional[str]) -> str:
    if not s:
        return ""
    # Convert common unit markers to a canonical 'suite <num>'
    s = re.sub(r"#\s*(\d+)", r"suite \1", s, flags=re.I)
    s = re.sub(r"\b(ste\.?|suite|unit|apt|no\.?|number)\b", "suite", s, flags=re.I)
    s = re.sub(r"\bsuite\s*(\d+)", r"suite \1", s, flags=re.I)
    return s


def normalize_street_only(s: Optional[str]) -> str:
    return normalize_text(normalize_unit_synonyms(s))


def equalish(a: Optional[str], b: Optional[str], *, threshold: float = 1.0) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if not na and not nb:
        return True
    if not na or not nb:
        return False
    if threshold >= 1.0:
        return na == nb
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def extract_enigma_street(enigma_full: Optional[str], srow: Dict[str, Any]) -> str:
    """Trim 'STATE ZIP' and trailing CITY from Enigma's matched_full_address to get the street line."""
    if not enigma_full:
        return ""
    s = enigma_full.strip()
    # Remove 'STATE ZIP' tail if present
    m = STATE_ZIP_TAIL_RE.search(s)
    if m:
        s = s[: m.start()].rstrip(", ")
    # Remove trailing city token if it matches Google city
    city = (srow.get("city") or "").strip()
    if city:
        s = re.sub(r"[, ]+\b" + re.escape(city) + r"\b\s*$", "", s, flags=re.I)
    return s.strip(", ")


# ----------------------------
# Data structures
# ----------------------------
@dataclass
class MatchRow:
    enigma_summary_id: str
    project_id: str
    search_result_id: Optional[str]
    place_id: Optional[str]
    enigma_id: Optional[str]
    g_address_full: Optional[str]
    enigma_matched_full_address: Optional[str]
    enigma_street: Optional[str]
    g_street: Optional[str]
    # Triage fields
    g_city: Optional[str]
    g_state: Optional[str]
    g_zip: Optional[str]
    e_city: Optional[str]
    e_state: Optional[str]
    e_zip: Optional[str]
    city_equal: bool = False
    state_equal: bool = False
    zip_equal: bool = False
    # Metrics
    enigma_annual_revenue: Optional[float] = None
    enigma_yoy_growth: Optional[float] = None
    enigma_ticket_size: Optional[float] = None
    # Decisions
    street_equal: bool = False
    equal_after_norm: bool = False
    reason: str = "mismatch"  # mismatch | match | missing_search_result | missing_place_id | missing_enigma_business | no_matched_full_address
    category: Optional[str] = None  # cross_city_state | cross_zip | cross_street | none


# ----------------------------
# Supabase client
# ----------------------------

def get_supabase() -> Client:
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")
    return create_client(url, key)


# ----------------------------
# Fetch helpers (paged + bulk)
# ----------------------------

def try_fetch_enigma_page(sb: Client, project_id: str, start: int, end: int) -> List[Dict[str, Any]]:
    sel = "id, project_id, annual_revenue, yoy_growth, ticket_size, search_result_id"
    q = sb.table("enigma_summaries").select(sel).eq("project_id", project_id).range(start, end)
    resp = q.execute()
    return resp.data or []


def fallback_fetch_enigma_page(sb: Client, start: int, end: int) -> List[Dict[str, Any]]:
    sel = "id, project_id, annual_revenue, yoy_growth, ticket_size, search_result_id"
    q = sb.table("enigma_summaries").select(sel).range(start, end)
    resp = q.execute()
    return resp.data or []


def chunked(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i : i + n] for i in range(0, len(lst), n)]


def fetch_search_results_bulk(sb: Client, srids: List[str]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    if not srids:
        return results
    selects = [
        "id, name, address, city, state, postal_code, place_id, google_places_id, project_id",
        "id, name, address, city, state, zip, place_id, google_places_id, project_id",
        "id, name, address, place_id, google_places_id, project_id",
        "id, name, address, place_id, project_id",
    ]
    for sel in selects:
        try:
            for batch in chunked(srids, 500):
                resp = sb.table("search_results").select(sel).in_("id", batch).execute()
                for row in resp.data or []:
                    results[str(row["id"])] = row
            return results
        except Exception:
            results.clear()
            continue
    return results


def fetch_enigma_businesses_bulk(sb: Client, place_ids: List[str], gpids: List[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_place: Dict[str, Dict[str, Any]] = {}
    by_gpid: Dict[str, Dict[str, Any]] = {}
    sel = (
        "id, enigma_id, matched_full_address, matched_city, matched_state, matched_postal_code, "
        "full_address, place_id, google_places_id, project_id"
    )
    if place_ids:
        for batch in chunked(place_ids, 500):
            resp = sb.table("enigma_businesses").select(sel).in_("place_id", batch).execute()
            for row in resp.data or []:
                if row.get("place_id"):
                    by_place[str(row["place_id"])] = row
    if gpids:
        for batch in chunked(gpids, 500):
            resp = sb.table("enigma_businesses").select(sel).in_("google_places_id", batch).execute()
            for row in resp.data or []:
                if row.get("google_places_id"):
                    by_gpid[str(row["google_places_id"])] = row
    return by_place, by_gpid


# ----------------------------
# Row building + printing
# ----------------------------

def build_google_full_address(srow: Dict[str, Any]) -> Optional[str]:
    if not srow:
        return None
    zip_like = srow.get("postal_code") or srow.get("zip")
    parts = [srow.get("address"), srow.get("city"), srow.get("state"), zip_like]
    return " ".join([x for x in parts if x]) if any(parts) else None


def build_match_row(erow: Dict[str, Any], srow: Dict[str, Any], ebiz: Dict[str, Any], *, min_addr_sim: float) -> MatchRow:
    g_full = build_google_full_address(srow)
    g_street = srow.get("address") if srow else None

    enigma_full = ebiz.get("matched_full_address") if ebiz else None
    enigma_street = extract_enigma_street(enigma_full, srow) if enigma_full else None

    g_city = srow.get("city") if srow else None
    g_state = srow.get("state") if srow else None
    g_zip = srow.get("postal_code") or srow.get("zip") if srow else None

    e_city = ebiz.get("matched_city") if ebiz else None
    e_state = ebiz.get("matched_state") if ebiz else None
    e_zip = ebiz.get("matched_postal_code") if ebiz else None

    mr = MatchRow(
        enigma_summary_id=str(erow.get("id")),
        project_id=str(erow.get("project_id")),
        search_result_id=erow.get("search_result_id"),
        place_id=srow.get("place_id") if srow else None,
        enigma_id=ebiz.get("enigma_id") if ebiz else None,
        g_address_full=g_full,
        enigma_matched_full_address=enigma_full,
        enigma_street=enigma_street,
        g_street=g_street,
        g_city=g_city,
        g_state=g_state,
        g_zip=g_zip,
        e_city=e_city,
        e_state=e_state,
        e_zip=e_zip,
        enigma_annual_revenue=erow.get("annual_revenue"),
        enigma_yoy_growth=erow.get("yoy_growth"),
        enigma_ticket_size=erow.get("ticket_size"),
    )

    if not srow:
        mr.reason = "missing_search_result"
        mr.category = None
        return mr
    if not mr.place_id:
        mr.reason = "missing_place_id"
    if not ebiz:
        mr.reason = "missing_enigma_business"
        mr.category = None
        return mr
    if not enigma_full:
        mr.reason = "no_matched_full_address"
        mr.category = None
        return mr

    # 1) Street-only equality (strict)
    g_street_norm = normalize_street_only(g_street)
    e_street_norm = normalize_street_only(enigma_street)
    mr.street_equal = g_street_norm == e_street_norm and bool(g_street_norm)

    # 2) Full-string fallback (optionally fuzzy)
    mr.equal_after_norm = mr.street_equal or equalish(mr.g_address_full, mr.enigma_matched_full_address, threshold=min_addr_sim)

    # Triage booleans
    mr.city_equal = normalize_text(g_city) == normalize_text(e_city)
    mr.state_equal = normalize_text(g_state) == normalize_text(e_state)
    mr.zip_equal = normalize_text(g_zip) == normalize_text(e_zip)

    # Category
    if mr.equal_after_norm:
        mr.reason = "match"
        mr.category = None
    else:
        mr.reason = "mismatch"
        if not (mr.city_equal and mr.state_equal):
            mr.category = "cross_city_state"
        elif not mr.zip_equal:
            mr.category = "cross_zip"
        else:
            mr.category = "cross_street"

    return mr


def print_row(mr: MatchRow, *, verbose: bool) -> bool:
    """Return True if this row is a *mismatch* (for CSV collection)."""
    if mr.equal_after_norm and not verbose:
        return False
    print("-" * 80)
    tag = "ADDR MISMATCH" if not mr.equal_after_norm else "CHECK"
    print(
        f"[{tag}] enigma_summary_id={mr.enigma_summary_id}  search_result_id={mr.search_result_id}  "
        f"place_id={mr.place_id}  enigma_id={mr.enigma_id}"
    )
    print(f"  GOOGLE  address: {mr.g_address_full}")
    print(f"  ENIGMA  matched: {mr.enigma_matched_full_address if mr.enigma_matched_full_address else '(missing)'}")
    if mr.enigma_street or mr.g_street:
        print(f"  STREET  google='{mr.g_street}'  enigma='{mr.enigma_street}'  street_equal={mr.street_equal}")
    # Show triage fields for mismatches
    if mr.reason != "match":
        print(
            f"  CITY/ST/ZIP  google=({mr.g_city}, {mr.g_state}, {mr.g_zip})  "
            f"enigma=({mr.e_city}, {mr.e_state}, {mr.e_zip})  "
            f"equal(city={mr.city_equal}, state={mr.state_equal}, zip={mr.zip_equal})"
        )
        print(f"  reason: {mr.reason}  category: {mr.category}")
    print(
        f"  METRICS revenue=${(mr.enigma_annual_revenue or 0):,.0f}  "
        f"yoy={(mr.enigma_yoy_growth or 0):.2%}  ticket=${(mr.enigma_ticket_size or 0):,.2f}"
    )
    return mr.reason == "mismatch"


def write_csv(rows: List[MatchRow], path: str) -> None:
    fieldnames = [
        "enigma_summary_id",
        "project_id",
        "search_result_id",
        "place_id",
        "enigma_id",
        "g_address_full",
        "enigma_matched_full_address",
        "g_street",
        "enigma_street",
        "g_city",
        "g_state",
        "g_zip",
        "e_city",
        "e_state",
        "e_zip",
        "city_equal",
        "state_equal",
        "zip_equal",
        "street_equal",
        "equal_after_norm",
        "reason",
        "category",
        "enigma_annual_revenue",
        "enigma_yoy_growth",
        "enigma_ticket_size",
        "g_address_full_norm",
        "enigma_matched_full_address_norm",
        "g_street_norm",
        "enigma_street_norm",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for mr in rows:
            w.writerow(
                {
                    "enigma_summary_id": mr.enigma_summary_id,
                    "project_id": mr.project_id,
                    "search_result_id": mr.search_result_id,
                    "place_id": mr.place_id,
                    "enigma_id": mr.enigma_id,
                    "g_address_full": mr.g_address_full,
                    "enigma_matched_full_address": mr.enigma_matched_full_address,
                    "g_street": mr.g_street,
                    "enigma_street": mr.enigma_street,
                    "g_city": mr.g_city,
                    "g_state": mr.g_state,
                    "g_zip": mr.g_zip,
                    "e_city": mr.e_city,
                    "e_state": mr.e_state,
                    "e_zip": mr.e_zip,
                    "city_equal": mr.city_equal,
                    "state_equal": mr.state_equal,
                    "zip_equal": mr.zip_equal,
                    "street_equal": mr.street_equal,
                    "equal_after_norm": mr.equal_after_norm,
                    "reason": mr.reason,
                    "category": mr.category,
                    "enigma_annual_revenue": mr.enigma_annual_revenue,
                    "enigma_yoy_growth": mr.enigma_yoy_growth,
                    "enigma_ticket_size": mr.enigma_ticket_size,
                    "g_address_full_norm": normalize_text(mr.g_address_full),
                    "enigma_matched_full_address_norm": normalize_text(mr.enigma_matched_full_address),
                    "g_street_norm": normalize_street_only(mr.g_street),
                    "enigma_street_norm": normalize_street_only(mr.enigma_street),
                }
            )
    print(f"\n📄 CSV written: {path}")


# ----------------------------
# Main
# ----------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True, help="search_projects.id UUID")
    p.add_argument("--limit", type=int, default=100, help="Max rows to evaluate")
    p.add_argument("--csv", type=str, default=None, help="Path to write mismatches CSV")
    p.add_argument("--min-addr-sim", type=float, default=1.0, help="1.0=strict equality after normalization; <1.0 enables fuzzy")
    p.add_argument("--verbose", action="store_true", help="Print all rows, not just mismatches")
    p.add_argument("--page-size", type=int, default=500, help="How many enigma_summaries to fetch per page")
    p.add_argument("--debug", action="store_true", help="Print extra diagnostics and samples")
    args = p.parse_args()

    # Early UUID validation and canonicalization
    raw_project = (args.project or "").strip()
    try:
        canonical = str(uuid.UUID(raw_project))
        if canonical != raw_project:
            print(f"[INFO] Canonicalized project UUID to {canonical}")
        args.project = canonical
    except Exception:
        print(
            f"ERROR: --project '{raw_project}' is not a valid UUID.\n"
            f"       Double-check the value (length should be 36, last block 12 hex chars).\n"
            f"       Example valid ID: 27e72d0e-a4bc-4a7f-9b37-feea156e1d2a"
        )
        sys.exit(2)

    print(
        f"[START] project={args.project} limit={args.limit} page_size={args.page_size} sim={args.min_addr_sim}"
    )

    sb = get_supabase()

    # Quick project sanity check (count + sample)
    if args.debug:
        try:
            resp_count = (
                sb.table("enigma_summaries")
                .select("id", count="exact")
                .eq("project_id", args.project)
                .limit(1)
                .execute()
            )
            total = getattr(resp_count, "count", None)
            print(f"[DEBUG] enigma_summaries count for project: {total}")
        except Exception as e:
            print(f"[DEBUG] count(enigma_summaries) failed: {e}")
        try:
            sample = (
                sb.table("enigma_summaries")
                .select("id, project_id, search_result_id")
                .eq("project_id", args.project)
                .limit(5)
                .execute()
            )
            print(f"[DEBUG] sample enigma_summaries rows: {len(sample.data or [])}")
            for r in sample.data or []:
                print(f"[DEBUG]  es.id={r.get('id')}  srid={r.get('search_result_id')}")
        except Exception as e:
            print(f"[DEBUG] sample(enigma_summaries) failed: {e}")

    evaluated = 0
    mismatches: List[MatchRow] = []

    # Debug counters
    total_enigma_rows = 0
    total_srids = 0
    total_srows = 0
    total_place_ids = 0
    total_gpids = 0
    total_ebiz_rows_place = 0
    total_ebiz_rows_gpid = 0
    missing_srow = 0
    missing_place = 0
    resolved_via_gpid = 0
    missing_enigma_business = 0

    # Category counters
    cat_counts: Counter = Counter()

    # Paging
    start = 0
    fallback_mode = False
    page_no = 0

    while evaluated < args.limit:
        end = start + args.page_size - 1
        try:
            page = try_fetch_enigma_page(sb, args.project, start, end)
        except APIError as e:
            if not fallback_mode:
                print(
                    f"[WARN] Server-side project filter failed: {e}. Falling back to unfiltered paging + client-side filter."
                )
                fallback_mode = True
            page = fallback_fetch_enigma_page(sb, start, end)

        if not page:
            if args.debug:
                print(f"[DEBUG] No more rows at range {start}-{end}.")
            break

        page_no += 1
        if args.debug:
            print(
                f"[PAGE {page_no}] fetched {len(page)} enigma_summaries rows (range {start}-{end})  fallback={fallback_mode}"
            )

        total_enigma_rows += len(page)

        # If fallback mode, reduce to just this project client-side
        if fallback_mode:
            before = len(page)
            page = [r for r in page if str(r.get("project_id")) == args.project]
            if args.debug:
                print(f"[PAGE {page_no}] client-side filtered {before}→{len(page)} for project match")
            if not page:
                start += args.page_size
                continue

        # Collect SRIDs and bulk fetch
        srids = list({str(r.get("search_result_id")) for r in page if r.get("search_result_id")})
        total_srids += len(srids)
        srows_map = fetch_search_results_bulk(sb, srids)
        total_srows += len(srows_map)

        # Collect place_ids and (optional) google_places_ids; bulk fetch enigma_businesses
        place_ids = list({str(r.get("place_id")) for r in srows_map.values() if r.get("place_id")})
        gpids = list({str(r.get("google_places_id")) for r in srows_map.values() if r.get("google_places_id")})
        total_place_ids += len(place_ids)
        total_gpids += len(gpids)
        ebiz_place_map, ebiz_gpid_map = fetch_enigma_businesses_bulk(sb, place_ids, gpids)
        total_ebiz_rows_place += len(ebiz_place_map)
        total_ebiz_rows_gpid += len(ebiz_gpid_map)

        if args.debug:
            print(
                f"[PAGE {page_no}] srids={len(srids)}  srows_map={len(srows_map)}  place_ids={len(place_ids)}  gpids={len(gpids)}  ebiz_place={len(ebiz_place_map)}  ebiz_gpid={len(ebiz_gpid_map)}"
            )

        # Evaluate
        for erow in page:
            if evaluated >= args.limit:
                break
            srid = str(erow.get("search_result_id")) if erow.get("search_result_id") else None
            srow = srows_map.get(srid, {})
            if not srow:
                missing_srow += 1
            place_id = srow.get("place_id") if srow else None
            if not place_id:
                missing_place += 1

            # Prefer place_id join, then fallback to google_places_id join
            ebiz = ebiz_place_map.get(place_id) if place_id else None
            if not ebiz and srow and srow.get("google_places_id"):
                ebiz = ebiz_gpid_map.get(str(srow.get("google_places_id")))
                if ebiz:
                    resolved_via_gpid += 1
            if not ebiz:
                missing_enigma_business += 1
                ebiz = {}

            mr = build_match_row(erow, srow, ebiz, min_addr_sim=args.min_addr_sim)
            is_mismatch = print_row(mr, verbose=args.verbose)
            if is_mismatch:
                mismatches.append(mr)
                if mr.category:
                    cat_counts[mr.category] += 1

            evaluated += 1

        start += args.page_size

    # Summary
    print("\nSummary:")
    print(f"  Total evaluated (this project): {evaluated} (target={args.limit})")
    print(f"  Address mismatches: {len(mismatches)}")
    if mismatches:
        print("  By category:")
        for cat in ("cross_street", "cross_zip", "cross_city_state"):
            print(f"    - {cat}: {cat_counts.get(cat, 0)}")

    print("\nDiagnostics:")
    print(f"  enigma_summaries fetched: {total_enigma_rows}")
    print(f"  search_result_ids collected: {total_srids}")
    print(f"  search_results returned: {total_srows}")
    print(f"  place_ids collected: {total_place_ids}")
    print(f"  gpids collected: {total_gpids}")
    print(f"  enigma_businesses by place_id: {total_ebiz_rows_place}")
    print(f"  enigma_businesses by google_places_id: {total_ebiz_rows_gpid}")
    print(f"  resolved via google_places_id: {resolved_via_gpid}")
    print(f"  missing enigma_business: {missing_enigma_business}")

    if args.csv:
        write_csv(mismatches, args.csv)


if __name__ == "__main__":
    main()
