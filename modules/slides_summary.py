# slides_summary.py

import subprocess
from pptx import Presentation
from pptx.util import Inches, Pt

SUMMARY_TEMPLATE = "modules/downloaded_summary_template.pptx"


def generate_summary_slide(output_path, trusted, end_date, summary_stats, summary_analysis):
    ppt = Presentation(SUMMARY_TEMPLATE)
    slide = ppt.slides[0]
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "{TBD TITLE}" in shape.text:
            shape.text_frame.clear()
            shape.text_frame.paragraphs[0].add_run().text = "Exhibit 5: Market Overview"
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
