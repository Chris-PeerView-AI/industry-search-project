# modules/review_results.py

import streamlit as st

def review_and_edit(project_config):
    st.info(f"Reviewing results for: {project_config['name']}")
    st.warning("No results to review yet — placeholder screen.")
