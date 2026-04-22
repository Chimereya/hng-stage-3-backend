# Insighta Intelligence Engine

A queryable demographic intelligence API built with FastAPI and PostgreSQL (Neon).
Clients can filter, sort, paginate, and query profiles using natural language.

---

## Tech Stack

- **FastAPI** — API framework
- **PostgreSQL (Neon)** — Database
- **SQLAlchemy** — ORM
- **uuid6** — UUID v7 generation
- **pycountry** — Country name lookup

---

## Project Structure

### Updated stage 1 structure

hng-stage-2/
├── app/
│   ├── __init__.py
│   ├── main.py          # App initialization & endpoints
│   ├── parser.py        # Rule-based natural language query logic
│   ├── models.py        # SQLAlchemy database models
│   ├── schemas.py       # Pydantic validation models
│   ├── services.py      # External API calls (genderize, agify, nationalize)
│   └── database.py      # DB connection & session management
├── seed.py              # One-time database seeding script
├── seed_profiles.json   # 2026 seed profiles
├── .env                 # Environment variables (not committed of course)
└── requirements.txt

---

## Setup & Installation

### 1. Clone the repo
```bash
git clone https://github.com/Chimereya/hng-stage-2.git
cd hng-stage-2
```

### 2. Create and activate virtual environment
```bash
python -m venv env       # For linux, you might want to use 'python3'
source env/bin/activate  # Windows: env\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
Create a `.env` file in the project root:
Then add: DATABASE_URL=your_neon_postgresql_connection_string to it

### 5. Seed the database
```bash
python seed.py
```
Re-running the seed script will not create duplicate records(Idempotency).

### 6. Run the server
```bash
uvicorn app.main:app --reload
```

---

## API Endpoints

### `GET /api/profiles`
Fetch profiles with optional filtering, sorting, and pagination.

**Query Parameters:**
| Parameter | Type | Description |
|---|---|---|
| `gender` | string | `male` or `female` |
| `age_group` | string | `child`, `teenager`, `adult`, `senior` |
| `country_id` | string | ISO 2-letter code e.g. `NG`, `KE` |
| `min_age` | integer | Minimum age |
| `max_age` | integer | Maximum age |
| `min_gender_probability` | float | Minimum gender confidence score |
| `min_country_probability` | float | Minimum country confidence score |
| `sort_by` | string | `age`, `created_at`, `gender_probability` |
| `order` | string | `asc` or `desc` (default: `asc`) |
| `page` | integer | Page number (default: `1`) |
| `limit` | integer | Results per page (default: `10`, max: `50`) |

**Example Query:**

GET /api/profiles?gender=male&country_id=NG&min_age=25&sort_by=age&order=desc&page=1&limit=10

**Response:**
```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 120,
  "data": [...]
}
```

---

### `GET /api/profiles/search`
Query profiles using plain English natural language.

**Query Parameters:**
| Parameter | Type | Description |
|---|---|---|
| `q` | string | Natural language query |
| `page` | integer | Page number (default: `1`) |
| `limit` | integer | Results per page (default: `10`, max: `50`) |

**Example Query:**

GET /api/profiles/search?q=young males from nigeria
GET /api/profiles/search?q=adult females from kenya
GET /api/profiles/search?q=seniors above 65

**Supported Query Patterns:**
| Query | Parsed As |
|---|---|
| `young males` | gender=male, min_age=16, max_age=24 |
| `females above 30` | gender=female, min_age=30 |
| `people from angola` | country_id=AO |
| `adult males from kenya` | gender=male, age_group=adult, country_id=KE |
| `male and female teenagers above 17` | age_group=teenager, min_age=17 |

**Response:**
```json
{
  "status": "success",
  "page": 1,
  "limit": 10,
  "total": 45,
  "data": [...]
}
```

**Error — uninterpretable query:**
```json
{
  "status": "error",
  "message": "Unable to interpret query"
}
```

---

### `POST /api/profiles`
Create a new profile by fetching demographic data from external APIs.

**Request Body:**
```json
{
  "name": "John Doe"
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "id": "...",
    "name": "john doe",
    "gender": "male",
    "gender_probability": 0.95,
    "age": 35,
    "age_group": "adult",
    "country_id": "US",
    "country_name": "United States",
    "country_probability": 0.85,
    "created_at": "2026-04-22T07:00:00Z"
  }
}
```

---

### `GET /api/profiles/{id}`
Fetch a single profile by its UUID.

**Response:**
```json
{
  "status": "success",
  "data": { ... }
}
```

---

### `DELETE /api/profiles/{id}`
Delete a profile by its UUID. Returns `204 No Content`.

---

## Error Responses

All errors follow this structure:
```json
{
  "status": "error",
  "message": "<error message>"
}
```

| Status Code | Meaning |
|---|---|
| `400` | Missing or empty parameter |
| `422` | Invalid parameter type or value |
| `404` | Profile not found |
| `500` | Internal server error |
| `502` | External API failure |

---

## Natural Language Query — How It Works

The `/api/profiles/search` endpoint uses **rule-based parsing only** — no AI or LLMs at all.

The parser (`app/parser.py`) works in this order:
1. **Gender** — detects keywords like `male`, `females`, `men`, `women`
2. **Age group** — detects `child`, `teenager`, `adult`, `senior`, `young` (maps to ages 16–24)
3. **Age ranges** — detects phrases like `above 30`, `under 25`, `between 20 and 40`
4. **Country** — checks nationality adjectives (`nigerian` → `NG`) then uses `pycountry` for country names (`nigeria` → `NG`)

If no filters can be extracted, returns `"Unable to interpret query"`.

---

## Database Schema

| Field | Type | Notes |
|---|---|---|
| `id` | UUID v7 | Primary key |
| `name` | VARCHAR | Unique |
| `gender` | VARCHAR | `male` or `female` |
| `gender_probability` | FLOAT | Confidence score |
| `age` | INT | Exact age |
| `age_group` | VARCHAR | `child`, `teenager`, `adult`, `senior` |
| `country_id` | VARCHAR(2) | ISO code e.g. `NG` |
| `country_name` | VARCHAR | Full country name e.g. `Nigeria` |
| `country_probability` | FLOAT | Confidence score |
| `created_at` | TIMESTAMP | Auto-generated UTC |

---

## Data Seeding

The database is pre-seeded with 2,026 profiles from `seed_profiles.json`.

To re-seed (Don't worry, it is safe to run multiple times — no duplicates created):
```bash
python seed.py
```
