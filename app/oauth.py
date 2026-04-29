import os
import httpx
from urllib.parse import urlencode
from fastapi import HTTPException

GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI  = os.getenv("GITHUB_REDIRECT_URI")
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL     = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL      = "https://api.github.com/user"
GITHUB_EMAILS_URL    = "https://api.github.com/user/emails"


def get_github_auth_url(state: str, code_challenge: str = None) -> str:
    if not GITHUB_CLIENT_ID or not GITHUB_REDIRECT_URI:
        raise ValueError("GitHub OAuth environment variables not set")
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
    }
    # Only include PKCE params for web flow
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"

    return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str, code_verifier: str = None) -> str:
    if not code:
        raise HTTPException(400, "Authorization code is required")

    data = {
        "client_id": GITHUB_CLIENT_ID,
        "client_secret": GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": GITHUB_REDIRECT_URI,
    }
    # Only include code_verifier if present (CLI omits it, web includes it)
    if code_verifier:
        data["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data=data,
        )

    result = response.json()
    if response.status_code != 200 or "access_token" not in result:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": result.get("error_description", "GitHub OAuth failed"),
            },
        )
    return result["access_token"]


async def get_github_user(access_token: str) -> dict:
    if not access_token:
        raise HTTPException(400, "Access token required")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        user_response  = await client.get(GITHUB_USER_URL,   headers=headers)
        email_response = await client.get(GITHUB_EMAILS_URL, headers=headers)

    if user_response.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "error",
                "message": "Failed to fetch GitHub user profile"
            }
        )

    user_data = user_response.json()

    email = None
    if email_response.status_code == 200:
        email_data = email_response.json()
        if isinstance(email_data, list):
            for e in email_data:
                if e.get("primary") and e.get("verified"):
                    email = e.get("email")
                    break

    return {
        "github_id": str(user_data.get("id")),
        "username":  user_data.get("login"),
        "email":  email or user_data.get("email"),
        "avatar_url": user_data.get("avatar_url"),
    }
