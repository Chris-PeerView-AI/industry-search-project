# generate_project_report.py (main entrypoint)

import os
import shutil
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from modules.slides_exhibit import (
    generate_chart_slide,
    generate_revenue_chart,
    generate_yoy_chart,
    generate_ticket_chart,
    generate_market_size_chart,
    generate_map_chart
)
from modules.slides_summary import generate_summary_slide, generate_llama_summary, get_latest_period_end, generate_paginated_business_table_slides
from modules.slides_admin import generate_title_slide
from modules.convert_slides_to_pdf import convert_and_merge_slides
from pptx import Presentation

# Constants
REVENUE_SLIDE_TITLE = "Exhibit 1: Annual Revenue"
YOY_SLIDE_TITLE = "Exhibit 2: YoY Growth"
TICKET_SLIDE_TITLE = "Exhibit 3: Average Ticket Size"
MARKET_SLIDE_TITLE = "Exhibit 4: Market Size"
MAP_SLIDE_TITLE = "Exhibit 5: Benchmark Map"
SUMMARY_SLIDE_TITLE = "Exhibit 6: Market Overview"
TITLE_TEMPLATE = "modules/downloaded_title_template.pptx"
INTRO_TEMPLATE = "modules/downloaded_intro_template.pptx"
EXHIBIT_INTRO_TEMPLATE = "modules/downloaded_exhibit_intro_template.pptx"
APPENDIX_INTRO_TEMPLATE = "modules/downloaded_appendix_intro_template.pptx"
DISCLOSURES_TEMPLATE = "modules/downloaded_disclosures_template.pptx"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# Load environment
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3")
os.makedirs(OUTPUT_DIR, exist_ok=True)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def copy_template_slides(template_path, output_path_prefix, start_slide_num):
    output_path = f"{output_path_prefix}_{start_slide_num}.pptx"
    shutil.copy(template_path, output_path)
    print(f"✅ Copied template file to: {output_path}")

def export_project_pptx(project_id: str, supabase):
    print(f"🚀 Starting export for project ID: {project_id}")

    # Clean output directory for this project
    project_output_dir = os.path.join(OUTPUT_DIR, project_id)
    if os.path.exists(project_output_dir):
        shutil.rmtree(project_output_dir)
    os.makedirs(project_output_dir, exist_ok=True)

    from modules.download_templates import download_all_templates
    download_all_templates()

    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    print(f"📊 {len(summaries)} rows found in enigma_summaries.")
    if not summaries:
        print("❌ No data found for this project.")
        return

    # Fetch project metadata (industry and city)
    project_meta = (
        supabase.table("search_projects")
        .select("industry, location")
        .eq("id", project_id)
        .single()
        .execute()
        .data
    )

    if not project_meta:
        print("❌ Project metadata not found.")
        return

    industry = project_meta.get("industry", "Industry")
    city = project_meta.get("location", "City")

    # Title Slide
    title_path = generate_title_slide(
        project_output_dir=project_output_dir,
        template_path=TITLE_TEMPLATE,
        city=city,
        industry=industry,
        add_cover_art=True  # enables the auto-generated cover image
        # subtitle="Custom benchmark and market intelligence using trusted third‑party data.",  # optional
    )

    # Intro Slides (Slide 10+)
    copy_template_slides(INTRO_TEMPLATE, os.path.join(project_output_dir, "slide_10_intro"), 0)

    end_date = get_latest_period_end(supabase, project_id)
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    slide_summaries = {}

    # Exhibit Intro (Slide 20)
    copy_template_slides(EXHIBIT_INTRO_TEMPLATE, os.path.join(project_output_dir, "slide_20_exhibit_intro"), 0)

    def save_slide(title, chart_func, filename, summaries, summary_text):
        image_path = os.path.join(project_output_dir, filename.replace(".pptx", ".png"))
        if chart_func(image_path, summaries):
            ppt = generate_chart_slide(title, image_path, summary_text)
            ppt.save(os.path.join(project_output_dir, filename))
            print(f"✅ Saved {title} to: {filename}")

    # Revenue
    sorted_rev = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    top_rev = ", ".join(f"{b['name']} (${b['annual_revenue']:,.0f})" for b in sorted_rev[:3])
    avg_rev = sum(b["annual_revenue"] for b in trusted) / len(trusted)
    med_rev = sorted(b["annual_revenue"] for b in trusted)[len(trusted) // 2]
    range_rev = max(b["annual_revenue"] for b in trusted) - min(b["annual_revenue"] for b in trusted)
    cluster_text = "tightly clustered" if range_rev < 0.2 * avg_rev else "widely spread"
    summary_revenue = f"Top: {top_rev}. Mean: ${avg_rev:,.0f}, Median: ${med_rev:,.0f}. Distribution: {cluster_text}."
    slide_summaries["revenue"] = summary_revenue
    save_slide(REVENUE_SLIDE_TITLE, lambda path, summaries: generate_revenue_chart(path, summaries, end_date), "slide_21_revenue.pptx", summaries, summary_revenue)


    # YoY Growth
    sorted_yoy = sorted([b for b in trusted if b.get("yoy_growth") is not None], key=lambda x: x["yoy_growth"], reverse=True)
    top_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[:3])
    bottom_yoy = ", ".join(f"{b['name']} ({b['yoy_growth'] * 100:.1f}%)" for b in sorted_yoy[-3:])
    avg_yoy = sum(b["yoy_growth"] for b in sorted_yoy) / len(sorted_yoy)
    med_yoy = sorted(b["yoy_growth"] for b in sorted_yoy)[len(sorted_yoy) // 2]
    summary_yoy = f"Top growth: {top_yoy}. Declines: {bottom_yoy}. Avg: {avg_yoy * 100:.1f}%, Median: {med_yoy * 100:.1f}%."
    slide_summaries["yoy"] = summary_yoy
    save_slide(YOY_SLIDE_TITLE, lambda path, summaries: generate_yoy_chart(path, summaries, end_date), "slide_22_yoy.pptx", summaries, summary_yoy)


    # Ticket Size
    sorted_ticket = sorted(trusted, key=lambda x: x["ticket_size"], reverse=True)
    top_ticket = ", ".join(f"{b['name']} (${b['ticket_size']:,.0f})" for b in sorted_ticket[:3])
    avg_ticket = sum(b["ticket_size"] for b in sorted_ticket) / len(sorted_ticket)
    med_ticket = sorted(b["ticket_size"] for b in sorted_ticket)[len(sorted_ticket) // 2]
    summary_ticket = f"Top prices: {top_ticket}. Mean: ${avg_ticket:,.0f}, Median: ${med_ticket:,.0f}."
    slide_summaries["ticket"] = summary_ticket
    save_slide(
        TICKET_SLIDE_TITLE,
        lambda path, summaries: generate_ticket_chart(path, summaries, end_date),
        "slide_23_ticket.pptx",
        summaries,
        summary_ticket
    )

    # Market Size
    trusted_total = sum(b["annual_revenue"] for b in trusted)
    projected_total = trusted_total * 1.5
    from modules.slides_summary import get_market_size_analysis
    summary_market = get_market_size_analysis()
    slide_summaries["market"] = summary_market
    save_slide(
        MARKET_SLIDE_TITLE,
        lambda path, summaries: generate_market_size_chart(path, summaries, end_date),
        "slide_24_market_size.pptx",
        summaries,
        summary_market
    )

    # Map
    summary_map = f"Map of benchmark businesses around {city}, including trusted (green) and untrusted (gray) businesses."
    slide_summaries["map"] = summary_map
    save_slide(MAP_SLIDE_TITLE, generate_map_chart, "slide_25_map.pptx", summaries, summary_map)

    # Summary statistics (must follow calculated values)
    summary_stats = {
        "total": len(summaries),
        "trusted": len(trusted),
        "mean_revenue": avg_rev,
        "median_revenue": med_rev,
        "avg_ticket": avg_ticket,
        "mean_yoy": avg_yoy * 100
    }

    # ✅ Now generate summary slide after all exhibit summaries are complete
    summary_analysis = generate_llama_summary(slide_summaries, model_name=LLM_MODEL)
    summary_analysis = summary_analysis.replace("Pet Industry in [Location]", f"{industry} in {city}")
    summary_path = os.path.join(project_output_dir, "slide_11_market_summary.pptx")
    generate_summary_slide(summary_path, trusted, end_date, summary_stats, summary_analysis, city, industry, map_image_path=os.path.join(project_output_dir, "slide_25_map.png"))

    # Appendix Intro
    copy_template_slides(APPENDIX_INTRO_TEMPLATE, os.path.join(project_output_dir, "slide_40_appendix_intro"), 0)

    # Appendix Slides
    print("📎 Starting appendix slide generation...")
    appendix_dir = os.path.join(project_output_dir)
    os.makedirs(appendix_dir, exist_ok=True)

    # Gather tier reasons
    trusted_ids = [b["id"] for b in trusted]
    summaries_by_id = {b["id"]: b for b in summaries}
    search_ids = [b["search_result_id"] for b in trusted if b.get("search_result_id")]
    print(f"🔍 Trusted businesses with search_results_id: {len(search_ids)}")

    search_rows = supabase.table("search_results").select("id, tier_reason").in_("id", search_ids).execute().data
    tier_lookup = {r["id"]: r["tier_reason"] for r in search_rows}
    print(f"📝 Retrieved {len(tier_lookup)} tier_reason entries")

    # Add tier_reason to each business
    for b in trusted:
        sid = b.get("search_result_id")
        if sid and sid in tier_lookup:
            b["tier_reason"] = tier_lookup[sid]

    # Generate new business table slide
    from modules.slides_summary import generate_paginated_business_table_slides
    table_slide_path = os.path.join(appendix_dir, "slide_41_BusinessTable.pptx")

    sorted_trusted = sorted(trusted, key=lambda b: b.get("name", "").lower())

    generate_paginated_business_table_slides(
        output_dir=project_output_dir,
        businesses=sorted_trusted,
        base_title=f"{city}: {industry} Benchmark Businesses"
    )

    print(f"✅ Business summary table slide generated and added to appendix.")

    # Disclosures (Slide 999+)
    copy_template_slides(DISCLOSURES_TEMPLATE, os.path.join(project_output_dir, "slide_999_disclosures"), 0)

    # Convert and Merge PDF
    pdf_path = convert_and_merge_slides(project_output_dir, industry, city)
    print(f"💎 Final PDF report saved to {pdf_path}")

if __name__ == "__main__":
    export_project_pptx("5c36b37b-1530-43be-837a-8491d914dfc6", supabase)
