# ==============================================
# modules/Phase1_apply.py — wire Phase‑1 results into Supabase
# Small & testable: build taxonomy once per project, re-tier existing search_results, write back.
# Industry‑agnostic; uses current high‑recall tiering.
# ==============================================
from __future__ import annotations

import os
import json
from typing import Any, Dict, List

from dotenv import load_dotenv
from supabase import create_client, Client

try:
    # when imported as a package: import modules.Phase1_apply
    from .Phase1_rubric import build_taxonomy
    from .Phase1_tiering import extract_evidence, choose_tier
except ImportError:
    # when run directly as a script: python modules/Phase1_apply.py ...
    from Phase1_rubric import build_taxonomy
    from Phase1_tiering import extract_evidence, choose_tier


# -----------------------
# Config
# -----------------------
SEARCH_RESULTS_TABLE = "search_results"
PROJECTS_TABLE = "search_projects"

# Columns to update (customize here if your schema differs)
COL_TIER  = "tier"
COL_REASON = "tier_reason"
COL_CONF  = "audit_confidence"
COL_HITS  = None          # was "tier_hits"
COL_FLAGS = None          # was "tier_flags"

# Candidate columns we try to read from search_results
CANDIDATE_COLS = [
    "id", "name", "website", "title", "snippet",
    "categories", "schema_types", "google_types", "schema"
]


def _get_client() -> Client:
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY/ANON in env")
    return create_client(url, key)


def _fetch_project_meta(supabase: Client, project_id: str) -> Dict[str, Any]:
    # Try to get both name and industry if present
    cols = "id, name, industry"
    data = supabase.table(PROJECTS_TABLE).select(cols).eq("id", project_id).limit(1).execute().data
    if not data:
        raise RuntimeError(f"Project not found: {project_id}")
    return data[0]


def _fetch_candidates(supabase: Client, project_id: str, limit: int | None = None) -> List[Dict[str, Any]]:
    # Select all columns to avoid schema mismatches (robust to missing fields like 'url')
    q = supabase.table(SEARCH_RESULTS_TABLE).select("*").eq("project_id", project_id)
    if limit:
        q = q.limit(limit)
    return q.execute().data or []


def _mk_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": row.get("name") or "",
        "website": row.get("website") or row.get("url") or "",
        "title": row.get("title") or "",
        "snippet": row.get("snippet") or "",
        "categories": row.get("categories") or [],
        "google_types": row.get("google_types") or [],
        "schema_types": row.get("schema_types") or [],
        "schema": row.get("schema") or {},
    }


def apply_phase1_to_project(
    project_id: str,
    provider: str = "ollama",
    model: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run Phase‑1 rubric+tiering over existing search_results and write back.

    Parameters
    ----------
    project_id : str
        Supabase search_projects.id to process
    provider : str
        'ollama' | 'openai' | 'none' (use rules fallback)
    model : str | None
        Model name for provider
    limit : int | None
        Optional cap for # of rows to process (quick test)
    dry_run : bool
        If True, does not write to Supabase; returns would‑be updates
    """
    supabase = _get_client()

    # 1) Project meta → industry label
    proj = _fetch_project_meta(supabase, project_id)
    industry = (proj.get("industry") or proj.get("name") or "").strip()
    if not industry:
        raise RuntimeError("Project missing 'industry' or 'name' to seed taxonomy.")

    # 2) Build taxonomy once
    taxonomy = build_taxonomy(industry, focus=None, provider=provider, model=model)
    # Tag for traceability
    taxonomy["_industry"] = industry

    # 3) Load candidates
    rows = _fetch_candidates(supabase, project_id, limit=limit)

    updates: List[Dict[str, Any]] = []
    for row in rows:
        rid = row.get("id")
        cand = _mk_candidate(row)

        ev = extract_evidence(taxonomy, candidate=cand)
        # Pass extra context so expanded primary combos can leverage the industry
        decision = choose_tier(industry, taxonomy, ev, provider=provider, model=model)

        # Build update payload
        payload = {
            COL_TIER: decision.get("tier"),
            COL_REASON: decision.get("reason"),
            COL_CONF: float(decision.get("confidence", 0.0)),
        }
        # Optional JSON columns
        if COL_HITS:
            payload[COL_HITS] = json.dumps(decision.get("hits", {}))
        if COL_FLAGS:
            payload[COL_FLAGS] = json.dumps(decision.get("flags", {}))

        updates.append({"id": rid, **payload})

    # 4) Write back (unless dry_run)
    if not dry_run:
        for u in updates:
            supabase.table(SEARCH_RESULTS_TABLE).update(u).eq("id", u["id"]).execute()

    return {
        "project_id": project_id,
        "industry": industry,
        "count": len(updates),
        "dry_run": dry_run,
        "sample": updates[:5],
    }


if __name__ == "__main__":
    # Minimal manual runner example:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("project_id", help="search_projects.id to process")
    p.add_argument("--provider", default="ollama")
    p.add_argument("--model", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry_run", action="store_true")
    args = p.parse_args()

    out = apply_phase1_to_project(
        project_id=args.project_id,
        provider=args.provider,
        model=args.model,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(out, indent=2))
