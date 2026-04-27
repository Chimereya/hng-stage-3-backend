import secrets
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import User, RefreshToken
from ..auth import (
    create_access_token,
    create_refresh_token,
    verify_token
)
from ..oauth import (
    get_github_auth_url,
    exchange_code_for_token,
    get_github_user
)
import uuid
from uuid6 import uuid7
import os

router = APIRouter(prefix="/auth", tags=["Authentication"])


FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Temporary in-memory store for state and code_verifier
pending_states: dict = {}




def generate_pkce_pair():
    
    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return code_verifier, code_challenge


def save_refresh_token(db: Session, user_id: str, token: str) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_token = RefreshToken(
        id = uuid.uuid4(),
        token = token,
        user_id = user_id,
        expires_at= expires_at
    )
    db.add(db_token)
    db.commit()

