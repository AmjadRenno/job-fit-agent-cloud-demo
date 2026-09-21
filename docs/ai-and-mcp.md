# AI and MCP

Normal search is deterministic and reads persisted data. When a search returns no results, an optional agentic fallback may refine the query and rerun the same bounded persisted search.

It is disabled by default (`ENABLE_AGENTIC_SEARCH=false`). In demo mode it is available only through the narrow read-only fallback route, requires explicit enablement and an OpenAI key, has strict request/result limits, and never performs source discovery, matching, application changes, cover-letter generation, or external communication.

The local stdio MCP server starts with `python -m backend.app.mcp.server`. It exposes read-only search, job-detail, match-evidence, and source-health tools over the same persisted demo data. It is a local integration surface, not a hosted public endpoint.

Neither feature is required for the demo to run, and neither is a claim that V3 implements an autonomous agent system.
