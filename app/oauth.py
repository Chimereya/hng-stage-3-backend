import os
import httpx
from fastapi import HTTPException, status


GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
GITHUB_REDIRECT_URI  = os.getenv("GITHUB_REDIRECT_URI")


GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL     = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL      = "https://api.github.com/user"
GITHUB_EMAILS_URL    = "https://api.github.com/user/emails"


# Building the github auth URL manually to avoid issues with urlencode and PKCE parameters
def get_github_auth_url(state: str, code_challenge: str) -> str:
    """
    state means random string to prevent CSRF attacks
    """
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method" : "S256",
    }

    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{GITHUB_AUTHORIZE_URL}?{query}"


async def exchange_code_for_token(code: str, code_verifier: str) -> str:
    """
    After GitHub redirects me back with a code,
    it is exchanged for a GitHub access token.

    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri" : GITHUB_REDIRECT_URI,
                "code_verifier": code_verifier,
            }
        )

    data = response.json()

    
    if "error" in data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status" : "error",
                "message": data.get("error_description", "GitHub OAuth failed")
            }
        )

    return data["access_token"]


# Fetching the user github profile using the acess token.

async def get_github_user(access_token: str) -> dict:
   
    async with httpx.AsyncClient() as client:
        # Getting basic profile
        user_response = await client.get(
            GITHUB_USER_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept"       : "application/json"
            }
        )

        
        email_response = await client.get(
            GITHUB_EMAILS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept" : "application/json"
            }
        )

    user_data  = user_response.json()
    email_data = email_response.json()

    # here i'm trying to extract primary and verified email from the list of emails returned by github.
    email = None
    if isinstance(email_data, list):
        for e in email_data:
            if e.get("primary") and e.get("verified"):
                email = e.get("email")
                break

    return {
        "github_id" : str(user_data["id"]),
        "username"  : user_data["login"],
        "email"     : email or user_data.get("email"),
        "avatar_url": user_data.get("avatar_url"),
    }