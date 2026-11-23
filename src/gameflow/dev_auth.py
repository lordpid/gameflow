"""Development-only JWT issuer and verifier; not a MembersFlow integration."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ISSUER = "gameflow-development"
AUDIENCE = "gameflow-development"
PROVIDER = "membersflow"
bearer = HTTPBearer(auto_error=False)


class AuthError(HTTPException):
    pass


def _configured(environment: str, secret: str) -> bool:
    return environment == "development" and len(secret.encode("utf-8")) >= 32


def authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> str:
    secret = request.app.state.dev_jwt_secret
    if not _configured(request.app.state.environment, secret):
        raise AuthError(status_code=503, detail="auth_not_configured")
    if credentials is None:
        raise AuthError(
            status_code=401,
            detail="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = jwt.decode(
            credentials.credentials,
            secret,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
            options={"require": ["iss", "aud", "sub", "iat", "exp"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or subject != subject.strip():
            raise jwt.InvalidTokenError("invalid subject")
    except jwt.InvalidTokenError:
        raise AuthError(
            status_code=401,
            detail="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return subject


def main() -> None:
    parser = argparse.ArgumentParser(description="Issue a local development JWT, not a MembersFlow token.")
    parser.add_argument("subject", help="Existing development identity subject")
    args = parser.parse_args()
    secret = os.environ.get("GAMEFLOW_DEV_JWT_SECRET", "")
    if not _configured(os.environ.get("GAMEFLOW_ENV", ""), secret):
        parser.error("requires GAMEFLOW_ENV=development and GAMEFLOW_DEV_JWT_SECRET of at least 32 bytes")
    if not args.subject or args.subject != args.subject.strip():
        parser.error("subject must be nonempty and have no surrounding whitespace")
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": args.subject, "iat": now, "exp": now + timedelta(minutes=15)},
        secret,
        algorithm="HS256",
    )
    print(token, file=sys.stdout)


if __name__ == "__main__":
    main()
