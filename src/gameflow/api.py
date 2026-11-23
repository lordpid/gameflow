"""Event ingestion and profile reads for claimed identities."""

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

    @app.get("/v1/profile")
    def get_profile(provider: str, subject: str) -> JSONResponse:
        if app.state.database_url.strip() == "":
            return JSONResponse({"error": "not_configured"}, status_code=500)
        provider, subject = provider.strip(), subject.strip()
        if not provider or not subject:
            return JSONResponse({"error": "invalid_body"}, status_code=422)

        with psycopg.connect(app.state.database_url) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.xp, a.code, a.name
                FROM identity_providers AS ip
                JOIN identities AS i ON i.provider_id = ip.id
                LEFT JOIN game_profiles AS p ON p.identity_id = i.id
                LEFT JOIN user_achievements AS ua ON ua.profile_id = p.id
                LEFT JOIN achievements AS a ON a.id = ua.achievement_id
                WHERE ip.code = %s AND i.external_subject = %s
                ORDER BY a.code
                """,
                (provider, subject),
            ).fetchall()
        if not rows:
            return JSONResponse({"error": "identity_not_found"}, status_code=404)
        if rows[0][0] is None:
            return JSONResponse({"error": "profile_not_found"}, status_code=422)
        return JSONResponse(
            {
                "identity": {"provider": provider, "subject": subject},
                "profile": {"xp": rows[0][1]},
                "achievements": [
                    {"code": row[2], "name": row[3]} for row in rows if row[2] is not None
                ],
            }
        )

    return app


app = create_app(os.environ.get("DATABASE_URL", ""))
