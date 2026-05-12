# Changelog

All notable changes to this project will be documented in this file.

## 0.1.0 (2026-05-12)

### Added
- FastAPI server with `/ingest` endpoint for recording LLM API calls
- `/api/summary` endpoint with today/week/30d cost aggregation
- Runaway agent detection: flags any agent whose last-hour spend exceeds
  3x the 24h median hourly rate across all agents in the project
- Built-in pricing table for Anthropic, OpenAI, Gemini, and Groq models
- Jinja2 dashboard with hourly burn bar chart (Chart.js), agent table,
  model breakdown, and runaway alert section
- Docker support (`Dockerfile` with uvicorn on port 8080)
- SQLite persistence via SQLAlchemy 2.x, configurable via `COST_DB_PATH`
- 9 pytest tests covering ingest happy paths, validation errors, and
  summary aggregation
