import os
import sys
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches, Pt
import matplotlib.pyplot as plt
from test_01_download_templates import download_all_templates

sys.stdout.reconfigure(line_buffering=True)
print("✅ Script started")
print("📦 Importing libraries successful")

MODULES_DIR = os.path.dirname(__file__)
TITLE_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_title_template.pptx")
EXHIBIT_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_exhibit_template.pptx")
SUMMARY_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_summary_template.pptx")
OUTPUT_DIR = os.path.join(MODULES_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

REVENUE_SLIDE_TITLE = "Exhibit 1: Annual Revenue"
YOY_SLIDE_TITLE = "Exhibit 2: YoY Growth"
TICKET_SLIDE_TITLE = "Exhibit 3: Average Ticket Size"
MARKET_SLIDE_TITLE = "Exhibit 4: Market Size"

def generate_revenue_chart(path, summaries):
    print("📈 Generating revenue chart from Supabase data")
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    if not trusted:
        print("⚠️ No trusted businesses found. Skipping chart generation.")
        return False

    trusted = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["annual_revenue"] for b in trusted]
    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(names, [v / 1_000_000 for v in values], color="#4CAF50")
    ax.axhline(mean_val / 1_000_000, color='blue', linestyle='--', label=f"Mean: ${mean_val / 1_000_000:.1f}M")
    ax.axhline(median_val / 1_000_000, color='purple', linestyle=':', label=f"Median: ${median_val / 1_000_000:.1f}M")
    ax.set_title("Annual Revenue")
    ax.set_ylabel("Revenue ($M)")
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    print(f"✅ Revenue chart saved to: {path}")
    return True

def generate_yoy_chart(path, summaries):
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("yoy_growth") is not None]
    if not trusted:
        return False
    trusted = sorted(trusted, key=lambda x: x["yoy_growth"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["yoy_growth"] * 100 for b in trusted]
    avg = sum(values) / len(values)
    median = sorted(values)[len(values) // 2]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(names, values, color=["green" if v >= 0 else "red" for v in values])
    ax.axhline(avg, color='blue', linestyle='--', label=f"Mean: {avg:.1f}%")
    ax.axhline(median, color='purple', linestyle=':', label=f"Median: {median:.1f}%")
    ax.set_title("YoY Growth")
    ax.set_ylabel("Growth (%)")
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True

def generate_ticket_chart(path, summaries):
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("ticket_size") is not None]
    if not trusted:
        return False
    trusted = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    names = [b["name"][:20] + ("..." if len(b["name"]) > 20 else "") for b in trusted]
    values = [b["ticket_size"] for b in trusted]
    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values) // 2]
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(names, values, color="#4CAF50")
    ax.axhline(mean_val, color='blue', linestyle='--', label=f"Mean: ${mean_val:.0f}")
    ax.axhline(median_val, color='purple', linestyle=':', label=f"Median: ${median_val:.0f}")
    ax.set_title("Ticket Size")
    ax.set_ylabel("Dollars ($)")
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True

def generate_market_size_chart(path, summaries):
    trusted = [b for b in summaries if b.get("benchmark") == "trusted" and b.get("annual_revenue") is not None]
    if not trusted:
        return False
    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    fig, ax = plt.subplots(figsize=(5, 5))
    bars = ax.bar(["Verified", "Projected"], [trusted_total / 1_000_000, projected_total / 1_000_000],
                  color=["#4CAF50", "#C0C0C0"], edgecolor="black")
    bars[1].set_hatch("//")
    ax.set_title("Estimated Market Size")
    ax.set_ylabel("Revenue ($M)")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return True

def generate_chart_slide(chart_title, image_path, summary_text):
    ppt = Presentation(EXHIBIT_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "Exhibit {TBD}" in shape.text:
            shape.text_frame.clear()
            run = shape.text_frame.paragraphs[0].add_run()
            run.text = chart_title
            run.font.name = "Montserrat"
            run.font.size = Pt(36)
        elif "{TBD ANALYSIS}" in shape.text:
            shape.text_frame.clear()
            run = shape.text_frame.paragraphs[0].add_run()
            run.text = summary_text
            run.font.name = "Arial"
            run.font.size = Pt(11)
    left = Inches(0.75)
    top = Inches(2.0)
    width = Inches(7.5)
    slide.shapes.add_picture(image_path, left, top, width=width)
    return ppt

def export_project_pptx(project_id: str, supabase):
    print(f"🚀 Starting export for project ID: {project_id}")
    download_all_templates()
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if not summaries:
        print("❌ No data found for this project.")
        return None
    project_output_dir = os.path.join(OUTPUT_DIR, project_id)
    os.makedirs(project_output_dir, exist_ok=True)
    def save_slide(title, chart_func, filename, summaries, summary_text):
        image_path = os.path.join(project_output_dir, filename.replace(".pptx", ".png"))
        if chart_func(image_path, summaries):
            ppt = generate_chart_slide(title, image_path, summary_text)
            ppt.save(os.path.join(project_output_dir, filename))
            print(f"✅ Saved {title} to: {filename}")
    end_date = datetime.now().strftime("%B %Y")
    # Slide 2: Revenue
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    sorted_rev = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    top_rev = ", ".join(f"{b['name']} (${b['annual_revenue']:,.0f})" for b in sorted_rev[:3])
    avg_rev = sum(b["annual_revenue"] for b in trusted) / len(trusted)
    med_rev = sorted(b["annual_revenue"] for b in trusted)[len(trusted) // 2]
    range_rev = max(b["annual_revenue"] for b in trusted) - min(b["annual_revenue"] for b in trusted)
    cluster_text = "tightly clustered" if range_rev < 0.2 * avg_rev else "widely spread"
    summary_revenue = (
        f"This chart shows the annual revenue over the past 12 months ending in {end_date}. "
        f"The top businesses include {top_rev}. The mean revenue is ${avg_rev:,.0f} and the median is ${med_rev:,.0f}. "
        f"The data is {cluster_text}, providing insight into competitive distribution."
    )
    save_slide(REVENUE_SLIDE_TITLE, generate_revenue_chart, "slide_2.pptx", summaries, summary_revenue)
    # Slide 3: YoY
    sorted_yoy = sorted([b for b in trusted if b.get("yoy_growth") is not None], key=lambda x: x["yoy_growth"], reverse=True)
    top_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[:3])
    bottom_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[-3:])
    avg_yoy = sum(b["yoy_growth"] for b in sorted_yoy) / len(sorted_yoy)
    med_yoy = sorted(b["yoy_growth"] for b in sorted_yoy)[len(sorted_yoy) // 2]
    summary_yoy = (
        f"This chart shows YoY revenue growth ending in {end_date}. Top gainers include {top_yoy}. "
        f"Largest declines: {bottom_yoy}. Average growth: {avg_yoy * 100:.1f}%, Median: {med_yoy * 100:.1f}%."
    )
    save_slide(YOY_SLIDE_TITLE, generate_yoy_chart, "slide_3.pptx", summaries, summary_yoy)
    # Slide 4: Ticket Size
    sorted_ticket = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    top_ticket = ", ".join(f"{b['name']} (${b['ticket_size']:,.0f})" for b in sorted_ticket[:3])
    avg_ticket = sum(b["ticket_size"] for b in sorted_ticket) / len(sorted_ticket)
    med_ticket = sorted(b["ticket_size"] for b in sorted_ticket)[len(sorted_ticket) // 2]
    summary_ticket = (
        f"This chart shows average ticket size across trusted businesses. Highest ticket sizes: {top_ticket}. "
        f"Mean: ${avg_ticket:,.0f}, Median: ${med_ticket:,.0f}."
    )
    save_slide(TICKET_SLIDE_TITLE, generate_ticket_chart, "slide_4.pptx", summaries, summary_ticket)
    # Slide 5: Market Size
    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    summary_market = (
        f"This chart shows estimated market size based on verified data (${trusted_total:,.0f}) and a projected multiplier of 1.5. "
        f"Projected market is ${projected_total:,.0f}."
    )
    save_slide(MARKET_SLIDE_TITLE, generate_market_size_chart, "slide_5.pptx", summaries, summary_market)
    # Slide 1: Title
    title_path = os.path.join(project_output_dir, "slide_1_title.pptx")
    if not os.path.exists(title_path):
        title_prs = Presentation(TITLE_TEMPLATE)
        title_prs.save(title_path)
        print(f"✅ Saved title slide to: {title_path}")
    print("✅ All slides generated and saved individually.")

if __name__ == "__main__":
    from dotenv import load_dotenv
    from supabase import create_client, Client
    load_dotenv()
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    export_project_pptx("5c36b37b-1530-43be-837a-8491d914dfc6", supabase)
