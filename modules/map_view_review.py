import streamlit as st
import folium
from streamlit_folium import st_folium
from supabase import create_client
import os
from dotenv import load_dotenv
from modules.google_search import geocode_location, generate_grid
from math import cos, radians

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

    # Geocode original search location to use as true center
    try:
        center_lat, center_lng = geocode_location(location)
    except Exception:
        st.error("Failed to geocode project location.")
        return

    # Generate search grid and draw circles for each point
    search_points = generate_grid(center_lat, center_lng, max_radius_km)

    # Fetch businesses
    response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    businesses = response.data

    if not businesses:
        st.warning("No businesses found for this project.")
        return

    m = folium.Map(location=[center_lat, center_lng], zoom_start=12)

    # Add a light blue circle for each search point (5km radius)
    for lat, lng in search_points:
        folium.Circle(
            location=[lat, lng],
            radius=5000,  # 5km radius
            color="blue",
            fill=True,
            fill_opacity=0.03,
            weight=0.5
        ).add_to(m)

    # Add pins
    for b in businesses:
        lat = b.get("latitude")
        lng = b.get("longitude")
        if lat and lng:
            tier = b.get("tier", 3)
            color = tier_color(tier)

            popup = f"""
            <b>{b['name']}</b><br>
            Tier {tier}<br>
            {b.get('address', '')}<br>
            <i>Category:</i> {b.get('category', '')}<br>
            <i>Types:</i> {', '.join(b.get('types', []))}<br>
            <i>Headers:</i> {b.get('headers', '')[:150]}
            """

            folium.Marker(
                location=[lat, lng],
                popup=folium.Popup(popup, max_width=300),
                icon=folium.Icon(color=color)
            ).add_to(m)

    st_folium(m, width=800, height=600)
