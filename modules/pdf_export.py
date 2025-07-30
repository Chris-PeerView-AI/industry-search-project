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
    trusted = [s for s in summaries if s["benchmark"] == "trusted"]
    untrusted = [s for s in summaries if s["benchmark"] != "trusted"]

    with tempfile.TemporaryDirectory() as tmpdirname:
        map_html = os.path.join(tmpdirname, "map.html")
        map_png = os.path.join(tmpdirname, "map.png")

        m = folium.Map(location=[center_lat, center_lng], zoom_start=13)
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
        chrome_options.add_argument(f"--window-size=1000,800")
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
        driver.get(f"file://{map_html}")
        driver.save_screenshot(map_png)
        driver.quit()

        def save_bar_chart(title, labels, values, filename, arrow=False, highlight_lines=None):
            fig, ax = plt.subplots()
            bars = ax.bar(labels, values, color=["#4CAF50", "#2196F3"])
            ax.set_title(title)
            for bar in bars:
                height = bar.get_height()
                ax.annotate(f'{height:,.0f}', xy=(bar.get_x() + bar.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points", ha='center', va='bottom')
            if arrow:
                ax.annotate('', xy=(0.75, values[1]), xytext=(0.25, values[0]),
                            arrowprops=dict(facecolor='black', shrink=0.05))
            if highlight_lines:
                for val, color, style in highlight_lines:
                    ax.axhline(val, color=color, linestyle=style)
            plt.tight_layout()
            path = os.path.join(tmpdirname, filename)
            plt.savefig(path)
            plt.close()
            return path

        pdf = FPDF(orientation="P", unit="mm", format="Letter")

        pdf.add_page()
        pdf.set_font("Arial", size=14)
        pdf.cell(200, 10, txt="Map of Benchmark Businesses", ln=True, align="C")
        pdf.image(map_png, x=10, y=30, w=190)

        rev_chart = save_bar_chart("Avg Revenue", ["Last Year", "This Year"],
                                   [sum(t["annual_revenue"] / (1 + t["yoy_growth"]) for t in trusted) / len(trusted),
                                    sum(t["annual_revenue"] for t in trusted) / len(trusted)], "revenue.png", arrow=True)

        tix_chart = save_bar_chart("Avg Ticket Size", ["Last Year", "This Year"],
                                   [sum(t["ticket_size"] / (1 + t["yoy_growth"]) for t in trusted) / len(trusted),
                                    sum(t["ticket_size"] for t in trusted) / len(trusted)], "ticket.png", arrow=True)

        seasonality_vals = [t["seasonality_ratio"] for t in trusted]
        seasonality_chart = save_bar_chart("Seasonality Ratios", list(range(len(seasonality_vals))), seasonality_vals,
                                           "seasonality.png", arrow=False,
                                           highlight_lines=[(0.8, 'red', ':'), (0.9, 'orange', '--'),
                                                            (1.1, 'orange', '--'), (1.2, 'red', ':')])

        trusted_revenue = sum(t["annual_revenue"] for t in trusted)
        projected_revenue = trusted_revenue + len(untrusted) * (trusted_revenue / len(trusted)) if trusted else 0
        market_chart = save_bar_chart("Market Size (Total Revenue)", ["Trusted", "Projected"],
                                      [trusted_revenue, projected_revenue], "market.png")

        revs = sorted([t["annual_revenue"] for t in trusted])
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
        pdf.cell(200, 10, txt="Summary Charts", ln=True, align="C")
        pdf.image(rev_chart, x=10, y=30, w=90)
        pdf.image(tix_chart, x=110, y=30, w=90)
        pdf.image(seasonality_chart, x=10, y=120, w=90)
        pdf.image(market_chart, x=110, y=120, w=90)
        pdf.image(rev_per_biz_chart, x=10, y=210, w=190)

        for biz in trusted:
            name = biz["name"]
            rev = biz["annual_revenue"]
            rev_last = rev / (1 + biz["yoy_growth"])
            tix = biz["ticket_size"]
            tix_last = tix / (1 + biz["yoy_growth"])
            seas = biz["seasonality_ratio"]

            chart1 = save_bar_chart(f"{name} - Revenue", ["Last Year", "This Year"], [rev_last, rev], f"rev_{name}.png", arrow=True)
            chart2 = save_bar_chart(f"{name} - Ticket Size", ["Last Year", "This Year"], [tix_last, tix], f"tix_{name}.png", arrow=True)
            chart3 = save_bar_chart(f"{name} - Seasonality", [""], [seas], f"seas_{name}.png",
                                     highlight_lines=[(0.8, 'red', ':'), (0.9, 'orange', '--'),
                                                      (1.1, 'orange', '--'), (1.2, 'red', ':')])

            pdf.add_page()
            pdf.set_font("Arial", size=14)
            pdf.cell(200, 10, txt=f"Business Overview: {name}", ln=True, align="C")
            pdf.image(chart1, x=10, y=30, w=90)
            pdf.image(chart2, x=110, y=30, w=90)
            pdf.image(chart3, x=10, y=120, w=90)

        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(200, 10, txt="Non-Trusted Businesses", ln=True, align="C")
        for biz in untrusted:
            name = biz["name"]
            addr = biz.get("address", "")
            pdf.cell(0, 10, txt=f"- {name}, {addr}", ln=True)

        os.makedirs("downloads", exist_ok=True)
        output_path = os.path.join("downloads", f"benchmark_report_{project_id}.pdf")
        pdf.output(output_path)

        st.session_state["pdf_path"] = output_path
        st.session_state["pdf_ready"] = True
        st.success("✅ PDF successfully generated.")
        st.markdown("Scroll down if you don't see the download button right away.")