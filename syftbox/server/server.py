import contextlib
import os
import platform
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from jinja2 import Template
from loguru import logger
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.trace import Span
from typing_extensions import Any, AsyncGenerator, Optional, Union

from syftbox import __version__
from syftbox.lib.http import (
    HEADER_GEO_COUNTRY,
    HEADER_OS_ARCH,
    HEADER_OS_NAME,
    HEADER_OS_VERSION,
    HEADER_SYFTBOX_PYTHON,
    HEADER_SYFTBOX_USER,
    HEADER_SYFTBOX_VERSION,
)
from syftbox.lib.lib import (
    get_datasites,
)
from syftbox.server.analytics import log_analytics_event
from syftbox.server.logger import setup_logger
from syftbox.server.middleware import LoguruMiddleware, RequestSizeLimitMiddleware, VersionCheckMiddleware
from syftbox.server.settings import ServerSettings, get_server_settings
from syftbox.server.telemetry import (
    OTEL_ATTR_CLIENT_OS_ARCH,
    OTEL_ATTR_CLIENT_OS_NAME,
    OTEL_ATTR_CLIENT_OS_VER,
    OTEL_ATTR_CLIENT_PYTHON,
    OTEL_ATTR_CLIENT_USER,
    OTEL_ATTR_CLIENT_USER_LOC,
    OTEL_ATTR_CLIENT_VERSION,
    setup_otel_exporter,
)
from syftbox.server.users.auth import get_current_user

from .api.v1.sync_router import router as sync_router
from .emails.router import router as emails_router
from .users.router import router as users_router

current_dir = Path(__file__).parent


def create_folders(folders: list[Path]) -> None:
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)


def server_request_hook(span: Span, scope: dict[str, Any]) -> None:
    if not span.is_recording():
        return
    # headers k/v pairs are bytes
    headers: dict[bytes, bytes] = dict(scope.get("headers", {}))
    span.set_attribute(OTEL_ATTR_CLIENT_VERSION, headers.get(HEADER_SYFTBOX_VERSION.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_PYTHON, headers.get(HEADER_SYFTBOX_PYTHON.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_USER, headers.get(HEADER_SYFTBOX_USER.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_OS_NAME, headers.get(HEADER_OS_NAME.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_OS_VER, headers.get(HEADER_OS_VERSION.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_OS_ARCH, headers.get(HEADER_OS_ARCH.encode(), ""))
    span.set_attribute(OTEL_ATTR_CLIENT_USER_LOC, headers.get(HEADER_GEO_COUNTRY.encode(), ""))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI, settings: Optional[ServerSettings] = None) -> AsyncGenerator:
    # Startup
    settings = settings or ServerSettings()

    setup_logger(logs_folder=settings.logs_folder)

    logger.info(f"Starting SyftBox Server {__version__}. Python {platform.python_version()}")
    logger.info(settings)

    if settings.otel_enabled:
        logger.info("OTel Exporter is ENABLED")
        setup_otel_exporter(settings.env.value)
    else:
        logger.info("OTel Exporter is DISABLED")

    yield {
        "server_settings": settings,
    }

    logger.info("Shutting down server")


app = FastAPI(lifespan=lifespan)
app.include_router(emails_router)
app.include_router(sync_router)
app.include_router(users_router)
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
app.add_middleware(LoguruMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(VersionCheckMiddleware)

FastAPIInstrumentor.instrument_app(app, server_request_hook=server_request_hook)
SQLite3Instrumentor().instrument()

# Define the ASCII art
ascii_art = rf"""
 ____         __ _   ____
/ ___| _   _ / _| |_| __ )  _____  __
\___ \| | | | |_| __|  _ \ / _ \ \/ /
 ___) | |_| |  _| |_| |_) | (_) >  <
|____/ \__, |_|  \__|____/ \___/_/\_\
       |___/        {__version__:>17}


# Install Syftbox (MacOS and Linux)
curl -LsSf [[SERVER_URL]]/install.sh | sh

# Run the client
syftbox client
"""


@app.get("/", response_class=PlainTextResponse)
async def get_ascii_art(request: Request) -> str:
    return ascii_art.replace("[[SERVER_URL]]", str(request.url).rstrip("/"))


def get_file_list(directory: Union[str, Path] = ".") -> list[dict[str, Any]]:
    # TODO rewrite with pathlib
    directory = str(directory)

    file_list = []
    for item in os.listdir(directory):
        item_path = os.path.join(directory, item)
        is_dir = os.path.isdir(item_path)
        size = os.path.getsize(item_path) if not is_dir else "-"
        mod_time = datetime.fromtimestamp(os.path.getmtime(item_path)).strftime("%Y-%m-%d %H:%M:%S")

        file_list.append({"name": item, "is_dir": is_dir, "size": size, "mod_time": mod_time})

    return sorted(file_list, key=lambda x: (not x["is_dir"], x["name"].lower()))


@app.get("/datasites", response_class=HTMLResponse)
async def list_datasites(
    request: Request, server_settings: ServerSettings = Depends(get_server_settings)
) -> HTMLResponse:
    files = get_file_list(server_settings.snapshot_folder)
    template_path = current_dir / "templates" / "datasites.html"
    html = ""
    with open(template_path) as f:
        html = f.read()
    template = Template(html)

    html_content = template.render(
        {
            "request": request,
            "files": files,
            "current_path": "/",
        }
    )
    return html_content


@app.get("/datasites/{path:path}", response_class=HTMLResponse)
async def browse_datasite(
    request: Request,
    path: str,
    server_settings: ServerSettings = Depends(get_server_settings),
) -> HTMLResponse:
    if path == "":  # Check if path is empty (meaning "/datasites/")
        return RedirectResponse(url="/datasites")

    snapshot_folder = str(server_settings.snapshot_folder)
    datasite_part = path.split("/")[0]
    datasites = get_datasites(snapshot_folder)
    if datasite_part in datasites:
        slug = path[len(datasite_part) :]
        if slug == "":
            slug = "/"
        datasite_path = os.path.join(snapshot_folder, datasite_part)
        datasite_public = datasite_path + "/public"
        if not os.path.exists(datasite_public):
            return "No public datasite"

        slug_path = os.path.abspath(datasite_public + slug)
        if os.path.exists(slug_path) and os.path.isfile(slug_path):
            if slug_path.endswith(".html") or slug_path.endswith(".htm"):
                return FileResponse(slug_path)
            elif slug_path.endswith(".md"):
                with open(slug_path, "r") as file:
                    content = file.read()
                return PlainTextResponse(content)
            elif slug_path.endswith(".json") or slug_path.endswith(".jsonl"):
                return FileResponse(slug_path, media_type="application/json")
            elif slug_path.endswith(".yaml") or slug_path.endswith(".yml"):
                return FileResponse(slug_path, media_type="application/x-yaml")
            elif slug_path.endswith(".log") or slug_path.endswith(".txt"):
                return FileResponse(slug_path, media_type="text/plain")
            elif slug_path.endswith(".py"):
                return FileResponse(slug_path, media_type="text/plain")
            else:
                return FileResponse(slug_path, media_type="application/octet-stream")

        # show directory
        if not path.endswith("/") and os.path.exists(path + "/") and os.path.isdir(path + "/"):
            return RedirectResponse(url=f"{path}/")

        index_file = os.path.abspath(slug_path + "/" + "index.html")
        if os.path.exists(index_file):
            with open(index_file, "r") as file:
                html_content = file.read()
            return HTMLResponse(content=html_content, status_code=200)

        if os.path.isdir(slug_path):
            files = get_file_list(slug_path)
            template_path = current_dir / "templates" / "folder.html"
            html = ""
            with open(template_path) as f:
                html = f.read()
            template = Template(html)
            html_content = template.render(
                {
                    "datasite": datasite_part,
                    "request": request,
                    "files": files,
                    "current_path": path,
                }
            )
            return html_content
        else:
            # return 404
            message_404 = f"No file or directory found at /datasites/{datasite_part}{slug}"
            return HTMLResponse(content=message_404, status_code=404)

    return f"No Datasite {datasite_part} exists"


@app.post("/register")
async def register(
    request: Request,
    server_settings: ServerSettings = Depends(get_server_settings),
) -> JSONResponse:
    data = await request.json()
    email = data["email"]

    # create datasite snapshot folder
    datasite_folder = Path(server_settings.snapshot_folder) / email
    os.makedirs(datasite_folder, exist_ok=True)

    logger.info(f"> {email} registering, snapshot folder: {datasite_folder}")
    log_analytics_event("/register", email)

    return JSONResponse({"status": "success", "token": "0"}, status_code=200)


@app.post("/log_event")
async def log_event(
    request: Request,
    email: str = Depends(get_current_user),
) -> JSONResponse:
    data = await request.json()
    log_analytics_event("/log_event", email, **data)
    return JSONResponse({"status": "success"}, status_code=200)


@app.get("/install.sh")
async def install() -> FileResponse:
    install_script = current_dir / "templates" / "install.sh"
    return FileResponse(install_script, media_type="text/plain")


@app.get("/icon.png")
async def icon() -> FileResponse:
    icon_path = current_dir / "assets" / "icon.png"
    return FileResponse(icon_path, media_type="image/png")


@app.get("/info")
async def info() -> dict:
    return {
        "version": __version__,
    }
