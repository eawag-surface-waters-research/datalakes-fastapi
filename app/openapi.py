"""
Serves two OpenAPI specs:
- The public spec holds only the endpoints marked with `tags=[PUBLIC_TAG]`. It's meant for third-party users and is served at FastAPI's default `/openapi.json`, `/docs` and `/redoc`.
- The full spec holds every endpoint. It's meant for administrators and integrators and is served at `/admin/openapi.json`, `/admin/docs` and `/admin/redoc`.

The `PUBLIC_TAG` is removed from both specs such that the public spec doesn't show it
"""

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

PUBLIC_TAG = "Public"
ADMIN_OPENAPI_URL = "/admin/openapi.json"
ADMIN_DOCS_URL = "/admin/docs"
ADMIN_REDOC_URL = "/admin/redoc"


def public_routes(app: FastAPI) -> list[RouteContext]:
  """The API routes marked with `PUBLIC_TAG`."""
  return [ctx for ctx in iter_route_contexts(app.routes) if isinstance(ctx.original_route, APIRoute) and PUBLIC_TAG in (ctx.tags or [])]


def build_openapi(app: FastAPI, routes: Sequence[Any]) -> dict[str, Any]:
  schema = get_openapi(
    title=app.title,
    version=app.version,
    openapi_version=app.openapi_version,
    summary=app.summary,
    description=app.description,
    terms_of_service=app.terms_of_service,
    contact=app.contact,
    license_info=app.license_info,
    routes=routes,
    tags=app.openapi_tags,
    servers=app.servers,
    separate_input_output_schemas=app.separate_input_output_schemas,
    external_docs=app.openapi_external_docs,
  )

  # remove the `PUBLIC_TAG` from the tags of every operation
  for path_item in schema.get("paths", {}).values():
    for operation in path_item.values():
      if "tags" in operation:
        operation["tags"] = [tag for tag in operation["tags"] if tag != PUBLIC_TAG]
        if not operation["tags"]:
          del operation["tags"]

  return schema


def setup_openapi(app: FastAPI) -> None:
  def public_openapi() -> dict[str, Any]:
    if app.openapi_schema is None:  # assign it lazily and only once
      app.openapi_schema = build_openapi(app, public_routes(app))
    return app.openapi_schema

  app.openapi = public_openapi

  full_schema: dict[str, Any] = {}

  @app.get(ADMIN_OPENAPI_URL, include_in_schema=False)
  def admin_openapi() -> JSONResponse:
    if not full_schema:  # assign it lazily and only once
      full_schema.update(build_openapi(app, app.routes))
    return JSONResponse(full_schema)

  @app.get(ADMIN_DOCS_URL, include_in_schema=False)
  def admin_docs(request: Request) -> HTMLResponse:
    root_path = request.scope.get("root_path", "").rstrip("/")
    return get_swagger_ui_html(openapi_url=root_path + ADMIN_OPENAPI_URL, title=f"{app.title} (admin) - Swagger UI")

  @app.get(ADMIN_REDOC_URL, include_in_schema=False)
  def admin_redoc(request: Request) -> HTMLResponse:
    root_path = request.scope.get("root_path", "").rstrip("/")
    return get_redoc_html(openapi_url=root_path + ADMIN_OPENAPI_URL, title=f"{app.title} (admin) - ReDoc")
