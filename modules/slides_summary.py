# slides_summary.py

import os

import subprocess
from pptx import Presentation
from pptx.util import Inches, Pt
from datetime import datetime

SUMMARY_TEMPLATE = "modules/downloaded_summary_template.pptx"

def generate_summary_slide(output_path, trusted, end_date, summary_stats, summary_analysis, city="", industry="", map_image_path=None):
    ppt = Presentation(SUMMARY_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue

        text = shape.text_frame.text
        if "{TBD TITLE}" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = f"{city}: {industry}".strip()
            run.font.name = "Montserrat"
            run.font.size = Pt(30)
            run.font.bold = True
            continue

        replacements = {
            "{TBD AS OF DATE}": end_date,
            "{TBD TOTAL BUSINESSES}": str(summary_stats.get("total", "-")),
            "{TBD TRUSTED BUSINESSES}": str(summary_stats.get("trusted", "-")),
            "{TBD: MEAN REVENUE}": f"${summary_stats.get('mean_revenue', 0):,.0f}",
            "{TBD YOY GROWTH}": f"{summary_stats.get('mean_yoy', 0):.1f}%",
            "{TBD MEDIAN REVENUE}": f"${summary_stats.get('median_revenue', 0):,.0f}",
            "{TBD MEDIAM REVENUE}": f"${summary_stats.get('median_revenue', 0):,.0f}",
            "{TBD AVERAGE TICKET SIZE}": f"${summary_stats.get('avg_ticket', 0):,.0f}",
            "{TBD MEDIAN TICKET}": f"${summary_stats.get('median_ticket', 0):,.0f}",
        }

        for key, val in replacements.items():
            if key in text:
                text = text.replace(key, val)

        if "{TBD SUMMARY ANALYSIS}" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_analysis.strip()
            run.font.size = Pt(7)
        else:
            shape.text_frame.text = text

    # Add map image if provided
    if map_image_path and os.path.exists(map_image_path):
        from PIL import Image
        img = Image.open(map_image_path)
        img_width, img_height = img.size
        max_width_inches = 4.0
        max_height_inches = 3.5
        width_ratio = max_width_inches * 96 / img_width
        height_ratio = max_height_inches * 96 / img_height
        scale = min(width_ratio, height_ratio)
        final_width = Inches(img_width * scale / 96)
        final_height = Inches(img_height * scale / 96)
        left = Inches(4.0)
        top = Inches(2.25)
        slide.shapes.add_picture(
            map_image_path,
            left=left,
            top=top,
            width=final_width,
            height=final_height
        )

    ppt.save(output_path)
    print(f"✅ Saved summary slide to: {output_path}")

def generate_llama_summary(slide_summaries: dict, model_name: str = "llama3") -> str:
    sentiment = "neutral"
    try:
        mean_yoy = float(slide_summaries.get("yoy", "0").split("Avg:")[-1].split("%")[0])
        if mean_yoy > 10:
            sentiment = "positive"
        elif mean_yoy < 0:
            sentiment = "negative"
    except:
        pass

    prompt = f"""
You are a market research consultant. Based on the following summaries:

1. Revenue: {slide_summaries.get('revenue')}
2. YoY Growth: {slide_summaries.get('yoy')}
3. Ticket Size: {slide_summaries.get('ticket')}
4. Market Size: {slide_summaries.get('market')}

Write a professional, approximately 700-word summary about the market's attractiveness, notable patterns, standout businesses, and whether this is a good location to open a new business.
The tone should be {sentiment}. If growth is above 10%, highlight strong momentum. If it's below 0%, note concerning trends. If in-between, maintain a balanced tone.
""".strip()

    result = subprocess.run(
        ["ollama", "run", model_name],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8").strip()

def get_market_size_analysis():
    return (
        "This chart compares the total verified revenue from businesses with high-quality data to an estimate "
        "for the full local market. The upper bound assumes that businesses without usable data perform similarly "
        "to those with data, which likely overstates true market size. Poor data quality is often associated with smaller "
        "businesses or those facing operational challenges."
    )

def generate_appendix_slide(output_path, business: dict, template_path="modules/downloaded_summary_template.pptx"):
    print(f"🧩 Generating appendix slide for: {business.get('name')} (enigma_id: {business.get('enigma_id')})")

    required_fields = ["annual_revenue", "ticket_size", "yoy_growth"]
    for field in required_fields:
        if business.get(field) is None:
            print(f"⚠️  Missing field {field} for business {business.get('name')}, skipping.")
            return
    from pptx import Presentation
    from pptx.util import Inches, Pt

    ppt = Presentation(template_path)
    slide = ppt.slides[0]

    name = business.get("name", "Business")
    city = business.get("city", "")
    state = business.get("state", "")
    revenue = business.get("annual_revenue")
    yoy = business.get("yoy_growth")
    ticket = business.get("ticket_size")
    tier_reason = business.get("tier_reason", "")

    # Add content
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue

        text = shape.text_frame.text
        if "{TBD TITLE}" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = f"{name} ({city}, {state})"
            run.font.size = Pt(26)
            run.font.bold = True
            continue

        if "{TBD SUMMARY ANALYSIS}" in text:
            summary_text = f"This business had estimated revenue of ${revenue:,.0f}, with an average ticket size of ${ticket:,.0f} and year-over-year growth of {yoy*100:.1f}%."
            if tier_reason:
                summary_text += f" Reason for inclusion: {tier_reason.strip()}"
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_text
            run.font.size = Pt(7)
        else:
            for key in ["{TBD AS OF DATE}", "{TBD TOTAL BUSINESSES}", "{TBD TRUSTED BUSINESSES}", "{TBD: MEAN REVENUE}"]:
                text = text.replace(key, "")
            shape.text_frame.text = text

    ppt.save(output_path)

def get_latest_period_end(supabase: object, project_id: str) -> str:
    resp = (
        supabase.table("enigma_metrics")
        .select("period_end_date")
        .eq("project_id", project_id)
        .order("period_end_date", desc=True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return datetime.strptime(resp.data[0]["period_end_date"], "%Y-%m-%d").strftime("%B %Y")
    return datetime.now().strftime("%B %Y")
