from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


DIST = Path(__file__).parent / "dist"

app = FastAPI(title="spgame UI host")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "has_dist": (DIST / "index.html").exists()}


@app.get("/config.json")
async def runtime_config():
    """Runtime-injected so we don't rebuild React per environment."""
    return JSONResponse(
        {
            "apiBase": os.environ.get("SERVER_URL", ""),
        },
        headers={"Cache-Control": "no-store"},
    )


# Serve static assets from dist/. We mount last so /config.json and /healthz win.
if DIST.exists():
    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Try exact file match first (for /assets/foo.js etc.)
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # SPA fallback to index.html for client-side routing
        index = DIST / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")
        return Response(
            "<h1>spgame UI not yet built</h1>"
            "<p>Run <code>npm install && npm run build</code> in the <code>ui/</code> "
            "directory; it writes to <code>ui-host/dist</code>.</p>",
            media_type="text/html",
            status_code=503,
        )
else:
    @app.get("/")
    async def missing_dist():
        return JSONResponse(
            {"error": "dist/ not present. Build the UI first: `npm run build` in ui/."},
            status_code=503,
        )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
