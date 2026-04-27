from fastapi import Depends, HTTPException, Header, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .database import get_db
from .models import User
from .auth import verify_token


# This tells fastapi to look for a bearer token in the Authorization header
bearer_scheme = HTTPBearer()




def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Extracts and validates the JWT token from the
    Authorization header, then returns the current user.

    Every protected endpoint will use this dependency.
    """
    # Extract the token from the Authorization header
    token = credentials.credentials

    # Verify the token and get the payload
    payload = verify_token(token, token_type="access")

    # Get the user ID from the token payload
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid token payload"}
        )

    # Look up the user in the database
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "User not found"}
        )

    # Check if user account is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Account is deactivated"}
        )

    return user


# Adding role enforcement to the dependency


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Admin access required"}
        )
    return current_user


def require_analyst(current_user: User = Depends(get_current_user)) -> User:
    """
    Allows both admin and analyst users through.
    
    """
    if current_user.role not in ["admin", "analyst"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Access denied"}
        )
    return current_user


# Checking API version
def require_api_version(x_api_version: str = Header(None)) -> None:
    
    if x_api_version is None or x_api_version != "1":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "status": "error",
                "message": "API version header required"
            }
        )