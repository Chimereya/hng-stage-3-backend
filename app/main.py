import time
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from .database import engine
from . import models, database
from .routers import auth, profiles
from dotenv import load_dotenv


load_dotenv() 


logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)



# Setting up the ratelimiter
# Uses the caller's IP address as the key
limiter = Limiter(key_func=get_remote_address)

# Create tables on startup
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Insighta Labs+")

# Attaching the rate limiter to app
app.state.limiter = Limiter(key_func=get_remote_address)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SlowAPIMiddleware)

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()

    try:
        response = await call_next(request)
    except Exception as e:
        logger.error(
            f"{request.method} {request.url.path} → ERROR {e}"
        )
        raise

    duration = round((time.time() - start_time) * 1000, 2)

    logger.info(
        f"{request.method} {request.url.path} "
        f"→ {response.status_code} "
        f"({duration}ms)"
    )

    return response


# ----------------------------------------------------------------
# EXCEPTION HANDLERS
# ----------------------------------------------------------------

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "status" : "error",
            "message": "Too many requests. Please slow down."
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    status_code = 422
    message = "Invalid query parameters"

    for error in errors:
        error_msg = error.get("msg", "")
        error_type = error.get("type", "")
        error_loc = error.get("loc", [])

        if error_type == "missing" and error_loc and error_loc[-1] == "name":
            status_code = 400
            message = "Missing or empty name"
            break

        if "Missing or empty name" in error_msg:
            status_code = 400
            message = "Missing or empty name"
            break

    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "message": message}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"}
    )



app.include_router(auth.router)

app.include_router(profiles.router)


# simple health check
@app.get("/")
def root():
    return {
        "status" : "success",
        "message": "Insighta Labs+ API is running"
    }