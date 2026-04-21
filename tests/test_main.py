import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from fastapi import HTTPException

@pytest.mark.asyncio
async def test_create_profile_success(mocker):
    """Test successful creation by mocking the external service"""
    mock_intel = {
        "gender": "male",
        "gender_probability": 0.95,
        "sample_size": 500,
        "age": 25,
        "age_group": "adult",
        "country_id": "US",
        "country_probability": 0.15
    }
    # Mock the service so it doesn't call real APIs
    mocker.patch("app.services.get_profile_intelligence", return_value=mock_intel)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/profiles", json={"name": "testuser"})
    
    assert response.status_code == 201
    assert response.json()["status"] == "success"

@pytest.mark.asyncio
async def test_idempotency_returns_200(mocker):
    """Test that duplicate names return 200 and 'already exists'"""
    mock_intel = {
        "gender": "female", "gender_probability": 1.0, "sample_size": 10,
        "age": 20, "age_group": "adult", "country_id": "NG", "country_probability": 0.5
    }
    mocker.patch("app.services.get_profile_intelligence", return_value=mock_intel)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # First creation
        await ac.post("/api/profiles", json={"name": "repeat"})
        # Second attempt
        response = await ac.post("/api/profiles", json={"name": "repeat"})
        
    assert response.status_code == 200
    assert response.json()["message"] == "Profile already exists"

@pytest.mark.asyncio
async def test_invalid_name_502_mocked(mocker):
    """Verify 502 error handling"""
    mocker.patch(
        "app.services.get_profile_intelligence", 
        side_effect=HTTPException(status_code=502, detail="Genderize returned an invalid response")
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/profiles", json={"name": "---"})
        
    assert response.status_code == 502
    assert response.json()["message"] == "Genderize returned an invalid response"

@pytest.mark.asyncio
async def test_get_profiles_list():
    """Verify the list endpoint works"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/profiles")
        
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)