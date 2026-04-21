from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Any, List, Optional

class ProfileCreate(BaseModel):
    name: Any  # Accept anything, validate below for correct 400 vs 422 split
 
    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Invalid type")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Missing or empty name")
        return cleaned

class ProfileResponse(BaseModel):
    id: str
    name: str
    gender: str
    gender_probability: float
    sample_size: int
    age: int
    age_group: str
    country_id: str
    country_probability: float
    created_at: datetime

    @field_validator("gender_probability", "country_probability")
    def round_floats(cls, value: Any) -> float:
        return round(float(value), 2)

    model_config = {
        "from_attributes": True,
        "json_encoders": {
            datetime: lambda v: v.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }

class ProfileListItem(BaseModel):
    id: str
    name: str
    gender: str
    age: int
    age_group: str
    country_id: str

    model_config = {
        "from_attributes": True,
    }

class SuccessResponse(BaseModel):
    status: str = "success"
    message: Optional[str] = Field(default=None, exclude_none=True)
    data: ProfileResponse

    model_config = {
        "from_attributes": True,
    }

class ListResponse(BaseModel):
    status: str = "success"
    count: int
    data: List[ProfileListItem]

    model_config = {
        "from_attributes": True,
    }
