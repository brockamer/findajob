"""Aggregates all sub-module routers into a single `router` the app includes."""

from fastapi import APIRouter, Depends

from findajob.web.onboarding_guard import require_onboarding_complete
from findajob.web.routes import (
    board,
    board_actions,
    config,
    docs,
    feedback,
    healthz,
    ingest,
    landing,
    materials,
    onboarding,
    speculative,
    stats,
    tools,
)

_guard = [Depends(require_onboarding_complete)]

router = APIRouter()
router.include_router(materials.router, dependencies=_guard)
router.include_router(healthz.router)
router.include_router(landing.router)
router.include_router(board.router, dependencies=_guard)
router.include_router(board_actions.router, dependencies=_guard)
router.include_router(ingest.router)
router.include_router(speculative.router, dependencies=_guard)
router.include_router(stats.router, dependencies=_guard)
router.include_router(config.router)
router.include_router(tools.router)
router.include_router(onboarding.router)
router.include_router(docs.router)
router.include_router(feedback.router)
