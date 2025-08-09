# modules/TEST_pretty_map.py
"""
Standalone test to generate a prettier Folium map and save a PNG screenshot
for a single project without touching the main codebase.
"""
from __future__ import annotations
import argparse
import logging
import math
import os
import time
from datetime import datetime
from typing import Tuple, List, Dict

import pandas as pd
from dotenv import load_dotenv
from supabase import create_client, Client
import folium
from geopy.distance import geodesic
from branca.element import Element
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, JavascriptException

OUTPUT_ROOT = os.path.join("modules", "output")
WINDOW_W, WINDOW_H = 1200, 800
TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
TILE_ATTR = "© OpenStreetMap contributors © CARTO"
TILE_WAIT_HARD_TIMEOUT_SEC = 12.0
TILE_POLL_INTERVAL_SEC = 0.25

logger = logging.getLogger("TEST_pretty_map")

def setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.debug("Logging initialized at %s", level)

def get_supabase() -> Client:
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment.")
    logger.debug("Supabase URL: %s", url)
    return create_client(url, key)

def fetch_businesses(project_id: str, supabase: Client) -> pd.DataFrame:
    logger.info("Fetching businesses for project %s", project_id)
    resp = supabase.table("enigma_summaries").select("name, latitude, longitude, benchmark").eq("project_id", project_id).execute()
    df = pd.DataFrame(resp.data or [])
    logger.debug("Raw rows: %d", len(df))
    if df.empty:
        return df
    for col in ("latitude", "longitude"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    before = len(df)
    df = df[df["latitude"].notna() & df["longitude"].notna()].copy()
    logger.info("Rows with valid coords: %d (dropped %d)", len(df), before - len(df))
    trusted = (df.get("benchmark", "").astype(str).str.lower() == "trusted").sum()
    logger.info("Trusted: %d | Other: %d", trusted, len(df) - trusted)
    return df

# -----------------------------
# Zoom computation
# -----------------------------

def compute_zoom_for_circle(center_lat: float, radius_m: float, target_fraction_of_height: float, height_px: int) -> float:
    """Compute Leaflet zoom so that a circle of radius_m meters appears with diameter ~= target_fraction_of_height * map height.
    Uses WebMercator meters-per-pixel formula: mpp = 156543.03392 * cos(lat) / 2**z
    """
    # Desired pixel radius
    rp = (target_fraction_of_height * height_px) / 2.0
    if rp <= 0:
        rp = 1
    mpp_needed = radius_m / rp
    denom = 156543.03392 * math.cos(math.radians(center_lat))
    if mpp_needed <= 0 or denom <= 0:
        return 13.0
    z = math.log2(denom / mpp_needed)
    logger.info("Computed zoom: %.3f (target fraction=%.2f, height=%d, rp=%.1f px)", z, target_fraction_of_height, height_px, rp)
    return max(2.0, min(19.0, z))


def build_map(df: pd.DataFrame) -> Tuple[folium.Map, Dict[str, float]]:
    center_lat, center_lng = df["latitude"].mean(), df["longitude"].mean()
    logger.info("Center lat/lng: %.6f, %.6f", center_lat, center_lng)

    # Radius based on farthest point (keeps underlying area constant)
    farthest_km = max(geodesic((center_lat, center_lng), (lat, lng)).km for lat, lng in zip(df["latitude"], df["longitude"]))
    radius_m = max(200, int(farthest_km * 1000))

    # Compute zoom so the circle diameter ~ 60% of map height
    desired_zoom = compute_zoom_for_circle(center_lat, radius_m, target_fraction_of_height=0.60, height_px=WINDOW_H)

    # Build map with that zoom (no fit_bounds)
    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=desired_zoom,  # pass float
        tiles=None,
        control_scale=False,
        zoom_control=False,
        max_zoom=19,
    )

    # Allow fractional zoom & set exact level via JS
    js = Element(f"""
    <script>
    var map = document.querySelector('.leaflet-container')._leaflet_map;
    if (map) {{
      map.options.zoomSnap = 0;
      map.options.zoomDelta = 0.1;
      map.setZoom({desired_zoom});
    }}
    </script>
    """)
    m.get_root().html.add_child(js)

    # Larger legend styling
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

    folium.TileLayer(TILE_URL, name="Positron", attr=TILE_ATTR, control=False).add_to(m)

    # Subtle basemap fade & desaturation so markers pop
    css = Element(
        """
        <style>
          .leaflet-container .leaflet-tile { opacity: 0.92; filter: saturate(0.85) brightness(1.02); }
        </style>
        """
    )
    m.get_root().html.add_child(css)

    # Force fractional zoom precisely via JS (avoid Leaflet zoomSnap rounding)
    _map_id = m.get_name()
    frac_js = Element(f"""
    <script>
      (function() {{
        var m = {_map_id};
        if (m) {{
          m.options.zoomSnap = 0;
          m.options.zoomDelta = 0.1;
          m.setZoom({desired_zoom:.4f}, {{animate:false}});
          setTimeout(function() {{ m.invalidateSize(false); }}, 60);
        }}
      }})();
    </script>
    """)
    m.get_root().html.add_child(frac_js)

    # Dashed outline ring (fallback to solid if folium version lacks dash_array)
    try:
        folium.Circle(
            location=[center_lat, center_lng],
            radius=radius_m,
            color="#2c7fb8",
            fill=True,
            fill_opacity=0.05,
            weight=1.2,
            opacity=0.8,
            tooltip=f"Search Radius: {radius_m / 1000:.2f} km",
            dash_array="6 6",
        ).add_to(m)
        logger.debug("Applied dashed radius ring with dash_array 6 6")
    except TypeError:
        folium.Circle(
            location=[center_lat, center_lng],
            radius=radius_m,
            color="#2c7fb8",
            fill=True,
            fill_opacity=0.05,
            weight=1.2,
            opacity=0.8,
            tooltip=f"Search Radius: {radius_m / 1000:.2f} km",
        ).add_to(m)
        logger.debug("dash_array unsupported; used solid ring")

    # Markers
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

    # Bigger legend with subtle shadow
    legend_html = (
        """
        <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                    background: rgba(255,255,255,0.96); border: 1px solid #d0d0d0;
                    border-radius: 6px; padding: 10px 12px; font-size: 15px; line-height: 1.5;
                    box-shadow: 0 2px 10px rgba(0,0,0,.18);">
          <div style="font-weight:600; margin-bottom:6px;">Legend</div>
          <div><span style="display:inline-block;width:12px;height:12px;border:2px solid #fff;background:#2ca25f;border-radius:50%;margin-right:8px;"></span>Trusted</div>
          <div><span style="display:inline-block;width:12px;height:12px;border:2px solid #fff;background:#7f8c8d;border-radius:50%;margin-right:8px;"></span>Other</div>
        </div>
        """
    )
    m.get_root().html.add_child(Element(legend_html))

    meta = {"center_lat": center_lat, "center_lng": center_lng, "radius_m": float(radius_m), "count": int(len(df)), "desired_zoom": desired_zoom}
    logger.info("Meta: %s", meta)
    return m, meta


def save_html_and_png(m: folium.Map, html_path: str, png_path: str) -> None:
    logger.info("Saving HTML → %s", html_path)
    m.save(html_path)
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Stabilize device pixel ratio so the circle appears at a consistent CSS pixel size
    options.add_argument("--force-device-scale-factor=1")
    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as e:
        logger.error("Unable to start Chrome WebDriver: %s", e)
        raise
    try:
        driver.set_window_size(WINDOW_W, WINDOW_H)
        target = "file://" + os.path.abspath(html_path)
        logger.info("Loading: %s", target)
        driver.get(target)
        try:
            final_zoom = driver.execute_script(
                "return (window && window.L && window.L) ? (Object.values(window).find(v => v && v._zoom)?.getZoom?.() || null) : null;")
            logger.debug("Final Leaflet zoom (pre-wait): %s", final_zoom)
        except JavascriptException:
            pass
        deadline = time.time() + TILE_WAIT_HARD_TIMEOUT_SEC
        polls = 0
        while time.time() < deadline:
            polls += 1
            try:
                loading = driver.execute_script("return document.querySelectorAll('.leaflet-tile-loading').length")
                loaded_imgs = driver.execute_script("return document.querySelectorAll('.leaflet-tile-loaded, .leaflet-tile').length")
            except JavascriptException:
                loading, loaded_imgs = 0, 0
            logger.debug("Tile poll %d → loading=%s, tiles_seen=%s", polls, loading, loaded_imgs)
            if int(loading) == 0 and int(loaded_imgs) > 0:
                time.sleep(0.4)
                break
            time.sleep(TILE_POLL_INTERVAL_SEC)
        driver.save_screenshot(png_path)
        logger.info("Saved PNG → %s", png_path)
    finally:
        driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a pretty Folium map PNG for a project.")
    parser.add_argument("--project", required=True, help="Project UUID")
    parser.add_argument("--loglevel", default=os.getenv("TEST_PRETTY_MAP_LOGLEVEL", "INFO"), help="Logging level")
    args = parser.parse_args()
    setup_logging(args.loglevel)
    out_dir = os.path.join(OUTPUT_ROOT, args.project)
    os.makedirs(out_dir, exist_ok=True)
    supabase = get_supabase()
    df = fetch_businesses(args.project, supabase)
    if df.empty:
        logger.error("No businesses with valid coordinates found for project %s", args.project)
        return 2
    m, meta = build_map(df)
    logger.debug("Map meta: %s", meta)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(out_dir, f"test_map_{stamp}.html")
    png_path = os.path.join(out_dir, f"test_map_{stamp}.png")
    save_html_and_png(m, html_path, png_path)
    logger.info("✅ Success. HTML: %s | PNG: %s", html_path, png_path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
