# modules/google_search.py

import streamlit as st

def search_and_expand(project_config):
    st.info(f"Simulated search for industry: {project_config['industry']} in {project_config['location']}")
    st.success("Search complete (placeholder).")
    return True
