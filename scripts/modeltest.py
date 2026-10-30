from google import genai
from google.genai import types
P="project-422405a7-b76c-49df-b1c"
c = genai.Client(vertexai=True, project=P, location="global")
for m in ["gemini-3.5-flash-lite","gemini-3.8-flash","gemini-4-argon","gemini-3.5-flash"]:
    try:
        r = c.models.generate_content(model=m, contents="Reply with just: ok")
        print(m, "OK", (r.text or "").strip()[:20])
    except Exception as e:
        print(m, "ERR", str(e)[:200])
for loc in ["global","asia-south1","us-central1"]:
    try:
        cc = genai.Client(vertexai=True, project=P, location=loc)
        r = cc.models.embed_content(model="gemini-embedding-001", contents=["hello"], config=types.EmbedContentConfig(output_dimensionality=768))
        print("embed", loc, "OK", len(r.embeddings[0].values))
    except Exception as e:
        print("embed", loc, "ERR", str(e)[:150])
