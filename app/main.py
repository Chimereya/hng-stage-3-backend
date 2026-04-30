import time
import logging
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from .limiter import limiter
from .database import engine
from . import models
from .routers import auth, profiles
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Create tables on startup
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Insighta Labs+")

# ----------------------------------------------------------------
# RATE LIMITER
# ----------------------------------------------------------------
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda req, exc: JSONResponse(
    status_code=429,
    content={"status": "error", "message": "Too many requests. Please slow down."},
))

# ----------------------------------------------------------------
# MIDDLEWARE
# ----------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://insighta-frontend-nu.vercel.app"],
    allow_credentials=True,
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
        logger.error(f"{request.method} {request.url.path} → ERROR {e}")
        raise
    duration = round((time.time() - start_time) * 1000, 2)
    logger.info(
        f"{request.method} {request.url.path} "
        f"→ {response.status_code} ({duration}ms)"
    )
    return response

# ----------------------------------------------------------------
# EXCEPTION HANDLERS
# ----------------------------------------------------------------
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
        content={"status": "error", "message": message},
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
    )

# ----------------------------------------------------------------
# ROUTERS
# ----------------------------------------------------------------
app.include_router(auth.router)
app.include_router(profiles.router)

@app.get("/")
def root():
    return {"status": "success", "message": "Insighta Labs+ API is running"}
