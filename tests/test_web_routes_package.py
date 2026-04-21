"""Router package aggregates all sub-module routers and exposes a single `router`."""

from findajob.web.routes import router as aggregated


def test_router_is_apirouter():
    from fastapi import APIRouter

    assert isinstance(aggregated, APIRouter)


def test_routes_include_healthz_and_materials_and_landing():
    paths = [r.path for r in aggregated.routes]
    assert "/healthz" in paths
    assert "/materials/" in paths  # materials_index after Task 5 rename
    assert "/materials/{fingerprint}" in paths
    assert "/materials/{fingerprint}/{filename}" in paths
