"""
Centralized Folium map generator used by:
  - slides_exhibit.generate_map_chart (PPT screenshot)
  - benchmark_review_ui (Streamlit preview)

Goals
-----
• Consistent aesthetics (Positron tiles, circle markers, dashed radius ring, bigger legend with shadow)
• Stable screenshots at exact visual scale (fractional zoom, DPR=1)
• Bounded, logged tile-wait in headless Chrome (no endless loops)

API
---
- build_map(df: pd.DataFrame, *, zoom_fraction=0.75) -> folium.Map
- save_html_and_png(m: folium.Map, html_path: str, png_path: str, window=(1200,800)) -> None
- generate_map_png_from_summaries(summaries: list[dict], output_path: str, *, zoom_fraction=0.75) -> bool

Optional helper if you want to fetch inside here:
- generate_map_png_from_project(project_id: str, supabase, output_dir: str, *, zoom_fraction=0.75) -> str | None
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Iterable, Tuple, Optional

import pandas as pd
import folium
from branca.element import Element
from geopy.distance import geodesic

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, JavascriptException

# -----------------------------
# Defaults / constants
# -----------------------------
POSI_TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
POSI_TILE_ATTR = "© OpenStreetMap contributors © CARTO"
WINDOW_DEFAULT = (1200, 800)
TILE_WAIT_HARD_TIMEOUT_SEC = 12.0
TILE_POLL_INTERVAL_SEC = 0.25


@dataclass
class MapMeta:
    center_lat: float
    center_lng: float
    radius_m: float
    count: int
    desired_zoom: float


# -----------------------------
# Zoom math (Web Mercator)
# -----------------------------

def _meters_per_pixel(lat_deg: float, zoom: float) -> float:
    """Return meters per CSS pixel at given latitude/zoom in EPSG:3857."""
    return 156543.03392 * math.cos(math.radians(lat_deg)) / (2 ** zoom)


def compute_zoom_for_circle(lat_deg: float, radius_m: float, window_h_px: int, *, target_fraction: float) -> float:
    """Compute zoom so that the circle **diameter** occupies target_fraction of the viewport height.
    target_fraction in (0,1]; e.g. 0.75 → diameter ~ 75% of height.
    """
    rpx_target = (window_h_px * target_fraction) / 2.0  # radius in pixels
    # meters_per_pixel = radius_m / rpx
    # => 156543.03392 * cos(lat) / 2^z = radius_m / rpx  =>
    # z = log2(156543.03392 * cos(lat) * rpx / radius_m)
    numerator = 156543.03392 * math.cos(math.radians(lat_deg)) * rpx_target
    if radius_m <= 0:
        radius_m = 200.0
    z = math.log2(max(numerator / radius_m, 1e-6))
    return max(1.0, min(z, 19.0))


# -----------------------------
# Core builders
# -----------------------------

def build_map(df: pd.DataFrame, *, zoom_fraction: float = 0.75) -> tuple[folium.Map, MapMeta]:
    df = df[df["latitude"].notna() & df["longitude"].notna()].copy()
    if df.empty:
        raise ValueError("No valid lat/lon rows")

    center_lat = float(df["latitude"].mean())
    center_lng = float(df["longitude"].mean())

    # Radius based on farthest point from center
    farthest_km = max(geodesic((center_lat, center_lng), (lat, lng)).km for lat, lng in zip(df["latitude"], df["longitude"]))
    radius_m = max(200, int(farthest_km * 1000))

    desired_zoom = compute_zoom_for_circle(center_lat, radius_m, WINDOW_DEFAULT[1], target_fraction=zoom_fraction)

    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=int(round(desired_zoom)),  # set integer first, then fractional via JS
        tiles=None,
        control_scale=False,
        zoom_control=False,
        max_zoom=19,
        width=WINDOW_DEFAULT[0],
        height=WINDOW_DEFAULT[1],
    )
    folium.TileLayer(POSI_TILE_URL, name="Positron", attr=POSI_TILE_ATTR, control=False).add_to(m)

    # Subtle basemap fade & desaturation
    css = Element(
        """
        <style>
          .leaflet-container .leaflet-tile { opacity: 0.92; filter: saturate(0.85) brightness(1.02); }
        </style>
        """
    )
    m.get_root().html.add_child(css)

    # Force fractional zoom (avoid Leaflet snapping)
    map_id = m.get_name()
    js = Element(
        f"""
        <script>
        (function() {{
          var m = {map_id};
          if (m) {{
            m.options.zoomSnap = 0; m.options.zoomDelta = 0.1;
            m.setZoom({desired_zoom:.4f}, {{animate:false}});
            setTimeout(function() {{ m.invalidateSize(false); }}, 60);
          }}
        }})();
        </script>
        """
    )
    m.get_root().html.add_child(js)

    # Dashed search radius ring
    try:
        folium.Circle(
            location=[center_lat, center_lng],
            radius=radius_m,
            color="#2c7fb8",
            fill=True,
            fill_opacity=0.05,
            weight=1.2,
            opacity=0.8,
            dash_array="6 6",
            tooltip=f"Search Radius: {radius_m/1000:.2f} km",
        ).add_to(m)
    except TypeError:
        folium.Circle(
            location=[center_lat, center_lng],
            radius=radius_m,
            color="#2c7fb8",
            fill=True,
            fill_opacity=0.05,
            weight=1.2,
            opacity=0.8,
            tooltip=f"Search Radius: {radius_m/1000:.2f} km",
        ).add_to(m)

    # Circle markers with white stroke
    for _, row in df.iterrows():
        fill = "#2ca25f" if str(row.get("benchmark", "")).lower() == "trusted" else "#7f8c8d"
        folium.CircleMarker(
            location=[row["latitude"], row["longitude"]],
            radius=8,
            weight=2,
            color="#ffffff",
            fill=True,
            fill_color=fill,
            fill_opacity=0.95,
            tooltip=row.get("name", ""),
        ).add_to(m)

    # Larger legend with soft shadow
    legend_html = """
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: rgba(255,255,255,0.96); border: 1px solid #d0d0d0;
                border-radius: 6px; padding: 14px 16px; font-size: 18px; line-height: 1.6;
                box-shadow: 0 2px 8px rgba(0,0,0,.15);">
      <div style="font-weight:600; margin-bottom:8px;">Legend</div>
      <div><span style="display:inline-block;width:14px;height:14px;border:2px solid #fff;background:#2ca25f;border-radius:50%;margin-right:8px;"></span>Trusted</div>
      <div><span style="display:inline-block;width:14px;height:14px;border:2px solid #fff;background:#7f8c8d;border-radius:50%;margin-right:8px;"></span>Other</div>
    </div>
    """

    m.get_root().html.add_child(Element(legend_html))

    meta = MapMeta(center_lat, center_lng, float(radius_m), int(len(df)), float(desired_zoom))
    return m, meta


# -----------------------------
# Screenshot helper
# -----------------------------

def save_html_and_png(m: folium.Map, html_path: str, png_path: str, window: Tuple[int, int] = WINDOW_DEFAULT) -> None:
    m.save(html_path)
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--force-device-scale-factor=1")  # stabilize CSS pixel geometry

    driver = webdriver.Chrome(options=options)
    try:
        driver.set_window_size(*window)
        driver.get("file://" + os.path.abspath(html_path))

        # Bounded tile wait
        deadline = time.time() + TILE_WAIT_HARD_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                loading = driver.execute_script("return document.querySelectorAll('.leaflet-tile-loading').length")
                tiles_seen = driver.execute_script("return document.querySelectorAll('.leaflet-tile, .leaflet-tile-loaded').length")
            except JavascriptException:
                loading, tiles_seen = 0, 0
            if int(loading) == 0 and int(tiles_seen) > 0:
                time.sleep(0.4)
                break
            time.sleep(TILE_POLL_INTERVAL_SEC)

        driver.save_screenshot(png_path)
    finally:
        driver.quit()


# -----------------------------
# Public entry points
# -----------------------------

def generate_map_png_from_summaries(summaries: Iterable[dict], output_path: str, *, zoom_fraction: float = 0.75) -> bool:
    df = pd.DataFrame(list(summaries))
    df = df[df["latitude"].notna() & df["longitude"].notna()]
    if df.empty:
        return False

    m, meta = build_map(df, zoom_fraction=zoom_fraction)
    html_path = output_path.replace(".png", ".html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_html_and_png(m, html_path, output_path, window=WINDOW_DEFAULT)
    return True


def generate_map_png_from_project(project_id: str, supabase, output_dir: str, *, zoom_fraction: float = 0.75) -> Optional[str]:
    # Pull minimal columns
    resp = (
        supabase.table("enigma_summaries")
        .select("name, latitude, longitude, benchmark")
        .eq("project_id", project_id)
        .execute()
    )
    df = pd.DataFrame(resp.data or [])
    df = df[df["latitude"].notna() & df["longitude"].notna()]
    if df.empty:
        return None
    m, meta = build_map(df, zoom_fraction=zoom_fraction)
    html_path = os.path.join(output_dir, "test_map.html")
    png_path = os.path.join(output_dir, "test_map.png")
    save_html_and_png(m, html_path, png_path, window=WINDOW_DEFAULT)
    return png_path
