"""Aggregates all sub-module routers into a single `router` the app includes."""

from fastapi import APIRouter

from findajob.web.routes import board, healthz, landing, materials

router = APIRouter()
router.include_router(materials.router)
router.include_router(healthz.router)
router.include_router(landing.router)
router.include_router(board.router)
