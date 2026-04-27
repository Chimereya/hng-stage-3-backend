from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from fastapi import HTTPException, status
import os


# Configurations for JWT


SECRET_KEY = os.getenv("JWT_SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = 3
REFRESH_TOKEN_EXPIRE_DAYS = 5


def create_access_token(data: dict) -> str:
    """This function will create a short-lived access token (3 minutes).
    the 'data' will will definitely include the user's id androle."""
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload.update({"exp": expire, "type": "access"})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    """
    Creates a short-lived refresh token (5 minutes).
    It is used to get a new access token without re-logging in.
    """
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + timedelta(minutes=REFRESH_TOKEN_EXPIRE_MINUTES)
    payload.update({"exp": expire, "type": "refresh"})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str, token_type: str) -> dict:
    """
    This decodes and validates a JWT token.
    Raises 401 if the token is invalid or expired.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"status": "error", "message": "Invalid or expired token"},
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        
        if payload.get("type") != token_type:
            raise credentials_exception

        return payload

    except JWTError:
        raise credentials_exception