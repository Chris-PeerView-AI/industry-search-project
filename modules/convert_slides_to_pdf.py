# convert_slides_to_pdf.py

import os
import re
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

    # Match filenames like slide_1.pptx, slide_1_title.pptx, slide_10.pptx, etc.
    slide_files = [f for f in os.listdir(project_output_dir) if re.match(r"slide_(\d+).*\.pptx$", f)]
    slide_files.sort(key=lambda f: int(re.match(r"slide_(\d+)", f).group(1)))

    for filename in slide_files:
        pptx_path = os.path.join(project_output_dir, filename)
        pdf_path = pptx_path.replace(".pptx", ".pdf")
        pptx_to_pdf_libreoffice(pptx_path, pdf_path)
        pdf_paths.append(pdf_path)
        print(f"✅ Converted {filename} to PDF")

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
