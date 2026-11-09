"""Request/response schemas for the virtual try-on endpoint."""

from pydantic import BaseModel, Field, model_validator


class BodyProfile(BaseModel):
    """Optional body measurements — injected into the prompt to help the model
    reproduce the person's build and the garment's fit. All fields optional."""

    height_cm: float | None = Field(default=None, ge=50, le=250)
    weight_kg: float | None = Field(default=None, ge=10, le=300)
    bust_cm: float | None = Field(default=None, ge=30, le=200)
    waist_cm: float | None = Field(default=None, ge=30, le=200)
    hip_cm: float | None = Field(default=None, ge=30, le=200)
    build: str | None = Field(default=None, max_length=100, description="体型描述，如 偏瘦/匀称/健壮")
    notes: str | None = Field(default=None, max_length=300, description="其他补充，如肩宽、肤色、发型")


class TryOnRequest(BaseModel):
    """Person photos + one garment photo, all base64-encoded (no data: prefix needed).

    `person_images` accepts 1..6 photos of the SAME person (front / side / half-body…).
    More angles = better face & build consistency. `person_image` is kept as a
    backward-compatible alias for a single photo.
    """

    person_image: str | None = Field(default=None, min_length=100, description="主照片（向后兼容）")
    person_images: list[str] = Field(default_factory=list, max_length=6, description="同一人的多张照片")
    garment_image: str = Field(..., min_length=100, description="Garment photo, base64")
    prompt: str | None = Field(default=None, max_length=2000)
    size: str | None = Field(default=None, max_length=32)
    body: BodyProfile | None = Field(default=None, description="身高/体重/三围等，用于还原体型")
    preserve_face: bool = Field(default=True, description="开启 input_fidelity=high 锁定人脸")

    @model_validator(mode="after")
    def at_least_one_photo(self) -> "TryOnRequest":
        if not self.person_images and self.person_image:
            self.person_images = [self.person_image]
        if not self.person_images:
            raise ValueError("至少需要一张本人照片 (person_images)")
        return self


class TryOnResponse(BaseModel):
    image_base64: str
    mime_type: str = "image/png"
    model: str
    mode: str
    person_photo_count: int = 1


class TryOnStatus(BaseModel):
    enabled: bool
    configured: bool
    model: str
    mode: str
    base_url_host: str | None = None
    max_person_photos: int = 6
