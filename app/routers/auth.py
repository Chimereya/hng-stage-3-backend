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
from ..dependencies import (
    require_api_version,
    get_current_user
)
from ..oauth import (
    get_github_auth_url,
    exchange_code_for_token,
    get_github_user
)
import uuid
from uuid6 import uuid7
import os
from slowapi import Limiter
from slowapi.util import get_remote_address



limiter = Limiter(key_func=get_remote_address)


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


def save_refresh_token(db: Session, user_id, token: str) -> None:
    from datetime import timedelta
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_token = RefreshToken(
        token = token,
        user_id = user_id,
        expires_at = expires_at
    )
    db.add(db_token)
    db.commit()


def get_or_create_user(db: Session, github_user: dict) -> User:
    user = db.query(User).filter(
        User.github_id == github_user["github_id"]
    ).first()

    if not user:
        user = User(
            github_id  = github_user["github_id"],
            username = github_user["username"],
            email = github_user["email"],
            avatar_url = github_user["avatar_url"],
            role = "analyst",
            is_active  = True,
        )
        db.add(user)

    # Update login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


@router.get("/github")
@limiter.limit("10/minute")
def github_login(request: Request, source: str = "web"):
    
    state = secrets.token_urlsafe(32)

    # Generate PKCE pair for web flow
    code_verifier, code_challenge = generate_pkce_pair()

    # i'm gonna store state and code_verifier temporarily in a dictionary.
    pending_states[state] = {
        "code_verifier": code_verifier,
        "source": source
    }

    auth_url = get_github_auth_url(state, code_challenge)
    return RedirectResponse(auth_url)




# github will redirect to this endpoint after user authorizes the app
@router.get("/github/callback")
@limiter.limit("10/minute")
async def github_callback(
    request : Request,
    response: Response,
    code : str = None,
    state : str = None,
    db : Session = Depends(get_db)
):
    # validating state for csrf protection
    if not state or state not in pending_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "Invalid or expired state"}
        )

    stored  = pending_states.pop(state)
    code_verifier = stored["code_verifier"]
    source  = stored["source"]

    # exchange the code for an access token from github
    github_token = await exchange_code_for_token(code, code_verifier)

    # fetch user info from github
    github_user = await get_github_user(github_token)
    user  = get_or_create_user(db, github_user)

    # issue our own jwt tokens
    token_data = {"sub": str(user.id), "role": user.role}
    access_token  = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # save refresh token to the database
    save_refresh_token(db, user.id, refresh_token)

    if source == "web":
        redirect_response = RedirectResponse(
            url=f"{FRONTEND_URL}/dashboard",
            status_code=302
        )
        redirect_response.set_cookie(...)
        redirect_response.set_cookie(...)
        return redirect_response

    if source == "cli":
        cli_redirect = (
            f"http://localhost:8484/callback"
            f"?access_token={access_token}"
            f"&refresh_token={refresh_token}"
            f"&username={user.username}"
            f"&email={user.email}"
            f"&role={user.role}"
            f"&avatar_url={user.avatar_url}"
            f"&state={state}"
        )
        return RedirectResponse(cli_redirect)

    return JSONResponse(
        {"status": "error", "message": "Invalid or missing source parameter"},
        status_code=400
    )



@router.post("/refresh")
@limiter.limit("10/minute")
def refresh_tokens(
    request: Request,
    db: Session = Depends(get_db),
):
    # Try to get token from cli or web
    refresh_token = None

    # Check cookie first that is web portal
    refresh_token = request.cookies.get("refresh_token")

    # If not in cookie, check request body that is cli
    if not refresh_token:
        try:
            body = request.json()
            refresh_token = body.get("refresh_token")
        except Exception:
            refresh_token = None

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "Refresh token required"},
        )

    payload = verify_token(refresh_token, token_type="refresh")
    user_id = payload.get("sub")

    db_token = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == refresh_token,
            RefreshToken.is_revoked == False,
        )
        .first()
    )

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Refresh token has been revoked"},
        )

    # Invalidate old token immediately
    db_token.is_revoked = True
    db.commit()

    # Get the user
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "User not found or deactivated"},
        )

    # Issue new token pair
    token_data = {"sub": str(user.id), "role": user.role}
    new_access_token = create_access_token(token_data)
    new_refresh_token = create_refresh_token(token_data)

    # Save new refresh token
    save_refresh_token(db, user.id, new_refresh_token)

    return JSONResponse(
        {
            "status": "success",
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
        }
    )


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

    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return {"status": "success", "message": "Logged out successfully"}




@router.get("/whoami")
@limiter.limit("10/minute")
def whoami(
    request : Request,
    current_user: User = Depends(get_current_user),
):
    """
    Returns the currently authenticated user's info.
    Used by the CLI to display who is logged in.
    Requires a valid access token.
    """
    return {
        "status": "success",
        "data"  : {
            "id"        : str(current_user.id),
            "username"  : current_user.username,
            "email"     : current_user.email,
            "role"      : current_user.role,
            "avatar_url": current_user.avatar_url,
        }
    }



@router.post("/cli/callback")
@limiter.limit("10/minute")
async def cli_callback(
    request: Request,
    db : Session = Depends(get_db)
):
    """
    CLI-specific callback endpoint.
    Accepts code + code_verifier as JSON body.
    Returns tokens as JSON.
    """
    body = await request.json()
    code = body.get("code")
    code_verifier = body.get("code_verifier")

    if not code or not code_verifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "code and code_verifier required"}
        )

    # Exchange code for GitHub token
    github_token = await exchange_code_for_token(code, code_verifier)

    # Fetch GitHub user info
    github_user = await get_github_user(github_token)

    # Create or update user in DB
    user = get_or_create_user(db, github_user)

    # Issue tokens
    token_data    = {"sub": str(user.id), "role": user.role}
    access_token  = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # Save refresh token
    save_refresh_token(db, user.id, refresh_token)

    return JSONResponse({
        "status" : "success",
        "access_token" : access_token,
        "refresh_token": refresh_token,
        "user" : {
            "username": user.username,
            "email": user.email,
            "role" : user.role,
            "avatar_url": user.avatar_url,
        }
    })