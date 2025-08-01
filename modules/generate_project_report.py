# generate_project_report.py (main entrypoint)

import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from slides_admin import generate_title_slide_if_needed
from slides_exhibit import (
    generate_chart_slide,
    generate_revenue_chart,
    generate_yoy_chart,
    generate_ticket_chart,
    generate_market_size_chart,
)
from slides_summary import generate_summary_slide, generate_llama_summary, get_latest_period_end
from convert_slides_to_pdf import convert_and_merge_slides

# Constants
REVENUE_SLIDE_TITLE = "Exhibit 1: Annual Revenue"
YOY_SLIDE_TITLE = "Exhibit 2: YoY Growth"
TICKET_SLIDE_TITLE = "Exhibit 3: Average Ticket Size"
MARKET_SLIDE_TITLE = "Exhibit 4: Market Size"
SUMMARY_SLIDE_TITLE = "Exhibit 5: Market Overview"
TITLE_TEMPLATE = "modules/downloaded_title_template.pptx"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
os.makedirs(OUTPUT_DIR, exist_ok=True)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def export_project_pptx(project_id: str, supabase):
    print(f"🚀 Starting export for project ID: {project_id}")
    from test_01_download_templates import download_all_templates
    download_all_templates()

    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    print(f"📊 {len(summaries)} rows found in enigma_summaries.")
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

    end_date = get_latest_period_end(supabase, project_id)
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    slide_summaries = {}

    industry = summaries[0].get("industry", "Industry")
    city = summaries[0].get("city", "City")

    # Revenue
    sorted_rev = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    top_rev = ", ".join(f"{b['name']} (${b['annual_revenue']:,.0f})" for b in sorted_rev[:3])
    avg_rev = sum(b["annual_revenue"] for b in trusted) / len(trusted)
    med_rev = sorted(b["annual_revenue"] for b in trusted)[len(trusted) // 2]
    range_rev = max(b["annual_revenue"] for b in trusted) - min(b["annual_revenue"] for b in trusted)
    cluster_text = "tightly clustered" if range_rev < 0.2 * avg_rev else "widely spread"
    summary_revenue = f"Top: {top_rev}. Mean: ${avg_rev:,.0f}, Median: ${med_rev:,.0f}. Distribution: {cluster_text}."
    slide_summaries["revenue"] = summary_revenue
    save_slide(REVENUE_SLIDE_TITLE, generate_revenue_chart, "slide_2.pptx", summaries, summary_revenue)

    # YoY Growth
    sorted_yoy = sorted([b for b in trusted if b.get("yoy_growth") is not None], key=lambda x: x["yoy_growth"], reverse=True)
    top_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[:3])
    bottom_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[-3:])
    avg_yoy = sum(b["yoy_growth"] for b in sorted_yoy) / len(sorted_yoy)
    med_yoy = sorted(b["yoy_growth"] for b in sorted_yoy)[len(sorted_yoy) // 2]
    summary_yoy = f"Top growth: {top_yoy}. Declines: {bottom_yoy}. Avg: {avg_yoy*100:.1f}%, Median: {med_yoy*100:.1f}%."
    slide_summaries["yoy"] = summary_yoy
    save_slide(YOY_SLIDE_TITLE, generate_yoy_chart, "slide_3.pptx", summaries, summary_yoy)

    # Ticket Size
    sorted_ticket = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    top_ticket = ", ".join(f"{b['name']} (${b['ticket_size']:,.0f})" for b in sorted_ticket[:3])
    avg_ticket = sum(b["ticket_size"] for b in sorted_ticket) / len(sorted_ticket)
    med_ticket = sorted(b["ticket_size"] for b in sorted_ticket)[len(sorted_ticket) // 2]
    summary_ticket = f"Top prices: {top_ticket}. Mean: ${avg_ticket:,.0f}, Median: ${med_ticket:,.0f}."
    slide_summaries["ticket"] = summary_ticket
    save_slide(TICKET_SLIDE_TITLE, generate_ticket_chart, "slide_4.pptx", summaries, summary_ticket)

    # Market Size
    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    from slides_summary import get_market_size_analysis
    summary_market = get_market_size_analysis()
    slide_summaries["market"] = summary_market
    save_slide(MARKET_SLIDE_TITLE, generate_market_size_chart, "slide_5.pptx", summaries, summary_market)

    # Title Slide
    generate_title_slide_if_needed(project_output_dir, TITLE_TEMPLATE)

    # Summary Slide
    summary_analysis = generate_llama_summary(slide_summaries, model_name=LLM_MODEL)
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

    # Convert and Merge PDF
    pdf_path = convert_and_merge_slides(project_output_dir, industry, city)
    print(f"📎 Final PDF report saved to {pdf_path}")

if __name__ == "__main__":
    export_project_pptx("5c36b37b-1530-43be-837a-8491d914dfc6", supabase)
