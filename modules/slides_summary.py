# slides_summary.py


import os
import subprocess
from pptx import Presentation
from pptx.util import Inches, Pt
from datetime import datetime
from pptx.enum.text import PP_ALIGN
from PIL import Image, ImageDraw
from io import BytesIO

SUMMARY_TEMPLATE = "modules/downloaded_summary_template.pptx"
INDIVIDUAL_TEMPLATE = "modules/downloaded_businessview_template.pptx"

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
            shape.text_frame.text = text  # Ensure replaced text is assigned back

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


def generate_individual_business_slide(output_path, business: dict, end_date: str, industry: str, city: str):
    print(f"🧩 Generating business slide for: {business.get('name')}")

    ppt = Presentation(INDIVIDUAL_TEMPLATE)
    slide = ppt.slides[0]

    replacements = {
        "{TBD TITLE}": business.get("name", "Business"),
        "{TBD AS OF DATE}": end_date,
        "{TBD ADDRESS}": f"{business.get('address', '')}, {business.get('city', '')}, {business.get('state', '')}",
        "{TBD: MEAN REVENUE}": f"${business.get('annual_revenue', 0):,.0f}",
        "{TBD YOY GROWTH}": f"{business.get('yoy_growth', 0)*100:.1f}%",
        "{TBD AVERAGE TICKET SIZE}": f"${business.get('ticket_size', 0):,.0f}",
    }

    summary_text = (
        f"This business had estimated revenue of ${business.get('annual_revenue', 0):,.0f}, with an average ticket size of ${business.get('ticket_size', 0):,.0f} "
        f"and year-over-year growth of {business.get('yoy_growth', 0)*100:.1f}%."
    )
    if business.get("tier_reason"):
        summary_text += f" Reason for inclusion: {business['tier_reason'].strip()}"

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue

        text = shape.text_frame.text
        for key, val in replacements.items():
            if key in text:
                text = text.replace(key, val)

        if "{TBD SUMMARY ANALYSIS}" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_text
            run.font.size = Pt(7)
        else:
            shape.text_frame.text = text
            for p in shape.text_frame.paragraphs:
                p.alignment = PP_ALIGN.LEFT

    # Draw map pin overlay if lat/lon is available
    if business.get("latitude") and business.get("longitude"):
        try:
            map_path = f"modules/output_map_with_pin_{business['id']}.png"
            base_map = Image.open(f"modules/output/{business['project_id']}/slide_25_map.png").convert("RGBA")
            draw = ImageDraw.Draw(base_map)
            x, y = business['map_x'], business['map_y']  # assuming preprojected pixel coords
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill="gold", outline="black")
            base_map.save(map_path)

            slide.shapes.add_picture(map_path, Inches(4.0), Inches(2.25), width=Inches(4.0), height=Inches(3.5))
        except Exception as e:
            print(f"⚠️ Map rendering error: {e}")

    ppt.save(output_path)
    print(f"✅ Saved individual business slide: {output_path}")


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
