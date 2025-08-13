PeerView AI — End‑to‑End Pipeline (Phases 1–3)

A Streamlit + Python system for discovering local businesses, enriching them with third‑party data, and generating a polished market benchmarking report (PPTX → merged PDF).

Phase 1: Capture project details, use Google Places to find candidate businesses, and classify them with a local LLM (Ollama). Store all candidates for review.

Phase 2: Match each candidate to Enigma OperatingLocation(s), write a canonical mapping row, and pull per‑project financial metrics.

Phase 3: Review/curate trusted businesses, generate exhibits and a QA‑stable map, and export a final deck + merged PDF.

Quick Start

Create and populate a .env (see Environment).

Phase 1 — run the discovery UI to collect Google Places and tier them.

Phase 2 — run the Enigma UI to match, upsert mapping rows, and fetch metrics per project.

Phase 3 — run the Review/Export UI to adjust trusted flags and export the final PDF.

Phase 1 — Project setup & Google Places discovery

Goal: create a project, search Google Places for candidate businesses, and classify them (Tier 1..3), storing results for later phases.

Key modules (UI + logic)

main_ui.py — Orchestrates the Phase‑1 flow with 3 steps: project setup → Google search + LLM tiering → review (map or manual).

modules/project_config.py — Create or select an existing project (name, industry, location, target count, max radius).

modules/google_search.py — Geocode the center, generate a spiral grid, query Google Nearby Search at each grid point, then classify and insert results into Supabase.

modules/map_view_review.py — Map preview of all candidates with a search ring radius based on the farthest candidate.

modules/review_results.py — Manual review pane with tier filtering, notes, and flags; allows overrides and simple audits.

Data flow (Phase 1)

Project config is created or loaded and cached in st.session_state.

Geocode & spiral grid around the center (step size ≈2.5 km) within max_radius_km.

Nearby Search (Google Places) is executed per grid point with a fixed SEARCH_RADIUS_KM (default 5).

Classification via local LLaMA (Ollama) — result is parsed into JSON (tier, category, summary). Optional GPT‑4 re‑audit for Tier 1s if OPENAI_API_KEY is set.

Insert each unique candidate into search_results with project linkage and lat/lng.

Review on a map (pins colored by tier) or through a manual list with notes and flagging.

Supabase tables (Phase 1)

search_projects (project header)

id (uuid) — project identifier

name — e.g., "Test: Golf Simulators in Northvale"

industry — e.g., "Golf Simulators"

location — e.g., "Northvale, New Jersey"

target_count (int) — target unique businesses to collect

max_radius_km (int) — spiral search reach

optional: active (bool), use_gpt_audit (bool)

search_results (candidate businesses discovered per project)

Keys: id (uuid), project_id (uuid), place_id (text)

Core fields: name, address, city, state, zip, website, google_maps_url

Geo: latitude (float), longitude (float)

LLM/tiering: tier (int), tier_reason (text), category (text)

Ops: manual_override (bool), notes (text), flagged (bool)

Optional/derived: page_title (text) (from lightweight scrape)

Recommended indexes/constraints

CREATE INDEX IF NOT EXISTS search_results_project_idx ON public.search_results(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS search_results_project_place_uidx
  ON public.search_results(project_id, place_id);

Note: We standardize on Google’s place_id in Phase 1. In Phase 2’s mapping table we also include a dedicated google_places_id (alias of place_id) to support upserts and long‑term compatibility.

Phase 2 — Enigma matching & per‑project metrics

Goal: for each selected Google business, create a canonical mapping row to the Enigma OperatingLocation and pull card/transaction metrics keyed to the specific project.

Core components

UI: pull_enigma_ui.py (select candidates for pull, show match confidence, reasons, and deselect‑all for test loops).

Puller: modules/pull_enigma_data_for_business.py (GraphQL to Enigma; confidence scoring; mapping upsert; per‑project metrics write).

Matching & confidence (typical policy)

1.00 — street + city + state agree (name ≥ 0.85)

0.95 — name ≥ 0.93 with ZIP+state agree (city may differ)

0.90 — name ≥ 0.88 with ZIP+state agree

0.80 — street matches; only one of city/state agrees

0.70 — name + city + state match (no zip)

else 0.40

Behavior

Mapping is saved for any score (reviewable/upgradeable later).

Metrics are fetched only for conf ≥ 0.90.

Trusted defaults to conf ≥ 0.95 for presentation (configurable).

Supabase schema (Phase 2)

enigma_businesses — canonical mapping per Google Place

id (uuid) PK (default gen_random_uuid())

google_places_id (text) and/or place_id (text) — Google identifier

enigma_id (text) — Enigma OperatingLocation id

Google fields: business_name, full_address, city, state, zip

Enigma fields: enigma_name, matched_full_address, matched_city, matched_state, matched_postal_code

Diagnostics: match_confidence (numeric), match_reason (text), match_method (text)

Pull metadata: date_pulled (date), pull_session_id (uuid), pull_timestamp (timestamptz)

enigma_metrics — per‑project card metrics

Keys: id (uuid), business_id (uuid FK→enigma_businesses.id), project_id (uuid)

Measures: quantity_type (text) (e.g., card_revenue_amount, avg_transaction_size), raw_quantity (numeric), projected_quantity (numeric)

Periods: period (text), period_start_date (date), period_end_date (date)

Ops: pull_session_id, pull_timestamp, pull_status, pull_notes

Upsert & dedupe (required)

CREATE UNIQUE INDEX IF NOT EXISTS enigma_metrics_dedupe_project_uidx
  ON public.enigma_metrics (business_id, project_id, quantity_type, period, period_end_date);

Phase 3 — Benchmark Review & Report Export

Goal: curate trusted businesses, preview the map, and export a finalized deck (PPTX) merged into a single PDF.

UI & orchestration

benchmark_review_ui.py — select project, run DQ, toggle trusted/low, preview the map with a zoom slider, export.

modules/generate_project_report.py — export_project_pptx(project_id, supabase) builds all slides and merges PDFs.

modules/slides_exhibit.py — revenue, YoY, ticket size, market size, map; saves PNGs; stamps them into the Exhibit template.

modules/slides_summary.py — summary KPIs + LLM narrative; reuses the same map PNG as the map exhibit; appendix business tables.

modules/convert_slides_to_pdf.py — per‑slide PPTX → PDF → merged final.

modules/map_generator.py — stable, aspect‑ratio‑aware Leaflet screenshot pipeline (headless Chrome element capture).

Phase‑3 data consumers

search_projects — report metadata (industry/location) and selection.

enigma_summaries — curated row per matched business used to build exhibits: name, address, annual_revenue (12m), ticket_size, yoy_growth, latitude, longitude, benchmark (trusted/low), search_result_id.

search_results — lookup details such as tier_reason for appendix.

enigma_metrics — authoritative period_end_date for the “As of {Month YYYY}”.

Map generation (stability features)

Aspect‑ratio aware PNG render to the slide’s ChartAnchor ratio (no rails/cropping surprises).

Element‑level screenshot via Selenium of the map HTML element (no window chrome).

Forced viewport via CDP; crisp DPR=1; optional center‑dot overlay for QA.

Summary slide reuses the identical map PNG to ensure styling parity.

Environment

Required in .env:

SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
GOOGLE_PLACES_API_KEY=
ENIGMA_API_KEY=
LLM_MODEL=llama3            # for local LLaMA via Ollama
OPENAI_API_KEY=             # optional: GPT‑4 audit of Tier‑1s in Phase 1
GOOGLE_SERVICE_ACCOUNT_FILE=# used for downloading PPTX templates (Phase 3)

Chrome requirement (Phase 3 map exporter): Headless Chrome + matching chromedriver available on the host.

How to Run

Phase 1 (discovery)

streamlit run main_ui.py
# 1) Define or Load Project
# 2) Run Google Search and Categorize
# 3) Review Results (Map View or Manual Review)

Phase 2 (Enigma match & metrics)

streamlit run pull_enigma_ui.py
# Deselect all → select a few to test → Submit Selected
# watch console for [pull]/[match]/[DB]/[metrics]

Phase 3 (review & export)

streamlit run benchmark_review_ui.py
# choose project → run DQ → toggle trusted → export PPTX/PDF

Or call the exporter directly:

from modules.generate_project_report import export_project_pptx, supabase
export_project_pptx("<project-uuid>", supabase)

Troubleshooting Quick Reference

Google quota / warning — Nearby Search returns error_message: retry later or narrow the grid; check API key & billing.

Duplicate candidates — ensure the (project_id, place_id) unique index exists; we also de‑dup in memory by place_id before inserts.

PNG size mismatch (Phase 3 map) — verify Chrome/chromedriver match; the exporter uses CDP to lock viewport size.

ON CONFLICT error (Phase 2) — DB lacks the dedupe unique index for metrics; create enigma_metrics_dedupe_project_uidx.

FK error (Phase 2) — write the mapping row to enigma_businesses and use its id for enigma_metrics.business_id.

Appendix — SQL snippets

Phase‑1 sanity checks

-- Count per project
SELECT project_id, COUNT(*)
FROM public.search_results
GROUP BY 1
ORDER BY 2 DESC;

-- Tier distribution
SELECT tier, COUNT(*) FROM public.search_results GROUP BY 1 ORDER BY 1;

-- Flags & overrides
SELECT COUNT(*) FILTER (WHERE manual_override) AS overrides,
       COUNT(*) FILTER (WHERE flagged) AS flagged
FROM public.search_results
WHERE project_id = '<project-uuid>';

Phase‑2 indices

CREATE EXTENSION IF NOT EXISTS pgcrypto;
ALTER TABLE public.enigma_businesses ALTER COLUMN id SET DEFAULT gen_random_uuid();
CREATE UNIQUE INDEX IF NOT EXISTS enigma_metrics_dedupe_project_uidx
  ON public.enigma_metrics (business_id, project_id, quantity_type, period, period_end_date);

Phase‑3 latest end date

SELECT MAX(period_end_date) FROM public.enigma_metrics WHERE project_id = '<project-uuid>';

Open items to confirm (non‑blocking)

place_id vs google_places_id: keep both in enigma_businesses for clarity? Current recommendation is to store place_id in Phase 1 and mirror it as google_places_id in Phase 2 for upserts.

Map popup fields: do we want to persist scraped headers and Google types on search_results so the map popups can always show them?

Radius strategy: the current Phase 1 flow uses a spiral grid with a fixed 5 km Nearby Search radius per grid point. Do we also want the adaptive radius escalation (e.g., 1 km → 2.5 km → 5 km) behavior discussed earlier?

Tier‑1 audit: keep the optional GPT‑4 audit toggle in Phase 1, or rely only on the local LLaMA model?

