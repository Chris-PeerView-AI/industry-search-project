import os
import subprocess
from datetime import datetime
from pptx import Presentation
from pptx.util import Inches, Pt
from dotenv import load_dotenv
from supabase import create_client, Client
import matplotlib.pyplot as plt
from PIL import Image

# Constants
REVENUE_SLIDE_TITLE = "Exhibit 1: Annual Revenue"
YOY_SLIDE_TITLE = "Exhibit 2: YoY Growth"
TICKET_SLIDE_TITLE = "Exhibit 3: Average Ticket Size"
MARKET_SLIDE_TITLE = "Exhibit 4: Market Size"
SUMMARY_SLIDE_TITLE = "Exhibit 5: Market Overview"
TITLE_TEMPLATE = "modules/downloaded_title_template.pptx"
EXHIBIT_TEMPLATE = "modules/downloaded_exhibit_template.pptx"
SUMMARY_TEMPLATE = "modules/downloaded_summary_template.pptx"

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Utility Functions
def generate_chart_slide(chart_title, image_path, summary_text):
    ppt = Presentation(EXHIBIT_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "Exhibit {TBD}" in shape.text:
            shape.text_frame.clear()
            shape.text_frame.paragraphs[0].add_run().text = chart_title
        elif "{TBD ANALYSIS}" in shape.text:
            shape.text_frame.clear()
            shape.text_frame.paragraphs[0].add_run().text = summary_text

    img = Image.open(image_path)
    width = Inches(7.5)
    top = Inches(2.0)
    left = Inches((10 - 7.5) / 2)
    slide.shapes.add_picture(image_path, left, top, width=width)
    return ppt

def generate_summary_slide(output_path, trusted, end_date, summary_stats, summary_analysis):
    ppt = Presentation(SUMMARY_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "{TBD TITLE}" in shape.text:
            shape.text_frame.clear()
            shape.text_frame.paragraphs[0].add_run().text = SUMMARY_SLIDE_TITLE
        elif "{TBD AS OF DATE}" in shape.text:
            shape.text = shape.text.replace("{TBD AS OF DATE}", end_date)
        elif "{TBD TOTAL BUSINESSES}" in shape.text:
            shape.text = shape.text.replace("{TBD TOTAL BUSINESSES}", str(summary_stats["total"]))
        elif "{TBD TRUSTED BUSINESSES}" in shape.text:
            shape.text = shape.text.replace("{TBD TRUSTED BUSINESSES}", str(summary_stats["trusted"]))
        elif "{TBD: MEAN REVENUE}" in shape.text:
            shape.text = shape.text.replace("{TBD: MEAN REVENUE}", f"${summary_stats['mean_revenue']:,.0f}")
        elif "{TBD YOY GROWTH}" in shape.text:
            shape.text = shape.text.replace("{TBD YOY GROWTH}", f"{summary_stats['mean_yoy']:.1f}%")
        elif "{TBD MEDIAM REVENUE}" in shape.text:
            shape.text = shape.text.replace("{TBD MEDIAM REVENUE}", f"${summary_stats['median_revenue']:,.0f}")
        elif "{TBD AVERAGE TICKET SIZE}" in shape.text:
            shape.text = shape.text.replace("{TBD AVERAGE TICKET SIZE}", f"${summary_stats['avg_ticket']:,.0f}")
        elif "{TBD SUMMARY ANALYSIS}" in shape.text:
            shape.text_frame.clear()
            shape.text_frame.paragraphs[0].add_run().text = summary_analysis
    ppt.save(output_path)

def generate_llama_summary(slide_summaries: dict) -> str:
    prompt = f"""
You are a market research consultant. Based on the following summaries:

1. Revenue: {slide_summaries.get('revenue')}
2. YoY Growth: {slide_summaries.get('yoy')}
3. Ticket Size: {slide_summaries.get('ticket')}
4. Market Size: {slide_summaries.get('market')}

Write a short, professional summary about the market's attractiveness, notable patterns, standout businesses, and whether this is a good location to open a new business.
""".strip()

    result = subprocess.run(
        ["ollama", "run", LLM_MODEL],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8").strip()

def export_project_pptx(project_id: str, supabase):
    print(f"🚀 Starting export for project ID: {project_id}")
    from test_01_download_templates import download_all_templates
    from generate_charts import generate_revenue_chart, generate_yoy_chart, generate_ticket_chart, generate_market_size_chart

    download_all_templates()
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if not summaries:
        print("❌ No data found for this project.")
        return

    project_output_dir = os.path.join(OUTPUT_DIR, project_id)
    os.makedirs(project_output_dir, exist_ok=True)
    def save_slide(title, chart_func, filename, summaries, summary_text):
        image_path = os.path.join(project_output_dir, filename.replace(".pptx", ".png"))
        if chart_func(image_path, summaries):
            ppt = generate_chart_slide(title, image_path, summary_text)
            ppt.save(os.path.join(project_output_dir, filename))
            print(f"✅ Saved {title} to: {filename}")

    end_date = datetime.now().strftime("%B %Y")
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    slide_summaries = {}

    sorted_rev = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    top_rev = ", ".join(f"{b['name']} (${b['annual_revenue']:,.0f})" for b in sorted_rev[:3])
    avg_rev = sum(b["annual_revenue"] for b in trusted) / len(trusted)
    med_rev = sorted(b["annual_revenue"] for b in trusted)[len(trusted) // 2]
    range_rev = max(b["annual_revenue"] for b in trusted) - min(b["annual_revenue"] for b in trusted)
    cluster_text = "tightly clustered" if range_rev < 0.2 * avg_rev else "widely spread"
    summary_revenue = f"Top: {top_rev}. Mean: ${avg_rev:,.0f}, Median: ${med_rev:,.0f}. Distribution: {cluster_text}."
    slide_summaries["revenue"] = summary_revenue
    save_slide(REVENUE_SLIDE_TITLE, generate_revenue_chart, "slide_2.pptx", summaries, summary_revenue)

    sorted_yoy = sorted([b for b in trusted if b.get("yoy_growth") is not None], key=lambda x: x["yoy_growth"], reverse=True)
    top_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[:3])
    bottom_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[-3:])
    avg_yoy = sum(b["yoy_growth"] for b in sorted_yoy) / len(sorted_yoy)
    med_yoy = sorted(b["yoy_growth"] for b in sorted_yoy)[len(sorted_yoy) // 2]
    summary_yoy = f"Top growth: {top_yoy}. Declines: {bottom_yoy}. Avg: {avg_yoy*100:.1f}%, Median: {med_yoy*100:.1f}%."
    slide_summaries["yoy"] = summary_yoy
    save_slide(YOY_SLIDE_TITLE, generate_yoy_chart, "slide_3.pptx", summaries, summary_yoy)

    sorted_ticket = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    top_ticket = ", ".join(f"{b['name']} (${b['ticket_size']:,.0f})" for b in sorted_ticket[:3])
    avg_ticket = sum(b["ticket_size"] for b in sorted_ticket) / len(sorted_ticket)
    med_ticket = sorted(b["ticket_size"] for b in sorted_ticket)[len(sorted_ticket) // 2]
    summary_ticket = f"Top prices: {top_ticket}. Mean: ${avg_ticket:,.0f}, Median: ${med_ticket:,.0f}."
    slide_summaries["ticket"] = summary_ticket
    save_slide(TICKET_SLIDE_TITLE, generate_ticket_chart, "slide_4.pptx", summaries, summary_ticket)

    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    summary_market = f"Verified revenue: ${trusted_total:,.0f}, Projected market: ${projected_total:,.0f} (1.5x)."
    slide_summaries["market"] = summary_market
    save_slide(MARKET_SLIDE_TITLE, generate_market_size_chart, "slide_5.pptx", summaries, summary_market)

    title_path = os.path.join(project_output_dir, "slide_1_title.pptx")
    if not os.path.exists(title_path):
        title_prs = Presentation(TITLE_TEMPLATE)
        title_prs.save(title_path)
        print(f"✅ Saved title slide to: {title_path}")

    summary_analysis = generate_llama_summary(slide_summaries)
    summary_stats = {
        "total": len(summaries),
        "trusted": len(trusted),
        "mean_revenue": avg_rev,
        "median_revenue": med_rev,
        "avg_ticket": avg_ticket,
        "mean_yoy": avg_yoy * 100
    }
    summary_path = os.path.join(project_output_dir, "slide_6_summary.pptx")
    generate_summary_slide(summary_path, trusted, end_date, summary_stats, summary_analysis)
    print("✅ All slides including summary slide generated.")

if __name__ == "__main__":
    export_project_pptx("5c36b37b-1530-43be-837a-8491d914dfc6", supabase)
