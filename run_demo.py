"""Quick demo script testing GeminiBrainRunner with the newly loaded .env credentials."""
import sys
from pathlib import Path

# Add src layout to path
src_dir = Path(__file__).parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain import GeminiBrainRunner, settings

print("Loaded Settings:")
print(f"  - Gemini API Key configured: {'Yes' if settings.gemini_api_key else 'No'}")
print(f"  - Accutax Base URL: {settings.accutax_base_url}")
print(f"  - DB Host / Port / Name: {settings.db_host}:{settings.db_port}/{settings.db_name}")

runner = GeminiBrainRunner()
print("\nTesting query execution...")

res = runner.run(
    query="How do I create an invoice in Accutax?",
    organization_id=27,
)

print("\n--- Execution Result ---")
print(f"Routing Path : {res['routing_info']['path']}")
print(f"Intent Type  : {res['routing_info']['type_label']}")
print(f"Answer       :\n{res['answer']}")
