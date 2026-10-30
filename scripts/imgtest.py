import asyncio, time
from google.genai import types
from app.llm import client
async def main():
    for m in ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image"]:
        t=time.time()
        try:
            r = await client().aio.models.generate_content(model=m, contents="E-commerce studio product photo of a silver charm bracelet with moon and star charms on a plain white background",
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]))
            parts=[p for p in r.candidates[0].content.parts if p.inline_data]
            print(m, "OK", len(parts[0].inline_data.data), parts[0].inline_data.mime_type, round(time.time()-t,1),"s")
            open(f"../../scripts/{m}.png","wb").write(parts[0].inline_data.data)
        except Exception as e: print(m, "ERR", str(e)[:200])
asyncio.run(main())
