"""Aggregates all sub-module routers into a single `router` the app includes."""

from fastapi import APIRouter, Depends

from findajob.web.onboarding_guard import require_onboarding_complete
from findajob.web.routes import (
    board,
    board_actions,
    config,
    docs,
    feedback,
    gmail_config,
    healthz,
    ingest,
    landing,
    materials,
    notifications,
    onboarding,
    onboarding_feed_config,
    onboarding_gmail_config,
    speculative,
    stats,
    tools,
)

_guard = [Depends(require_onboarding_complete)]

router = APIRouter()
router.include_router(materials.router, dependencies=_guard)
router.include_router(healthz.router)
# #339 Task 9: landing route is now guarded so a fresh stack — where the
# user lands by typing the bare URL — redirects directly into onboarding
# instead of rendering the marketing-style landing page with no signal
# that onboarding is the next step. The redirect is exitable: once on
# /onboarding/ the user can navigate freely via the top nav to /tools/,
# /docs/, etc. The cached app.state.onboarding_complete flag makes the
# redirect zero-cost on every request after the first post-onboarding hit.
router.include_router(landing.router, dependencies=_guard)
router.include_router(board.router, dependencies=_guard)
router.include_router(board_actions.router, dependencies=_guard)
router.include_router(ingest.router)
router.include_router(speculative.router, dependencies=_guard)
router.include_router(stats.router, dependencies=_guard)
router.include_router(config.router)
router.include_router(gmail_config.router)
router.include_router(tools.router)
router.include_router(onboarding.router)
router.include_router(onboarding_feed_config.router)
router.include_router(onboarding_gmail_config.router)
router.include_router(docs.router)
router.include_router(feedback.router)
router.include_router(notifications.router, dependencies=_guard)
