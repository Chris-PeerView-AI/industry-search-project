# modules/pdf_only_export.py

import os
from modules.convert_slides_to_pdf import convert_and_merge_slides
from supabase import Client

def generate_final_pdf(project_id: str, industry: str, city: str) -> str:
    """
    Converts all PPTX slides in the project output folder to a single final PDF.
    """
    output_dir = os.path.join("modules", "output", project_id)
    if not os.path.exists(output_dir):
        raise FileNotFoundError(f"Output folder for project {project_id} does not exist.")

    pdf_path = convert_and_merge_slides(output_dir, industry, city)
    print(f"✅ Final PDF created at: {pdf_path}")
    return pdf_path

def get_project_meta(project_id: str, supabase: Client) -> dict:
    """
    Returns metadata for a given project, including industry and city.
    """
    result = (
        supabase.table("search_projects")
        .select("industry, location")
        .eq("id", project_id)
        .single()
        .execute()
    )
    return result.data or {}
