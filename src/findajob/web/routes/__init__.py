"""Aggregates all sub-module routers into a single `router` the app includes."""

from fastapi import APIRouter

from findajob.web.routes import board, board_actions, healthz, ingest, landing, materials, stats

router = APIRouter()
router.include_router(materials.router)
router.include_router(healthz.router)
router.include_router(landing.router)
router.include_router(board.router)
router.include_router(board_actions.router)
router.include_router(ingest.router)
router.include_router(stats.router)
