"""Compare per-turn latency of candidate voice models (same prompt, no tools)."""
import asyncio
import time

from app import llm

PROMPT = "Caller: Haan ji, mera order kab tak aayega? Context: order LL5048, Celestial Charm Set, in transit with Ecom Express, ETA 3 Nov."
SYSTEM = "You are a phone agent for an Indian D2C brand. Reply in one short Hinglish sentence."


async def main():
    for model, thinking in [("gemini-3.5-flash", "minimal"), ("gemini-3.5-flash-lite", "minimal"), ("gemini-3.5-flash-lite", None)]:
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            r = await llm.generate(model=model, contents=PROMPT, system=SYSTEM, thinking=thinking, purpose="latency")
            times.append(round(time.perf_counter() - t0, 2))
        print(model, thinking, times, (r.text or "")[:80])


asyncio.run(main())
