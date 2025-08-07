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


def generate_revenue_chart(path, summaries, end_date: str):
    import matplotlib.font_manager as fm

    apply_peerview_style()

    # Use Montserrat font globally
    plt.rcParams['font.family'] = 'Montserrat'

    # Sort trusted businesses by revenue
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    trusted = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)

    # Extract names and values with disambiguation for duplicates
    seen_names = {}
    def disambiguate(name):
        base = name[:20]
        if base in seen_names:
            seen_names[base] += 1
            return f"{base[:17]}…{seen_names[base]}"
        seen_names[base] = 1
        return base if len(name) <= 20 else base[:19] + "…1"

    names = [disambiguate(b["name"]) for b in trusted]
    values = [b["annual_revenue"] for b in trusted]
    values_millions = [v / 1_000_000 for v in values]

    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]

    # Title subtitle
    subtitle = f"As of {end_date}" if end_date else ""

    # Highlight colors: gold, silver, bronze for top 3, then soft green
    colors = ["#D4AF37", "#C0C0C0", "#CD7F32"] + ["#A2D5AB"] * (len(values) - 3)

    fig, ax = plt.subplots(figsize=(12, 5.5))

    # Aesthetic improvements
    fig.patch.set_facecolor("#F8F8F8")       # Chart canvas background
    ax.set_facecolor("#FFFFFF")              # Plot area background
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_visible(False)
    ax.spines['bottom'].set_visible(False)

    # Add a border around chart area manually
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1)
        spine.set_edgecolor("#CCCCCC")

    bars = ax.bar(names, values_millions, color=colors, width=0.6)

    # Annotate all bars with values
    for bar, val in zip(bars, values_millions):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.15, f"${val:.1f}M",
                ha='center', va='bottom', fontsize=8, rotation=90)

    # Add mean and median lines
    ax.axhline(mean_val / 1_000_000, color='#4682B4', linestyle='--', linewidth=1,
               label=f"Mean: ${mean_val / 1_000_000:.1f}M")
    ax.axhline(median_val / 1_000_000, color='#9370DB', linestyle=':', linewidth=1,
               label=f"Median: ${median_val / 1_000_000:.1f}M")

    # Add top performer callout
    top_bar = bars[0]
    ax.annotate("Top Performer",
                xy=(top_bar.get_x() + top_bar.get_width() / 2, top_bar.get_height()),
                xytext=(top_bar.get_x() + 0.5, top_bar.get_height() + 1.0),
                arrowprops=dict(arrowstyle='->', lw=0.8),
                ha='center', fontsize=9)

    chart_title = "Annual Revenue"
    ax.set_title(chart_title, fontsize=16, fontweight='bold', color="#333333", pad=20)
    if subtitle:
        ax.text(0.5, 1.03, subtitle, transform=ax.transAxes,
                fontsize=10, color="#555555", ha='center')

    ax.set_ylabel("Revenue ($M)", fontsize=11, color="#333333")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8, color="#333333")
    ax.tick_params(axis='y', labelsize=9, colors="#333333")
    ax.grid(False)  # Disable grid lines completely
    ax.legend(loc='upper right', frameon=False)

    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True








def generate_yoy_chart(path, summaries, end_date: str):
    import matplotlib.font_manager as fm

    apply_peerview_style()
    plt.rcParams['font.family'] = 'Montserrat'

    # Filter and sort
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("yoy_growth") is not None]
    trusted = sorted(trusted, key=lambda x: x["yoy_growth"], reverse=True)

    # Disambiguate duplicate names
    seen_names = {}
    def disambiguate(name):
        base = name[:20]
        if base in seen_names:
            seen_names[base] += 1
            return f"{base[:17]}…{seen_names[base]}"
        seen_names[base] = 1
        return base if len(name) <= 20 else base[:19] + "…1"

    names = [disambiguate(b["name"]) for b in trusted]
    values = [round(b["yoy_growth"] * 100) for b in trusted]  # round to nearest percent

    avg = sum(values) / len(values)
    median = sorted(values)[len(values) // 2]

    # Colors: green for growth, red for decline
    colors = ["#4CAF50" if v >= 0 else "#E57373" for v in values]

    fig, ax = plt.subplots(figsize=(12, 5.5))
    fig.patch.set_facecolor("#F8F8F8")
    ax.set_facecolor("#FFFFFF")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1)
        spine.set_edgecolor("#CCCCCC")

    bars = ax.bar(names, values, color=colors, width=0.6)

    for bar, val in zip(bars, values):
        offset = 1.0 if abs(val) < 10 else 0.5
        ax.text(bar.get_x() + bar.get_width() / 2, val + (offset if val >= 0 else -offset), f"{val:.0f}%",
                ha='center', va='bottom' if val >= 0 else 'top', fontsize=8, weight='bold')

    ax.axhline(avg, color='#4682B4', linestyle='--', linewidth=1, label=f"Mean: {avg:.1f}%")
    ax.axhline(median, color='#9370DB', linestyle=':', linewidth=1, label=f"Median: {median:.1f}%")
    ax.axhline(0, color='#CCCCCC', linewidth=0.5)  # subtle zero line

    chart_title = "Year over Year Revenue Growth"
    ax.set_title(chart_title, fontsize=16, fontweight='bold', color="#333333", pad=28)
    if end_date:
        ax.text(0.5, 1.06, f"As of {end_date}", transform=ax.transAxes,
                fontsize=10, color="#555555", ha='center')

    ax.set_ylabel("Growth (%)", fontsize=11, color="#333333")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8, color="#333333")
    ax.tick_params(axis='x', labelsize=9, colors="#333333")  # X-axis back on
    ax.tick_params(axis='y', labelsize=9, colors="#333333")
    ax.grid(False)
    ax.legend(loc='upper right', frameon=False)

    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True





def generate_ticket_chart(path, summaries, end_date: str):
    import matplotlib.font_manager as fm

    apply_peerview_style()
    plt.rcParams['font.family'] = 'Montserrat'

    # Filter and sort
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("ticket_size") is not None]
    trusted = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)

    # Disambiguate duplicate names
    seen_names = {}
    def disambiguate(name):
        base = name[:20]
        if base in seen_names:
            seen_names[base] += 1
            return f"{base[:17]}…{seen_names[base]}"
        seen_names[base] = 1
        return base if len(name) <= 20 else base[:19] + "…1"

    names = [disambiguate(b["name"]) for b in trusted]
    values = [round(b["ticket_size"]) for b in trusted]

    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]

    fig, ax = plt.subplots(figsize=(12, 5.5))
    fig.patch.set_facecolor("#F8F8F8")
    ax.set_facecolor("#FFFFFF")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1)
        spine.set_edgecolor("#CCCCCC")

    bars = ax.bar(names, values, color="#4CAF50", width=0.6)

    for bar, val in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 0.5, f"${val}",
                ha='center', va='bottom', fontsize=8, weight='bold')

    ax.axhline(mean_val, color='#4682B4', linestyle='--', linewidth=1, label=f"Mean: ${mean_val:.0f}")
    ax.axhline(median_val, color='#9370DB', linestyle=':', linewidth=1, label=f"Median: ${median_val:.0f}")
    ax.axhline(0, color='#CCCCCC', linewidth=0.5)  # baseline

    chart_title = "Average Ticket Size"
    ax.set_title(chart_title, fontsize=16, fontweight='bold', color="#333333", pad=28)
    if end_date:
        ax.text(0.5, 1.06, f"As of {end_date}", transform=ax.transAxes,
                fontsize=10, color="#555555", ha='center')

    ax.set_ylabel("Dollars ($)", fontsize=11, color="#333333")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8, color="#333333")
    ax.tick_params(axis='x', labelsize=9, colors="#333333")
    ax.tick_params(axis='y', labelsize=9, colors="#333333")
    ax.grid(False)
    ax.legend(loc='upper right', frameon=False)

    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True



def generate_market_size_chart(path, summaries, end_date: str):
    import matplotlib.font_manager as fm

    apply_peerview_style()
    plt.rcParams['font.family'] = 'Montserrat'

    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("annual_revenue") is not None]
    untrusted = [b for b in summaries if b.get("benchmark") == "low" and b.get("annual_revenue") is not None]

    trusted_total = sum(b["annual_revenue"] for b in trusted)
    num_trusted = len(trusted)
    num_untrusted = len(untrusted)
    projected_total = trusted_total * (num_trusted + num_untrusted) / max(num_trusted, 1)

    lower_millions = trusted_total / 1_000_000
    upper_millions = projected_total / 1_000_000

    fig, ax = plt.subplots(figsize=(8, 5))
    fig.patch.set_facecolor("#F8F8F8")
    ax.set_facecolor("#FFFFFF")

    bars = ax.bar(["Verified Revenue", "Projected Total"], [lower_millions, upper_millions],
                  color=["#4CAF50", "#C0C0C0"], edgecolor="black", width=0.5)
    bars[1].set_hatch("//")

    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(1)
        spine.set_edgecolor("#CCCCCC")

    for bar, val in zip(bars, [lower_millions, upper_millions]):
        ax.text(bar.get_x() + bar.get_width() / 2, val + 1, f"${val:.1f}M",
                ha='center', va='bottom', fontsize=9, weight='bold')

    # Annotate business counts
    ax.text(0, lower_millions + 3, f"{num_trusted} businesses", ha='center', fontsize=8, color="#333333")
    ax.text(1, upper_millions + 3, f"{num_trusted + num_untrusted} total (incl. {num_untrusted} projected)",
            ha='center', fontsize=8, color="#333333")

    # Grid and labels
    ax.axhline(0, color='#CCCCCC', linewidth=0.5)
    ax.set_ylabel("Revenue ($M)", fontsize=11, color="#333333")
    ax.set_title("Estimated Market Revenue Potential", fontsize=16, fontweight='bold', color="#333333", pad=28)
    if end_date:
        ax.text(0.5, 1.06, f"As of {end_date}", transform=ax.transAxes,
                fontsize=10, color="#555555", ha='center')

    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Verified Revenue", "Projected Total"], fontsize=9, color="#333333")
    ax.tick_params(axis='y', labelsize=9, colors="#333333")
    ax.grid(False)

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
