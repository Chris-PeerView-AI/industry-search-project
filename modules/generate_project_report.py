import os
import sys
sys.stdout.reconfigure(line_buffering=True)
print("✅ Script started")
print("📦 Importing libraries successful")
from pptx import Presentation
from pptx.util import Inches, Pt
from test_01_download_templates import download_all_templates
import matplotlib.pyplot as plt

MODULES_DIR = os.path.dirname(__file__)
TITLE_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_title_template.pptx")
EXHIBIT_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_exhibit_template.pptx")
SUMMARY_TEMPLATE = os.path.join(MODULES_DIR, "downloaded_summary_template.pptx")
OUTPUT_PATH = os.path.join(MODULES_DIR, "benchmark_report_OUTPUT_PLACEHOLDER.pptx")  # to be set dynamically


def generate_revenue_chart(path, summaries):
    print("📈 Generating revenue chart from Supabase data")
    trusted = [b for b in summaries if b.get("benchmark") == "trusted"]
    if not trusted:
        print("⚠️ No trusted businesses found. Skipping chart generation.")
        return False

    trusted = sorted(trusted, key=lambda x: x["annual_revenue"], reverse=True)
    names = [b["name"] for b in trusted]
    values = [b["annual_revenue"] for b in trusted]
    mean_val = sum(values) / len(values)
    median_val = sorted(values)[len(values)//2]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(names, values, color="#4CAF50")
    ax.axhline(mean_val, color='blue', linestyle='--', label=f"Mean: ${mean_val:,.0f}")
    ax.axhline(median_val, color='purple', linestyle=':', label=f"Median: ${median_val:,.0f}")
    ax.set_title("Trusted Businesses - Annual Revenue")
    ax.set_ylabel("Revenue ($)")
    ax.set_xticklabels(names, rotation=45, ha="right")
    ax.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    print(f"✅ Revenue chart saved to: {path}")
    return True


def generate_chart_slide(chart_title, image_path, summary_text):
    print(f"🔧 Generating chart slide: {chart_title}")
    print(f"🔍 Using image: {image_path}")
    print(f"📝 Summary: {summary_text}")

    exhibit_ppt = Presentation(EXHIBIT_TEMPLATE)
    slide = exhibit_ppt.slides[0]

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if "{TBD TITLE}" in shape.text:
            print("🪄 Replacing title placeholder")
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = chart_title
            run.font.name = "Montserrat"
            run.font.size = Pt(36)
        elif "{TBD ANALYSIS}" in shape.text:
            print("🪄 Replacing analysis placeholder")
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            run = p.add_run()
            run.text = summary_text
            run.font.name = "Arial"
            run.font.size = Pt(11)

    # Insert chart image (assuming image placeholder area known/fixed)
    left = Inches(0.75)
    top = Inches(2.0)
    width = Inches(7.5)
    print("🖼️ Inserting chart image onto slide")
    slide.shapes.add_picture(image_path, left, top, width=width)

    return slide


def export_project_pptx(project_id: str = "5c36b37b-1530-43be-837a-8491d914dfc6", supabase=None):
    print(f"🚀 Starting export for project ID: {project_id}")

    # Step 1: Re-download templates
    print("⬇️ Downloading templates...")
    print("📁 Calling download_all_templates...")
    download_all_templates()

    # Step 2: Load main output deck
    print("📂 Creating new presentation")
    print("📄 Instantiating final presentation")
    final_ppt = Presentation(TITLE_TEMPLATE)
    print("🧱 Using title template as base presentation")

    # Step 3: Append title slide (unchanged)
    print("➕ Adding title slide")
    print("📑 Loading title template")
    title_ppt = Presentation(TITLE_TEMPLATE)
    # Title slide is already first slide from template — no manual cloning needed

    # Step 4: Generate and add Revenue chart slide (sample)
    chart_title = "Exhibit B: Revenue Overview"
    image_path = os.path.join(MODULES_DIR, "sample_chart.png")
    summary_text = "This chart shows revenue by business with mean and median lines."

    print("🧪 Checking for sample chart image")
    if supabase:
        summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
        success = generate_revenue_chart(image_path, summaries)
    else:
        success = False

    if os.path.exists(image_path):
        print("📊 Adding revenue chart slide")
        slide_content = generate_chart_slide(chart_title, image_path, summary_text)
        chart_layout = final_ppt.slide_layouts[6]  # use blank layout
        chart_slide = final_ppt.slides.add_slide(chart_layout)
        for shape in slide_content.shapes:
            if shape.shape_type == 1:  # textbox
                new_shape = chart_slide.shapes.add_textbox(shape.left, shape.top, shape.width, shape.height)
                new_tf = new_shape.text_frame
                new_tf.clear()
                for para in shape.text_frame.paragraphs:
                    new_para = new_tf.add_paragraph()
                    for run in para.runs:
                        new_run = new_para.add_run()
                        new_run.text = run.text
                        new_run.font.name = run.font.name
                        new_run.font.size = run.font.size
            elif shape.shape_type == 13:  # picture
                from io import BytesIO
                image_stream = BytesIO(shape.image.blob)
                slide_width = final_ppt.slide_width
                img_width = shape.width
                centered_left = (slide_width - img_width) / 2
                chart_slide.shapes.add_picture(image_stream, centered_left, shape.top, shape.width, shape.height)
    else:
        print(f"❌ Chart image not found: {image_path}")

    # Step 5: TODO: Add industry summary slide
    # Step 6: TODO: Add map exhibit slide

    # Step 7: Save
    output_file = OUTPUT_PATH.replace("OUTPUT_PLACEHOLDER", project_id)
    print(f"💾 Saving report to: {os.path.abspath(output_file)}")
    final_ppt.save(output_file)
    print(f"✅ Report saved to: {output_file}")
    return output_file


if __name__ == "__main__":
    export_project_pptx()
