# slides_admin.py

from pptx import Presentation
import os

def generate_title_slide_if_needed(project_output_dir: str, title_template_path: str) -> None:
    title_path = os.path.join(project_output_dir, "slide_1_title.pptx")
    if not os.path.exists(title_path):
        title_prs = Presentation(title_template_path)
        title_prs.save(title_path)
        print(f"✅ Saved title slide to: {title_path}")
