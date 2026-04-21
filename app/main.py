from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from uuid6 import uuid7
from . import models, schemas, services, database

# Initialize DB tables
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Profile Intelligence Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Spec requires status field to be the string "502" for 502 errors
    status_val = str(exc.status_code) if exc.status_code == 502 else "error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": status_val, "message": str(exc.detail)},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    status_code = 422
    message = "Invalid type"

    for error in errors:
        error_msg = error.get("msg", "")
        error_type = error.get("type", "")
        error_loc = error.get("loc", [])

        # Missing name field entirely
        if error_type == "missing" and error_loc and error_loc[-1] == "name":
            status_code = 400
            message = "Missing or empty name"
            break

        # Our custom validator raised "Missing or empty name"
        if "Missing or empty name" in error_msg:
            status_code = 400
            message = "Missing or empty name"
            break

        # "Invalid type" stays 422 (our custom validator raises this for non-strings)

    return JSONResponse(
        status_code=status_code,
        content={"status": "error", "message": message},
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
    )


def _serialize_profile(profile: models.Profile) -> dict:
    """Serialize a profile model to a dict with consistent ISO 8601 UTC timestamp."""
    created_at = profile.created_at
    # Ensure UTC ISO 8601 format: 2026-04-01T12:00:00Z
    if created_at is not None:
        ts = created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        ts = None
    return {
        "id": profile.id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": round(float(profile.gender_probability), 2),
        "sample_size": profile.sample_size,
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_probability": round(float(profile.country_probability), 2),
        "created_at": ts,
    }


@app.post("/api/profiles", status_code=201)
async def create_profile(request: schemas.ProfileCreate, db: Session = Depends(database.get_db)):
    name_clean = request.name.lower().strip()

    # Check for existing profile (idempotency)
    existing = db.query(models.Profile).filter(models.Profile.name == name_clean).first()

    if existing:
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Profile already exists",
                "data": _serialize_profile(existing),
            },
        )

    # Fetch from external APIs
    intel = await services.get_profile_intelligence(name_clean)

    new_profile = models.Profile(
        id=str(uuid7()),
        name=name_clean,
        **intel,
    )
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)

    return JSONResponse(
        status_code=201,
        content={
            "status": "success",
            "data": _serialize_profile(new_profile),
        },
    )


@app.get("/api/profiles")
def get_profiles(
    gender: str = None,
    country_id: str = None,
    age_group: str = None,
    db: Session = Depends(database.get_db),
):
    query = db.query(models.Profile)
    if gender:
        query = query.filter(models.Profile.gender == gender.lower())
    if country_id:
        query = query.filter(models.Profile.country_id == country_id.upper())
    if age_group:
        query = query.filter(models.Profile.age_group == age_group.lower())

    results = query.all()
    data = [
        {
            "id": p.id,
            "name": p.name,
            "gender": p.gender,
            "age": p.age,
            "age_group": p.age_group,
            "country_id": p.country_id,
        }
        for p in results
    ]
    return JSONResponse(
        status_code=200,
        content={"status": "success", "count": len(data), "data": data},
    )


@app.get("/api/profiles/{profile_id}")
def get_single_profile(profile_id: str, db: Session = Depends(database.get_db)):
    profile = db.query(models.Profile).filter(models.Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return JSONResponse(
        status_code=200,
        content={"status": "success", "data": _serialize_profile(profile)},
    )


@app.delete("/api/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: str, db: Session = Depends(database.get_db)):
    profile = db.query(models.Profile).filter(models.Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    db.delete(profile)
    db.commit()
    return None
