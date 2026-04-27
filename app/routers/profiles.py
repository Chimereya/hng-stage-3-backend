import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc
from ..database import get_db
from ..models import Profile
from ..schemas import ProfileCreate, ProfileResponse
from ..services import get_profile_intelligence
from ..parser import parse_query
from ..dependencies import (
    require_admin,
    require_analyst,
    require_api_version
)
from ..models import User

router = APIRouter(prefix="/api", tags=["Profiles"])

VALID_SORT_FIELDS = ["age", "created_at", "gender_probability"]
VALID_ORDERS      = ["asc", "desc"]
VALID_GENDERS     = ["male", "female"]
VALID_AGE_GROUPS  = ["child", "teenager", "adult", "senior"]



def serialize_profile(profile: Profile) -> dict:
    """Serializing a profile model to a dictionary."""
    created_at = profile.created_at
    ts = created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if created_at else None
    return {
        "id" : str(profile.id),
        "name" : profile.name,
        "gender": profile.gender,
        "gender_probability"  : round(float(profile.gender_probability), 2),
        "age" : profile.age,
        "age_group": profile.age_group,
        "country_id" : profile.country_id,
        "country_name" : profile.country_name,
        "country_probability" : round(float(profile.country_probability), 2),
        "created_at" : ts,
    }


def build_links(base_url: str, page: int, limit: int, total: int) -> dict:
    """
    Building next/prev pagination links.
    it is supposed to return null for prev on page 1 and next on last page.
    """
    total_pages = (total + limit - 1) // limit
    return {
        "self": f"{base_url}?page={page}&limit={limit}",
        "next": f"{base_url}?page={page + 1}&limit={limit}" if page < total_pages else None,
        "prev": f"{base_url}?page={page - 1}&limit={limit}" if page > 1 else None,
    }




def apply_filters(
    query,
    gender= None,
    age_group= None,
    country_id = None,
    min_age = None,
    max_age = None,
    min_gender_probability = None,
    min_country_probability = None,
):
    
    if gender:
        query = query.filter(Profile.gender == gender.lower())
    if age_group:
        query = query.filter(Profile.age_group == age_group.lower())
    if country_id:
        query = query.filter(Profile.country_id == country_id.upper())
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    if min_gender_probability is not None:
        query = query.filter(Profile.gender_probability >= min_gender_probability)
    if min_country_probability is not None:
        query = query.filter(Profile.country_probability >= min_country_probability)
    return query


def apply_sorting(query, sort_by: str = None, order: str = "desc"):
    valid_fields = {
        "age" : Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }
    if sort_by and sort_by in valid_fields:
        column = valid_fields[sort_by]
        query  = query.order_by(
            column.desc() if order == "desc" else column.asc()
        )
    return query

# Endpoint for creating a new profile only meant for admin
@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body : ProfileCreate,
    db : Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    api_version_check: None = Depends(require_api_version),
):
    name_clean = body.name.lower().strip()

    # Return existing profile if name already exists
    existing = db.query(Profile).filter(
        Profile.name == name_clean
    ).first()
    if existing:
        return JSONResponse(
            status_code=200,
            content={
                "status" : "success",
                "message": "Profile already exists",
                "data"   : serialize_profile(existing)
            }
        )

    # Fetch from genderize, agify, nationalize
    intel = await get_profile_intelligence(name_clean)
    profile = Profile(name=name_clean, **intel)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return JSONResponse(
        status_code=201,
        content={"status": "success", "data": serialize_profile(profile)}
    )



@router.get("/profiles")
def list_profiles(
    db : Session = Depends(get_db),
    current_user : User = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
    gender : str = Query(None),
    age_group : str = Query(None),
    country_id : str = Query(None),
    min_age : int = Query(None),
    max_age: int = Query(None),
    min_gender_probability  : float= Query(None, ge=0.0, le=1.0),
    min_country_probability : float= Query(None, ge=0.0, le=1.0),
    sort_by: str = Query(None),
    order : str = Query("desc"),
    page : int = Query(1, ge=1),
    limit : int = Query(10, ge=1, le=50),
):
    

    # Validate inputs
    if sort_by and sort_by not in VALID_SORT_FIELDS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    if order and order not in VALID_ORDERS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    if gender and gender.lower() not in VALID_GENDERS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )
    if age_group and age_group.lower() not in VALID_AGE_GROUPS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Invalid query parameters"}
        )

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender  = gender,
        age_group = age_group,
        country_id = country_id,
        min_age  = min_age,
        max_age = max_age,
        min_gender_probability  = min_gender_probability,
        min_country_probability = min_country_probability,
    )
    query = apply_sorting(query, sort_by, order)

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return JSONResponse(
        status_code=200,
        content={
            "status" : "success",
            "page": page,
            "limit": limit,
            "total": total,
            "total_pages": total_pages,
            "links" : build_links("/api/profiles", page, limit, total),
            "data": [serialize_profile(p) for p in profiles],
        }
    )




@router.get("/profiles/export")
def export_profiles(
    db : Session = Depends(get_db),
    current_user: User  = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
    format : str= Query("csv"),
    gender : str = Query(None),
    country_id : str= Query(None),
    age_group : str = Query(None),
    min_age : int = Query(None),
    max_age : int= Query(None),
    min_gender_probability  : float  = Query(None),
    min_country_probability : float = Query(None),
    sort_by : str = Query("created_at"),
    order: str = Query("desc"),
):
    """Exports profiles as a CSV file with same filters as list."""
    if format != "csv":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "Only csv format is supported"}
        )

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender  = gender,
        age_group = age_group,
        country_id = country_id,
        min_age = min_age,
        max_age = max_age,
        min_gender_probability = min_gender_probability,
        min_country_probability = min_country_probability,
    )
    query = apply_sorting(query, sort_by, order)
    profiles = query.all()

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at"
    ])
    for p in profiles:
        writer.writerow([
            str(p.id), p.name, p.gender, p.gender_probability,
            p.age, p.age_group, p.country_id, p.country_name,
            p.country_probability,
            p.created_at.isoformat() if p.created_at else None
        ])

    output.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename  = f"profiles_{timestamp}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type = "text/csv",
        headers    = {"Content-Disposition": f"attachment; filename={filename}"}
    )




@router.get("/profiles/search")
def search_profiles(
    db : Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
    q: str = Query(None),
    page : int = Query(1, ge=1),
    limit : int = Query(10, ge=1, le=50),
):
    if not q or not q.strip():
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Invalid query parameters"}
        )

    filters = parse_query(q.strip())
    if filters is None:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": "Unable to interpret query"}
        )

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender = filters.get("gender"),
        age_group = filters.get("age_group"),
        country_id = filters.get("country_id"),
        min_age = filters.get("min_age"),
        max_age = filters.get("max_age"),
    )

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return JSONResponse(
        status_code=200,
        content={
            "status": "success",
            "page" : page,
            "limit" : limit,
            "total" : total,
            "total_pages": total_pages,
            "links" : build_links("/api/profiles/search", page, limit, total),
            "data": [serialize_profile(p) for p in profiles],
        }
    )


@router.get("/profiles/{profile_id}")
def get_profile(
    profile_id : str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
):
    """Returns a single profile by ID."""
    profile = db.query(Profile).filter(
        Profile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": "Profile not found"}
        )

    return JSONResponse(
        status_code=200,
        content={"status": "success", "data": serialize_profile(profile)}
    )



@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(
    profile_id  : str,
    db : Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    api_version_check: None = Depends(require_api_version),
):
    
    profile = db.query(Profile).filter(
        Profile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "error", "message": "Profile not found"}
        )

    db.delete(profile)
    db.commit()

    return None