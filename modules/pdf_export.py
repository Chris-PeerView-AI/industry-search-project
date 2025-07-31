from fpdf import FPDF
import streamlit as st
import os
import tempfile
import folium
from streamlit_folium import st_folium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
import matplotlib.pyplot as plt
import numpy as np

def export_project_pdf(project_id, supabase):
    st.session_state["pdf_ready"] = False
    summaries = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).execute().data
    if not summaries:
        st.error("No business summaries found for this project.")
        return

    center_lat = summaries[0]["latitude"]
    center_lng = summaries[0]["longitude"]
    city = summaries[0].get("city", "City")
    industry = summaries[0].get("industry", "Industry")
    map_title = f"{city}: {industry}"

    trusted = [s for s in summaries if s["benchmark"] == "trusted"]
    untrusted = [s for s in summaries if s["benchmark"] != "trusted"]

    with tempfile.TemporaryDirectory() as tmpdirname:
        map_html = os.path.join(tmpdirname, "map.html")
        map_png = os.path.join(tmpdirname, "map.png")

        m = folium.Map(location=[center_lat, center_lng], zoom_start=11)
        for biz in summaries:
            color = "gray" if biz["benchmark"] != "trusted" else "green"
            folium.Marker(
                location=[biz["latitude"], biz["longitude"]],
                popup=biz["name"],
                icon=folium.Icon(color=color)
            ).add_to(m)
        m.save(map_html)

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"--window-size=1200,1000")
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
        driver.get(f"file://{map_html}")
        driver.save_screenshot(map_png)
        driver.quit()

        def save_bar_chart(title, labels, values, filename, arrow=False, y_max=None):
            fig, ax = plt.subplots()
            bars = ax.bar(labels, values, color=["#4CAF50", "#2196F3"])
            ax.set_title(title)
            if y_max:
                ax.set_ylim(0, y_max)
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f'{height:,.0f}', xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points", ha='center', va='bottom')
            if arrow:
                ax.annotate('', xy=(0.75, values[1]), xytext=(0.25, values[0]),
                            arrowprops=dict(facecolor='black', shrink=0.05))
            plt.tight_layout()
            path = os.path.join(tmpdirname, filename)
            plt.savefig(path)
            plt.close()
            return path

        pdf = FPDF(orientation="P", unit="mm", format="Letter")
        pdf.add_page()
        pdf.set_font("Arial", size=14)
        pdf.cell(200, 10, txt=map_title, ln=True, align="C")
        pdf.image(map_png, x=10, y=30, w=190)

        trusted_revs = [t["annual_revenue"] for t in trusted]
        rev_max = max(trusted_revs) * 1.25 if trusted_revs else 1
        benchmark_avg = np.mean(trusted_revs) if trusted_revs else 0
        rev_chart = save_bar_chart("Avg Revenue", ["Last Year", "This Year"],
                                   [sum(t["annual_revenue"] / (1 + t["yoy_growth"]) for t in trusted) / len(trusted),
                                    sum(t["annual_revenue"] for t in trusted) / len(trusted)],
                                   "revenue.png", arrow=True, y_max=max(rev_max, benchmark_avg * 2))

        tix_chart = save_bar_chart("Avg Ticket Size", ["Last Year", "This Year"],
                                   [
                                       sum(
                                           t["annual_revenue"] / (1 + t["yoy_growth"]) / (t["transaction_count"] / (1 + t["yoy_growth"]))
                                           for t in trusted
                                       ) / len(trusted),
                                       sum(t["ticket_size"] for t in trusted) / len(trusted)
                                   ],
                                   "ticket.png", arrow=True)

        trusted_revenue = sum(t["annual_revenue"] for t in trusted)
        projected_revenue = trusted_revenue + len(untrusted) * (trusted_revenue / len(trusted)) if trusted else 0
        market_chart = save_bar_chart("Market Size (Total Revenue)", ["Trusted", "Projected"],
                                      [trusted_revenue, projected_revenue], "market.png")

        revs = sorted(trusted_revs)
        mean_val = np.mean(revs)
        median_val = np.median(revs)
        fig, ax = plt.subplots()
        ax.bar(range(len(revs)), revs, color="#90CAF9")
        ax.axhline(mean_val, color='green', linestyle='--', label='Mean')
        ax.axhline(median_val, color='purple', linestyle=':', label='Median')
        ax.set_title("Revenue per Business")
        ax.legend()
        plt.tight_layout()
        rev_per_biz_chart = os.path.join(tmpdirname, "rev_per_business.png")
        plt.savefig(rev_per_biz_chart)
        plt.close()

        pdf.add_page()
        pdf.set_font("Arial", size=14)
        pdf.cell(200, 10, txt=map_title, ln=True, align="C")
        pdf.image(rev_chart, x=10, y=30, w=90)
        pdf.image(tix_chart, x=110, y=30, w=90)
        pdf.image(market_chart, x=10, y=120, w=90)
        pdf.image(rev_per_biz_chart, x=110, y=120, w=90)

        for i in range(0, len(trusted), 2):
            pdf.add_page()
            pdf.set_font("Arial", size=12)
            for j in range(2):
                if i + j >= len(trusted):
                    break
                biz = trusted[i + j]
                y_offset = 10 + j * 130
                pdf.set_xy(10, y_offset)
                pdf.set_font("Arial", size=12)
                pdf.cell(180, 10, txt=biz["name"], ln=True)
                pdf.set_x(10)
                pdf.cell(180, 10, txt=biz.get("address", ""), ln=True)

                # Revenue chart
                rev = biz["annual_revenue"]
                rev_last = rev / (1 + biz["yoy_growth"])
                rev_chart_path = save_bar_chart(f"Revenue", ["Last Year", "This Year"], [rev_last, rev], f"rev_{i+j}.png", arrow=True)
                pdf.image(rev_chart_path, x=10, y=y_offset + 20, w=90)

                # Ticket size chart
                transactions = biz["transaction_count"]
                ticket_this_year = biz["ticket_size"]
                ticket_last_year = (rev / (1 + biz["yoy_growth"])) / (transactions / (1 + biz["yoy_growth"])) if transactions else 0
                tix_chart_path = save_bar_chart(f"Ticket Size", ["Last Year", "This Year"], [ticket_last_year, ticket_this_year], f"tix_{i+j}.png", arrow=True)
                pdf.image(tix_chart_path, x=110, y=y_offset + 20, w=90)

                # Map
                small_map = folium.Map(location=[biz["latitude"], biz["longitude"]], zoom_start=13)
                folium.Marker(location=[biz["latitude"], biz["longitude"]], icon=folium.Icon(color="blue")).add_to(small_map)
                submap_path = os.path.join(tmpdirname, f"map_{i+j}.png")
                submap_html = os.path.join(tmpdirname, f"map_{i+j}.html")
                small_map.save(submap_html)
                driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
                driver.get(f"file://{submap_html}")
                driver.save_screenshot(submap_path)
                driver.quit()
                pdf.image(submap_path, x=10, y=y_offset + 90, w=90)
                pdf.image(submap_path, x=10, y=y_offset + 20, w=90)

        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, txt="Data Quality Issues", ln=True, align="C")
        for i, biz in enumerate(untrusted):
            x = 10 if i % 2 == 0 else 110
            y = 20 + (i // 2) * 10
            pdf.set_xy(x, y)
            pdf.cell(90, 10, txt=f"- {biz['name']}, {biz.get('address', '')}", ln=False)

        os.makedirs("downloads", exist_ok=True)
        output_path = os.path.join("downloads", f"benchmark_report_{project_id}.pdf")
        pdf.output(output_path)

        st.session_state["pdf_path"] = output_path
        st.session_state["pdf_ready"] = True
        st.success("✅ PDF successfully generated.")
        st.markdown("Scroll down if you don't see the download button right away.")
