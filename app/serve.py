"""Run the owner app and the public booking app in one process (a single Python on the Pi).

    python -m app.serve

Environment:
  TA_HOST / TA_PORT                 owner UI + API          (default 0.0.0.0:8000, LAN)
  TA_PUBLIC_HOST / TA_PUBLIC_PORT   public booking page     (default 127.0.0.1:8001; expose via Tailscale Funnel)
  TA_PUBLIC=0                       don't start the public server
"""
from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .main import create_app
from .public_app import create_public_app


async def _main() -> None:
    owner = uvicorn.Server(uvicorn.Config(
        create_app(), host=os.environ.get("TA_HOST", "0.0.0.0"), port=int(os.environ.get("TA_PORT", "8000")),
        log_level="info", proxy_headers=False))
    servers = [owner.serve()]
    if os.environ.get("TA_PUBLIC", "1") == "1":
        public = uvicorn.Server(uvicorn.Config(
            create_public_app(), host=os.environ.get("TA_PUBLIC_HOST", "127.0.0.1"),
            port=int(os.environ.get("TA_PUBLIC_PORT", "8001")), log_level="warning", proxy_headers=False,
            lifespan="off"))
        servers.append(public.serve())
    await asyncio.gather(*servers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(_main())
