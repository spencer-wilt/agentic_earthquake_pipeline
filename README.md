# Agentic Earthquake Data Pipeline (Google ADK)

An end-to-end data engineering pipeline orchestrated by Google's Agent Development Kit (ADK).

The pipeline runs in one of two modes that share the same step functions ([pipeline/steps.py](pipeline/steps.py)):

**LLM mode (when `GOOGLE_API_KEY` is set)** - a Gemini-powered ADK `LlmAgent` orchestrates the pipeline by calling four tools. It decides the order, checks each tool's result, retries on errors, and writes the AI insight shown in the report.

| Tool | What it does |
|---|---|
| `ingest_data` | Pulls the last 30 days of earthquakes (M2.5+) from the public [USGS Earthquake API](https://earthquake.usgs.gov/fdsnws/event/1/) (no key needed) |
| `process_data` | Drops records with missing magnitude/time and non-earthquake events, then aggregates by day, magnitude band and region |
| `store_data` | Saves raw and processed JSON to Google Cloud Storage (or `./output` in local mode) |
| `build_report` | Builds a self-contained HTML page (Chart.js charts + tables) with the agent's insight, and stores it |

**Fallback mode (no key, or if the LLM call fails)** - a deterministic `SequentialAgent` runs equivalent sub-agents (`ingest_agent`, `process_agent`, `storage_agent`, `report_agent`) in order, passing data through ADK session state. Same outputs, minus the AI insight. See [pipeline/agent.py](pipeline/agent.py).

## Quick start (no cloud account needed)

```bash
git clone https://github.com/spencer-wilt/agentic_earthquake_pipeline.git
cd agentic_earthquake_pipeline
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run_pipeline.py
```

Then open `output/report/index.html` in a browser. Local mode writes the same files that would go to the cloud bucket.

## Run with Google Cloud Storage

1. Create a free Google Cloud project and a bucket (Console, Cloud Storage, Create bucket).
2. Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install) and authenticate: `gcloud auth application-default login`
3. Copy `.env.example` to `.env` and set:
   ```
   STORAGE_BACKEND=gcs
   GCS_BUCKET=your-bucket-name
   GOOGLE_CLOUD_PROJECT=your-project-id
   ```
4. `python run_pipeline.py`. Files appear in the bucket under `raw/`, `processed/` and `report/index.html`.

Optionally, to view the report from the bucket, make that object public or download it from the Console.

## Enable LLM mode (Gemini)

Get a free key at https://aistudio.google.com/apikey and add `GOOGLE_API_KEY=...` to `.env`. The default model is `gemini-3.1-flash-lite`; override it with `GEMINI_MODEL` if your key has a different one available. If the Gemini call fails (overloaded, quota, bad model name), `run_pipeline.py` automatically falls back to the deterministic pipeline.

## Run through the ADK UI

```bash
adk web      # then pick "pipeline" in the dropdown and send any message
```

## Configuration

All settings are environment variables (see `.env.example`): `STORAGE_BACKEND`, `GCS_BUCKET`, `DAYS_BACK`, `MIN_MAGNITUDE`, `GOOGLE_API_KEY`, `GEMINI_MODEL`.

## Security

No credentials are stored in this repo. `.env` is git-ignored. GCS uses your local Application Default Credentials.
