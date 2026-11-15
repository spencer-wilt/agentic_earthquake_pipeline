"""ADK agents for the earthquake pipeline. Two modes share the same step functions (steps.py):

  * LLM mode (GOOGLE_API_KEY set): a Gemini-powered LlmAgent orchestrates four tools
    (ingest_data -> process_data -> store_data -> build_report), decides the order,
    retries on errors, and writes the insight text shown in the report.
  * Fallback mode (no key): a SequentialAgent runs equivalent sub-agents deterministically.
"""
import json
from typing import AsyncGenerator

from google.adk.agents import BaseAgent, LlmAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext
from google.genai import types

from . import config, steps


def _event(agent, text, delta):
    return Event(
        author=agent.name,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        actions=EventActions(state_delta=delta),
    )


class IngestAgent(BaseAgent):
    """Pulls raw GeoJSON from the USGS API."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        raw = steps.fetch_earthquakes()
        yield _event(self, f"Ingested {len(raw.get('features', []))} raw records from USGS.", {"raw": raw})


class ProcessAgent(BaseAgent):
    """Filters bad records and aggregates by day / magnitude band / region."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        summary = steps.process(ctx.session.state["raw"])
        yield _event(self, f"Kept {summary['total_events']} events, dropped {summary['dropped_events']}.",
                     {"summary": summary})


class StorageAgent(BaseAgent):
    """Saves raw + processed JSON to cloud object storage (GCS) or ./output."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        locs = [
            steps.store("raw/earthquakes_raw.json", json.dumps(st["raw"]), "application/json"),
            steps.store("processed/earthquakes_summary.json", json.dumps(st["summary"], indent=2),
                        "application/json"),
        ]
        yield _event(self, "Stored: " + ", ".join(locs), {"stored": locs})


class ReportAgent(BaseAgent):
    """Renders the HTML visualization and stores it."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        html = steps.render_html(st["summary"], st.get("insight", ""))
        loc = steps.store("report/index.html", html, "text/html")
        yield _event(self, f"Report written to {loc}", {"report_location": loc})


# ---------------------------------------------------------------------------
# Mode 1 (default when a Gemini key is set): an LLM orchestrator agent that
# decides the order of steps by calling tools. Tools keep big data in session
# state and return only small results to the model.
# ---------------------------------------------------------------------------
def ingest_data(tool_context: ToolContext) -> dict:
    """Fetch recent earthquake records from the USGS API and keep them in session state."""
    try:
        raw = steps.fetch_earthquakes()
    except Exception as e:  # return errors to the agent so it can decide to retry
        return {"status": "error", "message": f"USGS request failed: {e}"}
    tool_context.state["raw"] = raw
    return {"status": "ok", "raw_records": len(raw.get("features", []))}


def process_data(tool_context: ToolContext) -> dict:
    """Clean the ingested data and aggregate it by day, magnitude band and region.
    Returns a compact summary of the results."""
    if "raw" not in tool_context.state:
        return {"status": "error", "message": "No raw data yet. Call ingest_data first."}
    summary = steps.process(tool_context.state["raw"])
    tool_context.state["summary"] = summary
    return {
        "status": "ok",
        "total_events": summary["total_events"],
        "dropped_events": summary["dropped_events"],
        "avg_magnitude": summary["avg_magnitude"],
        "max_magnitude": summary["max_magnitude"],
        "top_regions": summary["top_regions"][:5],
        "biggest_events": summary["biggest_events"][:3],
        "events_per_day": summary["by_day"],
    }


def store_data(tool_context: ToolContext) -> dict:
    """Save the raw and processed data to cloud object storage (GCS)."""
    st = tool_context.state
    if "summary" not in st:
        return {"status": "error", "message": "Nothing to store. Call ingest_data and process_data first."}
    try:
        locs = [
            steps.store("raw/earthquakes_raw.json", json.dumps(st["raw"]), "application/json"),
            steps.store("processed/earthquakes_summary.json", json.dumps(st["summary"], indent=2),
                        "application/json"),
        ]
    except Exception as e:
        return {"status": "error", "message": f"Storage failed: {e}"}
    return {"status": "ok", "stored": locs}


def build_report(insight: str, tool_context: ToolContext) -> dict:
    """Build the HTML report page and save it to storage.

    Args:
        insight: 2-3 sentence plain-English summary of the notable patterns in the data,
            written only from the processed results.
    """
    st = tool_context.state
    if "summary" not in st:
        return {"status": "error", "message": "No processed data. Call process_data first."}
    try:
        loc = steps.store("report/index.html", steps.render_html(st["summary"], insight), "text/html")
    except Exception as e:
        return {"status": "error", "message": f"Report storage failed: {e}"}
    return {"status": "ok", "report_location": loc}


llm_orchestrator = LlmAgent(
    name="earthquake_pipeline",
    model=config.GEMINI_MODEL,
    description="LLM-orchestrated earthquake data engineering pipeline",
    instruction=(
        "You orchestrate a data pipeline using your tools. Run these steps in order:\n"
        "1. ingest_data  2. process_data  3. store_data  4. build_report\n"
        "Rules:\n"
        "- Call one tool at a time and check its 'status' before moving on.\n"
        "- If a tool returns status 'error', retry it once; if it fails again, stop and explain "
        "which step failed and why. Never skip ahead past a failed step.\n"
        "- For build_report, write the 'insight' argument as 2-3 plain-English sentences about the "
        "most notable patterns (busiest region, strongest event, any trend by day) using ONLY the "
        "numbers returned by process_data. Do not invent facts.\n"
        "- When finished, reply with a brief summary: records processed, where files were stored, "
        "and the report location."
    ),
    tools=[ingest_data, process_data, store_data, build_report],
)

# ---------------------------------------------------------------------------
# Mode 2 (fallback, no API key needed): deterministic SequentialAgent.
# ---------------------------------------------------------------------------
sequential_pipeline = SequentialAgent(
    name="earthquake_pipeline",
    description="Deterministic earthquake data engineering pipeline",
    sub_agents=[
        IngestAgent(name="ingest_agent", description="Fetch data from USGS API"),
        ProcessAgent(name="process_agent", description="Clean and aggregate"),
        StorageAgent(name="storage_agent", description="Save to cloud storage"),
        ReportAgent(name="report_agent", description="Build HTML report"),
    ],
)

root_agent = llm_orchestrator if config.HAS_LLM else sequential_pipeline
