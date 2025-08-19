# ==============================================
# modules/Phase1_apply_ui.py — Streamlit admin for Phase‑1 apply
# Small & testable: pick a project → choose provider/model/limit → apply to Supabase.
# Uses apply_phase1_to_project() under the hood.
# ==============================================
import os
import json
import streamlit as st

from Phase1_apply import apply_phase1_to_project

# Known projects for convenience (edit as needed)
KNOWN_PROJECTS = {
    "Golf Simulators": "23c25087-db5a-49bc-ad6f-432f7480acdf",
    "Medspa": "675a043a-e47e-44cb-8cfa-d09ad4107d2d",
    "Coffee Shops": "27e72d0e-a4bc-4a7f-9b37-feea156e1d2a",
}

st.set_page_config(page_title="Phase‑1 Apply", layout="centered")
st.title("Phase‑1 Apply to Supabase (High‑Recall)")

# --- Controls ---
col1, col2 = st.columns(2)
with col1:
    project_name = st.selectbox("Project", list(KNOWN_PROJECTS.keys()))
with col2:
    project_id = st.text_input("Project ID", value=KNOWN_PROJECTS[project_name])

provider = st.radio("Provider", ["none", "ollama", "openai"], index=0, horizontal=True)
model_default = "llama3" if provider == "ollama" else ("gpt-4o-mini" if provider == "openai" else "")
model = st.text_input("Model (optional)", value=model_default)

col3, col4 = st.columns(2)
with col3:
    limit = st.number_input("Limit (optional)", min_value=1, step=10, value=50)
with col4:
    dry_run = st.checkbox("Dry run (no writes)", value=True)

run = st.button("Apply Phase‑1")

# --- Run ---
if run:
    with st.spinner("Running Phase‑1…"):
        out = apply_phase1_to_project(
            project_id=project_id,
            provider=provider,
            model=(model or None),
            limit=int(limit) if limit else None,
            dry_run=dry_run,
        )
    st.success("Done")

    st.subheader("Summary")
    st.json({k: v for k, v in out.items() if k != "sample"})

    st.subheader("Sample updates")
    if out.get("sample"):
        st.dataframe(out["sample"])
    else:
        st.write("(No sample rows)")

st.caption("Environment: requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or ANON key) in .env")
