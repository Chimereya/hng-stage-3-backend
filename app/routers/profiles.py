import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..limiter import limiter
from ..models import Profile, User
from ..schemas import ProfileCreate
from ..services import get_profile_intelligence
from ..parser import parse_query
from ..dependencies import require_admin, require_analyst, require_api_version

router = APIRouter(
    prefix="/api",
    tags=["Profiles"],
    dependencies=[Depends(require_api_version)],
)

VALID_SORT_FIELDS = ["age", "created_at", "gender_probability"]
VALID_ORDERS = ["asc", "desc"]


# ----------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------

def serialize_profile(profile: Profile) -> dict:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "gender": profile.gender,
        "gender_probability": round(float(profile.gender_probability), 2),
        "age": profile.age,
        "age_group": profile.age_group,
        "country_id": profile.country_id,
        "country_name": profile.country_name,
        "country_probability": round(float(profile.country_probability), 2),
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
    }


def build_links(base_path: str, page: int, limit: int, total: int) -> dict:
    total_pages = (total + limit - 1) // limit
    return {
        "self": f"{base_path}?page={page}&limit={limit}",
        "next": f"{base_path}?page={page + 1}&limit={limit}" if page < total_pages else None,
        "prev": f"{base_path}?page={page - 1}&limit={limit}" if page > 1 else None,
    }


def apply_filters(query, **filters):
    if filters.get("gender"):
        query = query.filter(Profile.gender == filters["gender"].lower())
    if filters.get("age_group"):
        query = query.filter(Profile.age_group == filters["age_group"].lower())
    if filters.get("country_id"):
        query = query.filter(Profile.country_id == filters["country_id"].upper())
    if filters.get("min_age") is not None:
        query = query.filter(Profile.age >= filters["min_age"])
    if filters.get("max_age") is not None:
        query = query.filter(Profile.age <= filters["max_age"])
    if filters.get("min_gender_probability") is not None:
        query = query.filter(Profile.gender_probability >= filters["min_gender_probability"])
    if filters.get("min_country_probability") is not None:
        query = query.filter(Profile.country_probability >= filters["min_country_probability"])
    return query


def apply_sorting(query, sort_by=None, order="desc"):
    mapping = {
        "age": Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }
    if sort_by in mapping:
        col = mapping[sort_by]
        query = query.order_by(col.desc() if order == "desc" else col.asc())
    return query


# ----------------------------------------------------------------
# CREATE PROFILE — admin only
# POST /api/profiles
# ----------------------------------------------------------------

@router.post("/profiles", status_code=201)
@limiter.limit("60/minute")
async def create_profile(
    request: Request,
    body: ProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    name_clean = body.name.lower().strip()

    existing = db.query(Profile).filter(Profile.name == name_clean).first()
    if existing:
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "message": "Profile already exists",
                "data": serialize_profile(existing),
            },
        )

    intel = await get_profile_intelligence(name_clean)
    profile = Profile(name=name_clean, **intel)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return {"status": "success", "data": serialize_profile(profile)}


# ----------------------------------------------------------------
# LIST PROFILES
# GET /api/profiles
# ----------------------------------------------------------------

@router.get("/profiles")
@limiter.limit("60/minute")
def list_profiles(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    gender: str = Query(None),
    age_group: str = Query(None),
    country_id: str = Query(None),
    min_age: int = Query(None),
    max_age: int = Query(None),
    min_gender_probability: float = Query(None, ge=0, le=1),
    min_country_probability: float = Query(None, ge=0, le=1),
    sort_by: str = Query(None),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
):
    query = db.query(Profile)
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

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_links("/api/profiles", page, limit, total),
        "data": [serialize_profile(p) for p in profiles],
    }


# ----------------------------------------------------------------
# EXPORT CSV  (must come BEFORE /{profile_id} to avoid route clash)
# GET /api/profiles/export
# ----------------------------------------------------------------

@router.get("/profiles/export")
@limiter.limit("60/minute")
def export_profiles(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    format: str = Query("csv"),
    # Same filter/sort params as list_profiles (TRD requirement)
    gender: str = Query(None),
    age_group: str = Query(None),
    country_id: str = Query(None),
    min_age: int = Query(None),
    max_age: int = Query(None),
    min_gender_probability: float = Query(None, ge=0, le=1),
    min_country_probability: float = Query(None, ge=0, le=1),
    sort_by: str = Query(None),
    order: str = Query("desc"),
):
    if format != "csv":
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Only csv format is supported"},
        )

    query = db.query(Profile)
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
    profiles = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at",
    ])
    for p in profiles:
        writer.writerow([
            str(p.id), p.name, p.gender, p.gender_probability,
            p.age, p.age_group, p.country_id, p.country_name,
            p.country_probability,
            p.created_at.isoformat() if p.created_at else None,
        ])

    output.seek(0)
    timestamp = int(datetime.now(timezone.utc).timestamp())
    filename = f"profiles_{timestamp}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ----------------------------------------------------------------
# SEARCH
# GET /api/profiles/search
# ----------------------------------------------------------------

@router.get("/profiles/search")
@limiter.limit("60/minute")
def search_profiles(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    q: str = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
):
    if not q or not q.strip():
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Invalid query parameters"},
        )

    filters = parse_query(q.strip())
    if not filters:
        raise HTTPException(
            status_code=422,
            detail={"status": "error", "message": "Unable to interpret query"},
        )

    query = db.query(Profile)
    query = apply_filters(query, **filters)

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_links("/api/profiles/search", page, limit, total),
        "data": [serialize_profile(p) for p in profiles],
    }


# ----------------------------------------------------------------
# GET ONE
# GET /api/profiles/{profile_id}
# ----------------------------------------------------------------

@router.get("/profiles/{profile_id}")
@limiter.limit("60/minute")
def get_profile(
    profile_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Profile not found"},
        )
    return {"status": "success", "data": serialize_profile(profile)}


# ----------------------------------------------------------------
# DELETE — admin only
# DELETE /api/profiles/{profile_id}
# ----------------------------------------------------------------

@router.delete("/profiles/{profile_id}", status_code=204)
@limiter.limit("60/minute")
def delete_profile(
    profile_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(
            status_code=404,
            detail={"status": "error", "message": "Profile not found"},
        )
    db.delete(profile)
    db.commit()
    return None