import os
import secrets
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from fastapi import Query
from ..limiter import limiter
from ..database import get_db
from ..models import User, RefreshToken, PendingState
from ..auth import create_access_token, create_refresh_token, verify_token
from ..dependencies import get_current_user
from ..oauth import get_github_auth_url, exchange_code_for_token, get_github_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")


# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------

def generate_pkce_pair():
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return code_verifier, code_challenge


def save_refresh_token(db: Session, user_id: str, token: str):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_token = RefreshToken(token=token, user_id=user_id, expires_at=expires_at)
    db.add(db_token)
    db.commit()


def get_or_create_user(db: Session, github_user: dict) -> User:
    user = db.query(User).filter(User.github_id == github_user["github_id"]).first()

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
# INITIATE GITHUB OAUTH
# GET /auth/github
# ----------------------------------------------------------------


@router.get("/github")
@limiter.limit("10/minute")
def github_login(
    request: Request,
    source: str = "web",
    state: str = None,
    code_challenge: str = None,
    db: Session = Depends(get_db),
):
    final_state = state or secrets.token_urlsafe(32)

    if source == "web":
        code_verifier, challenge = generate_pkce_pair()
    else:
        # CLI provides its own code_challenge derived from its code_verifier
        if not code_challenge:
            raise HTTPException(400, "code_challenge required for CLI flow")
        code_verifier = ""   # CLI holds verifier locally
        challenge = code_challenge

    db.add(PendingState(
        state=final_state,
        code_verifier=code_verifier,
        source=source,
    ))
    db.commit()

    auth_url = get_github_auth_url(final_state, challenge)
    return RedirectResponse(auth_url)

# ----------------------------------------------------------------
# OAUTH CALLBACK
# GET /auth/github/callback
# ----------------------------------------------------------------
@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    db: Session = Depends(get_db),
):
    if not code or not state:
        raise HTTPException(400, "Missing code or state")

    stored = db.query(PendingState).filter(PendingState.state == state).first()
    if not stored:
        raise HTTPException(400, "Invalid or expired state")
    
    source = stored.source
    
    if source == "cli":
        code_verifier = None
    else:
        code_verifier = stored.code_verifier
    
    db.delete(stored)
    db.commit()
    
    github_token = await exchange_code_for_token(code, code_verifier)
    github_user = await get_github_user(github_token)

    # 3. Handle User Record
    user = get_or_create_user(db, github_user)

    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")

    # 4. Generate Internal App Tokens
    token_payload = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_payload)
    refresh_token = create_refresh_token(token_payload)

    # Save refresh token for rotation/revocation logic
    save_refresh_token(db, str(user.id), refresh_token)

    # 5. Handle CLI Flow (Tokens in URL)
    if source == "cli":
        return RedirectResponse(
            f"http://localhost:8484/callback"
            f"?access_token={access_token}&refresh_token={refresh_token}"
        )

    # 6. Handle WEB Flow (Tokens in HttpOnly Cookies)
    # Redirect to dashboard without tokens in the URL to keep it clean/secure
    response = RedirectResponse(url=f"{FRONTEND_URL}/dashboard")

    # Access Token Cookie (180s expiry per HNG rules)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,     # Required for SameSite="none"
        samesite="none", # Required for cross-site Vercel deployments
        max_age=180,
    )

    # Refresh Token Cookie (300s expiry per HNG rules)
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=300,
    )

    return response

# ----------------------------------------------------------------
# REFRESH TOKENS
# POST /auth/refresh
# ----------------------------------------------------------------

@router.post("/refresh")
@limiter.limit("10/minute")
async def refresh_tokens(
    request: Request,
    db: Session = Depends(get_db),
):
    # Support both cookie (web) and JSON body (CLI)
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        try:
            body = await request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            pass

    if not refresh_token:
        raise HTTPException(400, detail={"status": "error", "message": "Refresh token required"})

    payload = verify_token(refresh_token, "refresh")
    user_id = payload.get("sub")

    db_token = db.query(RefreshToken).filter(
        RefreshToken.token == refresh_token,
        RefreshToken.is_revoked.is_(False),
    ).first()

    if not db_token:
        raise HTTPException(401, detail={"status": "error", "message": "Refresh token is invalid or already used"})

    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(401, detail={"status": "error", "message": "Refresh token expired"})

    # Rotate: revoke old, issue new pair
    db_token.is_revoked = True
    db.commit()

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(403, detail={"status": "error", "message": "User not allowed"})

    token_data = {"sub": str(user.id), "role": user.role}
    new_access = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)
    save_refresh_token(db, str(user.id), new_refresh)

    return {
        "status": "success",
        "access_token": new_access,
        "refresh_token": new_refresh,
    }


# ----------------------------------------------------------------
# LOGOUT
# POST /auth/logout
# ----------------------------------------------------------------

@router.post("/logout")
@limiter.limit("10/minute")
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

    response.delete_cookie("access_token", httponly=True, secure=True, samesite="none")
    response.delete_cookie("refresh_token", httponly=True, secure=True, samesite="none")

    return {"status": "success", "message": "Logged out successfully"}


# ----------------------------------------------------------------
# WHOAMI
# GET /auth/whoami
# ----------------------------------------------------------------

@router.get("/whoami")
@limiter.limit("10/minute")
def whoami(
    request: Request,
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
        },
    }


# ----------------------------------------------------------------
# CLI DIRECT CALLBACK (CLI sends code + verifier directly)
# POST /auth/cli/callback
# ----------------------------------------------------------------

@router.post("/cli/callback")
@limiter.limit("10/minute")
async def cli_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    body = await request.json()
    code = body.get("code")
    code_verifier = body.get("code_verifier")

    if not code or not code_verifier:
        raise HTTPException(
            400,
            detail={"status": "error", "message": "code and code_verifier required"},
        )

    github_token = await exchange_code_for_token(code, code_verifier)
    github_user = await get_github_user(github_token)

    user = get_or_create_user(db, github_user)
    if not user.is_active:
        raise HTTPException(403, detail={"status": "error", "message": "Account is deactivated"})

    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)
    save_refresh_token(db, str(user.id), refresh_token)

    return {
        "status": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "avatar_url": user.avatar_url,
        },
    }
