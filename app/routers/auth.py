import os
import secrets
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from ..limiter import limiter

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, RefreshToken, PendingState
from ..auth import create_access_token, create_refresh_token, verify_token
from ..dependencies import get_current_user
from ..oauth import (
    get_github_auth_url,
    exchange_code_for_token,
    get_github_user
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------

def generate_pkce_pair():
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return code_verifier, code_challenge


def save_refresh_token(db: Session, user_id, token: str):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_token = RefreshToken(
        token=token,
        user_id=user_id,
        expires_at=expires_at
    )
    db.add(db_token)
    db.commit()


def get_or_create_user(db: Session, github_user: dict) -> User:
    user = db.query(User).filter(
        User.github_id == github_user["github_id"]
    ).first()

    if not user:
        user = User(
            github_id=github_user["github_id"],
            username=github_user["username"],
            email=github_user["email"],
            avatar_url=github_user["avatar_url"],
            role="analyst",
            is_active=True,
        )
        db.add(user)

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


# ----------------------------------------------------------------
# AUTH ROUTES
# ----------------------------------------------------------------

@router.get("/github")
@limiter.limit("10/minute")
def github_login(
    request: Request,
    source: str = "web",
    state: str = None,
    code_challenge: str = None,
    db: Session = Depends(get_db)
):
    final_state = state or secrets.token_urlsafe(32)
    if source == "web":
        code_verifier, challenge = generate_pkce_pair()
    else:
        if not code_challenge:
            raise HTTPException(400, "code_challenge required for CLI")
        code_verifier = None
        challenge = code_challenge
    db.add(PendingState(
        state=final_state,
        code_verifier=code_verifier or "",
        source=source
    ))
    db.commit()
    auth_url = get_github_auth_url(final_state, challenge)
    return RedirectResponse(auth_url)


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    db: Session = Depends(get_db)
):
    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    stored = db.query(PendingState).filter(
        PendingState.state == state
    ).first()

    if not stored:
        raise HTTPException(400, "Invalid or expired state")

    code_verifier = stored.code_verifier
    source = stored.source

    db.delete(stored)
    db.commit()

    # Exchange token (reuse oauth.py)
    github_token = await exchange_code_for_token(code, code_verifier)
    github_user = await get_github_user(github_token)

    user = get_or_create_user(db, github_user)

    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")

    token_payload = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    save_refresh_token(db, user.id, refresh_token)

    if source == "cli":
        
        return RedirectResponse(
            f"http://localhost:8484/callback?"
            f"access_token={access_token}&refresh_token={refresh_token}"
        )

    response = RedirectResponse(f"{FRONTEND_URL}/dashboard")
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=False,
        samesite="lax"
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=False,
        samesite="lax"
    )
    return response



@router.post("/refresh")
@limiter.limit("10/minute")
async def refresh_tokens(
    request: Request,
    db: Session = Depends(get_db),
):

    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        try:
            body = await request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            pass

    if not refresh_token:
        raise HTTPException(400, "Refresh token required")

    payload = verify_token(refresh_token, "refresh")
    user_id = payload.get("sub")

    db_token = db.query(RefreshToken).filter(
        RefreshToken.token == refresh_token,
        RefreshToken.is_revoked == False
    ).first()

    if not db_token:
        raise HTTPException(401, "Refresh token revoked")

    if db_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, "Refresh token expired")

    db_token.is_revoked = True
    db.commit()

    user = db.query(User).filter(User.id == user_id).first()

    if not user or not user.is_active:
        raise HTTPException(403, "User not allowed")

    token_data = {"sub": str(user.id), "role": user.role}
    new_access = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    save_refresh_token(db, user.id, new_refresh)

    return {
        "status": "success",
        "access_token": new_access,
        "refresh_token": new_refresh
    }



@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        try:
            body = await request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            pass

    if refresh_token:
        db_token = db.query(RefreshToken).filter(
            RefreshToken.token == refresh_token
        ).first()
        if db_token:
            db_token.is_revoked = True
            db.commit()

    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return {"status": "success", "message": "Logged out successfully"}



@router.get("/whoami")
def whoami(
    current_user: User = Depends(get_current_user),
):
    return {
        "status": "success",
        "data": {
            "id": str(current_user.id),
            "username": current_user.username,
            "email": current_user.email,
            "role": current_user.role,
            "avatar_url": current_user.avatar_url,
        }
    }



@router.post("/cli/callback")
async def cli_callback(
    request: Request,
    db: Session = Depends(get_db)
):
    limiter = request.app.state.limiter
    limiter.limit("10/minute")(lambda: None)()

    body = await request.json()
    code = body.get("code")
    code_verifier = body.get("code_verifier")

    if not code or not code_verifier:
        raise HTTPException(400, "code and code_verifier required")

    github_token = await exchange_code_for_token(code, code_verifier)
    github_user = await get_github_user(github_token)

    user = get_or_create_user(db, github_user)

    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    save_refresh_token(db, user.id, refresh_token)

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
        }
    }