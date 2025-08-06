
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

# --- Setup ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.set_page_config(page_title="Benchmark Review Tool", layout="wide")
st.title("📊 Benchmark Review & Report Prep")

# --- Project Selection ---
active_only = st.checkbox("Show Active Projects Only", value=True)
project_list_res = supabase.table("search_projects").select("id, name").order("created_at", desc=True).execute()
project_data = project_list_res.data
if active_only:
    project_data = [row for row in project_data if not row["name"].startswith("Test:")]
project_options = {row["name"]: row["id"] for row in project_data}
project_name = st.selectbox("Select Project", list(project_options.keys()))
project_id = project_options[project_name]

# --- Quality Filter Function ---
def is_low_quality(b, avg_ticket):
    revenue = b.get("annual_revenue")
    yoy = b.get("yoy_growth")
    ticket = b.get("ticket_size")
    lat = b.get("latitude")
    lng = b.get("longitude")
    return (
        revenue is None or revenue < 50_000
        or yoy is None or abs(yoy) > 1.0
        or ticket is None or ticket < (avg_ticket * 0.3) or ticket > (avg_ticket * 3.0)
        or lat is None or lng is None
    )

# --- Manual Trigger: Run Quality Filter ---
if st.button("🧹 Run Quality Filter on This Project"):
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if summaries:
        avg_ticket = sum([b["ticket_size"] for b in summaries if b.get("ticket_size")]) / max(len(summaries), 1)
        low_quality_ids = [b["id"] for b in summaries if is_low_quality(b, avg_ticket)]
        if low_quality_ids:
            for bid in low_quality_ids:
                supabase.table("enigma_summaries").update({"benchmark": "low"}).eq("id", bid).execute()
            st.success(f"✅ Marked {len(low_quality_ids)} businesses as low quality.")
        else:
            st.success("✅ All businesses passed quality check.")

# --- Auto Trigger if No Outputs Exist ---
project_output_dir = f"modules/output/{project_id}"
existing_files = os.listdir(project_output_dir) if os.path.exists(project_output_dir) else []
if not existing_files:
    st.info("🔍 Running initial data quality check for new project...")
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if summaries:
        avg_ticket = sum([b["ticket_size"] for b in summaries if b.get("ticket_size")]) / max(len(summaries), 1)
        low_quality_ids = [b["id"] for b in summaries if is_low_quality(b, avg_ticket)]
        if low_quality_ids:
            for bid in low_quality_ids:
                supabase.table("enigma_summaries").update({"benchmark": "low"}).eq("id", bid).execute()
            st.success(f"🛠️ Marked {len(low_quality_ids)} businesses as low quality.")

# --- Main UI ---
if project_id:
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    df = pd.DataFrame(summaries)

    if df.empty:
        st.warning("No summary data found for that project ID.")
    else:
        selected_name = st.selectbox("Select Business to Review", df["name"].tolist())
        row = df[df["name"] == selected_name].iloc[0]

        col1, col2, col3 = st.columns(3)
        if col1.button("♻️ Recalculate Business Metrics"):
            generate_enigma_summaries(project_id)
            st.success("Business metrics recalculated.")
            st.rerun()
        if col2.button("📈 Recalculate Benchmark Summary"):
            summarize_benchmark_stats(project_id)
            st.success("Benchmark summary updated.")
            st.rerun()
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
            st.subheader(f"📍 Input Business Info (Google Places)")
            st.markdown(f"**Name:** {row['name']}")
            st.markdown(f"**Address:** {row['address']}")

            # Fetch Enigma match by business_id (not place_id)
            enigma_match = supabase.table("enigma_businesses") \
                .select("business_name, matched_full_address, matched_city, matched_state, matched_postal_code") \
                .eq("id", row["business_id"]) \
                .maybe_single() \
                .execute()

            m = enigma_match.data if enigma_match and enigma_match.data else None

            if m:
                st.subheader("🔎 Enigma Match Info")
                st.markdown(f"**Matched Name:** {m.get('business_name', '—')}")
                st.markdown(f"**Matched Address:** {m.get('matched_full_address', '—')}")
                st.markdown(f"**Matched City/State/Zip:** {m.get('matched_city', '—')}, {m.get('matched_state', '—')} {m.get('matched_postal_code', '—')}")

            st.markdown("---")
            st.markdown(f"**Revenue:** ${row['annual_revenue']:,.0f}")
            st.markdown(f"**YoY Growth:** {row['yoy_growth']:.2%}")
            st.markdown(f"**Ticket Size:** ${row['ticket_size']:,.0f}")
            st.markdown(f"**Transactions:** {row['transaction_count']:,.0f}")
            st.markdown(f"**Seasonality Ratio:** {row['seasonality_ratio']:.2f}")
            st.markdown("---")

            st.subheader("🔍 Manual Match Comparison")

            if m:
                st.markdown(f"""
                <table>
                  <tr>
                    <th style="text-align:left;">Google Places</th>
                    <th style="text-align:left;">Enigma Match</th>
                  </tr>
                  <tr>
                    <td><b>{row['name']}</b><br>{row['address']}</td>
                    <td><b>{m.get('business_name', '—')}</b><br>{m.get('matched_full_address', '—')}</td>
                  </tr>
                </table>
                """, unsafe_allow_html=True)
            else:
                st.markdown("⚠️ No Enigma match found for this business.")

        with right:
            if benchmark_summary:
                s = benchmark_summary[0]
                st.subheader("📈 Benchmark Summary")
                st.markdown(f'''
                - **Businesses Included**: {s['benchmark_count']}
                - **Average Revenue**: ${s['average_annual_revenue']:,.0f}
                - **Median Revenue**: ${s['median_annual_revenue']:,.0f}
                - **Avg. Ticket Size**: ${s['average_ticket_size']:,.0f}
                - **Avg. Transactions**: {s['average_transaction_count']:,.0f}
                - **Avg. YoY Growth**: {s['average_yoy_growth']:.2%}
                - **Seasonality Ratio**: {s['average_seasonality_ratio']:.2f}
                ''')

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

# Draw radius around the farthest point
all_biz_res = supabase.table("enigma_summaries").select("latitude, longitude").eq("project_id", project_id).execute()
all_biz = pd.DataFrame(all_biz_res.data)
farthest_km = max(
    geodesic((center_lat, center_lng), (lat, lng)).km
    for lat, lng in zip(all_biz["latitude"], all_biz["longitude"])
    if pd.notnull(lat) and pd.notnull(lng)
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
