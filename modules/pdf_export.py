from fpdf import FPDF
import streamlit as st


def export_project_pdf(project_id, supabase):
    """
    Placeholder function to generate and download PDF summary for the given project.
    Will be extended to include full charting and data logic.
    """
    pdf = FPDF(orientation="P", unit="mm", format="Letter")
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(200, 10, txt="📄 Benchmark Summary PDF Placeholder", ln=True, align="C")
    pdf.cell(200, 10, txt=f"Project ID: {project_id}", ln=True, align="C")

    output_path = "/tmp/benchmark_report.pdf"
    pdf.output(output_path)

    with open(output_path, "rb") as f:
        st.download_button(
            label="📥 Download PDF Report",
            data=f,
            file_name="benchmark_report.pdf",
            mime="application/pdf"
        )
