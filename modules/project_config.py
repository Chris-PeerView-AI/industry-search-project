# modules/project_config.py

import streamlit as st
import os
from uuid import uuid4
from supabase import create_client
from dotenv import load_dotenv

# ✅ Load .env file
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_or_create_project(default_name, default_industry, default_location, default_target_count, default_max_radius_km):
    with st.form("project_form"):
        name = st.text_input("Project Name", default_name)
        industry = st.text_input("Industry", default_industry)
        location = st.text_input("Location", default_location)
        target_count = st.number_input("Target Number of Businesses", value=default_target_count, min_value=1)
        max_radius = st.number_input("Max Search Radius (km)", value=default_max_radius_km, min_value=1)
        submitted = st.form_submit_button("Start Project")

        if submitted:
            project_id = str(uuid4())
            data = {
                "id": project_id,
                "name": name,
                "industry": industry,
                "location": location,
                "target_count": target_count,
                "max_radius_km": max_radius,
            }
            supabase.table("search_projects").insert(data).execute()
            return data
    return None
