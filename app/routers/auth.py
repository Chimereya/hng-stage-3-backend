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



@router.get("/github")
def github_login(source: str = "web"):
    
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
async def github_callback(
    request : Request,
    response: Response,
    code: str = None,
    state: str = None,
    db: Session = Depends(get_db)
    ):
    
    # validating state for csrf protection
    if not state or state not in pending_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "Invalid or expired state"}
        )

    stored= pending_states.pop(state)
    code_verifier = stored["code_verifier"]
    source = stored["source"]

    # then w'll exchange the code for an access token from github
    github_token = await exchange_code_for_token(code, code_verifier)

    # fetching user info from github using the access token
    github_user = await get_github_user(github_token)

    user = get_or_create_user(db, github_user)

    # issue our own jwt tokens
    token_data = {"sub": str(user.id), "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    # Saving refresh token to the database
    save_refresh_token(db, user.id, refresh_token)

    # if the source is web, we set the tokens in httpOnly cookies and redirect to frontend dashboard
    if source == "web":
        redirect_response = RedirectResponse(
            url = f"{FRONTEND_URL}/dashboard",
            status_code=302
        )
        redirect_response.set_cookie(
            key = "access_token",
            value = access_token,
            httponly = True,
            secure = True,
            samesite = "lax", 
            max_age = 180 
        )
        redirect_response.set_cookie(
            key = "refresh_token",
            value = refresh_token,
            httponly = True,
            secure = True,
            samesite = "lax",
            max_age = 300
        )
        return redirect_response

    # the cli flow returns as json format
    return JSONResponse({
        "status": "success",
        "access_token" : access_token,
        "refresh_token": refresh_token,
        "user": {
            "username" : user.username,
            "email" : user.email,
            "role" : user.role,
            "avatar_url": user.avatar_url,
        }
    })


