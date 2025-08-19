# ================================
# FILE: main_ui.py  (Preview-first, single-screen inputs)
# PURPOSE:
# - Show ALL options up front (no separate "advanced" panel)
# - Let user choose Preview vs Run from the first screen
# - Only execute Google calls AFTER explicit approval (if Preview) or "Run now"
# ================================

"""
Phase-1 UI — Status & TODO (last updated: 2025-08-18)

WHAT WORKS TODAY
- One-page “New Project” form (no hidden advanced pane):
  • Inputs: project name, industry, location, target_count, max_radius_km, grid_step_km,
    fixed search_radius_km, breadth (narrow/normal/wide), oversample_factor,
    LLM profile on/off, LLM planner on/off, optional brand/subtype focus (+strict).
  • LLM Tiering controls: enable/disable LLM-only tier selection, choose provider (OpenAI/Ollama),
    specify tiering model (e.g., gpt-4o-mini or llama3). When enabled, numeric score is used
    only to rank within a tier; the LLM decides T1/T2/T3.
  • “Preview first” checkbox: if set, we show a plan page; if clear, we run immediately.
- Preview Plan gate:
  • Shows geocode, grid sample, and *multi-keyword* plan (LLM or fallback), with
    est_nearby_calls_max and the exact URLs we would hit (no API calls yet).
  • We *do not* pass Google “type” filters during discovery (recall-first).
  • Type hint is used for SCORING DISPLAY ONLY and is sanitized against keywords.
  • Planner “exclude_keywords” are **not** used to filter Queries; they’re saved and
    applied as soft negatives during scoring only (hybrid-friendly).
- Run (on approval):
  • Grid execution with fixed search radius, multiple keywords per node, oversample stop.
  • Place Details fetch; lightweight web scrape (title/meta/H1-H3/body sample + schema.org types).
  • Numeric scoring (0–100) with transparent reasons (+ small schema.org bonus).
  • Tier assignment:
      - If **LLM Tiering** is enabled → LLM picks T1/T2/T3; numeric score ranks within-tier.
      - If disabled → numeric → optional LLM re-audit override (confidence ≥ 0.70).
- Persistence (Supabase):
  • `search_projects.profile_json` (profile knobs), `search_projects.planner_json` (keywords plan).
  • `search_results` rows include: website, eligibility_score, score_reasons, predicted_tier (numeric),
    tier, tier_reason (LLM explanation or numeric/rationale), tier_source, audit_confidence,
    tier_llm_model (when LLM-tiered), web_signals (schema_types), coordinates, maps URL, etc.
- Review:
  • “Manual Review” sorted by Tier (1→3). Inside Tier, we sort by LLM confidence (desc) then
    numeric score (desc). Score & reasons are behind a “Details” toggle.
  • “Map View” available for spatial inspection.

KNOWN LIMITS / ROUGH EDGES
- Simulator venues (e.g., Players Club Virtual Golf, Golfzon Social) can land in Tier-3 if reviews are sparse
  or the page is JS-heavy and we miss body text; name-only evidence isn’t yet explicitly boosted.
- Planner “exclude_keywords” are treated as soft phrase-level penalties only in the fallback scoring shim.
  If your `phase1_lib` scoring is present, ensure it mirrors this behavior (recall-first).
- No query budget guard yet beyond the preview estimate; large grids × many keywords can be slow.
- De-duplication is currently Places-ID based (Google often dedups for us), not name+distance clustering.
- No per-industry special heuristics beyond the guardrailed defaults (by design); we rely on LLM+tokens.

NEAR-TERM TODO (SMALL, TESTABLE STEPS)
1) Simulator Evidence Boost (precision for Tier-1 without brand penalties)
   - Add helper that detects simulator signals in NAME and WEB (tokens: “golfzon”, “x-golf”, “five iron”,
     “trackman”, “foresight”, “uneekor”, “trugolf”, “skytrak”, “simulator”, “screen/virtual/indoor golf”,
     “golf lounge/studio/bay/suite”).
   - Apply a **score floor** (e.g., ≥62 name-only; ≥68 name+web) so true sim venues clear T1 threshold.
   - Add a **Tier-1 precision gate**: require any simulator evidence for T1; otherwise keep at T2.
   - Persist `sim_evidence` on each result; include in LLM audit payload.

2) Planner & Recall Controls
   - Cap keywords per node based on breadth *and* estimated budget; show a “reduce keywords” prompt if
     est_nearby_calls_max exceeds a threshold (e.g., 600).
   - If post-run unique candidates < target/2, auto-expand keyword set (fallback expansions).

3) Scoring Parity (if using phase1_lib’s full scoring)
   - Mirror “phrase-level soft negatives” behavior (recall-first), schema.org bonus, and website/rating floors.
   - Ensure type soft-denies only cover *obvious off-targets* (courses/ranges/parks); do not penalize
     venues typed as bar/restaurant if the name looks like a sim lounge.

4) UX polish
   - Manual review: add pill badges for brand tokens detected and schema types; expose `audit_confidence`.
   - Map: cluster markers; color by Tier; hover shows name + quick actions.

5) Ops/Resilience
   - Rate delay knob in UI; friendly messages for OVER_QUERY_LIMIT/REQUEST_DENIED.
   - Optional name+distance (≤150m) de-dup pass to collapse chain duplicates.

FLAGS / ENVs TO KNOW
- GOOGLE_PLACES_API_KEY — required.
- SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY — required for persistence.
- OLLAMA_URL / LLM_MODEL — used for local LLM profile/planner/audit.
- OPENAI_API_KEY — required if using OpenAI for LLM Tiering (GPT-4 family).
- ENABLE_LLM_AUDIT=1|0 — toggles re-audit when numeric-tier path is used.

SCHEMA ASSUMPTIONS (run the migrations we provided)
- search_projects: add JSONB columns `profile_json`, `planner_json`, booleans `use_llm_profile`, `use_llm_planner`,
  and (for tiering) `enable_llm_tiering` (bool), `tier_llm_provider` (text), `tier_llm_model` (text).
- search_results: add columns `eligibility_score` (int), `score_reasons` (text/json),
  `predicted_tier` (int), `tier_reason` (text), `tier_source` (text), `audit_confidence` (float),
  `tier_llm_model` (text), `web_signals` (jsonb). Keep `manual_override` boolean.

HOW TO TEST QUICKLY
- Try “Golf Simulators” in a suburb (e.g., Northvale, NJ) with breadth=normal, search_radius_km=5,
  grid_step_km=2.5, oversample_factor=2.0. Use preview to sanity-check calls. Approve & Run, then:
  • Verify Players Club Virtual Golf / Golfzon Social classification (currently may land T3 → TODO #1).
  • Check that courses/ranges aren’t T1 (precision gate in TODO #1 will harden this).
  • In Manual Review, confirm Tier sort and details toggle show reasons/web signals.

Keep this block updated as we iterate. It’s a reference for what’s intentional vs. pending work.
"""

from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv

# Project helpers (you already have these in your repo)
from modules.project_config import get_or_create_project, select_existing_project

# Google/Search step (Step 1/2 functions live here)
from modules.google_search import (
    search_and_expand,
    _finalize_profile,
    _render_preview,
    geocode_location,
)

# Review & Map
from modules.review_results import review_and_edit
from modules.map_view_review import map_review

# ---------------------------------
# App bootstrap
# ---------------------------------
load_dotenv()
st.set_page_config(page_title="Industry/Market Google API & Enigma Pull Project", layout="wide")
st.title("Industry/Market Google API & Enigma Pull Project")

# Step state
if "step" not in st.session_state:
    st.session_state.step = 0
if "project_config" not in st.session_state:
    st.session_state.project_config = None

# Persist UI fields across reruns
defaults = {
    "use_llm_profile": True,
    "focus_detail": "",
    "focus_strict": False,
    "search_radius_km": 5.0,
    "grid_step_km": 2.5,
    "action": "Preview first",  # or "Run now"
    # New LLM-tiering defaults
    "enable_llm_tiering": False,
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# Provider/model defaults for tiering
st.session_state.setdefault("tier_llm_provider", "ollama")
st.session_state.setdefault("tier_llm_model", os.getenv("LLM_MODEL", "llama3"))
st.session_state.setdefault("_last_provider", st.session_state.tier_llm_provider)

# ---------------------------------
# STEP 0: Single-screen project inputs + options
# ---------------------------------
if st.session_state.step == 0:
    st.header("1. Create / Load Project")

    col_left, col_right = st.columns([2, 1])

    # --- Left: Create new project (standard fields from your existing helper)
    with col_left:
        st.subheader("New Project")

        base_project = get_or_create_project(
            default_name="Test: Golf Simulators in Northvale",
            default_industry="Golf Simulators",
            default_location="Northvale, New Jersey",
            default_target_count=20,
            default_max_radius_km=25,
        )

        # If user just created a new base project, keep it
        if base_project:
            st.session_state.project_config = base_project

        proj = st.session_state.project_config
        if proj:
            st.success("Project created. Configure options below and choose what to do next.")

            # Always show options (no separate 'advanced' area)
            st.markdown("### Options")

            # LLM profile + focus (always visible)
            st.session_state.use_llm_profile = st.checkbox(
                "Enable LLM industry profile",
                value=st.session_state.use_llm_profile,
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                st.session_state.focus_detail = st.text_input(
                    "Brand/Subtype focus (optional)",
                    value=st.session_state.focus_detail,
                    placeholder="e.g., Drybar, Quick Quack, Scooter's Coffee",
                )
            with c2:
                st.session_state.focus_strict = st.checkbox(
                    "Strict brand only",
                    value=st.session_state.focus_strict,
                )

            # Fixed radius strategy (always visible)
            c3, c4 = st.columns(2)
            with c3:
                st.session_state.search_radius_km = st.number_input(
                    "Search radius (km)",
                    min_value=1.0, max_value=50.0, step=0.5,
                    value=float(st.session_state.search_radius_km),
                )
            with c4:
                st.session_state.grid_step_km = st.number_input(
                    "Grid step (km)",
                    min_value=0.5, max_value=10.0, step=0.5,
                    value=float(st.session_state.grid_step_km),
                )

            # --- NEW: LLM Tiering controls (LLM chooses the tier; score ranks within tier)
            st.markdown("### Tiering (LLM)")

            st.session_state.enable_llm_tiering = st.checkbox(
                "Use LLM-only for tier selection (score used only for rank within tier)",
                value=st.session_state.enable_llm_tiering,
                help="If on, the LLM decides T1/T2/T3 per candidate. Numeric score is still computed and used to order items within the same tier."
            )

            provider_display = st.selectbox(
                "Tiering LLM",
                ["OpenAI (GPT-4 family)", "Ollama (local)"],
                index=(0 if st.session_state.tier_llm_provider == "openai" else 1),
            )
            selected_provider = "openai" if "OpenAI" in provider_display else "ollama"

            # If provider changed, snap the model default appropriately
            if st.session_state._last_provider != selected_provider:
                st.session_state.tier_llm_model = "gpt-4o-mini" if selected_provider == "openai" else os.getenv("LLM_MODEL", "llama3")
                st.session_state._last_provider = selected_provider

            st.session_state.tier_llm_provider = selected_provider
            st.session_state.tier_llm_model = st.text_input(
                "Tier LLM model",
                value=st.session_state.tier_llm_model,
                help=("Examples: OpenAI → gpt-4o-mini, gpt-4o; Ollama → llama3, mistral, qwen2, etc.")
            )

            # Choose action (Preview vs Run)
            st.session_state.action = st.radio(
                "Action",
                ["Preview first", "Run now"],
                horizontal=True,
                index=0 if st.session_state.action == "Preview first" else 1,
            )

            # Build a full project dict (do NOT run yet)
            project_with_opts = dict(proj)
            project_with_opts.update({
                "use_llm_profile": bool(st.session_state.use_llm_profile),
                "focus_detail": st.session_state.focus_detail or None,
                "focus_strict": bool(st.session_state.focus_strict),

                "preview_mode": (st.session_state.action == "Preview first"),
                "search_radius_km": float(st.session_state.search_radius_km),
                "grid_step_km": float(st.session_state.grid_step_km),

                "use_llm_planner": True,        # default on
                "oversample_factor": 3.0,       # recall-first; safe to tune later

                # NEW: tiering knobs passed to search_and_expand()
                "enable_llm_tiering": bool(st.session_state.enable_llm_tiering),
                "tier_llm_provider": st.session_state.tier_llm_provider,
                "tier_llm_model": st.session_state.tier_llm_model,
            })
            # keep breadth stable if present; default to "normal"
            project_with_opts["breadth"] = project_with_opts.get("breadth", "normal")

            st.session_state.project_config = project_with_opts

            # Continue button (single, stable trigger)
            if st.button("Continue", type="primary"):
                if st.session_state.action == "Preview first":
                    st.session_state.step = 1
                else:
                    st.session_state.step = 3
                st.rerun()

    # --- Right: Load existing project (jumps straight to review)
    with col_right:
        st.subheader("Existing Project")
        existing = select_existing_project()
        if existing:
            st.session_state.project_config = existing
            # Existing projects go straight to review
            st.session_state.step = 2
            st.rerun()

# ---------------------------------
# STEP 1: Preview screen (no API calls yet)
# ---------------------------------
elif st.session_state.step == 1:
    st.header("2. Preview Plan")

    project = st.session_state.project_config
    if not project:
        st.session_state.step = 0
        st.rerun()

    try:
        # Coordinates for grid preview
        lat, lng = geocode_location(project["location"])
        # Finalize & persist profile_json on project
        settings, profile_json, type_hint, keyword = _finalize_profile(project)
        # Show preview and wait for explicit approval
        approved = _render_preview(lat, lng, project, settings, type_hint, keyword)
    except Exception as e:
        st.error(f"Preview setup failed: {e}")
        if st.button("⬅️ Back"):
            st.session_state.step = 0
            st.rerun()
        st.stop()

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("⬅️ Cancel / Edit Settings"):
            st.session_state.step = 0
            st.rerun()
    with col_b:
        if approved:
            st.session_state.step = 3
            st.rerun()

# ---------------------------------
# STEP 3: Execute search (only after Preview approval or Run now)
# ---------------------------------
elif st.session_state.step == 3:
    st.header("3. Run Google Search and Categorize")

    project = st.session_state.project_config
    if not project:
        st.session_state.step = 0
        st.rerun()

    # Ensure no preview inside the executor
    run_project = dict(project)
    run_project["preview_mode"] = False

    finished = search_and_expand(run_project)
    if finished:
        st.session_state.step = 2
        st.rerun()

# ---------------------------------
# STEP 2: Review
# ---------------------------------
elif st.session_state.step == 2:
    st.header("4. Review Results")

    project = st.session_state.project_config
    if not project:
        st.session_state.step = 0
        st.rerun()

    st.markdown(f"""
- **Name**: {project.get('name')}
- **Industry**: {project.get('industry')}
- **Location**: {project.get('location')}
- **Target Count**: {project.get('target_count')}
- **Max Radius**: {project.get('max_radius_km')} km
- **LLM Profile**: {"On" if project.get('use_llm_profile') else "Off"}
- **LLM Tiering**: {"On" if project.get('enable_llm_tiering') else "Off"}{(" · " + (project.get('tier_llm_provider') or "") + " · " + (project.get('tier_llm_model') or "")) if project.get('enable_llm_tiering') else ""}
""")

    view = st.radio("Choose View:", ["Map View", "Manual Review"], horizontal=True)
    if view == "Map View":
        map_review(project)
    else:
        review_and_edit(project)
