"""FastAPI 앱."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import STATIC_DIR, VERIFIED_CAPCUT_VERSION, pycapcut_version
from .routers import calibration, editing, files, publishing, sessions, setup
from .services.jobs import MANAGER
from .services.logging_util import get_logger, setup_logging

setup_logging()
log = get_logger(__name__)

app = FastAPI(title="캡컷 자동 편집기", version="1.0.0", docs_url="/api/docs", redoc_url=None)

app.include_router(setup.router)
app.include_router(files.router)
app.include_router(sessions.router)
app.include_router(calibration.router)
app.include_router(editing.router)
app.include_router(publishing.router)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> Dict[str, Any]:
    MANAGER.prune()
    return {
        "ok": True,
        "pycapcut": pycapcut_version(),
        "verified_capcut": VERIFIED_CAPCUT_VERSION,
    }


@app.exception_handler(RequestValidationError)
def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "message": "요청 값이 올바르지 않습니다. 화면을 새로고침한 뒤 다시 시도해 주세요.",
                "kind": "validation",
                "fields": [".".join(str(p) for p in err.get("loc", [])) for err in exc.errors()],
            }
        },
    )


@app.exception_handler(Exception)
def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("처리되지 않은 오류: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "message": f"예기치 못한 오류가 났습니다: {exc}. 하단 로그 패널에서 자세한 내용을 확인해 주세요.",
                "kind": "unhandled",
            }
        },
    )
