"""Event ingestion and profile reads for claimed identities."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from gameflow.dev_auth import AuthError, PROVIDER, authenticate, bearer
from gameflow.ingest import IngestError, ingest_event


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    subject: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, max_length=200)


def _profile_request_error(database_url: str, provider: str, subject: str) -> JSONResponse | None:
    if database_url.strip() == "":
        return JSONResponse({"error": "not_configured"}, status_code=500)
    if not provider or not subject:
        return JSONResponse({"error": "invalid_body"}, status_code=422)
    return None


def create_app(database_url: str, *, environment: str = "", dev_jwt_secret: str = "") -> FastAPI:
    app = FastAPI(title="GameFlow", version="0.1.0")
    app.state.database_url = database_url
    app.state.environment = environment
    app.state.dev_jwt_secret = dev_jwt_secret

    @app.exception_handler(AuthError)
    async def http_error(request: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    def authenticated_subject(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> str:
        return authenticate(request, credentials)

    def authorize_identity(provider: str, subject: str, authenticated: str) -> None:
        if provider.strip() != PROVIDER or subject.strip() != authenticated:
            raise AuthError(status_code=403, detail="identity_forbidden")

    @app.post("/v1/events")
    def post_event(body: EventIn, authenticated: str = Depends(authenticated_subject)) -> JSONResponse:
        if app.state.database_url.strip() == "":
            return JSONResponse({"error": "not_configured"}, status_code=500)
        if not body.provider.strip() or not body.subject.strip():
            return JSONResponse({"error": "invalid_body"}, status_code=422)
        authorize_identity(body.provider, body.subject, authenticated)
        try:
            with psycopg.connect(app.state.database_url) as conn:
                accepted, status = ingest_event(conn, body.model_dump())
        except IngestError as exc:
            return JSONResponse({"error": exc.code}, status_code=exc.status)
        return JSONResponse(accepted, status_code=status)

    @app.get("/v1/profile")
    def get_profile(
        provider: str, subject: str, authenticated: str = Depends(authenticated_subject)
    ) -> JSONResponse:
        provider, subject = provider.strip(), subject.strip()
        error = _profile_request_error(app.state.database_url, provider, subject)
        if error is not None:
            return error
        authorize_identity(provider, subject, authenticated)

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

    @app.get("/v1/profile/progression")
    def get_progression(
        provider: str, subject: str, authenticated: str = Depends(authenticated_subject)
    ) -> JSONResponse:
        provider, subject = provider.strip(), subject.strip()
        error = _profile_request_error(app.state.database_url, provider, subject)
        if error is not None:
            return error
        authorize_identity(provider, subject, authenticated)

        with psycopg.connect(app.state.database_url) as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.xp, t.id, COALESCE(a.code, t.reason), t.amount
                FROM identity_providers AS ip
                JOIN identities AS i ON i.provider_id = ip.id
                LEFT JOIN game_profiles AS p ON p.identity_id = i.id
                LEFT JOIN xp_transactions AS t ON t.profile_id = p.id
                LEFT JOIN achievements AS a ON a.id = t.achievement_id
                WHERE ip.code = %s AND i.external_subject = %s
                ORDER BY t.created_at, t.id
                """,
                (provider, subject),
            ).fetchall()
        if not rows:
            return JSONResponse({"error": "identity_not_found"}, status_code=404)
        if rows[0][0] is None:
            return JSONResponse({"error": "profile_not_found"}, status_code=422)
        return JSONResponse(
            {
                "xp": rows[0][1],
                "transactions": [
                    {"source": row[3], "xp": row[4]} for row in rows if row[2] is not None
                ],
            }
        )

    return app


app = create_app(
    os.environ.get("DATABASE_URL", ""),
    environment=os.environ.get("GAMEFLOW_ENV", ""),
    dev_jwt_secret=os.environ.get("GAMEFLOW_DEV_JWT_SECRET", ""),
)
