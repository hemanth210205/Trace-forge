"""Serves the built console (``frontend/dist``) from the API process, so one command runs the whole product.

- ``/api/...`` is never handled here: API routes, their 404/405 envelopes and the OpenAPI docs are unchanged.
- An existing file inside ``dist`` is served as-is (path traversal is refused); other paths without a file
  extension get ``index.html`` so client-side routes such as ``/projects/<id>/traces`` survive a reload.
- A missing asset (``/assets/x.js``) is a 404, not ``index.html``, so a stale build fails visibly.
"""

from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Match, Route
from starlette.types import Scope

# The console loads only its own bundle and calls only its own origin.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class FrontendRoute(Route):
    """A catch-all GET route that never matches API paths (so their method/404 handling is untouched)."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope["type"] == "http" and _is_api(scope.get("path", "")):
            return Match.NONE, {}
        return super().matches(scope)


def frontend_route(dist: Path) -> FrontendRoute | None:
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        return None

    async def serve(request: Request) -> Response:
        relative = request.path_params.get("path", "")
        candidate = (root / relative).resolve()
        if relative and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if "." in Path(relative).name:
            raise HTTPException(404, "Not found.")
        return FileResponse(index, headers={"Content-Security-Policy": CONTENT_SECURITY_POLICY})

    return FrontendRoute("/{path:path}", serve, methods=["GET", "HEAD"], include_in_schema=False)
