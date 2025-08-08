from pptx import Presentation
from datetime import datetime
import os

def _replace_placeholders_in_shape(shape, replacements):
    """Replace placeholders while preserving template formatting.
    We ONLY modify run.text where a placeholder appears and DO NOT touch
    alignment, font, autosize, or colors. This keeps the template's look.
    """
    if not shape.has_text_frame:
        return
    tf = shape.text_frame

    # Track if we successfully replaced anything at run-level
    replaced_any = False

    for p in tf.paragraphs:
        for run in p.runs:
            if not run.text:
                continue
            original = run.text
            new_text = original
            for k, v in replacements.items():
                if k in new_text:
                    new_text = new_text.replace(k, v)
            if new_text != original:
                run.text = new_text
                replaced_any = True

    # Fallback: if placeholders remained because they spanned runs (unlikely
    # with clean templates), do a minimal full-text replacement WITHOUT
    # resetting formatting: collect all text, replace, then reassign per first run.
    # NOTE: This is a last resort; best is to keep placeholders within a single shape/run.
    full_text = "".join(p.text for p in tf.paragraphs)
    if any(k in full_text for k in replacements.keys()) and not replaced_any:
        for k, v in replacements.items():
            full_text = full_text.replace(k, v)
        # Clear only the text content but keep paragraph objects; put text in first run
        # to avoid wiping formatting wholesale.
        if tf.paragraphs and tf.paragraphs[0].runs:
            # blank all runs
            for p in tf.paragraphs:
                for r in p.runs:
                    r.text = ""
            # put all text in first paragraph's first run
            tf.paragraphs[0].runs[0].text = full_text


def generate_title_slide(
    project_output_dir: str,
    template_path: str = "modules/downloaded_title_template.pptx",
    city: str = "City, ST",
    industry: str = "Industry",
    date_str: str | None = None,
    subtitle: str | None = None,
) -> str:
    if date_str is None:
        date_str = datetime.now().strftime("%B %Y")
    os.makedirs(project_output_dir, exist_ok=True)
    out_path = os.path.join(project_output_dir, "slide_1_title.pptx")
    prs = Presentation(template_path)
    slide = prs.slides[0]
    replacements = {
        "{TBD INDUSTRY}": industry,
        "{TBD LOCATION}": city,
        "{TBD DATE}": date_str,
    }
    if subtitle is not None:
        replacements["{TBD SUBTITLE}"] = subtitle
    for shape in slide.shapes:
        _replace_placeholders_in_shape(shape, replacements)
    prs.save(out_path)
    print(f"✅ Saved title slide to: {out_path}")
    return out_path
