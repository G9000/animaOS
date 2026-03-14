from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.routes.auth import router as auth_router
from .api.routes.chat import router as chat_router
from .api.routes.users import router as users_router
from .api.routes.vault import router as vault_router
from .config import settings
from .services.core import ensure_core_manifest

CORS_ORIGINS = [
    "http://localhost:1420",
    "http://localhost:5173",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
]


def create_app() -> FastAPI:
    ensure_core_manifest()
    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if isinstance(exc.detail, str):
            content: dict[str, object] = {"error": exc.detail}
        else:
            content = {"error": "Request failed", "details": exc.detail}
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "Invalid request",
                "details": exc.errors(),
            },
        )

    @app.get("/health", tags=["system"])
    @app.get("/api/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "server",
            "environment": settings.app_env,
        }

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(users_router)
    app.include_router(vault_router)

    return app


app = create_app()
