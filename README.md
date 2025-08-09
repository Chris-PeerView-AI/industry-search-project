PeerView AI — Benchmark Review & Report Builder (Context Pack)
What this is
PeerView AI is a Streamlit + Python app that turns Supabase project data (Enigma financials + Google Places metadata) into a polished market benchmarking report (PPTX → merged PDF). It’s built for entrepreneurs and owners to evaluate markets via peer benchmarks, growth, ticket size, market size, and a map—plus a narrative summary.

High‑level workflow
Select project in the Streamlit UI → run one‑time Data Quality Check (DQ) and adjust which businesses are included (“trusted”).

Generate report via export_project_pptx(project_id, supabase) → creates exhibits, summary, appendix, and disclosures from templates.

Convert all per‑slide PPTX files into a single merged PDF.

Key Python modules (what they do & what they pass)
1) benchmark_review_ui.py (Streamlit UI)
Lets you pick a project from Supabase search_projects (filtered to “active” if you want).

One‑time DQ check (tracked by a .dq_done marker file and st.session_state):

Marks a business “low” if:

annual_revenue < 50,000

missing/extreme yoy_growth

ticket_size < 30% or > 300% of average

missing latitude/longitude

Allows manual toggling of trusted/low per business.

Recalculates metrics; previews an interactive map and a sortable full business table.

Triggers report export.

Inputs: selected project_id.
Outputs: updated trusted flags in Supabase, trigger to generate_project_report.py.

2) generate_project_report.py (orchestration)
Primary entrypoint: export_project_pptx(project_id, supabase)

What it does

Creates a clean output folder: modules/output/{project_id}.

Downloads PPTX templates from Google Drive: modules/download_templates.py.

Pulls business rows from enigma_summaries and metadata from search_projects.

Builds slides in order:

Title (from slides_admin.generate_title_slide)

Intro (copies intro template)

Exhibit Intro (copies)

Exhibits (revenue, YoY, ticket size, market size, map) via slides_exhibit.py

Summary (stats + LLM narrative) via slides_summary.py

Appendix Intro (copies)

Appendix business tables (paginated) via slides_summary.generate_paginated_business_table_slides

Disclosures (copies)

Calls convert_slides_to_pdf.convert_and_merge_slides(...) to stitch into the final PDF.

Inputs: project_id, Supabase client.
Outputs: slide files slide_XX_*.pptx and final merged PDF in modules/output/{project_id}.

3) slides_admin.py (title + run‑safe text replacement)
generate_title_slide(project_output_dir, template_path, city, industry, date_str=None, subtitle=None, add_cover_art=False)

Replaces placeholders within runs (preserves font/spacing in the template).

Saves slide_1_title.pptx.

Inputs: city, industry, date/subtitle, template path.
Outputs: a single PPTX slide in the project output folder.

4) slides_exhibit.py (charts, map, and exhibit slides)
Matplotlib charts with PeerView defaults. We try to keep chart PNGs visually quiet so the PPT template drives the brand.

Functions:

generate_revenue_chart(path, summaries, end_date)

generate_yoy_chart(path, summaries, end_date)

generate_ticket_chart(path, summaries, end_date)

generate_market_size_chart(path, summaries, end_date)

Includes dynamic headroom so value labels don’t overlap bars/title.

generate_map_chart(path, summaries) → Folium map; headless Selenium/Chrome screenshot with fit_bounds so all points show.

generate_chart_slide(title, image_path, summary_text) → stamps chart PNG + analysis into the Exhibit template (prefers a named anchor like ChartAnchor, else uses the largest rectangle or safe margins).

Inputs: summaries (dicts for businesses), calculated end date.
Outputs: chart PNGs + exhibit PPTX slides.

5) slides_summary.py (industry summary, LLM narrative, business table)
generate_summary_slide(...)

Replaces stat placeholders and narrative block ({TBD SUMMARY ANALYSIS}) with smaller font & tighter line spacing for readability.

Places the map into a named MapPlaceholder if present, else uses a right‑panel fallback.

generate_llama_summary(slide_summaries, model_name="llama3")

Calls local ollama to produce ~700‑word narrative with tone (positive/neutral/negative) inferred from YoY.

get_market_size_analysis() → static text for the Market Size exhibit.

generate_paginated_business_table_slides(output_dir, businesses, base_title)

Builds 5‑column tables (Name, Address, Revenue, YoY, Ticket) over multiple slides.

Uses TableAnchor if present; otherwise safe margins.

Title is styled to match the header (white, larger), body font ~8pt with slightly shorter rows.

Inputs: trusted businesses, end date, precomputed summary stats, slide_summaries.
Outputs: slide_11_market_summary.pptx + slide_41_*BusinessTable.pptx (and subsequent pages).

6) convert_slides_to_pdf.py
Converts each slide_*.pptx to PDF and merges in order. Handles ordering keys so Disclosures (e.g., slide_999_*) land at the end.

7) download_templates.py
Uses a Google service account to download a known set of PPTX templates from Drive into modules/:

downloaded_title_template.pptx

downloaded_intro_template.pptx

downloaded_exhibit_intro_template.pptx

downloaded_exhibit_template.pptx

downloaded_summary_template.pptx

downloaded_appendix_intro_template.pptx

downloaded_businesstable_template.pptx

downloaded_disclosures_template.pptx

downloaded_businessview_template.pptx (optional per‑business slide)

Template placeholders (what code expects)
Title: {TBD INDUSTRY}, {TBD LOCATION}, {TBD DATE}, optional {TBD SUBTITLE}.

Exhibit: {TBD EXHIBIT TITLE}, {TBD ANALYSIS}.

Chart anchor: a shape named ChartAnchor (preferred), or else the largest rectangle, or fallback margins.

Summary: {TBD TITLE}, {TBD AS OF DATE}, {TBD TOTAL BUSINESSES}, {TBD TRUSTED BUSINESSES}, {TBD: MEAN REVENUE}, {TBD MEDIAN REVENUE}, {TBD YOY GROWTH}, {TBD AVERAGE TICKET SIZE}, {TBD SUMMARY ANALYSIS}, optional MapPlaceholder.

Appendix Table: title accepts {TBD Title} or {TBD TITLE}; optional TableAnchor for table placement.

Business View (optional): {TBD TITLE}, {TBD AS OF DATE}, {TBD ADDRESS}, {TBD: MEAN REVENUE}, {TBD YOY GROWTH}, {TBD AVERAGE TICKET SIZE}, {TBD SUMMARY ANALYSIS}.

Note: If you round‑trip templates through Google Slides, it can strip shape names. Our code handles that with smart fallbacks.

Supabase schema used
search_projects

id (UUID) — project identifier

name — project name

industry — e.g., “Coffee Shop”

location — e.g., “Austin, TX”

(optionally) flags like active if you filter in the UI

enigma_summaries (one row per matched business)

id, project_id, name, address

annual_revenue (12‑month), ticket_size, yoy_growth

transaction_count (if present)

latitude, longitude

benchmark = "trusted" or "low"

search_result_id (FK to search_results)

search_results (lookup metadata)

id

tier_reason (why it’s trusted/selected)

benchmark_summaries (optional aggregated stats per project)

enigma_metrics (for end date)

project_id, period_end_date

used by get_latest_period_end(...) to format “As of {Month YYYY}”

Environment & configuration
.env keys:

SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

GOOGLE_SERVICE_ACCOUNT_FILE (JSON path for Drive API)

LLM_MODEL (defaults to "llama3"; invoked via local ollama)

Output root: modules/output/{project_id} (holds slide PNGs/PPTX and final PDF).

Style & consistency rules we’ve implemented
Run‑safe text replacement: we replace placeholders inside text runs so the template’s font and spacing remain intact.

Exhibit charts: muted styling so the template does the branding; dynamic headroom avoids label/title collisions.

Summary analysis: 8pt font, line_spacing=1.1, small space_after, split into proper paragraphs.

Appendix tables: white, larger title; body 8pt; row height ~0.25"; page numbers in title.

Map: fit_bounds for all businesses; marker colors (trusted green, untrusted gray).

How to run (quick)
python
Copy
Edit
# 1) Ensure .env is set (Supabase + Google service account + optional LLM_MODEL)
# 2) Start Streamlit UI (optional), OR run the exporter directly:

from modules.generate_project_report import export_project_pptx, supabase
export_project_pptx("<your-project-uuid>", supabase)
The final PDF path prints at the end (in modules/output/<project-id>/).

Common “gotchas”
Template shape names lost after editing in Google Slides → our code falls back to largest rectangle or safe margins, but it’s best to keep the ChartAnchor, MapPlaceholder, and TableAnchor when possible.

Missing geos → businesses without lat/lng are flagged “low” by DQ and excluded from trusted exhibits.

Extreme YoY → filtered by DQ; you can still override in the UI.

Headless Chrome required for the map screenshot (Selenium). Make sure Chrome/driver are present in your environment.

Extending / customizing
Add an exhibit: create a chart function that saves a PNG, then call generate_chart_slide(title, png, summary_text).

Change section order or titles: do it in generate_project_report.py.

New placeholder? Add it to the relevant replacement dict in slides_admin.py or slides_summary.py.

