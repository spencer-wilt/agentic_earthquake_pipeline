"""Central configuration, read from environment variables (see .env.example)."""
import os
from pathlib import Path

try:  # optional: load .env if python-dotenv is installed (ADK installs it)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DAYS_BACK = int(os.getenv("DAYS_BACK", "30"))
MIN_MAGNITUDE = float(os.getenv("MIN_MAGNITUDE", "2.5"))

# "gcs" uploads to Google Cloud Storage; "local" writes only to ./output (no credentials needed)
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
GCS_BUCKET = os.getenv("GCS_BUCKET", "")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
HAS_LLM = bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_USE_VERTEXAI"))
