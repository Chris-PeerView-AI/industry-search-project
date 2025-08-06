# slides_exhibit.py

import matplotlib.pyplot as plt
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from PIL import Image
import os

EXHIBIT_TEMPLATE = "modules/downloaded_exhibit_template.pptx"



def apply_peerview_style():
    plt.style.use("ggplot")
    plt.rcParams.update({
        "font.family": "Montserrat",
        "axes.facecolor": "#f9f9f9",
        "figure.facecolor": "#ffffff",
        "axes.edgecolor": "#eeeeee",
        "axes.titleweight": "bold",
        "axes.titlesize": 16,
        "axes.labelcolor": "#333333",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "axes.grid": True,
        "grid.color": "#dddddd",
    })


def generate_chart_slide(chart_title, image_path, summary_text):
    ppt = Presentation(EXHIBIT_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "Exhibit {TBD}" in shape.text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = chart_title
            run.font.name = "Montserrat"
            run.font.size = Pt(30)
            run.font.bold = True
        elif "{TBD ANALYSIS}" in shape.text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_text
    img = Image.open(image_path)
    width = Inches(7.5)
    height = Inches(4.0)
    top = Inches(2.0)
    left = Inches(0.5)
    slide.shapes.add_picture(image_path, left, top, width=width, height=height)
    return ppt


def generate_revenue_chart(path, summaries):
    apply_peerview_style()
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    trusted = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["annual_revenue"] for b in trusted]
    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.bar(names, [v / 1_000_000 for v in values], color="#4CAF50")
    ax.axhline(mean_val / 1_000_000, color='blue', linestyle='--', label=f"Mean: ${mean_val / 1_000_000:.1f}M")
    ax.axhline(median_val / 1_000_000, color='purple', linestyle=':', label=f"Median: ${median_val / 1_000_000:.1f}M")
    ax.set_title("Annual Revenue")
    ax.set_ylabel("Revenue ($M)")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def generate_yoy_chart(path, summaries):
    apply_peerview_style()
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("yoy_growth") is not None]
    trusted = sorted(trusted, key=lambda x: x["yoy_growth"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["yoy_growth"] * 100 for b in trusted]
    avg = sum(values) / len(values)
    median = sorted(values)[len(values) // 2]
    fig, ax = plt.subplots(figsize=(10, 4))
    colors = ["green" if v >= 0 else "red" for v in values]
    bars = ax.bar(names, values, color=colors)
    ax.axhline(avg, color='blue', linestyle='--', label=f"Mean: {avg:.1f}%")
    ax.axhline(median, color='purple', linestyle=':', label=f"Median: {median:.1f}%")
    ax.set_title("YoY Growth")
    ax.set_ylabel("Growth (%)")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f"{height:.1f}%", xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 5), textcoords="offset points", ha='center', fontsize=8, weight='bold')
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def generate_ticket_chart(path, summaries):
    apply_peerview_style()
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("ticket_size") is not None]
    trusted = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["ticket_size"] for b in trusted]
    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]
    fig, ax = plt.subplots(figsize=(10, 4))
    bars = ax.bar(names, values, color="#4CAF50")
    ax.axhline(mean_val, color='blue', linestyle='--', label=f"Mean: ${mean_val:.0f}")
    ax.axhline(median_val, color='purple', linestyle=':', label=f"Median: ${median_val:.0f}")
    ax.set_title("Ticket Size")
    ax.set_ylabel("Dollars ($)")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f"${height:.0f}", xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 5), textcoords="offset points", ha='center', fontsize=8, weight='bold')
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def generate_market_size_chart(path, summaries):
    apply_peerview_style()
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("annual_revenue") is not None]
    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(["Lower Bound", "Upper Bound"], [trusted_total / 1_000_000, projected_total / 1_000_000],
                  color=["#4CAF50", "#C0C0C0"], edgecolor="black")
    bars[1].set_hatch("//")
    ax.set_title("Estimated Market Size")
    ax.set_ylabel("Revenue ($M)")
    for bar in bars:
        height = bar.get_height()
        ax.annotate(f"${height:.1f}M", xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 5), textcoords="offset points", ha='center', fontsize=8, weight='bold')
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True


def get_market_size_analysis():
    return (
        "This chart compares the total verified revenue from businesses with high-quality data to an estimate "
        "for the full local market. The upper bound assumes that businesses without usable data perform similarly "
        "to those with data, which likely overstates true market size. Poor data quality is often associated with smaller "
        "businesses or those facing operational challenges."
    )

def generate_map_chart(output_path, summaries):
    import folium
    import pandas as pd
    from geopy.distance import geodesic
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    import time
    import os

    df = pd.DataFrame(summaries)
    df = df[df["latitude"].notnull() & df["longitude"].notnull()]
    if df.empty:
        return False

    center_lat = df["latitude"].mean()
    center_lng = df["longitude"].mean()
    m = folium.Map(location=[center_lat, center_lng], zoom_start=13)  # default zoom_start won't matter due to fit_bounds

    # Determine bounds of the map to include all points
    latitudes = df["latitude"].tolist()
    longitudes = df["longitude"].tolist()
    sw = [min(latitudes), min(longitudes)]  # southwest corner
    ne = [max(latitudes), max(longitudes)]  # northeast corner
    m.fit_bounds([sw, ne])  # Adjust zoom and center dynamically

    # Draw radius circle using geodesic max distance from center
    farthest_km = max(
        geodesic((center_lat, center_lng), (lat, lng)).km
        for lat, lng in zip(df["latitude"], df["longitude"])
    )
    folium.Circle(
        location=[center_lat, center_lng],
        radius=farthest_km * 1000,
        color="blue",
        fill=True,
        fill_opacity=0.05,
        weight=0.7,
        popup=f"Search Radius: {farthest_km:.2f} km"
    ).add_to(m)

    for _, biz in df.iterrows():
        color = "gray" if biz.get("benchmark") != "trusted" else "green"
        folium.Marker(
            location=[biz["latitude"], biz["longitude"]],
            popup=biz.get("name", ""),
            icon=folium.Icon(color=color)
        ).add_to(m)

    tmp_html = output_path.replace(".png", ".html")
    m.save(tmp_html)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.set_window_size(900, 700)
    driver.get("file://" + os.path.abspath(tmp_html))
    time.sleep(2)
    driver.save_screenshot(output_path)
    driver.quit()
    return True
