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

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Benchmark Review Tool", layout="wide")
st.title("📊 Benchmark Review & Report Prep")

active_only = st.checkbox("Show Active Projects Only", value=True)
project_list_res = supabase.table("search_projects").select("id, name").order("created_at", desc=True).execute()
project_data = project_list_res.data
if active_only:
    project_data = [row for row in project_data if not row["name"].startswith("Test:")]
project_options = {row["name"]: row["id"] for row in project_data}
project_name = st.selectbox("Select Project", list(project_options.keys()))
project_id = project_options[project_name]

# Check output folder
project_output_dir = f"modules/output/{project_id}"
existing_files = os.listdir(project_output_dir) if os.path.exists(project_output_dir) else []

if not existing_files:
    st.info("🔍 Running initial data quality check for new project...")

    # Pull enigma summaries (already done later, but doing it now for setup)
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data

    if summaries:
        avg_ticket = sum([b["ticket_size"] for b in summaries if b.get("ticket_size")]) / max(len(summaries), 1)


        def is_low_quality(b):
            revenue = b.get("annual_revenue")
            yoy = b.get("yoy_growth")
            ticket = b.get("ticket_size")
            lat = b.get("latitude")
            lng = b.get("longitude")

            return (
                    revenue is None or revenue < 10_000
                    or yoy is None or abs(yoy) > 1.0
                    or ticket is None or ticket < (avg_ticket * 0.3) or ticket > (avg_ticket * 3.0)
                    or lat is None or lng is None
            )


        low_quality_ids = [b["id"] for b in summaries if is_low_quality(b)]

        if low_quality_ids:
            for biz_id in low_quality_ids:
                supabase.table("enigma_summaries").update({"benchmark": "low"}).eq("id", biz_id).execute()
            st.success(f"🛠️ Marked {len(low_quality_ids)} businesses as 'low' quality.")
        else:
            st.success("✅ All businesses passed data quality checks.")


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
        if st.radio("Include in Benchmark?", ["trusted", "low"], index=0 if row['benchmark'] == 'trusted' else 1, key=row['id']) != row['benchmark']:
            new_val = st.radio("Confirm Update:", ["trusted", "low"], horizontal=True, key=f"confirm_{row['id']}")
            if st.button("✅ Save Benchmark Flag"):
                supabase.table("enigma_summaries").update({"benchmark": new_val}).eq("id", row["id"]).execute()
                st.success(f"Updated benchmark status to '{new_val}'")
                st.rerun()

        import folium
        from streamlit_folium import st_folium
        from geopy.distance import geodesic

        center_lat, center_lng = row["latitude"], row["longitude"]
        m = folium.Map(location=[center_lat, center_lng], zoom_start=13)

        all_biz_res = supabase.table("enigma_summaries").select("latitude, longitude").eq("project_id", project_id).execute()
        all_biz = pd.DataFrame(all_biz_res.data)
        farthest_km = max(
            geodesic((center_lat, center_lng), (lat, lng)).km
            for lat, lng in zip(all_biz["latitude"], all_biz["longitude"]) if pd.notnull(lat) and pd.notnull(lng)
        )

        folium.Circle(
            location=[center_lat, center_lng],
            radius=farthest_km * 1000,
            color="blue",
            fill=True,
            fill_opacity=0.05,
            weight=0.7,
            popup=f"Search Radius: {farthest_km:.2f} km"
        ).add_to(m)

        for _, biz in df.iterrows():
            color = "gray" if biz["benchmark"] != "trusted" else "green"
            if biz["id"] == row["id"]:
                color = "yellow"
            folium.Marker(
                location=[biz["latitude"], biz["longitude"]],
                popup=biz["name"],
                icon=folium.Icon(color=color)
            ).add_to(m)

        st_folium(m, width=800, height=500)

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
