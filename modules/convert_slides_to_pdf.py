# convert_slides_to_pdf.py

import os
import subprocess
from PyPDF2 import PdfMerger
from datetime import datetime


def pptx_to_pdf_libreoffice(pptx_path: str, pdf_path: str):
    try:
        subprocess.run([
            "libreoffice", "--headless", "--convert-to", "pdf", pptx_path, "--outdir", os.path.dirname(pdf_path)
        ], check=True)
    except FileNotFoundError:
        raise RuntimeError("❌ LibreOffice CLI not found. Make sure 'libreoffice' is in your PATH.")


def convert_all_slides_to_pdf(project_output_dir: str):
    pdf_paths = []

    # Delete old PDFs
    for file in os.listdir(project_output_dir):
        if file.endswith(".pdf"):
            os.remove(os.path.join(project_output_dir, file))

    for i in range(1, 7):
        pptx_filename = f"slide_{i}_summary.pptx" if i == 6 else f"slide_{i}.pptx"
        pptx_path = os.path.join(project_output_dir, pptx_filename)
        pdf_path = pptx_path.replace(".pptx", ".pdf")
        if os.path.exists(pptx_path):
            pptx_to_pdf_libreoffice(pptx_path, pdf_path)
            pdf_paths.append(pdf_path)
            print(f"✅ Converted {pptx_filename} to PDF")
        else:
            print(f"⚠️ Missing: {pptx_filename}")
    return pdf_paths


def merge_pdfs(pdf_paths: list, output_pdf: str):
    merger = PdfMerger()
    for path in pdf_paths:
        merger.append(path)
    merger.write(output_pdf)
    merger.close()
    print(f"📄 Merged PDF created at {output_pdf}")


def convert_and_merge_slides(project_output_dir: str, industry: str, city: str):
    pdfs = convert_all_slides_to_pdf(project_output_dir)
    month_year = datetime.now().strftime("%B%Y")
    name_part = f"{month_year}_{industry}_{city}".replace(" ", "").replace("/", "-")
    output_path = os.path.join(project_output_dir, f"{name_part}.pdf")
    merge_pdfs(pdfs, output_path)
    return output_path
