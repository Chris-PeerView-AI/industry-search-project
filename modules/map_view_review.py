import streamlit as st
import folium
from streamlit_folium import st_folium
from supabase import create_client
import os
from dotenv import load_dotenv

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def tier_color(tier):
    return {
        1: "green",
        2: "orange",
        3: "red"
    }.get(tier, "gray")

def map_review(project_config):
    project_id = project_config["id"]
    max_radius_km = project_config["max_radius_km"]
    location = project_config["location"]

    # Fetch businesses
    response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    businesses = response.data

    if not businesses:
        st.warning("No businesses found for this project.")
        return

    # Determine map center
    center_lat = None
    center_lng = None
    valid_coords = [
        (b["latitude"], b["longitude"]) for b in businesses if b.get("latitude") and b.get("longitude")
    ]
    if valid_coords:
        center_lat, center_lng = valid_coords[0]

    m = folium.Map(location=[center_lat, center_lng], zoom_start=13)

    # Add search radius circle
    if center_lat and center_lng:
        folium.Circle(
            location=[center_lat, center_lng],
            radius=max_radius_km * 1000,
            color="blue",
            fill=True,
            fill_opacity=0.05
        ).add_to(m)

    # Add pins
    for b in businesses:
        lat = b.get("latitude")
        lng = b.get("longitude")
        if lat and lng:
            tier = b.get("tier", 3)
            color = tier_color(tier)
            popup = f"{b['name']}<br>Tier {tier}<br>{b.get('address', '')}"
            folium.Marker(
                location=[lat, lng],
                popup=popup,
                icon=folium.Icon(color=color)
            ).add_to(m)

    st_folium(m, width=800, height=600)
