import csv
import io
import codecs
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..database import get_db
from ..limiter import limiter
from ..models import Profile, User
from ..schemas import ProfileCreate
from ..services import get_profile_intelligence
from ..parser import parse_query
from ..dependencies import require_admin, require_analyst, require_api_version
from ..cache import cache_get, cache_set, cache_invalidate_all, make_cache_key
from uuid6 import uuid7

router = APIRouter(
    prefix="/api",
    tags=["Profiles"],
    dependencies=[Depends(require_api_version)],
)

VALID_SORT_FIELDS = {"age", "created_at", "gender_probability"}
VALID_ORDERS = {"asc", "desc"}

CSV_CHUNK_SIZE = 500

VALID_GENDERS = {"male", "female"}
VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}


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


def apply_sorting(query, sort_by: str | None = None, order: str = "desc"):
   
    if sort_by is not None and sort_by not in VALID_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": f"Invalid sort_by field. Must be one of: {', '.join(sorted(VALID_SORT_FIELDS))}"},
        )
    if order not in VALID_ORDERS:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Invalid order value. Must be 'asc' or 'desc'"},
        )

    mapping = {
        "age": Profile.age,
        "created_at": Profile.created_at,
        "gender_probability": Profile.gender_probability,
    }
    if sort_by in mapping:
        col = mapping[sort_by]
        query = query.order_by(col.desc() if order == "desc" else col.asc())
    return query


REQUIRED_FIELDS = {
    "name", "gender", "gender_probability",
    "age", "age_group", "country_id", "country_name", "country_probability"
}


def validate_csv_row(row: dict) -> dict:
    
    # Check required fields are present and non-empty
    for field in REQUIRED_FIELDS:
        if field not in row or row[field].strip() == "":
            raise ValueError("missing_fields")

    name = row["name"].strip().lower()
    if not name:
        raise ValueError("missing_fields")

    gender = row["gender"].strip().lower()
    if gender not in VALID_GENDERS:
        raise ValueError("invalid_gender")

    try:
        age = int(row["age"].strip())
        if age < 0 or age > 150:
            raise ValueError("invalid_age")
    except (ValueError, TypeError):
        raise ValueError("invalid_age")

    age_group = row["age_group"].strip().lower()
    if age_group not in VALID_AGE_GROUPS:
        raise ValueError("invalid_age_group")

    try:
        gender_probability = float(row["gender_probability"].strip())
        if not (0.0 <= gender_probability <= 1.0):
            raise ValueError("invalid_gender_probability")
    except (ValueError, TypeError):
        raise ValueError("invalid_gender_probability")

    try:
        country_probability = float(row["country_probability"].strip())
        if not (0.0 <= country_probability <= 1.0):
            raise ValueError("invalid_country_probability")
    except (ValueError, TypeError):
        raise ValueError("invalid_country_probability")

    country_id = row["country_id"].strip().upper()
    if len(country_id) != 2:
        raise ValueError("invalid_country_id")

    country_name = row["country_name"].strip()
    if not country_name:
        raise ValueError("missing_fields")

    return {
        "name": name,
        "gender": gender,
        "gender_probability": gender_probability,
        "age": age,
        "age_group": age_group,
        "country_id": country_id,
        "country_name": country_name,
        "country_probability": country_probability,
    }




@router.post("/profiles", status_code=201)
@limiter.limit("60/minute")

def create_profile(
    request: Request,
    body: ProfileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import asyncio

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

    # Run the async intelligence fetch in a new event loop from the sync context
    intel = asyncio.get_event_loop().run_until_complete(
        get_profile_intelligence(name_clean)
    )
    profile = Profile(name=name_clean, **intel)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    cache_invalidate_all()

    return {"status": "success", "data": serialize_profile(profile)}


@router.get("/profiles/export")
@limiter.limit("60/minute")
def export_profiles(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst),
    format: str = Query("csv"),
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
    # apply_sorting now validates and raises HTTPException on bad inputs
    query = apply_sorting(query, sort_by, order)

    CSV_HEADERS = [
        "id", "name", "gender", "gender_probability",
        "age", "age_group", "country_id", "country_name",
        "country_probability", "created_at",
    ]

   
    def generate_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_HEADERS)
        yield buf.getvalue()

        for p in query.yield_per(CSV_CHUNK_SIZE):
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                str(p.id), p.name, p.gender, p.gender_probability,
                p.age, p.age_group, p.country_id, p.country_name,
                p.country_probability,
                p.created_at.isoformat() if p.created_at else None,
            ])
            yield buf.getvalue()

    timestamp = int(datetime.now(timezone.utc).timestamp())
    filename = f"profiles_{timestamp}.csv"

    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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

    raw_filters = parse_query(q.strip())
    if not raw_filters:
        raise HTTPException(
            status_code=422,
            detail={"status": "error", "message": "Unable to interpret query"},
        )

    cache_key = make_cache_key("search_profiles", raw_filters, page, limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    query = db.query(Profile)
    query = apply_filters(query, **raw_filters)

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    result = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_links(str(request.url.path), page, limit, total),
        "data": [serialize_profile(p) for p in profiles],
    }

    cache_set(cache_key, result)
    return result


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
    raw_filters = {
        "gender": gender,
        "age_group": age_group,
        "country_id": country_id,
        "min_age": min_age,
        "max_age": max_age,
        "min_gender_probability": min_gender_probability,
        "min_country_probability": min_country_probability,
    }

    # Validate sort params early so cache is never populated with bad keys
    if sort_by is not None and sort_by not in VALID_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": f"Invalid sort_by field. Must be one of: {', '.join(sorted(VALID_SORT_FIELDS))}"},
        )
    if order not in VALID_ORDERS:
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Invalid order value. Must be 'asc' or 'desc'"},
        )

    cache_key = make_cache_key("list_profiles", raw_filters, page, limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    query = db.query(Profile)
    query = apply_filters(query, **raw_filters)
    query = apply_sorting(query, sort_by, order)

    total = query.count()
    profiles = query.offset((page - 1) * limit).limit(limit).all()
    total_pages = (total + limit - 1) // limit

    result = {
        "status": "success",
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "links": build_links("/api/profiles", page, limit, total),
        "data": [serialize_profile(p) for p in profiles],
    }

    cache_set(cache_key, result)
    return result


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

    cache_invalidate_all()
    return None


@router.post("/profiles/upload", status_code=200)
@limiter.limit("5/minute")
async def upload_profiles_csv(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if not file.filename.endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "Only .csv files are accepted"},
        )

    total_rows = 0
    inserted = 0
    skip_reasons: dict[str, int] = {}

    def record_skip(reason: str):
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    def record_skip_n(reason: str, n: int):
        skip_reasons[reason] = skip_reasons.get(reason, 0) + n

    chunk: list[dict] = []

    def flush_chunk():
        nonlocal inserted
        if not chunk:
            return
        attempted = len(chunk)

        try:
            stmt = pg_insert(Profile).values(chunk)
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
            result = db.execute(stmt)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise

        actually_inserted = result.rowcount
        inserted += actually_inserted
        duplicates = attempted - actually_inserted
        if duplicates > 0:
            record_skip_n("duplicate_name", duplicates)
        chunk.clear()

    try:
 
        stream = codecs.getreader("utf-8")(file.file, errors="strict")
        reader = csv.DictReader(stream)

        for row in reader:
            total_rows += 1

           
            if any(k is None or (isinstance(k, str) and k.strip() == "") for k in row.keys()):
                record_skip("malformed_row")
                continue

            # Also guard against None values from a short row
            if None in row.values():
                record_skip("malformed_row")
                continue

            try:
                clean = validate_csv_row(row)
            except ValueError as e:
                record_skip(str(e))
                continue

            clean["id"] = str(uuid7())
            clean["created_at"] = datetime.now(timezone.utc)

            chunk.append(clean)

            if len(chunk) >= CSV_CHUNK_SIZE:
                flush_chunk()

        flush_chunk()

  
    except UnicodeDecodeError:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail={"status": "error", "message": "File is not valid UTF-8. Please re-save as UTF-8 and retry."},
        )
    except csv.Error as e:
        record_skip("malformed_row")
        return JSONResponse(
            status_code=207,
            content={
                "status": "partial",
                "message": f"CSV parse error: {str(e)}",
                "total_rows": total_rows,
                "inserted": inserted,
              
                "skipped": total_rows - inserted,
                "reasons": skip_reasons,
            },
        )
    except SQLAlchemyError as e:
        db.rollback()
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"Database error during upload: {str(e)}",
                "total_rows": total_rows,
                "inserted": inserted,
                "skipped": total_rows - inserted,
                "reasons": skip_reasons,
            },
        )
    finally:
        await file.close()


    skipped = total_rows - inserted
    cache_invalidate_all()

    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "reasons": skip_reasons,
    }
