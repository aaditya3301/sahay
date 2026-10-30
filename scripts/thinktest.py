import asyncio, time
from google.genai import types
from app.llm import client
async def one(m, lvl):
    t=time.time()
    cfg = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=lvl)) if lvl else None
    try:
        r = await client().aio.models.generate_content(model=m, contents="Classify the intent of: 'mera order kab aayega, 4 din ho gaye'. Answer in 5 words.", config=cfg)
        print(f"{m:24} {str(lvl):8} {time.time()-t:5.1f}s thoughts={r.usage_metadata.thoughts_token_count}")
    except Exception as e: print(m, lvl, "ERR", str(e)[:120])
async def main():
    for m in ["gemini-3.5-flash-lite","gemini-3.8-flash","gemini-3.5-flash"]:
        for lvl in [None,"minimal","low"]:
            await one(m,lvl)
asyncio.run(main())
