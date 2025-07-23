# main_ui.py
import streamlit as st
import os
from dotenv import load_dotenv
from modules.project_config import get_or_create_project, select_existing_project
from modules.google_search import search_and_expand
from modules.review_results import review_and_edit
from modules.map_view_review import map_review

# Load environment variables
load_dotenv()

st.set_page_config(page_title="Industry Market Search Tool")
st.title("Industry/Market Google API & Enigma Pull Project")

# Step control for UI
if "step" not in st.session_state:
    st.session_state.step = 0

# Step 1: Project setup
if st.session_state.step == 0:
    st.header("1. Define or Load Project")

    tab1, tab2 = st.tabs(["➕ New Project", "📂 Existing Project"])

    with tab1:
        project_config = get_or_create_project(
            default_name="Test: Golf Simulators in Northvale",
            default_industry="Golf Simulators",
            default_location="Northvale, New Jersey",
            default_target_count=20,
            default_max_radius_km=25
        )
        if project_config:
            st.session_state.project_config = project_config
            st.session_state.step = 1
            st.rerun()

    with tab2:
        selected = select_existing_project()
        if selected:
            st.session_state.project_config = selected
            st.session_state.step = 2  # skip search since it's already run
            st.rerun()

# Step 2: Google API + LLM tiering
elif st.session_state.step == 1:
    st.header("2. Run Google Search and Categorize")
    finished = search_and_expand(st.session_state.project_config)
    if finished:
        st.session_state.step = 2
        st.rerun()

# Step 3: Review results
elif st.session_state.step == 2:
    st.header("3. Review Results")

    st.subheader("📋 Project Configuration")

    project = st.session_state.project_config

    st.markdown(f"""
    - **Name**: {project.get('name')}
    - **Industry**: {project.get('industry')}
    - **Location**: {project.get('location')}
    - **Target Count**: {project.get('target_count')}
    - **Max Radius**: {project.get('max_radius_km')} km
    - **GPT-4 Audit Enabled**: {"Yes" if project.get('use_gpt_audit') else "No"}
    """)

    view = st.radio(
        "Choose View:",
        ["Map View", "Manual Review"],
        horizontal=True
    )

    if view == "Map View":
        map_review(project)
    else:
        review_and_edit(project)

