import streamlit as st
from supabase import create_client
import os
from dotenv import load_dotenv
from collections import Counter

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def review_and_edit(project_config):
    project_id = project_config["id"]

    st.subheader("📊 Project Summary")

    # Tier filter UI
    tiers = st.multiselect("Filter by Tier", [1, 2, 3], default=[1, 2, 3])

    # Query with tier filter
    response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    rows = [r for r in response.data if r.get("tier") in tiers]

    if not rows:
        st.warning("No results found for this project.")
        return

    # Audit Summary
    tier_counts = Counter([r.get("tier", 3) for r in response.data])
    overrides = sum(1 for r in response.data if r.get("manual_override"))
    flagged = sum(1 for r in response.data if r.get("flagged"))

    st.markdown(f"""
    - **Tier Counts**: {dict(tier_counts)}
    - **Manual Overrides**: {overrides}
    - **Flagged for Follow-Up**: {flagged}
    """)

    st.caption(f"Reviewing {len(rows)} filtered businesses")

    updated = []

    for row in rows:
        col1, col2 = st.columns([2, 1])
        with col1:
            with st.expander(f"▶ **{row['name']}**", expanded=False):
                st.markdown(f"Tier {row.get('tier', 3)}: {row.get('tier_reason', '')}")
                note = st.text_area(f"Notes for {row['id'][:8]}", value=row.get("notes", ""))
                flagged = st.checkbox("🚩 Flag for follow-up", value=row.get("flagged", False), key=f"flag-{row['id']}")

                if row.get("page_title"):
                    st.markdown(f"**Page Title**: {row['page_title']}")
                if row.get("website"):
                    st.markdown(f"[Visit Website]({row['website']})", unsafe_allow_html=True)
                if row.get("google_maps_url"):
                    st.markdown(f"[View on Google Maps]({row['google_maps_url']})", unsafe_allow_html=True)
                if row.get("category"):
                    st.markdown(f"_LLM Category_: `{row['category']}`")

        with col2:
            current_tier = row.get("tier", 3)
            new_tier = st.selectbox(
                f"Tier for {row['id'][:8]}",
                options=[1, 2, 3],
                index=current_tier - 1 if current_tier in [1, 2, 3] else 2,
                key=row["id"]
            )

            if (
                new_tier != current_tier
                or note != row.get("notes")
                or flagged != row.get("flagged")
            ):
                updated.append({
                    "id": row["id"],
                    "tier": new_tier,
                    "notes": note,
                    "flagged": flagged,
                    "manual_override": True
                })

    if updated:
        if st.button("Save Changes"):
            for u in updated:
                supabase.table("search_results").update({
                    "tier": u["tier"],
                    "manual_override": True,
                    "notes": u["notes"],
                    "flagged": u["flagged"]
                }).eq("id", u["id"]).execute()
            st.success("Changes saved.")
            st.rerun()