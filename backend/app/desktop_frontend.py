from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


PRODUCTION_CSP = (
    "default-src 'self'; connect-src 'self'; script-src 'self'; "
    "media-src 'self' blob:; img-src 'self' data: blob: https://p3-aio.ecombdimg.com https://p9-aio.ecombdimg.com; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'"
)


class WorkbenchFiles(StaticFiles):
    """Serve the Vue history router without turning missing API/assets into HTML."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            route = PurePosixPath(path)
            if (exc.status_code != 404 or scope['method'] not in {'GET', 'HEAD'}
                    or route.suffix or '..' in route.parts
                    or (route.parts and route.parts[0] in {'api', 'assets', 'health'})):
                raise
            return await super().get_response('index.html', scope)
