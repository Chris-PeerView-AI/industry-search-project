# ================================
# FILE: modules/review_results.py
# PURPOSE (Phase-1, Step 2):
# - Sort manual review by tier (1..3) then eligibility_score DESC
# - Show a Details expander with numeric score + reasons
# ================================

from __future__ import annotations

import json
import streamlit as st
from supabase import create_client
import os
from dotenv import load_dotenv

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
    res = supabase.table("search_results") \
        .select("*") \
        .eq("project_id", project["id"]) \
        .order("tier", ascending=True) \
        .order("eligibility_score", ascending=False) \
        .execute()

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

        with st.expander(f"[T{tier}] {name}"):
            c1, c2 = st.columns([3, 2])

            with c1:
                st.write(row.get("address", ""))
                st.write(f"{row.get('city','')}, {row.get('state','')} {row.get('zip','')}")
                if url:
                    st.link_button("Open in Google Maps", url)
                if website:
                    st.link_button("Open Website", website)

            with c2:
                st.write(f"**Tier**: {tier}")
                st.write(f"**Reason**: {tier_reason or '—'}")

                with st.expander("Details (score & reasons)", expanded=False):
                    st.write(f"**Eligibility score**: {score if score is not None else '—'}")
                    st.code(_fmt_reasons(reasons))

            # Manual overrides
            cols = st.columns(4)
            with cols[0]:
                new_tier = st.selectbox("Set Tier", [1, 2, 3], index=[1,2,3].index(tier), key=f"tier_{row['id']}")
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
