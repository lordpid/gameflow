"""POST /v1/events. Claimed identity in, one catalog event out."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from gameflow.ingest import IngestError, ingest_event


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    subject: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=200)


def create_app(database_url: str) -> FastAPI:
    app = FastAPI(title="GameFlow", version="0.1.0")
    app.state.database_url = database_url

    @app.post("/v1/events")
    def post_event(body: EventIn) -> JSONResponse:
        if app.state.database_url.strip() == "":
            return JSONResponse({"error": "not_configured"}, status_code=500)
        try:
            with psycopg.connect(app.state.database_url) as conn:
                accepted, status = ingest_event(conn, body.model_dump())
        except IngestError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        return JSONResponse(accepted, status_code=status)

    return app


app = create_app(os.environ.get("DATABASE_URL", ""))
