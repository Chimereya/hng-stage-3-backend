import httpx
import asyncio
from fastapi import HTTPException

async def get_profile_intelligence(name: str):

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            responses = await asyncio.gather(
                client.get(f"https://api.genderize.io?name={name}"),
                client.get(f"https://api.agify.io?name={name}"),
                client.get(f"https://api.nationalize.io?name={name}"),
                return_exceptions=True
            )
        except Exception:
            raise HTTPException(status_code=502, detail="Genderize returned an invalid response")

        g_res, a_res, n_res = responses

        # Validate Genderize
        if isinstance(g_res, Exception) or g_res.status_code != 200:
            raise HTTPException(status_code=502, detail="Genderize returned an invalid response")
        g_data = g_res.json()
        # gender must be non-null and count must be non-zero
        if not g_data.get("gender") or not g_data.get("count"):
            raise HTTPException(status_code=502, detail="Genderize returned an invalid response")

        # Validate Agify
        if isinstance(a_res, Exception) or a_res.status_code != 200:
            raise HTTPException(status_code=502, detail="Agify returned an invalid response")
        a_data = a_res.json()
        age = a_data.get("age")
        if age is None:
            raise HTTPException(status_code=502, detail="Agify returned an invalid response")

        # Classify age group
        if age <= 12:
            age_group = "child"
        elif age <= 19:
            age_group = "teenager"
        elif age <= 59:
            age_group = "adult"
        else:
            age_group = "senior"

        # Validate Nationalize
        if isinstance(n_res, Exception) or n_res.status_code != 200:
            raise HTTPException(status_code=502, detail="Nationalize returned an invalid response")
        n_data = n_res.json()
        countries = n_data.get("country", [])
        if not countries:
            raise HTTPException(status_code=502, detail="Nationalize returned an invalid response")

        # Pick highest probability country
        top_country = max(countries, key=lambda x: x["probability"])

        return {
            "gender": g_data["gender"],
            "gender_probability": round(g_data["probability"], 2),
            "sample_size": g_data["count"],
            "age": age,
            "age_group": age_group,
            "country_id": top_country["country_id"],
            "country_probability": round(top_country["probability"], 2),
        }
