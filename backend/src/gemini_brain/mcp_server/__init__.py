"""MCP connector: Accutax AI as tools for ChatGPT, Claude and other MCP hosts.

A separate process (python -m gemini_brain.mcp_server). Every tool forwards the caller's own bearer to the
Accutax AI API, so tenant isolation, usage limits and the org-access audit log are the API's, unchanged.
v1 tools are read-only.
"""
