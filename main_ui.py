# main_ui.py
import streamlit as st
import os
from dotenv import load_dotenv
from modules.project_config import get_or_create_project
from modules.google_search import search_and_expand
from modules.review_results import review_and_edit

# Load environment variables
load_dotenv()

st.set_page_config(page_title="Industry Market Search Tool")
st.title("Industry/Market Google API & Enigma Pull Project")

# Step control for UI
if "step" not in st.session_state:
    st.session_state.step = 0

# Step 1: Project setup
if st.session_state.step == 0:
    st.header("1. Define Project")
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
        st.experimental_rerun()

# Step 2: Google API + LLM tiering
elif st.session_state.step == 1:
    st.header("2. Run Google Search and Categorize")
    finished = search_and_expand(st.session_state.project_config)
    if finished:
        st.session_state.step = 2
        st.experimental_rerun()

# Step 3: Review results
elif st.session_state.step == 2:
    st.header("3. Review & Edit Results")
    review_and_edit(st.session_state.project_config)
