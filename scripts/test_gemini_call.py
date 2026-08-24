import sys
from pathlib import Path
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain.config.settings import settings
from google import genai
from google.genai import types

client = genai.Client(api_key=settings.gemini_api_key)
try:
    res = client.models.generate_content(
        model="gemini-3.6-flash",
        contents="Say hello in 3 words",
        config=types.GenerateContentConfig(
            system_instruction="You are a helpful assistant",
            temperature=0.0,
            max_output_tokens=100
        )
    )
    print(f"[OK] Model returned: {res.text}")
except Exception as e:
    print(f"[FAIL]: {e}")
