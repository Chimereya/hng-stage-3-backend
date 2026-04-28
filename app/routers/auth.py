import secrets
import hashlib
import base64
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import User, RefreshToken, PendingState
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
def github_login(
    request: Request, 
    source: str = "web", 
    state: str = None,  # Accept state from CLI
    code_challenge: str = None, # Accept challenge from CLI
    db: Session = Depends(get_db)
):
    final_state = state or secrets.token_urlsafe(32)
    
    if source == "web":
        code_verifier, challenge = generate_pkce_pair()
    else:
     
        code_verifier = "cli_controlled" 
        challenge = code_challenge

    db.add(PendingState(state=final_state, code_verifier=code_verifier, source=source))
    db.commit()

    auth_url = get_github_auth_url(final_state, challenge)
    return RedirectResponse(auth_url)

# github will redirect to this endpoint after user authorizes the app
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import httpx

# Assuming these are imported from your existing auth logic
from .database import get_db
from .models import User, PendingState
from .security import create_access_token, create_refresh_token, save_refresh_token

router = APIRouter()

@router.get("/auth/github/callback")
async def github_callback(
    request: Request,
    code: str = None,
    state: str = None,
    db: Session = Depends(get_db)
):
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    # 1. Validate State & Retrieve PKCE Verifier
    stored_state = db.query(PendingState).filter(PendingState.state == state).first()
    if not stored_state:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    
    code_verifier = stored_state.code_verifier
    source = stored_state.source
    
    # Cleanup state immediately (one-time use)
    db.delete(stored_state)
    db.commit()

    # 2. Exchange Code for GitHub Access Token
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            params={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "code_verifier": code_verifier, # Required for PKCE
                "redirect_uri": GITHUB_REDIRECT_URI
            },
            headers={"Accept": "application/json"}
        )
        token_data = token_res.json()
        gh_access_token = token_data.get("access_token")

        # 3. Fetch GitHub User Profile
        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {gh_access_token}"}
        )
        github_user = user_res.json()

    # 4. Sync User in DB (Ensure UUID v7 is used for new users)
    user = db.query(User).filter(User.github_id == str(github_user['id'])).first()
    if not user:
        user = User(
            github_id=str(github_user['id']),
            username=github_user.get('login'),
            email=github_user.get('email'),
            avatar_url=github_user.get('avatar_url'),
            role="analyst" # Default role
        )
        db.add(user)
    
    # MANDATORY: Check if user is active
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    user.last_login_at = datetime.utcnow()
    db.commit()

    # 5. Issue Insighta Platform Tokens
    # Access Token: 3 mins (180s) | Refresh Token: 5 mins (300s)
    token_payload = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_payload, expires_delta=timedelta(minutes=3))
    refresh_token = create_refresh_token(token_payload, expires_delta=timedelta(minutes=5))

    # Store refresh token for invalidation tracking
    save_refresh_token(db, user.id, refresh_token)

    # 6. Interface-Specific Response
    if source == "cli":
        # Redirect to the CLI's local server (port 8484 as per your CLI code)
        cli_loopback_url = (
            f"http://localhost:8484/callback?"
            f"access_token={access_token}&"
            f"refresh_token={refresh_token}&"
            f"username={user.username}&"
            f"role={user.role}&"
            f"state={state}"
        )
        return RedirectResponse(url=cli_loopback_url)

    else:
        # Web Portal: Set Secure, HttpOnly cookies
        response = RedirectResponse(url=f"{FRONTEND_URL}/dashboard")
        response.set_cookie(
            key="access_token", 
            value=access_token, 
            httponly=True, 
            secure=True, 
            samesite="lax"
        )
        # Refresh token should also be HttpOnly
        response.set_cookie(
            key="refresh_token", 
            value=refresh_token, 
            httponly=True, 
            secure=True, 
            samesite="lax"
        )
        return response

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