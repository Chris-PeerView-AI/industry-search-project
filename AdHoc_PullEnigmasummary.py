import os
from dotenv import load_dotenv
from supabase import create_client, Client
from modules.business_metrics import generate_enigma_summaries

# --- Load Environment ---
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Project ID to summarize ---
PROJECT_ID = "9fc4bbce-3cf1-411a-a3e2-f4e178765d8b"

print(f"🚀 Starting summary generation for project ID: {PROJECT_ID}")
generate_enigma_summaries(PROJECT_ID)
print("✅ Summary generation complete.")
