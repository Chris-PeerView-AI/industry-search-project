# ================================
# FILE: modules/review_results.py
# PURPOSE:
# - Sort manual review by tier (1..3) then eligibility_score DESC
# - Show a Details expander with numeric score + reasons + web signals
# ================================

from __future__ import annotations

import json
import os
from dotenv import load_dotenv
import streamlit as st
from supabase import create_client

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def _fmt_reasons(r):
    if not r:
        return ""
    if isinstance(r, str):
        return r[:1200]
    try:
        return json.dumps(r)[:1200]
    except Exception:
        return str(r)[:1200]

def review_and_edit(project: dict):
    st.subheader("Manual Review")

    # Pull sorted results for this project
    # NOTE: supabase-py uses `desc=` not `ascending=`.
    qb = (
        supabase.table("search_results")
        .select("*")
        .eq("project_id", project["id"])
    )
    try:
        qb = qb.order("tier", desc=False).order("eligibility_score", desc=True)
    except TypeError:
        # fallback for very old clients that don't accept kwargs
        qb = qb.order("tier").order("eligibility_score", desc=True)
    res = qb.execute()

    rows = res.data or []

    # Filters
    tier_filter = st.multiselect("Filter by Tier", [1, 2, 3], default=[1, 2, 3])
    show_flagged = st.checkbox("Show only flagged", value=False)

    filtered = [
        r for r in rows
        if (r.get("tier") in tier_filter) and (not show_flagged or r.get("flagged"))
    ]

    st.write(f"{len(filtered)} businesses shown")

    for row in filtered:
        tier = row.get("tier", 3)
        name = row.get("name", "Unknown")
        url = row.get("google_maps_url")
        website = row.get("website")
        score = row.get("eligibility_score")
        reasons = row.get("score_reasons")
        tier_reason = row.get("tier_reason", "")
        web_signals = row.get("web_signals") or {}
        schema_types = web_signals.get("schema_types") or []

        with st.expander(f"[T{tier}] {name}"):
            c1, c2 = st.columns([3, 2])

            rating = row.get("rating") or row.get("google_rating") or row.get(
                "eligibility_rating")  # if you later persist rating separately
            reviews = row.get("user_ratings_total") or row.get("reviews")

            with c1:
                st.write(row.get("address", ""))
                st.write(f"{row.get('city', '')}, {row.get('state', '')} {row.get('zip', '')}")
                # quick badges
                badges = []
                if rating and isinstance(rating, (int, float)):
                    badges.append(f"⭐ {rating} ({reviews or 0})")
                if row.get("website"):
                    badges.append("🌐 website")
                schema_types = (row.get("web_signals") or {}).get("schema_types") or []
                if schema_types:
                    badges.append("schema: " + ", ".join(schema_types[:2]))
                if badges:
                    st.caption(" | ".join(badges))

                if row.get("google_maps_url"):
                    st.link_button("Open in Google Maps", row["google_maps_url"])
                if row.get("website"):
                    st.link_button("Open Website", row["website"])

            with c2:
                src = row.get("tier_source", "score")
                conf = row.get("audit_confidence", None)
                src_str = "LLM override" + (
                    f" ({conf:.2f})" if isinstance(conf, (int, float)) else "") if src == "llm_override" else "Score"
                st.write(f"**Tier**: {tier}  ·  **Source**: {src_str}")
                st.write(f"**Reason**: {row.get('tier_reason') or '—'}")

                with st.expander("Details (score, reasons, web signals)", expanded=False):
                    st.write(
                        f"**Eligibility score**: {row.get('eligibility_score') if row.get('eligibility_score') is not None else '—'}")
                    if schema_types:
                        st.write(f"**Schema.org types**: {', '.join(schema_types)}")
                    st.code(_fmt_reasons(row.get('score_reasons')))

            # Manual overrides
            cols = st.columns(4)
            with cols[0]:
                options = [1, 2, 3]
                idx = options.index(tier) if tier in options else 2  # safe default -> Tier 3
                new_tier = st.selectbox("Set Tier", options, index=idx, key=f"tier_{row['id']}")
            with cols[1]:
                flagged = st.checkbox("Flag", value=bool(row.get("flagged", False)), key=f"flag_{row['id']}")
            with cols[2]:
                notes = st.text_input("Notes", value=row.get("notes",""), key=f"notes_{row['id']}")
            with cols[3]:
                if st.button("Save", key=f"save_{row['id']}"):
                    supabase.table("search_results").update({
                        "tier": new_tier,
                        "manual_override": True if new_tier != tier else row.get("manual_override", False),
                        "flagged": flagged,
                        "notes": notes,
                    }).eq("id", row["id"]).execute()
                    st.success("Saved.")
