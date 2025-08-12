PeerView AI — Benchmark Review & Report Builder

PeerView AI is a Streamlit + Python app that turns Supabase project data (Enigma financials + Google Places metadata) into a polished market benchmarking report (PPTX → merged PDF). It’s built for entrepreneurs and owners to evaluate markets via peer benchmarks, growth, ticket size, market size, and a map—plus a narrative summary.

What’s new (Aug 2025)

• Map export is now **rail‑free**: we render the PNG to the slide’s ChartAnchor aspect ratio and capture an **element‑level screenshot** (not the whole window). No in‑PPT cropping needed.
• **Stable centering**: bbox‑midpoint center + fractional zoom; we explicitly call `setView([lat,lng], zoom)` to avoid Leaflet snapping.
• **Pixel‑locked viewport**: headless Chrome viewport is forced to the requested size via CDP and full‑bleed CSS. This prevents 1400×661‑style surprises.
• **QA guard**: after saving the PNG we verify its dimensions vs. the expected size and log `[MAP QA] ...` lines; optional env flag can downgrade to warnings.
• **Consistent assets**: the Summary slide reuses the exact Exhibit map PNG, so styling/zoom are identical.
• **UI zoom knob**: Streamlit slider (`zoom_fraction`) controls the search ring height as a % of image; the export uses the same value.
• **Diagnostics**: optional center‑dot overlay, bounded tile wait, DPR=1 for crisp screenshots.

High‑level workflow

Select project in the Streamlit UI.

Run one‑time Data Quality Check (DQ) and adjust which businesses are included (“trusted”).

Generate report via export_project_pptx(project_id, supabase) → creates exhibits, summary, appendix, and disclosures from templates.

Convert all per‑slide PPTX files into a single merged PDF.

Key Python modules (what they do & what they pass)

1) benchmark_review_ui.py (Streamlit UI)

Pick a project from Supabase search_projects (optionally filter to active).

One‑time DQ check (tracked by a .dq_done marker + st.session_state).

Marks a business low if:

annual_revenue < 50,000

missing/extreme yoy_growth

ticket_size < 30% or > 300% of average

missing latitude/longitude

Toggle trusted/low per business; metrics recalc.

Map Preview uses map_generator.build_map(...) and a zoom slider (0.60–0.90, default 0.75).

On export, the selected zoom is threaded through to the map exporter; the Summary slide reuses the same PNG.

Inputs: selected project_idOutputs: updated benchmark flags in Supabase, trigger to generate_project_report.py.

2) generate_project_report.py (orchestration)

Primary entrypoint: export_project_pptx(project_id, supabase)

Creates a clean output folder: modules/output/{project_id}.

Downloads PPTX templates: modules/download_templates.py.

Pulls business rows from enigma_summaries and metadata from search_projects.

Builds slides in order:

Title (slides_admin.generate_title_slide)

Intro (copy)

Exhibit Intro (copy)

Exhibits (revenue, YoY, ticket size, market size, map) via slides_exhibit.py

Summary (stats + LLM narrative + map image) via slides_summary.py

Appendix Intro (copy)

Appendix business tables (paginated) via slides_summary.generate_paginated_business_table_slides

Disclosures (copy)

Calls convert_slides_to_pdf.convert_and_merge_slides(...) to stitch into the final PDF.

Inputs: project_id, Supabase clientOutputs: slide files slide_XX_*.pptx and final merged PDF in modules/output/{project_id}.

3) slides_admin.py (title + run‑safe text replacement)

generate_title_slide(project_output_dir, template_path, city, industry, date_str=None, subtitle=None, add_cover_art=False)

Replaces placeholders within runs (preserves font/spacing).

Saves slide_1_title.pptx.

4) slides_exhibit.py (charts, map, and exhibit slides)

Matplotlib charts with PeerView defaults (muted; template drives brand). Dynamic headroom avoids label/title collisions.

Map pipeline:

generate_map_chart(path, summaries, zoom_fraction=None) delegates to map_generator.generate_map_png_from_summaries(...).

Reads the ChartAnchor aspect ratio from the exhibit template and renders the PNG to that ratio.

On placement, performs a tiny center‑crop to the exact anchor ratio and inserts the image to fill the anchor (no rails) and center it.

generate_chart_slide(title, image_path, summary_text) stamps chart PNG + analysis into the Exhibit template (prefers a shape named ChartAnchor; else largest rectangle).

Inputs: summaries (business dicts), calculated end_dateOutputs: chart PNGs + exhibit PPTX slides

5) slides_summary.py (industry summary, LLM narrative, business table)

generate_summary_slide(...) fills stats + narrative and places the same map PNG passed from the exhibit.

generate_llama_summary(slide_summaries, model_name="llama3") (via local ollama).

get_market_size_analysis() → static text for Market Size exhibit.

generate_paginated_business_table_slides(output_dir, businesses, base_title) builds 5‑column tables (Name, Address, Revenue, YoY, Ticket) over multiple slides.

Inputs: trusted businesses, end date, precomputed summary stats, slide_summariesOutputs: slide_11_market_summary.pptx + slide_41_*BusinessTable.pptx (and subsequent pages)

6) convert_slides_to_pdf.py

Converts each slide_*.pptx to PDF and merges in order. Ensures slide_999_* (Disclosures) land at the end.

7) download_templates.py

Uses a Google service account to download templates into modules/:

downloaded_title_template.pptx

downloaded_intro_template.pptx

downloaded_exhibit_intro_template.pptx

downloaded_exhibit_template.pptx

downloaded_summary_template.pptx

downloaded_appendix_intro_template.pptx

downloaded_businesstable_template.pptx

downloaded_disclosures_template.pptx

downloaded_businessview_template.pptx (optional per‑business slide)

8) map_generator.py (NEW)

Single source of truth for map visuals (Folium + Leaflet + Selenium screenshot).

Key behaviors

- **Aspect‑ratio aware rendering**: exporter computes `window = (height × anchor_ratio, height)` and renders to that exact pixel size.
- **Full‑bleed HTML**: CSS removes margins/scrollbars and locks the map `<div>` to the requested pixel size.
- **Element screenshot**: Selenium captures the map element by ID; no window chrome, no rails.
- **Viewport enforcement**: Chrome DevTools Protocol forces the viewport to the same `window` size; logs `[MAP QA] viewport=WxH target=WxH`.
- **Stable zoom & center**: fractional zoom stays enabled; center is the bbox midpoint; radius is farthest‑point distance.
- **QA guard**: after writing the PNG we verify dimensions and log `[MAP QA] final_png=WxH expected=WxH` (optional assert).

Exposed API

- `build_map(df, *, zoom_fraction=0.75, window=(w,h)) -> (folium.Map, MapMeta)`
- `save_html_and_png(m, html_path, png_path, window=(w,h))`
- `generate_map_png_from_summaries(summaries, output_path, *, zoom_fraction=0.75, aspect_ratio=None, window_height_px=800)`
- `generate_map_png_from_project(project_id, supabase, output_dir, *, zoom_fraction=0.75, aspect_ratio=None, window_height_px=800)`

Dependencies

- Headless Chrome + matching chromedriver.
- Chrome 109+ recommended for CDP viewport overrides.

Template placeholders (what code expects)

Title: {TBD INDUSTRY}, {TBD LOCATION}, {TBD DATE}, optional {TBD SUBTITLE}.

Exhibit: {TBD EXHIBIT TITLE}, {TBD ANALYSIS}.

Chart anchor: a shape named ChartAnchor (preferred), else the largest rectangle.

Summary: {TBD TITLE}, {TBD AS OF DATE}, {TBD TOTAL BUSINESSES}, {TBD TRUSTED BUSINESSES}, {TBD: MEAN REVENUE}, {TBD MEDIAN REVENUE}, {TBD YOY GROWTH}, {TBD AVERAGE TICKET SIZE}, {TBD SUMMARY ANALYSIS}, optional MapPlaceholder.

Appendix Table: title accepts {TBD Title} or {TBD TITLE}; optional TableAnchor for table placement.

Business View (optional): {TBD TITLE}, {TBD AS OF DATE}, {TBD ADDRESS}, {TBD: MEAN REVENUE}, {TBD YOY GROWTH}, {TBD AVERAGE TICKET SIZE}, {TBD SUMMARY ANALYSIS}.

Aspect ratio tips:

If you want to rely on defaults, set ChartAnchor to 3:2 (matches 1200×800).

For widescreen decks, ChartAnchor at 16:9 works great; exporter will render to that ratio and the placer will exact‑fit crop to remove any rails.

Note: If you round‑trip templates through Google Slides, it can strip shape names. Our code falls back to the largest rectangle and still does exact‑fit placement.

Supabase schema used

search_projects

id (UUID) — project identifier

name

industry — e.g., “Coffee Shop”

location — e.g., “Austin, TX”

(optional) flags like active

enigma_summaries (one row per matched business)

id, project_id, name, address

annual_revenue (12‑month), ticket_size, yoy_growth

transaction_count (if present)

latitude, longitude

benchmark = trusted or low

search_result_id (FK to search_results)

search_results (lookup metadata)

id

tier_reason (why it’s trusted/selected)

benchmark_summaries (optional aggregated stats per project)

enigma_metrics (for end date)

project_id, period_end_date

Used by get_latest_period_end(...) to format “As of {Month YYYY}”.

Environment & configuration

.env keys:

SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

GOOGLE_SERVICE_ACCOUNT_FILE (JSON path for Drive API)

LLM_MODEL (defaults to "llama3"; invoked via local ollama)

Run‑time knobs (env vars):

- `MAP_ZOOM_FRACTION` — if not provided by UI, defaults to 0.75 (recommended range 0.60–0.90).
- `MAP_DEBUG_OVERLAY` — set to 1 to draw a tiny red dot at computed map center (diagnostics) and print extra QA lines.
- `MAP_WINDOW_HEIGHT_PX` (optional) — target render height (defaults 800).
- `MAP_USE_FRACTIONAL_ZOOM` (optional) — set 0 to disable fractional zoom JS.
- `MAP_STRICT_DIM_CHECK` (optional) — default `1`. If `0`, PNG size mismatches print a warning instead of raising.

Output root: modules/output/{project_id} (slide PNGs/PPTX + final PDF).

Chrome requirement: Headless Chrome + matching chromedriver must be available on the host.

How to run (quick)

# 1) Ensure .env is set (Supabase + Google service account + optional LLM_MODEL)
# 2) Start Streamlit UI (optional), OR run the exporter directly:
from modules.generate_project_report import export_project_pptx, supabase
export_project_pptx("<your-project-uuid>", supabase)
# The final PDF path prints at the end (in modules/output/<project-id>/).

Streamlit UI

streamlit run benchmark_review_ui.py
# In the UI, choose a project, adjust trusted flags, move the Map zoom slider, then Export.

Test harness (map only)

python modules/TEST_pretty_map.py --project <project-uuid> --loglevel DEBUG

Style & consistency rules

Run‑safe text replacement preserves template fonts/spacing.

Exhibit charts: muted styling; dynamic headroom.

Summary analysis: ~8pt font, line_spacing=1.1, small space_after, proper paragraphs.

Appendix tables: white, larger title; body ~8pt; row height ~0.25"; page numbers in title.

Map: Positron tiles, dashed search radius ring, bbox midpoint center, trusted = green markers, others gray.

Common “gotchas” & troubleshooting

- **Side bars / rails** — ensure the slide’s ChartAnchor ratio matches your intent (3:2 or 16:9). The exporter renders to the anchor ratio and captures the **map element**, so rails usually indicate a viewport mismatch; check `[MAP QA] viewport=... target=...` logs.
- **PNG size mismatch (e.g., expected 1400×800 got 1400×661)** — headless viewport didn’t match the requested size. Verify Chrome/chromedriver match, and confirm the logs show `viewport=1400x800`. The exporter uses CDP to enforce this; if you see a mismatch, upgrade Chrome/driver.
- **Off‑center map** — we use bbox midpoint + `setView([lat,lng], zoom)` to avoid snapping. Turn on `MAP_DEBUG_OVERLAY=1` to visualize the center.
- **Missing geos** — businesses without lat/lng are flagged low by DQ and excluded from trusted exhibits.
- **Extreme YoY** — filtered by DQ; you can override in the UI.
- **PNG missing during export** — exporter asserts the expected PNG path after map generation; check Chrome/driver logs if it fails.

Extending / customizing

Add an exhibit: create a chart function that saves a PNG, then call generate_chart_slide(title, png, summary_text).

Change section order/titles: edit generate_project_report.py.

New placeholder? Add to the replacement dict in slides_admin.py or slides_summary.py.

Map tweaks: adjust legend text/size, marker radius, window height, or the zoom slider bounds in the UI.

