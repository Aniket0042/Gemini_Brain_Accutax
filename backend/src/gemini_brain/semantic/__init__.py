"""
semantic — Gemini Brain's client side of the Cube Core semantic layer.

Every governed figure comes from Cube views (Gemini_Brain_Accutax/semantic/model).
Tenant scope is bound by code: callers pass organization IDs that
api.auth.authorize_org_scope already approved, never IDs from a model.
See docs/CUBE_CORE_INTEGRATION_GUIDE_V2.md.
"""
