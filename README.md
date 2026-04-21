# hng-stage-1


##  Description
This is a production-grade FastAPI service that builds a Profile Intelligence Database.

It processes names to fetch demographic insights (gender, age, nationality) and persists them in a PostgreSQL database with built-in idempotency logic.

##  Features
- Predicts demographic intelligence (gender, age, nationality)

- Persists data in a relational PostgreSQL database

- Handles idempotency (200 OK for existing records)

- Implements Pydantic V2 for strict validation

- Robust error handling and custom exception handlers

- CORS enabled

- Fully tested with pytest and database isolation


## Technology Stack
- **Framework:** FastAPI
- **Language:** Python 3.12
- **Testing:** Pytest with AsyncIO
- **Database:** PostgreSQL with SQLAlchemy 2.0


##  API Endpoints

### 1. Create Profile
**POST** `/api/profiles`

#### Query Parameters
- `name` (string, required)

#### Example Request
/api/profiles?name=ella

##  Success Response

```json
{
  "status": "success",
  "data": {
    "id": "018ed47a-...",
    "name": "ella",
    "gender": "female",
    "gender_probability": 0.99,
    "age": 24,
    "country_id": "NG",
    "created_at": "2026-04-16T01:05:12Z"
  }
}
```
### 2. List Profiles
GET /api/profiles
Query Parameters

    gender (string, optional)

    country_id (string, optional)

    age_group (string, optional)

#### Example Request
/api/profiles?gender=female&country_id=NG

##  Success Response

```json
{
  "status": "success",
  "count": 1,
  "data": [
    {
      "id": "018ed47a-...",
      "name": "ella",
      "gender": "female",
      "country_id": "NG"
    }
  ]
}
```

### 3. Get Single Profile
GET /api/profiles/{profile_id}

#### Example Request
/api/profiles/018ed47a-6f1a-7b3e-9d2c-1a2b3c4d5e6f


##  Success Response

```json
{
  "status": "success",
  "data": {
    "id": "018ed47a-...",
    "name": "ella",
    "gender": "female",
    "age": 24,
    "country_id": "NG",
    "created_at": "2026-04-16T01:05:12Z"
  }
}
```

### 4. Delete Profile
DELETE /api/profiles/{profile_id}

#### Success Response
Status: 204 No Content


##  Setup & Installation

1. **Clone the repository**
```bash
git clone https://github.com/Chimereya/hng-stage-1.git
cd hng-stage-1
```



2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Run tests**
```bash
python3 -m pytest
```

4. **Run the application locally**
```bash
uvicorn app.main:app --reload
```

##  External APIs used
- https://api.genderize.io/
- https://api.agify.io/
- https://api.nationalize.io/


