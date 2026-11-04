"""Dashboard widget insights (formerly the standalone Accutax Insight Engine, port 8100).

A widget's own computed numbers come in; deterministic analytics work out every figure; Bedrock only writes the
one-line summary and a few qualitative bullets. Served by the AI API at /api/v1/insight/* (see routes.py).
"""
