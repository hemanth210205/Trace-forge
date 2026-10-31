import logging
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api.deps import DbSession

router = APIRouter(tags=["health"])
logger = logging.getLogger("traceforge.health")


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut, "description": "Database unavailable"}})
def health(db: DbSession) -> HealthOut | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("health check database failure: %s", type(exc).__name__)
        body = HealthOut(status="degraded", database="unavailable", version=__version__)
        return JSONResponse(body.model_dump(), status_code=503)
    return HealthOut(status="ok", database="ok", version=__version__)
