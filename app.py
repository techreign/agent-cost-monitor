import os
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Float, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from pricing_table import lookup_cost

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get("COST_DB_PATH", "./costs.db")
FREE_TIER_MAX_PROJECTS = 1

engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="AI Agent Cost Monitor", version="0.1.0")

# ---------------------------------------------------------------------------
# DB model
# ---------------------------------------------------------------------------
VALID_APIS = {"anthropic", "openai", "gemini", "groq"}


class Base(DeclarativeBase):
    pass


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    api = Column(String, nullable=False)
    model = Column(String, nullable=False)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)
    cost_usd = Column(Float, nullable=False)
    timestamp = Column(String, nullable=False)  # ISO-8601 string
    project_id = Column(String, nullable=False)
    agent_id = Column(String, nullable=True)
    request_id = Column(String, nullable=True)


Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------
class IngestPayload(BaseModel):
    api: str = Field(..., description="One of: anthropic, openai, gemini, groq")
    model: str
    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    cost_usd: Optional[float] = Field(None, ge=0)
    timestamp: Optional[str] = None
    project_id: str
    agent_id: Optional[str] = None
    request_id: Optional[str] = None

    @field_validator("api")
    @classmethod
    def api_must_be_valid(cls, v: str) -> str:
        if v.lower() not in VALID_APIS:
            raise ValueError(f"api must be one of {sorted(VALID_APIS)}")
        return v.lower()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session() -> Session:
    return SessionLocal()


def _runaway_agents(
    db: Session, project_id: Optional[str], window_hours: int = 1
) -> list[dict]:
    """
    Identify agents whose cost rate in the last `window_hours` is >3x the
    median hourly rate across all agents (same project, last 24 h).
    """
    cutoff_1h = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    base_filter = (
        "timestamp >= :cutoff AND project_id = :pid"
        if project_id
        else "timestamp >= :cutoff"
    )

    # Per-agent cost in last hour
    q_1h = f"""
        SELECT agent_id, SUM(cost_usd) as hourly_cost
        FROM cost_entries
        WHERE {base_filter.replace(":cutoff", ":cutoff_1h")}
          AND agent_id IS NOT NULL
        GROUP BY agent_id
    """
    params_1h: dict = {"cutoff_1h": cutoff_1h}
    if project_id:
        params_1h["pid"] = project_id

    rows_1h = db.execute(text(q_1h), params_1h).fetchall()
    if not rows_1h:
        return []

    # Median hourly cost over last 24 h (normalised per hour)
    q_24h = f"""
        SELECT agent_id, SUM(cost_usd) / 24.0 as norm_hourly
        FROM cost_entries
        WHERE {"timestamp >= :cutoff_24h AND project_id = :pid" if project_id else "timestamp >= :cutoff_24h"}
          AND agent_id IS NOT NULL
        GROUP BY agent_id
    """
    params_24h: dict = {"cutoff_24h": cutoff_24h}
    if project_id:
        params_24h["pid"] = project_id

    rows_24h = db.execute(text(q_24h), params_24h).fetchall()
    norm_hourly_values = [r.norm_hourly for r in rows_24h if r.norm_hourly is not None]
    if not norm_hourly_values:
        return []

    median_rate = statistics.median(norm_hourly_values)
    if median_rate == 0:
        return []

    runaways = []
    for row in rows_1h:
        if row.hourly_cost > 3 * median_rate:
            runaways.append(
                {
                    "agent_id": row.agent_id,
                    "hourly_cost_usd": round(row.hourly_cost, 6),
                    "median_hourly_usd": round(median_rate, 6),
                    "ratio": round(row.hourly_cost / median_rate, 2),
                }
            )
    return runaways


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/ingest", status_code=201)
def ingest(payload: IngestPayload):
    ts = payload.timestamp or _now_iso()
    cost = payload.cost_usd
    if cost is None:
        cost = lookup_cost(
            payload.api, payload.model, payload.input_tokens, payload.output_tokens
        )
        if cost is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"cost_usd not provided and model '{payload.api}/{payload.model}' "
                    "is not in the pricing table. Provide cost_usd explicitly."
                ),
            )

    db = _session()
    try:
        entry = CostEntry(
            api=payload.api,
            model=payload.model,
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            cost_usd=cost,
            timestamp=ts,
            project_id=payload.project_id,
            agent_id=payload.agent_id,
            request_id=payload.request_id,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
    finally:
        db.close()

    return {"id": entry.id, "cost_usd": cost, "timestamp": ts}


@app.get("/api/summary")
def summary(
    project_id: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
):
    db = _session()
    try:
        since_ts = (
            since or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        )

        base_params: dict = {"since": since_ts}
        pid_clause = ""
        if project_id:
            pid_clause = " AND project_id = :pid"
            base_params["pid"] = project_id

        # Total cost
        total_row = db.execute(
            text(
                f"SELECT SUM(cost_usd) as total FROM cost_entries WHERE timestamp >= :since{pid_clause}"
            ),
            base_params,
        ).fetchone()
        total_cost = total_row.total or 0.0

        # Today's cost
        today_start = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .isoformat()
        )
        today_params = {"since": today_start}
        if project_id:
            today_params["pid"] = project_id
        today_row = db.execute(
            text(
                f"SELECT SUM(cost_usd) as total FROM cost_entries WHERE timestamp >= :since{pid_clause}"
            ),
            today_params,
        ).fetchone()
        today_cost = today_row.total or 0.0

        # Week cost
        week_start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        week_params = {"since": week_start}
        if project_id:
            week_params["pid"] = project_id
        week_row = db.execute(
            text(
                f"SELECT SUM(cost_usd) as total FROM cost_entries WHERE timestamp >= :since{pid_clause}"
            ),
            week_params,
        ).fetchone()
        week_cost = week_row.total or 0.0

        # Top models
        model_rows = db.execute(
            text(
                f"SELECT model, api, SUM(cost_usd) as total "
                f"FROM cost_entries WHERE timestamp >= :since{pid_clause} "
                f"GROUP BY model, api ORDER BY total DESC LIMIT 10"
            ),
            base_params,
        ).fetchall()
        top_models = [
            {"model": r.model, "api": r.api, "total_usd": round(r.total, 6)}
            for r in model_rows
        ]

        # Hourly burn (last 48 h)
        hourly_start = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        hourly_params = {"since": hourly_start}
        if project_id:
            hourly_params["pid"] = project_id
        hourly_rows = db.execute(
            text(
                f"SELECT strftime('%Y-%m-%dT%H:00:00', timestamp) as hour, SUM(cost_usd) as total "
                f"FROM cost_entries WHERE timestamp >= :since{pid_clause} "
                f"GROUP BY hour ORDER BY hour ASC"
            ),
            hourly_params,
        ).fetchall()
        hourly_burn = [
            {"hour": r.hour, "cost_usd": round(r.total, 6)} for r in hourly_rows
        ]

        # Top agents last 24 h
        agent_start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        agent_params = {"since": agent_start}
        if project_id:
            agent_params["pid"] = project_id
        agent_rows = db.execute(
            text(
                f"SELECT agent_id, SUM(cost_usd) as total, COUNT(*) as reqs "
                f"FROM cost_entries WHERE timestamp >= :since{pid_clause} AND agent_id IS NOT NULL "
                f"GROUP BY agent_id ORDER BY total DESC LIMIT 20"
            ),
            agent_params,
        ).fetchall()
        top_agents = [
            {"agent_id": r.agent_id, "total_usd": round(r.total, 6), "requests": r.reqs}
            for r in agent_rows
        ]

        # Runaway agents
        runaways = _runaway_agents(db, project_id)
        runaway_ids = {r["agent_id"] for r in runaways}

        # Flag top_agents
        for a in top_agents:
            a["runaway"] = a["agent_id"] in runaway_ids

        return {
            "today_cost_usd": round(today_cost, 6),
            "week_cost_usd": round(week_cost, 6),
            "total_cost_usd": round(total_cost, 6),
            "runaway_agents": runaways,
            "top_models": top_models,
            "hourly_burn": hourly_burn,
            "top_agents": top_agents,
        }
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})
