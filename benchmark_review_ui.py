# PATCHED: benchmark_review_ui.py — add side-by-side Enigma/Google fields for the selected business
# Changes (Aug 12, 2025):
# 1) Remove Seasonality Ratio from the selected-business detail block.
# 2) Append Enigma-side fields (Enigma Name, Enigma Address) and the mapping confidence score.
# 3) Only show the top/most-recent mapping per Google Place (rn=1 equivalent), and include rows down to 0.70 confidence.
# 4) Keep existing benchmark toggle behavior (writes to enigma_summaries.benchmark).
#
# NOTE: This file shows only the modified sections + a small helper. Integrate these blocks into your existing file.

import streamlit as st
import pandas as pd
import os
from dotenv import load_dotenv
from supabase import create_client, Client
from modules.business_metrics import generate_enigma_summaries, summarize_benchmark_stats
from modules.OLD_pdf_export import export_project_pdf
from modules.pdf_only_export import generate_final_pdf
from modules.generate_project_report import export_project_pptx
from modules.pdf_only_export import generate_final_pdf, get_project_meta
from modules.slides_admin import generate_title_slide
from geopy.distance import geodesic
from modules.map_generator import build_map
from streamlit_folium import st_folium

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Benchmark Review Tool", layout="wide")
st.title("📊 Benchmark Review & Report Prep")


# ---------------- Helper: fetch latest Enigma mapping for a given search_result_id ----------------

def fetch_latest_mapping_for_search_result(search_result_id: str):
    """Return the most-recent mapping row from enigma_businesses for the Google Place behind this search_result.
    Uses search_results.place_id to join to enigma_businesses.place_id (since search_results.google_places_id may not exist).
    Includes matches down to 0.70 confidence. Returns a dict or None.
    """
    if not search_result_id:
        return None
    # Look up the Place ID for this search result row (no google_places_id column in this DB)
    sr = (
        supabase.table("search_results")
        .select("id, place_id")
        .eq("id", search_result_id)
        .limit(1)
        .execute()
        .data
    )
    if not sr:
        return None
    place_id = sr[0].get("place_id")
    if not place_id:
        return None

    # Get the latest mapping row for this place (rn=1 equivalent)
    eb_rows = (
        supabase.table("enigma_businesses")
        .select(
            "id, business_name, full_address, city, state, zip, "
            "enigma_name, matched_full_address, matched_city, matched_state, matched_postal_code, "
            "match_confidence, match_reason, place_id, enigma_id, pull_timestamp, date_pulled"
        )
        .eq("place_id", place_id)
        .order("pull_timestamp", desc=True)
        .order("date_pulled", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not eb_rows:
        return None
    row = eb_rows[0]
    # Enforce 0.70 lower bound per requirements
    try:
        conf = float(row.get("match_confidence") or 0.0)
    except Exception:
        conf = 0.0
    if conf < 0.70:
        return None
    return row


# ---------------- Project Picker (unchanged) ----------------
active_only = st.checkbox("Show Active Projects Only", value=True)
project_list_res = supabase.table("search_projects").select("id, name").order("created_at", desc=True).execute()
project_data = project_list_res.data
if active_only:
    project_data = [row for row in project_data if not row["name"].startswith("Test:")]
project_options = {row["name"]: row["id"] for row in project_data}
project_name = st.selectbox("Select Project", list(project_options.keys()))
project_id = project_options[project_name]

# Ensure project output dir exists
project_output_dir = f"modules/output/{project_id}"
os.makedirs(project_output_dir, exist_ok=True)

# ---------------- One-time Data Quality Check (unchanged logic) ----------------
DQ_MARKER = os.path.join(project_output_dir, ".dq_done")
if "dq_checked" not in st.session_state:
    st.session_state["dq_checked"] = {}

# (..snip.. existing DQ code ..)

# ---------------- Main Panel ----------------
if project_id:
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    df = pd.DataFrame(summaries)

    if df.empty:
        st.warning("No summary data found for that project ID.")
    else:
        col1, col2, col3 = st.columns(3)
        if col1.button("♻️ Recalculate Business Metrics"):
            generate_enigma_summaries(project_id)
            st.success("Business metrics recalculated.")
            st.rerun()
        if col2.button("📈 Recalculate Benchmark Summary"):
            summarize_benchmark_stats(project_id)
            st.success("Benchmark summary updated.")
        if col3.button("📤 Export PDF Report"):
            pdf_path = export_project_pptx(project_id, supabase)
            st.session_state["pdf_path"] = pdf_path
            st.session_state["pdf_ready"] = True

        if st.button("📄 Stitch Slides to Final PDF"):
            meta = get_project_meta(project_id, supabase)
            pdf_path = generate_final_pdf(project_id, meta['industry'], meta['location'])
            st.session_state["pdf_path"] = pdf_path
            st.session_state["pdf_ready"] = True

        if st.session_state.get("pdf_ready") and st.session_state.get("pdf_path"):
            st.success("Report is ready for download.")
            with open(st.session_state["pdf_path"], "rb") as f:
                st.download_button(
                    label="📥 Download PDF Report",
                    data=f,
                    file_name="benchmark_report.pdf",
                    mime="application/pdf",
                )

        benchmark_summary = supabase.table("benchmark_summaries").select("*").eq("project_id",
                                                                                 project_id).execute().data

        left, right = st.columns(2)
        with left:
            selected_name = st.selectbox("Select Business to Review", df["name"].tolist())
            row = df[df["name"] == selected_name].iloc[0]

            # NEW: fetch latest Enigma mapping for this business' Google Place
            latest_mapping = fetch_latest_mapping_for_search_result(row.get("search_result_id"))
            enigma_name = latest_mapping.get("enigma_name") if latest_mapping else None
            enigma_addr = latest_mapping.get("matched_full_address") if latest_mapping else None
            conf = latest_mapping.get("match_confidence") if latest_mapping else None

            st.subheader(f"📍 {row['name']}")
            # UPDATED: removed Seasonality Ratio; appended Enigma fields + Confidence
            st.markdown(
                f"""
                **Address:** {row.get('address', '—')}
                **Revenue:** ${row.get('annual_revenue', 0) :,.0f}
                **YoY Growth:** {(row.get('yoy_growth') or 0):.2%}
                **Ticket Size:** ${row.get('ticket_size', 0) :,.0f}
                **Transactions:** {row.get('transaction_count', 0) :,.0f}

                **Enigma Name:** {enigma_name or '—'}
                **Enigma Address:** {enigma_addr or '—'}
                **Match Confidence:** {(float(conf) if conf is not None else 0.0):.2f}
                """
            )
            st.markdown("---")

        with right:
            if benchmark_summary:
                s = benchmark_summary[0]
                st.subheader("📈 Benchmark Summary")
                st.markdown(
                    f"""
                    - **Businesses Included**: {s['benchmark_count']}
                    - **Average Revenue**: ${s['average_annual_revenue']:,.0f}
                    - **Median Revenue**: ${s['median_annual_revenue']:,.0f}
                    - **Avg. Ticket Size**: ${s['average_ticket_size']:,.0f}
                    - **Avg. Transactions**: {s['average_transaction_count']:,.0f}
                    - **Avg. YoY Growth**: {s['average_yoy_growth']:.2%}
                    """
                )

        st.markdown("---")
        current_benchmark = row['benchmark'] if isinstance(row.get('benchmark'), str) else 'trusted'
        choice = st.radio(
            "Include in Benchmark?",
            ["trusted", "low"],
            index=0 if current_benchmark == 'trusted' else 1,
            key=f"bench_{row['id']}"
        )
        if choice != current_benchmark:
            if st.button("✅ Save Benchmark Flag"):
                supabase.table("enigma_summaries").update({"benchmark": choice}).eq("id", row["id"]).execute()
                st.success(f"Updated benchmark status to '{choice}'")
                st.rerun()

        # --- Map Preview (unchanged) ---
        all_biz = (
            supabase.table("enigma_summaries").select("name, latitude, longitude, benchmark")
            .eq("project_id", project_id).execute().data
        )
        df_all = pd.DataFrame(all_biz)
        df_all = df_all[df_all["latitude"].notna() & df_all["longitude"].notna()]
        if not df_all.empty:
            m, meta = build_map(df_all, zoom_fraction=0.75)
            st_folium(m, width=1200, height=800)
        else:
            st.info("No mappable businesses for this project.")

        st.divider()
        st.header("📋 Full Table")
        # UPDATED: drop Seasonality column from the grid to match the detail block
        summary_cols = [
            "name", "annual_revenue", "yoy_growth", "ticket_size", "transaction_count", "benchmark"
        ]
        display_df = (
            df[summary_cols]
            .sort_values("annual_revenue", ascending=False)
            .rename(
                columns={
                    "name": "Business",
                    "annual_revenue": "Revenue",
                    "yoy_growth": "YoY Growth",
                    "ticket_size": "Ticket Size",
                    "transaction_count": "Transactions",
                    "benchmark": "Benchmark",
                }
            )
        )
        st.dataframe(display_df)
