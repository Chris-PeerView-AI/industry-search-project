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

def generate_chart_slide(chart_title, image_path, summary_text):
    print(f"🔧 Generating chart slide: {chart_title}")
    exhibit_ppt = Presentation(EXHIBIT_TEMPLATE)
    slide = exhibit_ppt.slides[0]

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "{TBD TITLE}" in shape.text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = chart_title
            run.font.name = "Montserrat"
            run.font.size = Pt(36)
        elif "{TBD ANALYSIS}" in shape.text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_text
            run.font.name = "Arial"
            run.font.size = Pt(11)

    left = Inches(0.75)
    top = Inches(2.0)
    width = Inches(7.5)
    slide.shapes.add_picture(image_path, left, top, width=width)
    return exhibit_ppt

def export_project_pptx(project_id: str, supabase):
    print(f"🚀 Starting export for project ID: {project_id}")
    download_all_templates()

    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if not summaries:
        print("❌ No data found for this project.")
        return None

    image_path = os.path.join(MODULES_DIR, "slide_2_revenue_chart.png")
    if not generate_revenue_chart(image_path, summaries):
        return None

    trusted = sorted([b for b in summaries if b.get("benchmark") == "trusted"], key=lambda x: x["annual_revenue"], reverse=True)
    end_date = datetime.now().strftime("%B %Y")
    if trusted:
        top_biz = trusted[:3]
        top_list = ", ".join(f"{b['name']} (${b['annual_revenue']:,.0f})" for b in top_biz)
        avg = sum(b['annual_revenue'] for b in trusted) / len(trusted)
        median = sorted(b['annual_revenue'] for b in trusted)[len(trusted) // 2]
        cluster_range = max(b['annual_revenue'] for b in trusted) - min(b['annual_revenue'] for b in trusted)
        summary_text = (
            f"This chart shows the annual revenue over the past 12 months ending in {end_date}. "
            f"The top businesses include {top_list}. The mean revenue is ${avg:,.0f} and the median is ${median:,.0f}. "
            f"The data is {'tightly clustered' if cluster_range < 0.2 * avg else 'widely spread'}, providing insight into competitive distribution."
        )
    else:
        summary_text = "This chart shows the annual revenue for all trusted businesses."

    chart_title = "Exhibit 1: Annual Revenue"
    ppt = generate_chart_slide(chart_title, image_path, summary_text)
    project_output_dir = os.path.join(OUTPUT_DIR, project_id)
    os.makedirs(project_output_dir, exist_ok=True)

    slide_path = os.path.join(project_output_dir, "slide_2.pptx")
    ppt.save(slide_path)
    print(f"✅ Revenue slide saved to: {slide_path}")

    title_path = os.path.join(project_output_dir, "slide_1_title.pptx")
    if not os.path.exists(title_path):
        title_prs = Presentation(TITLE_TEMPLATE)
        title_prs.save(title_path)
        print(f"✅ Saved title slide to: {title_path}")

    return None

if __name__ == "__main__":
    from dotenv import load_dotenv
    from supabase import create_client, Client

    load_dotenv()
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

    export_project_pptx("5c36b37b-1530-43be-837a-8491d914dfc6", supabase)
