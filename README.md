# PeerView AI — End‑to‑End Pipeline (Phases 1–3)

A Streamlit + Python system that:

* **Phase 1**: captures project details, uses Google Places to find candidate businesses, and classifies them with an LLM.
* **Phase 2**: matches each business to **Enigma** OperatingLocation(s), writes a canonical mapping row and **per‑project** metrics into Supabase.
* **Phase 3**: builds a polished benchmark report (PPTX → merged PDF) including charts, a QA‑stable map, and a narrative summary.

This README consolidates how to run all phases, what the schema looks like, and how to debug common issues.

---

## Quick Start

1. Set up `.env` (see **Environment**).
2. **Phase 1** — run the discovery UI to collect Google Places and tier them.
3. **Phase 2** — run the Enigma UI to match, upsert mapping rows, and fetch metrics per project.
4. **Phase 3** — run the Review/Export UI to adjust trusted flags and export the final PDF.

---

## Phase 1 — Project setup & Google Places discovery

**Goal**: create a project, search Google Places for candidate businesses, and classify them (Tier 1..3), storing the results for Phase 2.

### Inputs

* **Project**: name, industry (e.g., “Med Spa”), location (e.g., “Memphis, TN”).
* **Google Places**: Text Search + Place Details using project keywords.
* **LLM**: classifies each candidate into tiers and tags (e.g., likely in‑industry; borderline; exclude).

### Output tables

* **`search_projects`**

  * `id (uuid)` project identifier
  * `name, industry, location` (plus optional flags like `active`)
* **`search_results`** (one row per Google candidate for the project)

  * `id, project_id (fk)`, `google_places_id`/`place_id`, `name`, `address` (`street, city, state, zip`), `latitude, longitude`
  * `tier` (1 strong .. 3 weak), `tier_reason`, timestamps

> Tip: keep Phase‑1 results broad. You can down‑select in Phase 2 before pulling Enigma metrics.

---

## Phase 2 — Enigma pull, matching & metrics (per project)

**Goal**: for each selected Google business, create a canonical **mapping** row to the Enigma OperatingLocation and pull **card/transaction metrics** keyed to the specific project.

### Core components

* **UI**: `pull_enigma_ui.py`

  * Shows candidate businesses; includes **Deselect All**; you can push only a few through for testing.
  * Displays match confidence and reason (e.g., `name_zip_match`).
* **Puller**: `modules/pull_enigma_data_for_business.py` (v2.2.2)

  * GraphQL to Enigma; multi‑variant search; confidence scoring.
  * Writes mapping row to `enigma_businesses` and **per‑project** metrics to `enigma_metrics`.
  * Skips metrics for weak matches (`conf < 0.90`).
  * Logs `[pull] …`, `[match] …`, `[DB] …`, `[metrics] …` for traceability.

### Matching & confidence

* `1.00` — street + city + state agree (and name ≥ 0.85)
* `0.95` — strong name similarity (≥ 0.93) with ZIP+state agree (city may differ: Memphis vs Germantown)
* `0.90` — good name (≥ 0.88) with ZIP+state agree
* `0.80` — street matches, only one of city/state agrees
* `0.70` — name + city + state match (no zip)
* else `0.40`

**Behavior**

* **Mapping** is saved for any score (so you can review/upgrade later).
* **Metrics** are fetched only for `conf ≥ 0.90`.
* **Trusted** label in the UI defaults to `conf ≥ 0.95` (presentation‑only; configurable).

### Supabase schema (Phase 2)

**`enigma_businesses`** — canonical mapping row per Google Place

* `id (uuid)` **PK** (recommend default `gen_random_uuid()`)
* `enigma_id` (Enigma OperatingLocation id)
* `google_places_id`, `place_id`
* Google fields: `business_name`, `full_address`, `city`, `state`, `zip`
* Enigma fields: `enigma_name`, `matched_full_address`, `matched_city`, `matched_state`, `matched_postal_code`
* Pull metadata: `date_pulled (date)`, `pull_session_id (uuid)`, `pull_timestamp (timestamptz)`
* Match diagnostics: `match_method`, `match_confidence (numeric)`, `match_reason`

**Indexes / constraints**

* `enigma_businesses_pkey (id)`
* `enigma_businesses_google_places_id_key (google_places_id)` **UNIQUE** (conflict target used by code)
* (Optional) UNIQUE on `place_id` if you want — but a partial unique index may not satisfy `ON CONFLICT`.

**`enigma_metrics`** — per‑project metrics (card revenue, transactions, etc.)

* `id (uuid)` **PK**
* `business_id (uuid)` **FK → enigma\_businesses.id**
* `project_id (uuid)` (associate metrics to the project that pulled them)
* `quantity_type (text)` e.g., `card_revenue_amount`, `avg_transaction_size`, …
* `raw_quantity (numeric)`, `projected_quantity (numeric)`
* `period (text)` e.g., `3m`, `12m`, `2023`, `2024`
* `period_start_date (date)`, `period_end_date (date)`
* Pull metadata: `pull_session_id`, `pull_timestamp`, `pull_status`, `pull_notes`

**Indexes / constraints**

* `enigma_metrics_pkey (id)`
* `enigma_metrics_dedupe_project_uidx (business_id, project_id, quantity_type, period, period_end_date)` **UNIQUE** ← required for upsert dedupe per project

### Common issues & fixes

* **42P10** `ON CONFLICT` has no matching unique constraint

  * Use `ON CONFLICT (google_places_id)` in code **or** add `UNIQUE (place_id)` (not partial) in DB.
* **23502** NULL in `enigma_businesses.id`

  * Insert must send a UUID; add DB default `gen_random_uuid()` as a safety net.
* **23503** metrics FK violation

  * Ensure the mapping row exists and you’re using its `id` for `business_id` before writing metrics.
* Importing the wrong module version

  * In the UI: `sys.path.insert(0, modules_dir)` and inspect `inspect.getfile(puller_mod)`.

### Admin SQL (Phase 2)

```sql
-- Create the per‑project metrics dedupe index (one‑time)
CREATE UNIQUE INDEX IF NOT EXISTS enigma_metrics_dedupe_project_uidx
  ON public.enigma_metrics (business_id, project_id, quantity_type, period, period_end_date);

-- Drop legacy cross‑project index if present
DROP INDEX IF EXISTS public.enigma_metrics_dedupe_uidx;

-- Optional: default UUID for mapping PK
CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER TABLE public.enigma_businesses ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- Recent rows by project (sanity)
SELECT project_id, COUNT(*)
FROM public.enigma_metrics
WHERE pull_timestamp > NOW() - INTERVAL '2 hours'
GROUP BY project_id;

-- Duplicate check by conflict key (should return 0 rows)
SELECT business_id, project_id, quantity_type, period, period_end_date, COUNT(*) AS n
FROM public.enigma_metrics
GROUP BY 1,2,3,4,5
HAVING COUNT(*) > 1;

-- Residual NULL project_ids (view / cleanup)
SELECT COUNT(*) FROM public.enigma_metrics WHERE project_id IS NULL;
DELETE FROM public.enigma_metrics WHERE project_id IS NULL AND pull_timestamp > NOW() - INTERVAL '1 day';
```

---

## Phase 3 — Benchmark Review & Report Export

**Goal**: flag trusted businesses, review stats, preview the map, and export a finalized deck (PPTX) merged into a single PDF.

### UI

* `benchmark_review_ui.py` — select project, run a one‑time DQ check, toggle trusted/low, preview map (zoom slider), and **Export**.

### Orchestration

* `generate_project_report.py` → `export_project_pptx(project_id, supabase)` builds:

  * Title / Intro / Exhibit Intro
  * Exhibits (revenue, YoY, ticket size, market size, **map**) with PNG charts
  * Summary (stats + LLM narrative + **same map PNG** used in exhibits)
  * Appendix (paginated business tables), Disclosures
  * Converts all slides to PDF and merges in order

### Map generation (stability features)

* Aspect‑ratio aware export (renders PNG to the slide anchor’s ratio)
* Element‑level screenshot (no browser chrome)
* Forced viewport via Chrome DevTools Protocol
* Stable `setView` center (bbox midpoint) and fractional zoom
* QA guard logs: `[MAP QA] viewport=…`, `[MAP QA] final_png=…`

> See the in‑repo module docs for more on chart styles, template placeholders, and map exporter controls.

---

## Environment

Set these in `.env`:

```
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
ENIGMA_API_KEY=
GOOGLE_SERVICE_ACCOUNT_FILE=  # for template downloads
LLM_MODEL=llama3              # used for narrative in Phase 3
```

Optional runtime knobs (env):

* `MAP_ZOOM_FRACTION` (default 0.75)
* `MAP_DEBUG_OVERLAY` (0/1)
* `MAP_WINDOW_HEIGHT_PX` (default 800)
* `MAP_STRICT_DIM_CHECK` (default 1)

---

## How to Run

### Phase 1 (discovery)

```bash
streamlit run streamlit_business_browser.py
# set project, query Google Places, classify with LLM, save candidates
```

### Phase 2 (Enigma match & metrics)

```bash
streamlit run pull_enigma_ui.py
# Deselect all → select a few to test → Submit Selected
# watch console for [pull]/[match]/[DB]/[metrics]
```

### Phase 3 (review & export)

```bash
streamlit run benchmark_review_ui.py
# choose project → run DQ → toggle trusted → export PPTX/PDF
```

Or call the exporter directly:

```python
from modules.generate_project_report import export_project_pptx, supabase
export_project_pptx("<project-uuid>", supabase)
```

---

## Troubleshooting Quick Reference

* **ON CONFLICT error (42P10)** → DB lacks a matching UNIQUE constraint. Use `google_places_id` as conflict key or add a constraint on `place_id`.
* **NULL id (23502)** → Insert must provide `id`; add default `gen_random_uuid()` and/or generate in code.
* **FK error (23503)** → Write mapping first, then metrics; ensure you’re using the returned `id`.
* **No metrics for strong match** → Confirm `conf ≥ 0.90`; if city strings differ but ZIP/state match, the score is often 0.95 already.
* **Wrong module imported** → `sys.path.insert(0, modules_dir)`; print `inspect.getfile(...)` in the UI.

---

## Changelog (Aug 2025)

* **Per‑project metrics dedupe**: `ON CONFLICT (business_id, project_id, quantity_type, period, period_end_date)` and a matching UNIQUE index.
* **Robust mapping writes**: update by `id` if exists; otherwise insert with generated UUID; upsert fallback on `google_places_id`.
* **Guardrails**: raise if `project_id` missing; verbose logs show project context.
* **UI**: Deselect‑All button in Phase‑2 list; confidence/quality tags; trusted defaults to `conf ≥ 0.95`.

---

## Appendix: Useful SQL Snippets

**Latest trusted mapping per Place**

```sql
WITH latest_map AS (
  SELECT eb.*, ROW_NUMBER() OVER (
    PARTITION BY eb.google_places_id
    ORDER BY eb.pull_timestamp DESC NULLS LAST, eb.date_pulled DESC NULLS LAST
  ) rn
  FROM public.enigma_businesses eb
)
SELECT
  eb.business_name        AS google_name,
  eb.full_address         AS google_address,
  eb.city, eb.state, eb.zip,
  eb.enigma_name,
  eb.matched_full_address AS enigma_address,
  eb.matched_city, eb.matched_state, eb.matched_postal_code,
  eb.match_confidence, eb.match_reason,
  eb.place_id, eb.enigma_id, eb.pull_timestamp
FROM latest_map eb
WHERE rn = 1 AND eb.match_confidence >= 0.95
ORDER BY eb.business_name;
```

**Confidence distribution**

```sql
SELECT
  CASE
    WHEN match_confidence >= 0.95 THEN '>=0.95'
    WHEN match_confidence >= 0.90 THEN '0.90–0.949'
    WHEN match_confidence >= 0.70 THEN '0.70–0.899'
    ELSE '<0.70' END AS conf_bucket,
  match_reason,
  COUNT(*) AS n
FROM public.enigma_businesses
GROUP BY 1,2
ORDER BY 1 DESC, n DESC;
```

---

## Phase 3 — Benchmark Review & Report Builder (Deep Dive)

**PeerView AI — Benchmark Review & Report Builder** turns Supabase project data (Enigma financials + Google Places metadata) into a polished market benchmarking report (PPTX → merged PDF). It’s built for entrepreneurs and owners to evaluate markets via peer benchmarks, growth, ticket size, market size, and a map—plus a narrative summary.

### What’s new (Aug 2025)

* **Rail‑free map export**: PNG is rendered to the slide’s `ChartAnchor` aspect ratio and captured via **element‑level screenshot** (not the window). No in‑PPT cropping.
* **Stable centering**: bbox‑midpoint center + fractional zoom. We explicitly call `setView([lat,lng], zoom)` to avoid Leaflet snapping.
* **Pixel‑locked viewport**: Headless Chrome viewport is forced via CDP and full‑bleed CSS to the requested pixels (prevents 1400×661 style surprises).
* **QA guard**: after saving the PNG we verify its dimensions vs expected size and log `[MAP QA] ...` lines; env flag can downgrade to warnings.
* **Consistent assets**: the **Summary** slide reuses the exact Exhibit map PNG, so styling/zoom are identical.
* **UI zoom knob**: Streamlit slider (`zoom_fraction`) controls the search‑ring height as a % of image; export uses the same value.
* **Diagnostics**: optional center‑dot overlay, bounded tile wait, DPR=1 for crisp screenshots.

### High‑level workflow

1. Select project in the Streamlit UI.
2. Run one‑time Data Quality (DQ) and adjust which businesses are included (“trusted”).
3. Generate report via `export_project_pptx(project_id, supabase)` → creates exhibits, summary, appendix, and disclosures from templates.
4. Convert all per‑slide PPTX files into a single merged PDF.

### Key Python modules (what they do & what they pass)

1. **`benchmark_review_ui.py`** (Streamlit UI)

   * Pick a project from `search_projects` (optionally filter to `active`).
   * One‑time DQ check (tracked by `.dq_done` marker + `st.session_state`).
   * Marks a business *low* if:

     * `annual_revenue < 50,000`
     * missing/extreme `yoy_growth`
     * `ticket_size < 30%` or `> 300%` of average
     * missing `latitude/longitude`
   * Toggle trusted/low per business; metrics recalc.
   * **Map Preview** uses `map_generator.build_map(...)` and a zoom slider (0.60–0.90, default 0.75).
   * On export, the selected zoom is threaded through to the map exporter; the **Summary** slide reuses the same PNG.
   * **Inputs:** selected `project_id`
     **Outputs:** updated benchmark flags in Supabase, trigger to `generate_project_report.py`.

2. **`generate_project_report.py`** (orchestration)

   * Entry: `export_project_pptx(project_id, supabase)`
   * Creates clean output folder: `modules/output/{project_id}`.
   * Downloads PPTX templates: `modules/download_templates.py`.
   * Pulls business rows from `enigma_summaries` and metadata from `search_projects`.
   * Builds slides in order:

     * **Title** (`slides_admin.generate_title_slide`)
     * **Intro** (copy)
     * **Exhibit Intro** (copy)
     * **Exhibits** (revenue, YoY, ticket size, market size, **map**) via `slides_exhibit.py`
     * **Summary** (stats + LLM narrative + map image) via `slides_summary.py`
     * **Appendix Intro** (copy)
     * **Appendix business tables** (paginated) via `slides_summary.generate_paginated_business_table_slides`
     * **Disclosures** (copy)
   * Calls `convert_slides_to_pdf.convert_and_merge_slides(...)` to stitch into the final PDF.
   * **Inputs:** `project_id`, Supabase client
     **Outputs:** `slide_XX_*.pptx` files and final merged PDF in `modules/output/{project_id}`.

3. **`slides_admin.py`** (title + run‑safe text replacement)

   * `generate_title_slide(output_dir, template_path, city, industry, date_str=None, subtitle=None, add_cover_art=False)`
   * Replaces placeholders within runs (preserves font/spacing).
     **Output:** `slide_1_title.pptx`.

4. **`slides_exhibit.py`** (charts, map, and exhibit slides)

   * Matplotlib charts with PeerView defaults (muted; template drives brand). Dynamic headroom avoids label/title collisions.
   * **Map pipeline**:

     * `generate_map_chart(path, summaries, zoom_fraction=None)` delegates to `map_generator.generate_map_png_from_summaries(...)`.
     * Reads the `ChartAnchor` aspect ratio from the exhibit template and renders the PNG to that ratio.
     * On placement, performs a tiny center‑crop to the exact anchor ratio and inserts to fill the anchor (no rails) and center it.
   * `generate_chart_slide(title, image_path, summary_text)` stamps chart PNG + analysis into the Exhibit template (prefers a shape named `ChartAnchor`; else largest rectangle).
   * **Inputs:** `summaries` (business dicts), calculated `end_date`
     **Outputs:** chart PNGs + exhibit PPTX slides.

5. **`slides_summary.py`** (industry summary, LLM narrative, business table)

   * `generate_summary_slide(...)` fills stats + narrative and places the **same** map PNG passed from the exhibit.
   * `generate_llama_summary(slide_summaries, model_name="llama3")` (via local ollama).
   * `get_market_size_analysis()` → static text for Market Size exhibit.
   * `generate_paginated_business_table_slides(output_dir, businesses, base_title)` builds 5‑column tables (Name, Address, Revenue, YoY, Ticket) over multiple slides.
   * **Inputs:** trusted businesses, end date, precomputed summary stats, `slide_summaries`
     **Outputs:** `slide_11_market_summary.pptx` + `slide_41_*BusinessTable.pptx` (and subsequent pages).

6. **`convert_slides_to_pdf.py`**

   * Converts each `slide_*.pptx` to PDF and merges in order. Ensures `slide_999_*` (Disclosures) land at the end.

7. **`download_templates.py`**

   * Uses a Google service account to download templates into `modules/`:

     * `downloaded_title_template.pptx`
     * `downloaded_intro_template.pptx`
     * `downloaded_exhibit_intro_template.pptx`
     * `downloaded_exhibit_template.pptx`
     * `downloaded_summary_template.pptx`
     * `downloaded_appendix_intro_template.pptx`
     * `downloaded_businesstable_template.pptx`
     * `downloaded_disclosures_template.pptx`
     * `downloaded_businessview_template.pptx` (optional per‑business slide)

8. **`map_generator.py`** (NEW)

   * Single source of truth for map visuals (Folium + Leaflet + Selenium screenshot).
   * **Key behaviors**

     * Aspect‑ratio aware rendering: exporter computes `window = (height × anchor_ratio, height)` and renders to that exact pixel size.
     * Full‑bleed HTML: CSS removes margins/scrollbars and locks the map `<div>`.
     * Element screenshot: Selenium captures the map element by ID; no window chrome.
     * Viewport enforcement: CDP forces the viewport to the same `window` size; logs `[MAP QA] viewport=WxH target=WxH`.
     * Stable zoom & center: fractional zoom stays enabled; center is the bbox midpoint; radius is farthest‑point distance.
     * QA guard: after writing the PNG, verify dimensions and log `[MAP QA] final_png=WxH expected=WxH` (optional assert).
   * **Exposed API**

     * `build_map(df, *, zoom_fraction=0.75, window=(w,h)) -> (folium.Map, MapMeta)`
     * `save_html_and_png(m, html_path, png_path, window=(w,h))`
     * `generate_map_png_from_summaries(summaries, output_path, *, zoom_fraction=0.75, aspect_ratio=None, window_height_px=800)`
     * `generate_map_png_from_project(project_id, supabase, output_dir, *, zoom_fraction=0.75, aspect_ratio=None, window_height_px=800)`
   * **Dependencies**: Headless Chrome + matching chromedriver (Chrome 109+ recommended for CDP viewport overrides).

### Template placeholders (what code expects)

* **Title**: `{TBD INDUSTRY}`, `{TBD LOCATION}`, `{TBD DATE}`, optional `{TBD SUBTITLE}`.
* **Exhibit**: `{TBD EXHIBIT TITLE}`, `{TBD ANALYSIS}`.
* **Chart anchor**: a shape named `ChartAnchor` (preferred), else the largest rectangle.
* **Summary**: `{TBD TITLE}`, `{TBD AS OF DATE}`, `{TBD TOTAL BUSINESSES}`, `{TBD TRUSTED BUSINESSES}`, `{TBD: MEAN REVENUE}`, `{TBD MEDIAN REVENUE}`, `{TBD YOY GROWTH}`, `{TBD AVERAGE TICKET SIZE}`, `{TBD SUMMARY ANALYSIS}`, optional `MapPlaceholder`.
* **Appendix Table**: title accepts `{TBD Title}` or `{TBD TITLE}`; optional `TableAnchor` for table placement.
* **Business View (optional)**: `{TBD TITLE}`, `{TBD AS OF DATE}`, `{TBD ADDRESS}`, `{TBD: MEAN REVENUE}`, `{TBD YOY GROWTH}`, `{TBD AVERAGE TICKET SIZE}`, `{TBD SUMMARY ANALYSIS}`.

### Aspect ratio tips

* If you want to rely on defaults, set `ChartAnchor` to **3:2** (matches 1200×800).
* For widescreen decks, `ChartAnchor` at **16:9** works great; exporter will render to that ratio and the placer will exact‑fit crop to remove any rails.
* Note: If you round‑trip templates through Google Slides, it can strip shape names. Our code falls back to the largest rectangle and still does exact‑fit placement.

### Supabase schema used (Phase‑3 consumers)

* **`search_projects`**

  * `id (uuid)`, `name`, `industry`, `location`, optional flags like `active`
* **`enigma_summaries`** (one row per matched business)

  * `id`, `project_id`, `name`, `address`, `annual_revenue (12m)`, `ticket_size`, `yoy_growth`, `transaction_count` (optional), `latitude`, `longitude`, `benchmark` (trusted/low), `search_result_id`
* **`search_results`** (lookup metadata)

  * `id`, `tier_reason`
* **`benchmark_summaries`** (optional aggregated stats per project)
* **`enigma_metrics`** (for end date)

  * `project_id`, `period_end_date` (used by `get_latest_period_end(...)` → “As of {Month YYYY}”)

### Environment & configuration

* `.env` keys: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `LLM_MODEL` (defaults `llama3`; via local ollama)
* Run‑time knobs (env vars):

  * `MAP_ZOOM_FRACTION` — default 0.75 (recommended range 0.60–0.90)
  * `MAP_DEBUG_OVERLAY` — `1` to draw a tiny red dot at computed center (diagnostics)
  * `MAP_WINDOW_HEIGHT_PX` — target render height (defaults 800)
  * `MAP_USE_FRACTIONAL_ZOOM` — set `0` to disable fractional zoom JS
  * `MAP_STRICT_DIM_CHECK` — default `1`; if `0`, PNG size mismatches warn instead of raise
* **Output root**: `modules/output/{project_id}` (slide PNGs/PPTX + final PDF)
* **Chrome requirement**: Headless Chrome + matching chromedriver must be available on the host.

### How to run (quick)

```python
# 1) Ensure .env is set (Supabase + Google service account + optional LLM_MODEL)
# 2) Start the UI (optional), OR run the exporter directly:
from modules.generate_project_report import export_project_pptx, supabase
export_project_pptx("<your-project-uuid>", supabase)
# The final PDF path prints at the end (in modules/output/<project-id>/).
```

**Streamlit UI**

```bash
streamlit run benchmark_review_ui.py
# Choose a project, adjust trusted flags, move the Map zoom slider, then Export.
```

**Test harness (map only)**

```bash
python modules/TEST_pretty_map.py --project <project-uuid> --loglevel DEBUG
```

### Style & consistency rules

* Run‑safe text replacement preserves template fonts/spacing.
* Exhibit charts: muted styling; dynamic headroom.
* Summary analysis: \~8pt font, line\_spacing=1.1, small space\_after, proper paragraphs.
* Appendix tables: white, larger title; body \~8pt; row height \~0.25"; page numbers in title.
* Map: Positron tiles, dashed search radius ring, bbox midpoint center, `trusted = green` markers, others gray.

### Common “gotchas” & troubleshooting

* **Side bars / rails** — ensure the slide’s `ChartAnchor` ratio matches your intent (3:2 or 16:9). Exporter renders to the anchor ratio and captures the **map element**; if rails appear, check `[MAP QA] viewport=... target=...` logs.
* **PNG size mismatch** (e.g., expected 1400×800 got 1400×661) — headless viewport didn’t match the requested size. Verify Chrome/chromedriver match; logs should show `viewport=1400x800`. Exporter uses CDP to enforce; upgrade Chrome/driver if needed.
* **Off‑center map** — we use bbox midpoint + `setView([lat,lng], zoom)` to avoid snapping. Turn on `MAP_DEBUG_OVERLAY=1` to visualize center.
* **Missing geos** — businesses without lat/lng are flagged low by DQ and excluded from trusted exhibits.
* **Extreme YoY** — filtered by DQ; you can override in the UI.
* **PNG missing during export** — exporter asserts the expected PNG path after map generation; check Chrome/driver logs if it fails.

### Extending / customizing

* **Add an exhibit**: create a chart function that saves a PNG, then call `generate_chart_slide(title, png, summary_text)`.
* **Change section order/titles**: edit `generate_project_report.py`.
* **New placeholder?** Add to the replacement dict in `slides_admin.py` or `slides_summary.py`.
* **Map tweaks**: adjust legend text/size, marker radius, window height, or the zoom slider bounds in the UI.
