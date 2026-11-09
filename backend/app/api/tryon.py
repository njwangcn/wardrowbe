"""Virtual try-on endpoints.

POST /tryon/generate  — person photo + garment photo -> rendered "me wearing it" image
GET  /tryon/status    — whether the feature is enabled/configured (never exposes the key)

The generated image is returned as base64 in the JSON body (no new DB tables,
no storage writes, no schema migration needed), so it plugs into any existing
deployment with just a few env vars.
"""

import base64
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.models import User
from app.schemas.tryon import TryOnRequest, TryOnResponse, TryOnStatus
from app.services.tryon_service import (
    TryOnError,
    TryOnNotConfiguredError,
    TryOnService,
)
from app.utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tryon", tags=["Try-On"])


def _clean_b64(value: str) -> str:
    """Accept both raw base64 and a data: URI."""
    v = value.strip()
    if "," in v and v.startswith("data:"):
        v = v.split(",", 1)[1]
    return "".join(v.split())


@router.get("/status", response_model=TryOnStatus)
async def tryon_status() -> TryOnStatus:
    service = TryOnService()
    s = get_settings()
    return TryOnStatus(
        enabled=s.tryon_enabled,
        configured=service.is_configured(),
        model=s.tryon_model if service.is_configured() else "",
        mode=s.tryon_mode,
        base_url_host=service.status_host(),
    )


@router.post("/generate", response_model=TryOnResponse)
async def generate_tryon(
    request: TryOnRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> TryOnResponse:
    service = TryOnService()
    if not service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="虚拟试穿未配置，请在 .env 中设置 TRYON_* 后重启后端。",
        )

    person = _clean_b64(request.person_image)
    garment = _clean_b64(request.garment_image)

    # Allow callers to override the prompt only when explicitly permitted.
    prompt = request.prompt if get_settings().tryon_allow_env_prompt else None

    try:
        image_bytes, mime = await service.generate(person, garment, prompt, request.size)
    except TryOnNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from None
    except TryOnError as exc:
        logger.warning("tryon failed for user %s: %s", current_user.id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None

    return TryOnResponse(
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
        mime_type=mime,
        model=get_settings().tryon_model,
        mode=get_settings().tryon_mode,
    )
