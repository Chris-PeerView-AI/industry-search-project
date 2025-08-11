# --- File: modules/map_generator.py (PATCH v3.1) ---
# Fixes JS injection braces to avoid SyntaxError in f-strings and ensures proper map centering/aspect ratio.

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Iterable, Tuple, Optional
from string import Template

import pandas as pd
import folium
from branca.element import Element
from geopy.distance import geodesic

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import JavascriptException
from selenium.webdriver.common.by import By

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



def _set_exact_viewport(driver, width: int, height: int):
    """Force Chrome's *viewport* to the exact size using CDP. Works in headless.
    We also try set_window_size as a fallback. Logs final viewport for QA.
    """
    try:
        driver.set_window_size(width, height)
    except Exception as e:
        print(f"[MAP QA] set_window_size failed: {e}")
    try:
        driver.execute_cdp_cmd(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": int(width),
                "height": int(height),
                "deviceScaleFactor": 1,
                "mobile": False,
                "screenWidth": int(width),
                "screenHeight": int(height),
            },
        )
        driver.execute_cdp_cmd(
            "Emulation.setVisibleSize",
            {"width": int(width), "height": int(height)},
        )
    except Exception as e:
        print(f"[MAP QA] CDP viewport override failed: {e}")

    # Verify (and re-apply once if needed)
    try:
        iw, ih = driver.execute_script("return [window.innerWidth, window.innerHeight]")
        if iw != width or ih != height:
            print(f"[MAP QA] viewport mismatch after set: {iw}x{ih} (target {width}x{height}) — retrying")
            try:
                driver.execute_cdp_cmd(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": int(width),
                        "height": int(height),
                        "deviceScaleFactor": 1,
                        "mobile": False,
                        "screenWidth": int(width),
                        "screenHeight": int(height),
                    },
                )
                driver.execute_cdp_cmd(
                    "Emulation.setVisibleSize",
                    {"width": int(width), "height": int(height)},
                )
            except Exception as e:
                print(f"[MAP QA] second CDP attempt failed: {e}")
            iw, ih = driver.execute_script("return [window.innerWidth, window.innerHeight]")
        print(f"[MAP QA] viewport={iw}x{ih} target={width}x{height}")
    except Exception:
        pass


def compute_zoom_for_circle(lat_deg: float, radius_m: float, window_h_px: int, *, target_fraction: float) -> float:
    rpx_target = (window_h_px * target_fraction) / 2.0
    numerator = 156543.03392 * math.cos(math.radians(lat_deg)) * rpx_target
    if radius_m <= 0:
        radius_m = 200.0
    z = math.log2(max(numerator / radius_m, 1e-6))
    return max(1.0, min(z, 19.0))


def _bbox_midpoint(df: pd.DataFrame) -> tuple[float, float]:
    return (float((df["latitude"].min() + df["latitude"].max()) / 2.0),
            float((df["longitude"].min() + df["longitude"].max()) / 2.0))


def _radius_from_center(center: tuple[float, float], df: pd.DataFrame) -> int:
    clat, clng = center
    return max(200, int(max(
        geodesic((clat, clng), (lat, lng)).km for lat, lng in zip(df["latitude"], df["longitude"])) * 1000))


def build_map(df: pd.DataFrame, *, zoom_fraction: float = 0.75, window: Tuple[int, int] = WINDOW_DEFAULT) -> tuple[
    folium.Map, MapMeta]:
    """Build a Folium map sized exactly to `window` and centered via bbox midpoint.
    Keeps fractional zoom and adds full-bleed CSS so screenshots have no rails."""
    df = df[df["latitude"].notna() & df["longitude"].notna()].copy()
    if df.empty:
        raise ValueError("No valid lat/lon rows")

    center_lat, center_lng = _bbox_midpoint(df)
    radius_m = _radius_from_center((center_lat, center_lng), df)
    desired_zoom = compute_zoom_for_circle(center_lat, radius_m, window[1], target_fraction=zoom_fraction)

    m = folium.Map(
        location=[center_lat, center_lng],
        zoom_start=int(round(desired_zoom)),
        tiles=None,
        control_scale=False,
        zoom_control=False,
        max_zoom=19,
        width=window[0],
        height=window[1],
    )
    folium.TileLayer(POSI_TILE_URL, name="Positron", attr=POSI_TILE_ATTR, control=False).add_to(m)

    # Base tile style
    m.get_root().html.add_child(Element(
        """<style>.leaflet-container .leaflet-tile { opacity: 0.92; filter: saturate(0.85) brightness(1.02); }</style>"""
    ))

    # Full-bleed CSS (pixel-locked to window)
    css_tpl = Template(
        """
        <style>
          html, body { margin:0; padding:0; overflow:hidden; }
          #$map_id { position:fixed; top:0; left:0; width:${w}px; height:${h}px; }
          .folium-map, .leaflet-container { width:${w}px !important; height:${h}px !important; }
        </style>
        """
    )
    m.get_root().html.add_child(Element(css_tpl.substitute(map_id=m.get_name(), w=window[0], h=window[1])))

    # Fractional zoom + stable center (Template to avoid f-string/Jinja brace issues)
    js_tpl = Template(
        """
        <script>
        (function(){
          var m=$map_id;
          if(m){
            m.options.zoomSnap = 0; m.options.zoomDelta = 0.1;
            m.setZoom($zoom, {animate:false});
            m.setView([$lat, $lng], $zoom);
            setTimeout(function(){ m.invalidateSize(false); }, 60);
          }
        })();
        </script>
        """
    )
    js_str = js_tpl.substitute(
        map_id=m.get_name(),
        zoom=f"{desired_zoom:.4f}",
        lat=f"{center_lat:.6f}",
        lng=f"{center_lng:.6f}",
    )
    m.get_root().html.add_child(Element(js_str))

    # Radius ring + markers
    folium.Circle(
        location=[center_lat, center_lng],
        radius=radius_m,
        color="#2c7fb8",
        fill=True,
        fill_opacity=0.05,
        weight=1.2,
        opacity=0.8,
        dash_array="6 6",
    ).add_to(m)

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
        ).add_to(m)

    # Legend
    m.get_root().html.add_child(Element(
        """<div style='position: fixed; top: 12px; right: 12px; z-index: 9999; background: rgba(255,255,255,0.96); border: 1px solid #d0d0d0; border-radius: 6px; padding: 14px 16px; font-size: 18px; line-height: 1.6; box-shadow: 0 2px 8px rgba(0,0,0,.15);'><div style='font-weight:600; margin-bottom:8px;'>Legend</div><div><span style='display:inline-block;width:14px;height:14px;border:2px solid #fff;background:#2ca25f;border-radius:50%;margin-right:8px;'></span>Trusted</div><div><span style='display:inline-block;width:14px;height:14px;border:2px solid #fff;background:#7f8c8d;border-radius:50%;margin-right:8px;'></span>Other</div></div>"""
    ))

    return m, MapMeta(center_lat, center_lng, float(radius_m), int(len(df)), float(desired_zoom))



def save_html_and_png(m, html_path: str, png_path: str, window: tuple[int, int]):
    import os, time
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import JavascriptException
    from selenium.webdriver.common.by import By

    # Save HTML
    m.save(html_path)

    # Launch Chrome
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--force-device-scale-factor=1")
    options.add_argument(f"--window-size={window[0]},{window[1]}")

    driver = webdriver.Chrome(options=options)
    try:
        # Force the viewport to the exact size (CDP)
        _set_exact_viewport(driver, window[0], window[1])

        # Load the file
        driver.get("file://" + os.path.abspath(html_path))

        # One more time after content loads (some pages can alter metrics)
        _set_exact_viewport(driver, window[0], window[1])

        deadline = time.time() + 12.0
        driver.execute_script("document.body.style.overflow='hidden'")

        # Wait until tiles are loaded
        while time.time() < deadline:
            try:
                loading = driver.execute_script("return document.querySelectorAll('.leaflet-tile-loading').length")
                tiles_seen = driver.execute_script("return document.querySelectorAll('.leaflet-tile, .leaflet-tile-loaded').length")
            except JavascriptException:
                loading, tiles_seen = 0, 0
            if int(loading) == 0 and int(tiles_seen) > 0:
                time.sleep(0.4)
                break
            time.sleep(0.25)

        # Screenshot ONLY the map element (no window chrome)
        elem = driver.find_element(By.ID, m.get_name())
        elem.screenshot(png_path)

    finally:
        driver.quit()

    # QA: confirm final PNG dims
    try:
        from PIL import Image
        with Image.open(png_path) as im:
            w, h = im.size
        print(f"[MAP QA] final_png={w}x{h} expected={window[0]}x{window[1]}")
    except Exception as e:
        print(f"[MAP QA] could not read PNG for QA: {e}")


def generate_map_png_from_summaries(summaries: Iterable[dict], output_path: str, *, zoom_fraction: float = 0.75,
                                    aspect_ratio: Optional[float] = None, window_height_px: int = 800) -> bool:
    df = pd.DataFrame(list(summaries))
    df = df[df["latitude"].notna() & df["longitude"].notna()]
    if df.empty:
        return False
    if aspect_ratio is None:
        aspect_ratio = 3 / 2
    window = (int(window_height_px * aspect_ratio), int(window_height_px))
    m, _ = build_map(df, zoom_fraction=zoom_fraction, window=window)
    html_path = output_path.replace(".png", ".html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    save_html_and_png(m, html_path, output_path, window=window)

    # --- QA Guard: assert PNG dimensions match target (<=1px drift per axis) ---
    try:
        from PIL import Image
        with Image.open(output_path) as im:
            w, h = im.size
        exp_w, exp_h = window[0], window[1]
        dw, dh = abs(w - exp_w), abs(h - exp_h)
        print(f"[MAP QA] anchor_ratio={aspect_ratio:.6f} expected={exp_w}x{exp_h} actual={w}x{h} dW={dw}px dH={dh}px")
        if dw > 1 or dh > 1:
            raise AssertionError(f"Map PNG size mismatch: expected {exp_w}x{exp_h}, got {w}x{h} (Δ {dw}px, {dh}px)")
    except Exception as e:
        # Surface errors but do not crash export if PIL is missing; re-raise assertion
        if not isinstance(e, AssertionError):
            print(f"[MAP QA] Warning: could not verify PNG size ({e})")
        else:
            raise

    return True


def generate_map_png_from_project(project_id: str, supabase, output_dir: str, *, zoom_fraction: float = 0.75,
                                  aspect_ratio: Optional[float] = None) -> Optional[str]:
    resp = supabase.table("enigma_summaries").select("name, latitude, longitude, benchmark").eq("project_id",
                                                                                                project_id).execute()
    df = pd.DataFrame(resp.data or [])
    df = df[df["latitude"].notna() & df["longitude"].notna()]
    if df.empty:
        return None
    if aspect_ratio is None:
        aspect_ratio = 3 / 2
    window = (int(800 * aspect_ratio), 800)
    m, _ = build_map(df, zoom_fraction=zoom_fraction, window=window)
    html_path = os.path.join(output_dir, "test_map.html")
    png_path = os.path.join(output_dir, "test_map.png")
    save_html_and_png(m, html_path, png_path, window=window)
    return png_path
