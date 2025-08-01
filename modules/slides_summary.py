# slides_summary.py

import subprocess
from pptx import Presentation
from pptx.util import Inches, Pt
from datetime import datetime

SUMMARY_TEMPLATE = "modules/downloaded_summary_template.pptx"


def generate_summary_slide(output_path, trusted, end_date, summary_stats, summary_analysis):
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
            run.text = "Exhibit 5: Market Overview"
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
            truncated_lines = summary_analysis.strip().split("\n")[:10]
            truncated_text = "\n".join(truncated_lines)
            shape.text_frame.paragraphs[0].add_run().text = truncated_text
        else:
            shape.text_frame.text = text

    ppt.save(output_path)


def generate_llama_summary(slide_summaries: dict, model_name: str = "llama3") -> str:
    prompt = f"""
You are a market research consultant. Based on the following summaries:

1. Revenue: {slide_summaries.get('revenue')}
2. YoY Growth: {slide_summaries.get('yoy')}
3. Ticket Size: {slide_summaries.get('ticket')}
4. Market Size: {slide_summaries.get('market')}

Write a short, professional summary about the market's attractiveness, notable patterns, standout businesses, and whether this is a good location to open a new business.
""".strip()

    result = subprocess.run(
        ["ollama", "run", model_name],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.decode("utf-8").strip()


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
