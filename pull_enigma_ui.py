import streamlit as st
import uuid
from datetime import datetime
from supabase import create_client, Client
from modules.business_metrics import generate_enigma_summaries, summarize_benchmark_stats



# Fix path to import from modules directory
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "modules"))

from modules.pull_enigma_data_for_business import pull_enigma_data_for_business
from dotenv import load_dotenv

# --- Load Env ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- UI ---
st.title("📡 Enigma Data Pull Tool")

# Load available search projects from Supabase
def fetch_projects():
    response = supabase.table("search_projects").select("id, name").order("name").execute()
    return response.data

projects = fetch_projects()
project_options = {p["name"]: p["id"] for p in projects}

selected_project_name = st.selectbox("Select a Project", list(project_options.keys()))
project_id = project_options[selected_project_name]

if project_id:
    project_check = supabase.table("search_projects").select("id").eq("id", project_id).execute()
    if not project_check.data:
        st.error("Project not found. Please select a valid project.")
    else:
        with st.spinner("Loading businesses..."):
            response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
            all_businesses = response.data

        available_tiers = sorted(set([b.get("tier", 3) for b in all_businesses if b.get("tier") in [1, 2, 3]]))
        default_tiers = [t for t in [1] if t in available_tiers] or available_tiers[:1]
        selected_tiers = st.multiselect("Select Tiers to Pull From", available_tiers, default=default_tiers)
        filtered_businesses = [b for b in all_businesses if b.get("tier") in selected_tiers]

        with st.spinner("Checking existing Enigma data..."):
            existing = supabase.table("enigma_businesses").select("place_id, business_name").eq("project_id", project_id).execute().data
            existing_place_ids = set(e['place_id'] for e in existing if e.get("place_id"))

        businesses_to_pull = []
        skipped = []
        for b in filtered_businesses:
            pid = b.get("place_id")
            if not pid:
                skipped.append(b)
            elif pid not in existing_place_ids:
                businesses_to_pull.append(b)

        st.write(f"Total businesses in selected tiers: {len(filtered_businesses)}")
        st.write(f"Businesses with missing place_id (skipped): {len(skipped)}")
        st.write(f"Remaining to pull from Enigma: {len(businesses_to_pull)}")

        if businesses_to_pull:
            st.subheader("📋 Select Businesses to Pull")
            selected_rows = []

            for i, b in enumerate(businesses_to_pull):
                with st.expander(f"{b['name']} ({b.get('city')}, {b.get('state')})", expanded=False):
                    col1, col2 = st.columns([6, 1])
                    with col1:
                        st.write(f"Place ID: {b.get('place_id')}")
                        st.write(f"Tier: {b.get('tier')}")
                    with col2:
                        should_pull = st.checkbox("Pull?", key=f"pull_{i}", value=True)

                    if should_pull:
                        selected_rows.append(b)

            st.write(f"✅ {len(selected_rows)} businesses are pre-selected to pull")

            if st.button("Submit Selected", key="submit_selected"):
                pull_session_id = str(uuid.uuid4())
                pull_timestamp = datetime.utcnow()

                with st.spinner("Fetching from Enigma and storing in Supabase..."):
                    for b in selected_rows:
                        try:
                            b["project_id"] = project_id
                            b["pull_session_id"] = pull_session_id
                            b["pull_timestamp"] = pull_timestamp.isoformat()
                            b["google_places_id"] = b.get("google_places_id") or b.get("place_id")

                            pull_enigma_data_for_business(b)
                        except Exception as e:
                            st.error(f"❌ Failed to pull data for {b['name']}: {e}")
                    st.success("✅ Data pull complete.")

                # ✅ NEW: Trigger summary generation immediately after pull
                with st.spinner("Calculating summaries for project..."):
                    try:
                        generate_enigma_summaries(project_id)
                        st.success("📊 Enigma summaries updated.")

                        summarize_benchmark_stats(project_id)
                        st.success("📈 Benchmark summary updated.")
                    except Exception as e:
                        st.error(f"⚠️ Failed to generate summaries: {e}")

        if skipped:
            with st.expander("⚠️ Skipped Businesses (Missing place_id)"):
                st.write([b["name"] for b in skipped])

        if existing:
            with st.expander("📊 Businesses with Enigma Data Pulled"):
                st.write([e["business_name"] for e in existing if e.get("business_name")])
