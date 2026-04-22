# app/main.py
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from uuid6 import uuid7

from . import models, schemas, services, database
from .parser import parse_query

# Create tables on startup
models.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="Insighta Intelligence Engine")

# CORS — spec requires Access-Control-Allow-Origin: *
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------
# EXCEPTION HANDLERS
# ----------------------------------------------------------------

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    status_val = str(exc.status_code) if exc.status_code == 502 else "error"
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": status_val, "message": str(exc.detail)},
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
        content={"status": "error", "message": message},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": "Internal server error"},
    )


# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------

def serialize_profile(profile: models.Profile) -> dict:
    """Serialize a profile model to a dict with UTC ISO 8601 timestamp."""
    created_at = profile.created_at
    ts = created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if created_at else None
    return {
        "id": profile.id,
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": round(float(profile.gender_probability), 2),
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": round(float(profile.country_probability), 2),
        "created_at": ts,
    }


def apply_filters(query, gender, age_group, country_id, min_age, max_age,
                  min_gender_probability, min_country_probability):
    """Apply all supported filters to a SQLAlchemy query."""
    if gender:
        query = query.filter(models.Profile.gender == gender.lower())
    if age_group:
        query = query.filter(models.Profile.age_group == age_group.lower())
    if country_id:
        query = query.filter(models.Profile.country_id == country_id.upper())
    if min_age is not None:
        query = query.filter(models.Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(models.Profile.age <= max_age)
    if min_gender_probability is not None:
        query = query.filter(models.Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(models.Profile.country_probability >= min_country_probability)
    return query


def apply_sorting(query, sort_by, order):
    """Apply sorting to a SQLAlchemy query."""
    valid_sort_fields = {
        "age": models.Profile.age,
        "created_at": models.Profile.created_at,
        "gender_probability": models.Profile.gender_probability,
    }
    if sort_by and sort_by in valid_sort_fields:
        column = valid_sort_fields[sort_by]
        query = query.order_by(column.desc() if order == "desc" else column.asc())
    return query


# ----------------------------------------------------------------
# THE ENDPOINTS
# ----------------------------------------------------------------

@app.post("/api/profiles", status_code=201)
async def create_profile(
    request: schemas.ProfileCreate,
    db: Session = Depends(database.get_db)
):
    name_clean = request.name.lower().strip()

    # Return existing profile if name already exists
    existing = db.query(models.Profile).filter(
        models.Profile.name == name_clean
    ).first()

    if existing:
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Profile already exists",
                "data": serialize_profile(existing),
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
            "data": serialize_profile(new_profile),
        },
    )


@app.get("/api/profiles/search")
def search_profiles(
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(database.get_db),
):
    # Validate q parameter
    if not q or not q.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid query parameters"},
        )

    # Parse natural language query into filters
    filters = parse_query(q.strip())

    if filters is None:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Unable to interpret query"},
        )

    # Build query from parsed filters
    query = db.query(models.Profile)
    query = apply_filters(
        query,
        gender=filters.get("gender"),
        age_group=filters.get("age_group"),
        country_id=filters.get("country_id"),
        min_age=filters.get("min_age"),
        max_age=filters.get("max_age"),
        min_gender_probability=None,
        min_country_probability=None,
    )

    total = query.count()
    offset = (page - 1) * limit
    profiles = query.offset(offset).limit(limit).all()

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "page": page,
            "limit": limit,
            "total": total,
            "data": [serialize_profile(p) for p in profiles],
        },
    )


@app.get("/api/profiles")
def get_profiles(
    gender: Optional[str] = Query(default=None),
    age_group: Optional[str] = Query(default=None),
    country_id: Optional[str] = Query(default=None),
    min_age: Optional[int] = Query(default=None, ge=0),
    max_age: Optional[int] = Query(default=None, ge=0),
    min_gender_probability: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    min_country_probability: Optional[float] = Query(default=None, ge=0.0, le=1.0),
    sort_by: Optional[str] = Query(default=None),
    order: Optional[str] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(database.get_db),
):
    # Validate sort_by
    if sort_by and sort_by not in ["age", "created_at", "gender_probability"]:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"},
        )

    # Validate order
    if order and order not in ["asc", "desc"]:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"},
        )

    # Validate gender
    if gender and gender.lower() not in ["male", "female"]:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"},
        )

    # Validate age_group
    if age_group and age_group.lower() not in ["child", "teenager", "adult", "senior"]:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"},
        )

    query = db.query(models.Profile)

    query = apply_filters(
        query,
        gender=gender,
        age_group=age_group,
        country_id=country_id,
        min_age=min_age,
        max_age=max_age,
        min_gender_probability=min_gender_probability,
        min_country_probability=min_country_probability,
    )

    query = apply_sorting(query, sort_by, order)

    # Get total before pagination
    total = query.count()

    # Apply pagination
    offset = (page - 1) * limit
    profiles = query.offset(offset).limit(limit).all()

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "page": page,
            "limit": limit,
            "total": total,
            "data": [serialize_profile(p) for p in profiles],
        },
    )


@app.get("/api/profiles/{profile_id}")
def get_single_profile(
    profile_id: str,
    db: Session = Depends(database.get_db)
):
    profile = db.query(models.Profile).filter(
        models.Profile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    return JSONResponse(
        status_code=200,
        content={"status": "success", "data": serialize_profile(profile)},
    )


@app.delete("/api/profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id: str,
    db: Session = Depends(database.get_db)
):
    profile = db.query(models.Profile).filter(
        models.Profile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db.delete(profile)
    db.commit()
    return None