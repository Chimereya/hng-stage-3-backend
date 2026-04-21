# app/services.py
import httpx
import asyncio
import pycountry
from fastapi import HTTPException


def get_country_name(country_id: str) -> str:
    """Convert ISO 2-letter country code to full country name."""
    country = pycountry.countries.get(alpha_2=country_id.upper())
    return country.name if country else country_id


def classify_age_group(age: int) -> str:
    """Classify an age into the four Stage 2 age groups."""
    if age <= 12:
        return "child"
    elif age <= 19:
        return "teenager"
    elif age <= 59:
        return "adult"
    else:
        return "senior"


async def get_profile_intelligence(name: str) -> dict:
    """
    Fetch gender, age, and nationality data from external APIs
    and return a dict ready to be unpacked into a Profile model.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            responses = await asyncio.gather(
                client.get(f"https://api.genderize.io?name={name}"),
                client.get(f"https://api.agify.io?name={name}"),
                client.get(f"https://api.nationalize.io?name={name}"),
                return_exceptions=True,
            )
        except Exception:
            raise HTTPException(
                status_code=502,
                detail="Failed to reach external APIs",
            )

        g_res, a_res, n_res = responses

        # Validate Genderize
        if isinstance(g_res, Exception) or g_res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Genderize returned an invalid response",
            )
        g_data = g_res.json()
        if not g_data.get("gender") or not g_data.get("count"):
            raise HTTPException(
                status_code=502,
                detail="Genderize returned an invalid response",
            )

        # Validate Agify
        if isinstance(a_res, Exception) or a_res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Agify returned an invalid response",
            )
        a_data = a_res.json()
        age = a_data.get("age")
        if age is None:
            raise HTTPException(
                status_code=502,
                detail="Agify returned an invalid response",
            )

        # Validate Nationalize
        if isinstance(n_res, Exception) or n_res.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail="Nationalize returned an invalid response",
            )
        n_data = n_res.json()
        countries = n_data.get("country", [])
        if not countries:
            raise HTTPException(
                status_code=502,
                detail="Nationalize returned an invalid response",
            )

        # Pick highest probability country
        top_country = max(countries, key=lambda x: x["probability"])
        country_id = top_country["country_id"]

        return {
            "gender": g_data["gender"],
            "gender_probability": round(g_data["probability"], 2),
            "age": age,
            "age_group": classify_age_group(age),
            "country_id": country_id,
            "country_name": get_country_name(country_id),
            "country_probability": round(top_country["probability"], 2),
        }