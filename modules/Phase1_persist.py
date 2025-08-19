# ================================
# FILE: modules/Phase1_persist.py
# PURPOSE: Supabase helpers + safe upsert (avoid duplicate key errors)
# ================================

from __future__ import annotations
import os
from typing import Any, Dict, Optional
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Optional[Client] = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None

def upsert_result(row: Dict[str, Any]) -> None:
    """Upsert on (project_id, place_id)."""
    if not supabase:
        return
    try:
        # supabase-py supports on_conflict
        supabase.table("search_results").upsert(row, on_conflict="project_id,place_id").execute()
    except Exception:
        # Fallback: try update then insert
        try:
            supabase.table("search_results") \
                .update(row) \
                .match({"project_id": row["project_id"], "place_id": row["place_id"]}) \
                .execute()
        except Exception:
            try:
                supabase.table("search_results").insert(row).execute()
            except Exception:
                # swallow; UI already shows progress, and duplicates are benign
                pass

def persist_project_fields(project_id: str, updates: Dict[str, Any]) -> None:
    if not supabase or not project_id:
        return
    try:
        supabase.table("search_projects").update(updates).eq("id", project_id).execute()
    except Exception:
        pass
