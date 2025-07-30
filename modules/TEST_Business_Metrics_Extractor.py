from datetime import datetime
from typing import List, Dict, Optional
import pandas as pd
from supabase import create_client, Client
import os
from dotenv import load_dotenv

load_dotenv()

# Initialize Supabase client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def extract_business_metrics(enigma_metrics: pd.DataFrame, business_row: Dict) -> Dict:
    metrics = {
        "period_start": None,
        "period_end": None,
        "annual_revenue": None,
        "prior_year_estimate": None,
        "yoy_growth": None,
        "ticket_size": None,
        "transaction_count": None,
        "seasonality_ratio": None,
        "data_quality": "low",
        "benchmark": "low"
    }

    if enigma_metrics.empty:
        return metrics

    revenue_df = enigma_metrics[
        (enigma_metrics["quantity_type"] == "card_revenue_amount") &
        (enigma_metrics["period"] == "12m")
    ].sort_values("period_end_date", ascending=False)

    if revenue_df.empty:
        return metrics

    latest_row = revenue_df.iloc[0]
    metrics["annual_revenue"] = latest_row["projected_quantity"]
    metrics["period_start"] = latest_row["period_start_date"]
    metrics["period_end"] = latest_row["period_end_date"]

    yoy_row = enigma_metrics[
        (enigma_metrics["quantity_type"] == "card_revenue_yoy_growth") &
        (enigma_metrics["period"] == "12m") &
        (enigma_metrics["period_end_date"] == metrics["period_end"])
    ]
    metrics["yoy_growth"] = yoy_row.iloc[0]["projected_quantity"] if not yoy_row.empty else None
    if metrics["annual_revenue"] and metrics["yoy_growth"] not in [None, -1]:
        try:
            metrics["prior_year_estimate"] = metrics["annual_revenue"] / (1 + metrics["yoy_growth"])
        except ZeroDivisionError:
            metrics["prior_year_estimate"] = None

    ticket_row = enigma_metrics[
        (enigma_metrics["quantity_type"] == "avg_transaction_size") &
        (enigma_metrics["period"] == "12m") &
        (enigma_metrics["period_end_date"] == metrics["period_end"])
    ]
    metrics["ticket_size"] = ticket_row.iloc[0]["projected_quantity"] if not ticket_row.empty else None

    txn_row = enigma_metrics[
        (enigma_metrics["quantity_type"] == "card_transactions_count") &
        (enigma_metrics["period"] == "12m") &
        (enigma_metrics["period_end_date"] == metrics["period_end"])
    ]
    metrics["transaction_count"] = txn_row.iloc[0]["projected_quantity"] if not txn_row.empty else None

    three_m_df = enigma_metrics[
        (enigma_metrics["quantity_type"] == "card_revenue_amount") &
        (enigma_metrics["period"] == "3m")
    ].sort_values("period_end_date", ascending=False)

    if len(three_m_df) >= 2:
        recent_3m = three_m_df.iloc[0]["projected_quantity"]
        prior_3m = three_m_df.iloc[1]["projected_quantity"]
        metrics["seasonality_ratio"] = recent_3m / prior_3m if prior_3m else None

    # Determine data quality
    if metrics["annual_revenue"] and metrics["ticket_size"] and metrics["transaction_count"] and metrics["yoy_growth"] is not None:
        if metrics["annual_revenue"] > 0 and metrics["ticket_size"] > 0 and metrics["transaction_count"] > 0 and abs(metrics["yoy_growth"]) <= 1:
            metrics["data_quality"] = "trusted"
            metrics["benchmark"] = "trusted"

    return metrics

def summarize_benchmark_stats(project_id: str):
    # Remove old records before inserting new summary
    supabase.table("benchmark_summaries").delete().eq("project_id", project_id).execute()
    summaries_res = supabase.table("enigma_summaries").select("*").eq("project_id", project_id).eq("benchmark", "trusted").execute()
    summaries = pd.DataFrame(summaries_res.data)

    if summaries.empty:
        print("No trusted benchmark data available.")
        return

    agg = {
        "average_annual_revenue": summaries["annual_revenue"].mean(),
        "median_annual_revenue": summaries["annual_revenue"].median(),
        "average_ticket_size": summaries["ticket_size"].mean(),
        "average_transaction_count": summaries["transaction_count"].mean(),
        "average_yoy_growth": summaries["yoy_growth"].mean(),
        "average_seasonality_ratio": summaries["seasonality_ratio"].mean(),
        "benchmark_count": len(summaries)
    }

    agg["project_id"] = project_id
    agg["created_at"] = datetime.utcnow().isoformat()

    supabase.table("benchmark_summaries").insert(agg).execute()
    print("📊 Benchmark summary saved.")


if __name__ == "__main__":
    project_id = "5c36b37b-1530-43be-837a-8491d914dfc6"
    search_res = supabase.table("search_results").select("*").eq("project_id", project_id).execute()
    search_rows = search_res.data

    out_rows = []
    for row in search_rows:
        business_id = row.get("id")
        enriched = {
            "project_id": project_id,
            "name": row.get("name"),
            "address": row.get("address"),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "search_result_id": business_id
        }

        # Find corresponding enigma_businesses row by place_id
        match_res = supabase.table("enigma_businesses").select("id").eq("google_places_id", row.get("place_id")).execute()
        if match_res.data:
            enigma_business_id = match_res.data[0]["id"]
            metrics_res = supabase.table("enigma_metrics").select("*").eq("business_id", enigma_business_id).execute()
            metrics_df = pd.DataFrame(metrics_res.data)
            enriched.update(extract_business_metrics(metrics_df, row))
        else:
            enriched.update(extract_business_metrics(pd.DataFrame(), row))

        out_rows.append(enriched)

        # Remove existing summaries for the project to avoid duplicates
    supabase.table("enigma_summaries").delete().eq("project_id", project_id).execute()

    for row in out_rows:
        supabase.table("enigma_summaries").insert(row).execute()
        print("✅ Saved:", row["name"], "| Quality:", row["data_quality"], "| Benchmark:", row["benchmark"])

    # Run benchmark summary once all rows are saved
    summarize_benchmark_stats(project_id)
