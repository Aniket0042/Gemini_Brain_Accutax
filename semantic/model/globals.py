# Jinja helpers for the Cube model files.
#
# One model serves two Accutax database schemas:
#   v1 = accutax_bk_1_5 on the VM (older backend, gitaticara/accutax-backend-testing)
#   v2 = accutax_db on EC2 (Calamity010/accutax_bk_backend main: currency, retention, credit note applications)
# A cube that needs a v2-only column wraps it in {% if schema_v2() %} ... {% else %} ... {% endif %}.
# The v1 branch must stay exactly the SQL the VM runs today. Anything other than "v2" means v1,
# so a missing or mistyped setting keeps the VM behaviour.
# Plan: docs/CUBE_SCHEMA_SWITCH_DRAFT.md.
import os

from cube import TemplateContext

template = TemplateContext()


@template.function("schema_v2")
def schema_v2() -> bool:
    """True when cube.env sets ACCUTAX_SCHEMA=v2 (the EC2 database)."""
    return os.getenv("ACCUTAX_SCHEMA", "v1").strip().lower() == "v2"
