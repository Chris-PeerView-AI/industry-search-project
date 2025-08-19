# ================================
# FILE: modules/TEST_IndustryClassification.py
# PURPOSE: Offline LLM tiering harness (no Google calls)
# - Loads candidates for a project_id from Supabase
# - Builds an industry taxonomy (OpenAI | Llama | Fallback)
# - Extracts evidence per candidate and assigns Tier 1/2/3 (balanced or high-recall)
# - (Optional) Runs an OpenAI evaluator pass on borderline T1/T2 in batches
# - Shows a sortable table + CSV download with rich evidence columns
# ================================

from __future__ import annotations

# --- path shim (works whether launched from repo root or modules/) ---
import os, sys
HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import json
import pandas as pd
import streamlit as st
from dotenv import load_dotenv, find_dotenv

# --- env + setup ---
# Always load .env from project root (ROOT/.env), regardless of where Streamlit is launched
DOTENV_PATH = os.path.join(ROOT, ".env")
if os.path.exists(DOTENV_PATH):
    load_dotenv(dotenv_path=DOTENV_PATH)
else:
    # fallback: search upwards just in case
    load_dotenv(find_dotenv(usecwd=True))

# OpenAI helpers (optional)
OPENAI_AVAILABLE = True
try:
    from modules.openai_taxonomy import fetch_taxonomy_openai
    from modules.openai_evaluator import evaluate_tiers_openai
except Exception as _e:
    OPENAI_AVAILABLE = False
    fetch_taxonomy_openai = None
    evaluate_tiers_openai = None

# Rubric post-processor (required)
from Phase1_rubric import process_taxonomy

# --- strict imports of our rubric/tiering API ---
try:
    from Phase1_rubric import build_taxonomy  # industry-agnostic taxonomy builder
    from Phase1_tiering import extract_evidence, choose_tier  # evidence + tier decision
except Exception as e:
    st.error(f"Import error in Phase1_* modules: {e}")
    st.stop()

# --- optional Supabase (we'll render UI even if missing) ---
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None
    Client = None

# --- env + setup ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL") or ""
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
OLLAMA_MODEL = os.getenv("LLM_MODEL", "llama3")
DEFAULT_PROJECT = os.getenv("TEST_PROJECT_ID", "23c25087-db5a-49bc-ad6f-432f7480acdf")

st.set_page_config(page_title="TEST — Industry Tiering", layout="wide")
st.title("TEST — LLM Tiering (Industry-agnostic, no Google calls)")
st.caption("Builds a rubric for your industry, extracts evidence for each candidate, and assigns Tier 1/2/3. Use Bias=balanced for review, high-recall for seeding.")

# Sidebar diagnostics
st.sidebar.header("Environment")
st.sidebar.write(f"SUPABASE_URL: {'✅ set' if SUPABASE_URL else '❌ missing'}")
st.sidebar.write(f"SUPABASE_SERVICE_ROLE_KEY: {'✅ set' if SUPABASE_KEY else '❌ missing'}")
st.sidebar.write(f"OLLAMA model (LLM_MODEL): `{OLLAMA_MODEL}`")
st.sidebar.write(f"OPENAI_API_KEY: {'✅ set' if os.getenv('OPENAI_API_KEY') else '—'}")
st.sidebar.write(f"CWD: {os.getcwd()}")
st.sidebar.write(f".env at ROOT exists: {'✅' if os.path.exists(os.path.join(ROOT,'.env')) else '❌'}")
st.sidebar.write(f"OPENAI_API_KEY length: {len((os.getenv('OPENAI_API_KEY') or '').strip())}")
st.sidebar.write(f"OPENAI_AVAILABLE: {OPENAI_AVAILABLE}")
if not OPENAI_AVAILABLE:
    import traceback
    try:
        from modules import openai_taxonomy as _tmp_ot, openai_evaluator as _tmp_oe
    except Exception as _imp_err:
        st.sidebar.write("OpenAI import error detail:")
        st.sidebar.code("".join(traceback.format_exception_only(type(_imp_err), _imp_err)))


# --- helpers ---

def _connect_supabase():
    if not create_client:
        st.warning("supabase client not installed (`pip install supabase`). Running without DB.")
        return None
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.warning("Supabase env vars missing. Running without DB.")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"Could not initialize Supabase: {e}")
        return None


def _load_project(sb, project_id: str):
    if not sb:
        return {"industry": "(unknown)", "focus_detail": ""}
    try:
        res = sb.table("search_projects").select("*").eq("id", project_id).execute()
        rows = res.data or []
        return rows[0] if rows else None
    except Exception as e:
        st.error(f"Error loading project: {e}")
        return None


def _load_candidates(sb, project_id: str, limit: int = 500):
    if not sb:
        st.warning("No DB client; cannot fetch candidates.")
        return []
    try:
        # Pull common fields; web_signals may include schema_types etc.
        res = sb.table("search_results").select(
            "id,name,website,page_title,web_signals,tier,tier_reason,category,city,state"
        ).eq("project_id", project_id).limit(limit).execute()
        return res.data or []
    except Exception as e:
        st.error(f"Error loading candidates: {e}")
        return []

# --- UI controls ---
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    project_id = st.text_input("Project ID", value=DEFAULT_PROJECT)
with col2:
    taxonomy_source = st.selectbox("Taxonomy source", ["OpenAI (Responses, 1x)", "Llama/Ollama", "Fallback only"], index=0)
with col3:
    provider = st.selectbox("Tier Provider", ["none", "ollama", "openai"], index=0)
with col4:
    model = st.text_input("Model", value=OLLAMA_MODEL if provider == "ollama" else "gpt-5-mini")

bias = st.radio("Bias", ["balanced", "high-recall"], index=0, horizontal=True)
max_rows = st.number_input("Max rows", min_value=10, max_value=2000, step=10, value=300)

# Evaluator controls
colE1, colE2 = st.columns([1,1])
with colE1:
    eval_mode = st.selectbox("Evaluator", ["Off", "Borderline only", "All T1+T2"], index=0)
with colE2:
    eval_model = st.text_input("Eval model", value=os.getenv("OPENAI_EVAL_MODEL", "gpt-5-mini"))

run = st.button("Build taxonomy & classify", type="primary")

# --- main execution guarded with try/except so we always show something ---
try:
    sb = _connect_supabase()
    proj = _load_project(sb, project_id)
    if not proj:
        st.error("Project not found or DB offline.")
        st.stop()

    industry = proj.get("industry") or proj.get("name") or "(unknown)"
    focus = proj.get("focus_detail") or (proj.get("profile_json") or {}).get("focus_detail") or ""

    st.markdown(f"**Industry:** {industry}  ·  **Focus:** {focus or '—'}  ·  **Provider:** {provider}  ·  **Model:** {model}  ·  **Bias:** {bias}")

    with st.spinner("Building industry taxonomy…"):
        if taxonomy_source.startswith("OpenAI"):
            if not OPENAI_AVAILABLE:
                st.error("OpenAI client not available. Install `openai` and set OPENAI_API_KEY, or choose Llama/Fallback.")
                st.stop()
            raw_tax = fetch_taxonomy_openai(industry, focus=focus, model=model)
            taxonomy = process_taxonomy(raw_tax, industry, focus=focus)
            taxonomy["_provider"] = "openai-responses"
            taxonomy["_model"] = model
        elif taxonomy_source.startswith("Llama"):
            taxonomy = build_taxonomy(industry, focus=focus, provider="ollama", model=OLLAMA_MODEL)
        else:
            taxonomy = build_taxonomy(industry, focus=focus, provider="none", model=None)
        taxonomy["_industry"] = (industry or "").strip()

    st.caption("Taxonomy used (LLM-derived or fallback):")
    st.json(taxonomy)

    rows = _load_candidates(sb, project_id, limit=int(max_rows))
    if not rows:
        st.warning("No candidates returned from search_results for this project.")
        st.stop()

    out_records = []
    prog = st.progress(0.0, text="Classifying…")
    total = len(rows)

    for i, r in enumerate(rows, start=1):
        name = r.get("name") or ""
        website = r.get("website") or ""
        page_title = r.get("page_title") or ""

        # optional structured signals
        schema_types = []
        categories = []
        ws = r.get("web_signals")
        if isinstance(ws, dict):
            schema_types = ws.get("schema_types", []) or []

        # category may be a string or list in search_results
        cat = r.get("category")
        if isinstance(cat, list):
            categories = cat
        elif isinstance(cat, str) and cat.strip():
            categories = [cat.strip()]

        # Build evidence (lightweight; no live scraping)
        ev = extract_evidence(
            taxonomy,
            name=name,
            website=website,
            page_title=page_title,
            schema_types=schema_types,
            categories=categories,  # <— NEW
            google_types=[],
            text_snippet="",  # keep cheap for harness
        )

        # Ask tier engine (balanced or high-recall). Extra context (snippets) is folded into evidence.
        decision = choose_tier(
            industry,
            taxonomy,
            ev,
            provider=provider,
            model=model,
            bias=bias,
            snippets={"name": name, "page_title": page_title, "website": website},
        )

        hits = decision.get("hits", {})
        flags = decision.get("flags", {})
        out_records.append({
            "id": r.get("id"),
            "name": name,
            "website": website,
            "tier": decision.get("tier"),
            "conf": round(float(decision.get("confidence") or 0.0), 2),
            "reason": decision.get("reason") or "",
            "primary_hits": hits.get("primary"),
            "adjacent_hits": hits.get("adjacent"),
            "disqualifier_hits": hits.get("disqualifier"),
            "venue_taxonomy": hits.get("venue_taxonomy"),
            "venue_fallback": hits.get("venue_fallback"),
            "brand_hits": hits.get("brand"),
            "flags": flags,
            "page_title": page_title,
            "category": r.get("category"),
        })

        if total:
            prog.progress(i / total, text=f"Classified {i}/{total}")

    prog.empty()

    df = pd.DataFrame(out_records)

    # Optional evaluator (OpenAI) on a subset to minimize cost
    if eval_mode != "Off":
        if not OPENAI_AVAILABLE:
            st.warning("Evaluator selected, but OpenAI client is unavailable. Skipping evaluator.")
        else:
            # Filter candidates
            def borderline(row):
                if row["tier"] == 1 and 0.70 <= row["conf"] < 0.85:
                    return True
                if row["tier"] == 2:
                    syn_hit = bool(row["primary_hits"]) or bool(row["adjacent_hits"])
                    ven_ok = bool(row["venue_taxonomy"])
                    return syn_hit and ven_ok
                return False

            if eval_mode == "Borderline only":
                eval_df = df[df.apply(borderline, axis=1)].copy()
            else:
                eval_df = df[df["tier"].isin([1, 2])].copy()

            # Build items payload
            items = []
            for _, row in eval_df.iterrows():
                items.append({
                    "id": row["id"],
                    "name": row["name"],
                    "page_title": row["page_title"],
                    "category": row["category"],
                    "primary_hits": row["primary_hits"] or [],
                    "adjacent_hits": row["adjacent_hits"] or [],
                    "disqualifier_hits": row["disqualifier_hits"] or [],
                    "venue_hits_taxonomy": row["venue_taxonomy"] or [],
                    "venue_hits_fallback": row["venue_fallback"] or [],
                    "tier": int(row["tier"]),
                    "confidence": float(row["conf"]),
                    "reason": row["reason"],
                })

            with st.spinner(f"Evaluating {len(items)} borderline items…"):
                suggestions = evaluate_tiers_openai(items, taxonomy, model=eval_model, batch_size=50)

            # Join suggestions back
            sugg_by_id = {s["id"]: s for s in suggestions}
            df["eval_suggested_tier"] = df["id"].map(lambda x: sugg_by_id.get(x, {}).get("suggested_tier"))
            df["eval_disposition"] = df["id"].map(lambda x: sugg_by_id.get(x, {}).get("disposition"))
            df["eval_conf"] = df["id"].map(lambda x: sugg_by_id.get(x, {}).get("confidence"))
            df["eval_reason"] = df["id"].map(lambda x: sugg_by_id.get(x, {}).get("reason"))
            df["eval_evidence_tags"] = df["id"].map(lambda x: sugg_by_id.get(x, {}).get("evidence_tags"))

    # Ensure eval_* columns exist even if evaluator is Off
    for col in ["eval_suggested_tier","eval_disposition","eval_conf","eval_reason","eval_evidence_tags"]:
        if col not in df.columns:
            df[col] = None

    if not df.empty:
        df = df.sort_values(by=["tier", "conf"], ascending=[True, False])

    st.subheader("Results")
    display_cols = [
        "tier", "conf", "eval_suggested_tier", "eval_disposition", "eval_conf",
        "name", "website", "page_title", "category",
        "reason", "eval_reason", "eval_evidence_tags", "flags",
        "primary_hits", "adjacent_hits", "disqualifier_hits", "venue_taxonomy", "venue_fallback", "brand_hits",
    ]
    st.dataframe(df[display_cols].sort_values(by=["tier", "conf"], ascending=[True, False]), use_container_width=True)

    st.download_button(
        "Download CSV",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name="tiering_results.csv",
        mime="text/csv",
    )

except Exception as e:
    st.error("An unexpected error occurred.")
    st.exception(e)
