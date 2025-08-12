import os
import sys
import uuid
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from supabase import create_client, Client

# --- Path & module imports (ensure modules path is set before importing) ---
sys.path.append(os.path.join(os.path.dirname(__file__), "modules"))
from modules.business_metrics import generate_enigma_summaries, summarize_benchmark_stats
from modules.pull_enigma_data_for_business import pull_enigma_data_for_business
import inspect, modules.pull_enigma_data_for_business as puller_mod
st.caption(f"Using puller: {inspect.getfile(puller_mod)}")
st.caption(f"ON_CONFLICT_METRICS = {getattr(puller_mod, 'ON_CONFLICT_METRICS', 'MISSING')}")
st.caption(f"Has [metrics] log? {'[metrics]' in inspect.getsource(puller_mod.pull_enigma_data_for_business)}")


# --- Load Env & Supabase ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- UI ---
st.title("📡 Enigma Data Pull Tool")

# Load available search projects from Supabase
def fetch_projects():
    resp = supabase.table("search_projects").select("id, name").order("name").execute()
    return resp.data or []

projects = fetch_projects()
if not projects:
    st.warning("No projects found.")
    st.stop()

project_options = {p["name"]: p["id"] for p in projects}
selected_project_name = st.selectbox("Select a Project", list(project_options.keys()))
project_id = project_options[selected_project_name]

if project_id:
    project_check = supabase.table("search_projects").select("id").eq("id", project_id).execute()
    if not project_check.data:
        st.error("Selected project not found.")
        st.stop()

    with st.spinner("Loading businesses for project…"):
        resp = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
        all_businesses = resp.data or []
        st.caption(f"Loaded {len(all_businesses)} businesses for project: {selected_project_name} ({project_id})")
        if len(all_businesses) == 0:
            st.info(
                "No businesses found for this project. If this seems wrong, verify the 'search_results' table has rows for the selected project_id.")
    st.caption(f"Loaded {len(all_businesses)} businesses for project {selected_project_name} ({project_id})")

    available_tiers = sorted({b.get("tier", 3) for b in all_businesses if b.get("tier") in [1, 2, 3]})
    if not available_tiers:
        selected_tiers = []
        filtered_businesses = list(all_businesses)
    else:
        default_tiers = [t for t in [1] if t in available_tiers] or available_tiers[:1]
        selected_tiers = st.multiselect("Select Tiers to Pull From", available_tiers, default=default_tiers)
        filtered_businesses = [b for b in all_businesses if b.get("tier") in selected_tiers]
    st.caption(f"After tier filter: {len(filtered_businesses)} businesses")

    with st.spinner("Checking existing Enigma data…"):
        place_ids = [b.get("place_id") for b in filtered_businesses if b.get("place_id")]
        place_ids = list({pid for pid in place_ids if pid})

        existing_global = {}
        if place_ids:
            chunks = [place_ids[i : i + 500] for i in range(0, len(place_ids), 500)]
            for chunk in chunks:
                rows = (
                    supabase.table("enigma_businesses")
                    .select("place_id, enigma_id, enigma_name, match_confidence, pull_timestamp")
                    .in_("place_id", chunk)
                    .execute()
                    .data
                    or []
                )
                for r in rows:
                    existing_global[r["place_id"]] = r

    not_pulled = [b for b in filtered_businesses if not existing_global.get(b.get("place_id"))]
    already_pulled = [b for b in filtered_businesses if existing_global.get(b.get("place_id"))]

    selected_new = []
    if to_pull := not_pulled:
        st.subheader("🆕 To Pull (not in cache)")
        if st.button("Deselect all NEW", key="deselect_all_new"):
            for i in range(len(to_pull)):
                st.session_state[f"pull_new_{i}"] = False
            st.rerun()
        for i, b in enumerate(to_pull):
            with st.expander(f"{b['name']} ({b.get('city')}, {b.get('state')})", expanded=False):
                col1, col2 = st.columns([6, 1])
                with col1:
                    st.write(f"Place ID: {b.get('place_id')}")
                    st.write(f"Tier: {b.get('tier')}")
                with col2:
                    if st.checkbox("Pull?", key=f"pull_new_{i}", value=True):
                        selected_new.append(b)

    st.write(f"✅ {len(selected_new)} businesses selected for initial pull")

    force_repull_new = False
    if selected_new:
        force_repull_new = st.checkbox(
            "Force re‑pull for NEW pulls (ignore cache if found mid-run)", value=False
        )

    selected_repull = []
    if already_pulled:
        st.subheader("♻️ Already Pulled (repull?)")
        if st.button("Deselect all REPULL", key="deselect_all_repull"):
            for j in range(len(already_pulled)):
                st.session_state[f"repull_{j}"] = False
            st.rerun()
        for j, b in enumerate(already_pulled):
            cache = existing_global.get(b.get("place_id")) or {}
            conf = cache.get("match_confidence")
            conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else (str(conf) if conf is not None else "—")
            with st.expander(
                f"{b['name']} ({b.get('city')}, {b.get('state')}) — cache conf={conf_str}",
                expanded=False,
            ):
                col1, col2 = st.columns([6, 1])
                with col1:
                    st.write(f"Place ID: {b.get('place_id')}")
                    st.write(f"Tier: {b.get('tier')}")
                    if cache.get("enigma_name"):
                        st.write(f"Enigma Name: {cache.get('enigma_name')}")
                with col2:
                    if st.checkbox("Re‑pull?", key=f"repull_{j}", value=False):
                        selected_repull.append(b)

    st.write(f"🔁 {len(selected_repull)} businesses selected for re‑pull")

    if st.button("Submit Selected", key="submit_selected"):
        pull_session_id = str(uuid.uuid4())
        from datetime import datetime, timezone
        pull_timestamp = datetime.now(timezone.utc)

        with st.spinner("Fetching from Enigma and storing in Supabase..."):
            for b in selected_new:
                try:
                    b["project_id"] = project_id
                    b["pull_session_id"] = pull_session_id
                    b["pull_timestamp"] = pull_timestamp.isoformat()
                    b["google_places_id"] = b.get("google_places_id") or b.get("place_id")
                    pull_enigma_data_for_business(b, force_repull=force_repull_new)
                except Exception as e:
                    st.error(f"❌ Failed NEW pull for {b['name']}: {e}")

            for b in selected_repull:
                try:
                    b["project_id"] = project_id
                    b["pull_session_id"] = pull_session_id
                    b["pull_timestamp"] = pull_timestamp.isoformat()
                    b["google_places_id"] = b.get("google_places_id") or b.get("place_id")
                    pull_enigma_data_for_business(b, force_repull=True)
                except Exception as e:
                    st.error(f"❌ Failed REPULL for {b['name']}: {e}")

        st.success("✅ Data pull & repull complete.")

        with st.spinner("Calculating summaries for project..."):
            try:
                generate_enigma_summaries(project_id)
                st.success("📊 Enigma summaries updated.")
                summarize_benchmark_stats(project_id)
                st.success("📈 Benchmark summary updated.")
            except Exception as e:
                st.error(f"⚠️ Failed to generate summaries: {e}")

    if skipped := [b for b in filtered_businesses if not b.get("place_id")]:
        with st.expander("⚠️ Skipped Businesses (Missing place_id)"):
            st.write([b["name"] for b in skipped])

    if existing_global:
        with st.expander("📊 Businesses with Enigma Data Pulled (cached)"):
            st.write(
                [
                    (row.get("business_name") or row.get("enigma_name") or "—")
                    for row in existing_global.values()
                    if row.get("place_id")
                ]
            )
