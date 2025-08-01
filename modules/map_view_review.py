import streamlit as st
import folium
from streamlit_folium import st_folium
from supabase import create_client
import os
from dotenv import load_dotenv
from modules.google_search import geocode_location
from geopy.distance import geodesic

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

def calc_distance_km(lat1, lng1, lat2, lng2):
    return geodesic((lat1, lng1), (lat2, lng2)).km

def map_review(project_config):
    project_id = project_config["id"]
    location = project_config["location"]

    # Geocode original search location to use as true center
    try:
        center_lat, center_lng = geocode_location(location)
    except Exception:
        st.error("Failed to geocode project location.")
        return

    # Fetch businesses
    response = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    businesses = response.data

    if not businesses:
        st.warning("No businesses found for this project.")
        return

    # Compute farthest distance from center to any business
    farthest_km = max(
        calc_distance_km(center_lat, center_lng, b["latitude"], b["longitude"])
        for b in businesses
        if b.get("latitude") and b.get("longitude")
    )

    # Create map
    m = folium.Map(location=[center_lat, center_lng], zoom_start=12)

    # Draw single circle that covers all returned businesses
    folium.Circle(
        location=[center_lat, center_lng],
        radius=farthest_km * 1000,
        color="blue",
        fill=True,
        fill_opacity=0.05,
        weight=0.7,
        popup=f"Search Radius: {farthest_km:.2f} km"
    ).add_to(m)

    # Add pins for businesses
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
