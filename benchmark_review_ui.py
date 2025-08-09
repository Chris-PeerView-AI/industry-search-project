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

# -------------- Project Picker --------------
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

# -------------- One-time Data Quality Check --------------
# Streamlit reruns the script frequently; guard with both a file marker and session_state
DQ_MARKER = os.path.join(project_output_dir, ".dq_done")
if "dq_checked" not in st.session_state:
    st.session_state["dq_checked"] = {}

should_run_dq = (not os.path.exists(DQ_MARKER)) and (not st.session_state["dq_checked"].get(project_id))

if should_run_dq:
    st.info("🔍 Running initial data quality check for new project...")
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data

    if summaries:
        valid_tickets = [b.get("ticket_size") for b in summaries if isinstance(b.get("ticket_size"), (int, float))]
        avg_ticket = (sum(valid_tickets) / len(valid_tickets)) if valid_tickets else 0.0

        def is_low_quality(b):
            revenue = b.get("annual_revenue")
            yoy = b.get("yoy_growth")
            ticket = b.get("ticket_size")
            lat = b.get("latitude")
            lng = b.get("longitude")
            ticket_low = (ticket is None) or (avg_ticket > 0 and (ticket < 0.3 * avg_ticket or ticket > 3.0 * avg_ticket))
            return (
                revenue is None or revenue < 50_000
                or yoy is None or abs(yoy) > 1.0
                or ticket_low
                or lat is None or lng is None
            )

        # Only update rows that actually change
        to_low = [b["id"] for b in summaries if is_low_quality(b) and b.get("benchmark") != "low"]
        if to_low:
            for biz_id in to_low:
                supabase.table("enigma_summaries").update({"benchmark": "low"}).eq("id", biz_id).execute()
            st.success(f"🛠️ Marked {len(to_low)} businesses as 'low' quality.")
        else:
            st.success("✅ All businesses passed data quality checks or were already labeled.")

        # Write marker and session flag
        with open(DQ_MARKER, "w") as f:
            f.write("done\n")
        st.session_state["dq_checked"][project_id] = True
        st.rerun()
    else:
        st.info("ℹ️ No summaries found yet; skipping data quality check.")
        with open(DQ_MARKER, "w") as f:
            f.write("skipped-no-data\n")
        st.session_state["dq_checked"][project_id] = True
        st.rerun()
else:
    # Optional button to re-run DQ if needed
    cols = st.columns([1,1,6])
    with cols[0]:
        if st.button("♻️ Re-run Data Quality"):
            if os.path.exists(DQ_MARKER):
                os.remove(DQ_MARKER)
            st.session_state["dq_checked"][project_id] = False
            st.rerun()

# -------------- Main Panel --------------
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
                    mime="application/pdf"
                )

        benchmark_summary = supabase.table("benchmark_summaries").select("*").eq("project_id", project_id).execute().data

        left, right = st.columns(2)
        with left:
            selected_name = st.selectbox("Select Business to Review", df["name"].tolist())
            row = df[df["name"] == selected_name].iloc[0]
            st.subheader(f"📍 {row['name']}")
            st.markdown(f"""
            **Address:** {row['address']}
            **Revenue:** ${row['annual_revenue']:,.0f}
            **YoY Growth:** {row['yoy_growth']:.2%}
            **Ticket Size:** ${row['ticket_size']:,.0f}
            **Transactions:** {row['transaction_count']:,.0f}
            **Seasonality Ratio:** {row['seasonality_ratio']:.2f}
            """)
            st.markdown("---")

        with right:
            if benchmark_summary:
                s = benchmark_summary[0]
                st.subheader("📈 Benchmark Summary")
                st.markdown(f"""
                - **Businesses Included**: {s['benchmark_count']}
                - **Average Revenue**: ${s['average_annual_revenue']:,.0f}
                - **Median Revenue**: ${s['median_annual_revenue']:,.0f}
                - **Avg. Ticket Size**: ${s['average_ticket_size']:,.0f}
                - **Avg. Transactions**: {s['average_transaction_count']:,.0f}
                - **Avg. YoY Growth**: {s['average_yoy_growth']:.2%}
                - **Seasonality Ratio**: {s['average_seasonality_ratio']:.2f}
                """)

        st.markdown("---")
        # Inline control to flip benchmark flag for the selected business
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

        # --- Map Preview (Streamlit only; independent of PPT screenshot) ---
        # --- Map Preview (Streamlit only; matches PPT map style) ---
        all_biz = supabase.table("enigma_summaries").select(
            "name, latitude, longitude, benchmark"
        ).eq("project_id", project_id).execute().data
        df_all = pd.DataFrame(all_biz)
        df_all = df_all[df_all["latitude"].notna() & df_all["longitude"].notna()]

        if not df_all.empty:
            m, meta = build_map(df_all, zoom_fraction=0.75)
            st_folium(m, width=1200, height=800)
        else:
            st.info("No mappable businesses for this project.")

        st.divider()
        st.header("📋 Full Table")
        summary_cols = ["name", "annual_revenue", "yoy_growth", "ticket_size", "transaction_count", "seasonality_ratio", "benchmark"]
        display_df = df[summary_cols].sort_values("annual_revenue", ascending=False).rename(columns={
            "name": "Business",
            "annual_revenue": "Revenue",
            "yoy_growth": "YoY Growth",
            "ticket_size": "Ticket Size",
            "transaction_count": "Transactions",
            "seasonality_ratio": "Seasonality",
            "benchmark": "Benchmark"
        })
        st.dataframe(display_df)
