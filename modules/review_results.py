# modules/review_results.py

import streamlit as st
from supabase import create_client
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def review_and_edit(project_config):
    project_id = project_config["id"]

    response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    rows = response.data

    if not rows:
        st.warning("No results found for this project.")
        return

    st.caption(f"Reviewing {len(rows)} businesses for: {project_config['name']}")

    updated = []

    for row in rows:
        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**{row['name']}**")
            st.markdown(row.get("address", ""))
            st.markdown(f"_Reason:_ {row.get('tier_reason', '')}")
            if row.get("website"):
                st.markdown(f"[Website]({row['website']})")

        with col2:
            current_tier = row.get("tier", 3)
            new_tier = st.selectbox(
                f"Tier for {row['id'][:8]}",
                options=[1, 2, 3],
                index=current_tier - 1 if current_tier in [1, 2, 3] else 2,
                key=row["id"]
            )
            if new_tier != current_tier:
                updated.append({"id": row["id"], "tier": new_tier, "manual_override": True})

    if updated:
        if st.button("Save Changes"):
            for u in updated:
                supabase.table("search_results").update({
                    "tier": u["tier"], "manual_override": True
                }).eq("id", u["id"]).execute()
            st.success("Changes saved.")
            st.rerun()
