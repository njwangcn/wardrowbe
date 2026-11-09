"""Request/response schemas for the virtual try-on endpoint."""

from pydantic import BaseModel, Field


class TryOnRequest(BaseModel):
    """Two images, both base64-encoded (no data: prefix needed)."""

    person_image: str = Field(..., min_length=100, description="Your own front-facing photo, base64")
    garment_image: str = Field(..., min_length=100, description="Garment photo, base64")
    prompt: str | None = Field(default=None, max_length=2000)
    size: str | None = Field(default=None, max_length=32)


class TryOnResponse(BaseModel):
    image_base64: str
    mime_type: str = "image/png"
    model: str
    mode: str


class TryOnStatus(BaseModel):
    enabled: bool
    configured: bool
    model: str
    mode: str
    base_url_host: str | None = None
