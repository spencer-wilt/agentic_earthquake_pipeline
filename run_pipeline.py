"""Entry point: python run_pipeline.py"""
import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from pipeline.agent import root_agent, sequential_pipeline


async def run(agent):
    sessions = InMemorySessionService()
    runner = Runner(agent=agent, app_name="earthquake_pipeline", session_service=sessions)
    session = await sessions.create_session(app_name="earthquake_pipeline", user_id="user")
    msg = types.Content(role="user", parts=[types.Part(text="Run the pipeline")])
    async for event in runner.run_async(user_id="user", session_id=session.id, new_message=msg):
        if not (event.content and event.content.parts):
            continue
        for part in event.content.parts:
            if part.function_call:
                print(f"[{event.author}] calling tool: {part.function_call.name}")
            elif part.function_response:
                print(f"[tool] {part.function_response.name} -> {str(part.function_response.response)[:150]}")
            elif part.text:
                print(f"[{event.author}] {part.text}")


async def main():
    try:
        await run(root_agent)
    except Exception as e:
        if root_agent is sequential_pipeline:
            raise
        # e.g. Gemini overloaded/quota exhausted: still produce the outputs deterministically
        print(f"\nLLM orchestrator failed ({type(e).__name__}); falling back to the deterministic pipeline.\n")
        await run(sequential_pipeline)


if __name__ == "__main__":
    asyncio.run(main())
