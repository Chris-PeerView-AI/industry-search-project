# ================================
# FILE: main_ui.py  (Preview-first, single-screen inputs)
# PURPOSE:
# - Show ALL options up front (no separate "advanced" panel)
# - Let user choose Preview vs Run from the first screen
# - Only execute Google calls AFTER explicit approval (if Preview) or "Run now"
# ================================

from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

# Your modules
from modules.google_search import (
    search_and_expand,
    _finalize_profile,
    _render_preview,
    geocode_location,
)
from modules.project_config import get_or_create_project, select_existing_project
from modules.review_results import review_and_edit
from modules.map_view_review import map_review

# ---------------------------------
# App bootstrap
# ---------------------------------
load_dotenv()
st.set_page_config(page_title="Industry Market Search Tool", layout="wide")
st.title("Industry/Market Google API & Enigma Pull Project")

# Session state
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
}
for k, v in defaults.items():
    st.session_state.setdefault(k, v)

# ---------------------------------
# STEP 0: Single-screen project inputs + options
# ---------------------------------
if st.session_state.step == 0:
    st.header("1. Create / Load Project")

    col_left, col_right = st.columns([2, 1])

    # --- Left: Create new project (standard fields from your existing helper)
    with col_left:
        st.subheader("New Project")

        # get_or_create_project renders the standard fields and returns a dict on "Create"
        created = get_or_create_project(
            default_name="Test: Golf Simulators in Northvale",
            default_industry="Golf Simulators",
            default_location="Northvale, New Jersey",
            default_target_count=20,
            default_max_radius_km=25,
        )

        # If user just created a new base project, keep it
        if created:
            st.session_state.project_config = created

        proj = st.session_state.project_config
        if proj:
            st.success("Project created. Configure options below and choose what to do next.")

        # Always show options if we have a project (no separate 'advanced' area)
        if proj:
            st.markdown("### Options")

            # LLM profile + focus (always visible)
            st.session_state.use_llm_profile = st.checkbox(
                "Enable LLM industry profile",
                value=st.session_state.use_llm_profile,
            )
            c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
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
                "breadth": project_with_opts.get("breadth", "normal"),
            })
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
""")

    view = st.radio("Choose View:", ["Map View", "Manual Review"], horizontal=True)
    if view == "Map View":
        map_review(project)
    else:
        review_and_edit(project)
