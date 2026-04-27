import csv
import io
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc
from ..database import get_db
from ..models import Profile
from ..schemas import ProfileCreate, ProfileResponse, PaginatedResponse
from ..services import fetch_profile_data
from ..parser import parse_query
from ..dependencies import (
    require_admin,
    require_analyst,
    require_api_version
)
from ..models import User

router = APIRouter(prefix="/api", tags=["Profiles"])



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




def apply_filters(query, gender, country, age_group, min_age, max_age):
    
    if gender:
        query = query.filter(Profile.gender == gender)
    if country:
        query = query.filter(Profile.country_id == country)
    if age_group:
        query = query.filter(Profile.age_group == age_group)
    if min_age is not None:
        query = query.filter(Profile.age >= min_age)
    if max_age is not None:
        query = query.filter(Profile.age <= max_age)
    return query



# Endpoint for creating a new profile only meant for admin
@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile(
    body: ProfileCreate,
    db: Session = Depends(get_db),
    current_user: User    = Depends(require_admin),
    api_version_check : None    = Depends(require_api_version),
    ):
    
    # Check if profile with this name already exists
    existing = db.query(Profile).filter(
        Profile.name == body.name
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "error", "message": "Profile with this name already exists"}
        )

    # Fetch data from genderize, agify, nationalize
    profile_data = await fetch_profile_data(body.name)

    # Save to DB
    profile = Profile(**profile_data)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    return {
        "status": "success",
        "data"  : ProfileResponse.model_validate(profile)
    }




@router.get("/profiles")
def list_profiles(
    db : Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
    gender : str = Query(None),
    country : str = Query(None),
    age_group: str = Query(None),
    min_age: int = Query(None),
    max_age : int = Query(None),
    sort_by : str = Query("created_at"),
    order : str = Query("desc"),
    page : int = Query(1, ge=1),
    limit : int = Query(10, ge=1, le=100),
):
    query = db.query(Profile)

    # Applying the filters
    query = apply_filters(query, gender, country, age_group, min_age, max_age)

    # Applying sorting
    sort_column = getattr(Profile, sort_by, Profile.created_at)
    query = query.order_by(
        desc(sort_column) if order == "desc" else asc(sort_column)
    )

    # Getting the total count before pagination
    total = query.count()

    # Applying pagination
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return {
        "status" : "success",
        "page": page,
        "limit": limit,
        "total" : total,
        "total_pages": total_pages,
        "links" : build_links("/api/profiles", page, limit, total),
        "data": [ProfileResponse.model_validate(p) for p in profiles]
    }




@router.get("/profiles/export")
def export_profiles(
    db : Session = Depends(get_db),
    current_user: User    = Depends(require_analyst),
    api_version_check : None    = Depends(require_api_version),
    format : str = Query("csv"),
    gender: str = Query(None),
    country : str = Query(None),
    age_group: str= Query(None),
    min_age : int = Query(None),
    max_age : int  = Query(None),
    sort_by: str = Query("created_at"),
    order: str  = Query("desc"),
):
    """
    Exporting profiles as a CSV file.

    """
    if format != "csv":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "Only csv format is supported"}
        )

    query = db.query(Profile)
    query = apply_filters(query, gender, country, age_group, min_age, max_age)

    sort_column = getattr(Profile, sort_by, Profile.created_at)
    query = query.order_by(
        desc(sort_column) if order == "desc" else asc(sort_column)
    )

    profiles = query.all()

    # Build CSV in memory
    output  = io.StringIO()
    writer  = csv.writer(output)

    # Write header row
    writer.writerow([
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at"
    ])

    # Write data rows
    for p in profiles:
        writer.writerow([
            str(p.id), p.name, p.gender, p.gender_probability,
            p.age, p.age_group, p.country_id, p.country_name,
            p.country_probability,
            p.created_at.isoformat() if p.created_at else None
        ])

    output.seek(0)

    # Generate filename with timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename  = f"profiles_{timestamp}.csv"

    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type ="text/csv",
        headers ={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )




@router.get("/profiles/search")
def search_profiles(
    db : Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    api_version_check: None = Depends(require_api_version),
    q : str = Query(..., description="Natural language search query"),
    page : int  = Query(1, ge=1),
    limit : int = Query(10, ge=1, le=100),
):
    

    filters = parse_query(q)

    query = db.query(Profile)
    query = apply_filters(
        query,
        gender = filters.get("gender"),
        country = filters.get("country_id"),
        age_group = filters.get("age_group"),
        min_age = filters.get("min_age"),
        max_age = filters.get("max_age"),
    )

    total       = query.count()
    profiles    = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    return {
        "status" : "success",
        "page" : page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_links("/api/profiles/search", page, limit, total),
        "data": [ProfileResponse.model_validate(p) for p in profiles]
    }




@router.get("/profiles/{profile_id}")
def get_profile(
    profile_id  : str,
    db : Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
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

    return {"status": "success", "data": ProfileResponse.model_validate(profile)}




@router.delete("/profiles/{profile_id}")
def delete_profile(
    profile_id  : str,
    db          : Session = Depends(get_db),
    current_user: User    = Depends(require_admin),
    api_version_check: None    = Depends(require_api_version),
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

    return {"status": "success", "message": "Profile deleted successfully"}