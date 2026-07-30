import asyncio
import hmac
import importlib.util
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .api.routes.auth import router as auth_router
from .api.routes.capabilities import router as capabilities_router
from .api.routes.chat import router as chat_router
from .api.routes.config import router as config_router
from .api.routes.consciousness import router as consciousness_router
from .api.routes.core import router as core_router
from .api.routes.corefs import router as corefs_router
from .api.routes.corefs_security import router as corefs_security_router
from .api.routes.db import router as db_router
from .api.routes.diary import router as diary_router
from .api.routes.documents import router as documents_router
from .api.routes.eval import router as eval_router
from .api.routes.forgetting import router as forgetting_router
from .api.routes.graph import router as graph_router
from .api.routes.health import router as health_router
from .api.routes.images import router as images_router
from .api.routes.knowledge import router as knowledge_router
from .api.routes.memory import router as memory_router
from .api.routes.presence import router as presence_router
from .api.routes.soul import router as soul_router
from .api.routes.tasks import router as tasks_router
from .api.routes.telegram import router as telegram_router
from .api.routes.threads import router as threads_router
from .api.routes.users import router as users_router
from .api.routes.vault import router as vault_router
from .api.routes.ws import router as ws_router
from .config import (
    default_runtime_app_data_root,
    load_persisted_runtime_settings,
    settings,
)
from .db.pg_lifecycle import EmbeddedPG
from .db.runtime import (
    dispose_runtime_engine,
    ensure_pgvector,
    ensure_runtime_database_binding,
    ensure_runtime_tables,
    get_runtime_session_factory,
    init_runtime_engine,
)
from .db.user_store import ensure_per_user_databases_ready
from .services.core import acquire_core_lock, ensure_core_manifest, is_provisioned
from .services.corefs.instance_registry import (
    RuntimeInstanceBinding,
    RuntimeInstanceRegistry,
)
from .services.corefs.legacy_runtime import relocate_legacy_runtime
from .services.health.event_logger import emit as health_emit


def get_cors_origins() -> list[str]:
    origins = [
        "tauri://localhost",
        "https://tauri.localhost",
    ]
    if settings.app_env == "development":
        origins.extend(
            [
                "http://localhost:1420",
                "http://localhost:5173",
                "http://tauri.localhost",
            ]
        )
    return origins


# Paths exempt from sidecar-nonce validation.
_NONCE_EXEMPT_PATHS = frozenset({"/health", "/api/health", "/api/health/detailed",
                                "/api/health/check", "/api/health/logs", "/api/health/logs/summary"})
_NONCE_EXEMPT_PREFIXES = ("/api/health/",)
logger = logging.getLogger(__name__)
_active_runtime_registry: RuntimeInstanceRegistry | None = None
_active_runtime_binding: RuntimeInstanceBinding | None = None
_active_runtime_default_health_log = False


def _claim_runtime_instance(
    *,
    runtime_url: str | None = None,
) -> RuntimeInstanceBinding:
    global _active_runtime_binding, _active_runtime_default_health_log
    global _active_runtime_registry

    if _active_runtime_binding is not None:
        if runtime_url:
            assert _active_runtime_registry is not None
            _active_runtime_registry.verify_runtime_url_claim(
                _active_runtime_binding,
                runtime_url,
            )
        return _active_runtime_binding

    app_data_root = (
        Path(settings.runtime_app_data_dir)
        if settings.runtime_app_data_dir
        else default_runtime_app_data_root()
    ).expanduser().resolve()
    if app_data_root.is_relative_to(settings.data_dir.expanduser().resolve()):
        raise RuntimeError(
            "ANIMA_RUNTIME_APP_DATA_DIR must not resolve inside the portable Core"
        )
    registry = RuntimeInstanceRegistry(app_data_root)
    binding = registry.resolve(settings.data_dir, runtime_url=runtime_url)
    try:
        relocate_legacy_runtime(
            settings.data_dir,
            binding,
            postgres_running=False,
        )
    except BaseException:
        registry.release(binding)
        raise
    settings.runtime_instance_data_dir = str(binding.instance_root)
    if settings.health_log_dir:
        configured_health_logs = Path(settings.health_log_dir).expanduser().resolve()
        if configured_health_logs.is_relative_to(settings.data_dir.resolve()):
            registry.release(binding)
            settings.runtime_instance_data_dir = ""
            raise RuntimeError(
                "ANIMA_HEALTH_LOG_DIR must not resolve inside the portable Core"
            )
    else:
        settings.health_log_dir = str(binding.health_log_dir)
        _active_runtime_default_health_log = True
    _active_runtime_registry = registry
    _active_runtime_binding = binding
    return binding


def _release_runtime_instance_claim() -> None:
    global _active_runtime_binding, _active_runtime_default_health_log
    global _active_runtime_registry

    if _active_runtime_binding is not None and _active_runtime_registry is not None:
        _active_runtime_registry.release(_active_runtime_binding)
    if _active_runtime_default_health_log:
        settings.health_log_dir = ""
    settings.runtime_instance_data_dir = ""
    _active_runtime_binding = None
    _active_runtime_registry = None
    _active_runtime_default_health_log = False


def _start_embedded_pg() -> EmbeddedPG | None:
    """Start embedded PostgreSQL unless an explicit runtime URL is configured."""
    if settings.runtime_database_url:
        return None
    binding = _claim_runtime_instance()
    if importlib.util.find_spec("pgserver") is None:
        logger.warning(
            "pgserver is not installed; skipping embedded runtime PostgreSQL startup."
        )
        return None

    if settings.runtime_pg_data_dir:
        configured_pg_data = Path(settings.runtime_pg_data_dir).expanduser().resolve()
        if configured_pg_data != binding.active_pg_data_dir:
            raise RuntimeError(
                "ANIMA_RUNTIME_PG_DATA_DIR must match the claimed machine-local "
                "Core instance path; configure ANIMA_RUNTIME_APP_DATA_DIR instead"
            )
    pg_data_dir = binding.active_pg_data_dir

    pg = EmbeddedPG(data_dir=pg_data_dir)
    pg.start()
    return pg


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    from .services.sessions import unlock_session_store

    unlock_session_store.start()
    embedded_pg: EmbeddedPG | None = None
    runtime_binding: RuntimeInstanceBinding | None = None
    sweep_tasks: list[asyncio.Task[None]] = []

    try:
        runtime_binding = _claim_runtime_instance(
            runtime_url=settings.runtime_database_url or None
        )
        embedded_pg = _start_embedded_pg()
        load_persisted_runtime_settings()
        runtime_url = (
            embedded_pg.database_url
            if embedded_pg is not None
            else settings.runtime_database_url
        )
        if runtime_url:
            init_runtime_engine(
                runtime_url,
                echo=settings.database_echo,
                pool_size=settings.runtime_pool_size,
                max_overflow=settings.runtime_pool_max_overflow,
            )
            ensure_runtime_database_binding(
                core_id=runtime_binding.core_id,
                local_instance_id=runtime_binding.local_instance_id,
            )
            ensure_pgvector()
            ensure_runtime_tables()

            try:
                from .services.agent.inner_life.catchup import apply_offline_catchup

                catchup_results = await asyncio.to_thread(
                    apply_offline_catchup, get_runtime_session_factory()
                )
                if catchup_results:
                    logger.info(
                        "Offline presence catch-up applied for %d user(s)",
                        len(catchup_results),
                    )
            except Exception:
                logger.warning("Offline presence catch-up failed", exc_info=True)
    except BaseException:
        try:
            await unlock_session_store.shutdown()
        finally:
            try:
                dispose_runtime_engine()
            finally:
                if embedded_pg is not None:
                    embedded_pg.stop()
                _release_runtime_instance_claim()
        raise

    from .services.health.event_logger import (
        EventLogger,
        StructuredLogHandler,
        get_event_logger,
    )

    health_handler: StructuredLogHandler | None = None
    health_logger: EventLogger | None = None

    try:
        async def _periodic_inactivity_sweep() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    from .services.agent.eager_consolidation import inactivity_sweep

                    await inactivity_sweep()
                except Exception:
                    logger.warning("Inactivity sweep error", exc_info=True)

        async def _periodic_prune_sweep() -> None:
            while True:
                await asyncio.sleep(6 * 3600)
                try:
                    from .services.agent.eager_consolidation import (
                        prune_expired_messages,
                        prune_expired_transcripts,
                        prune_old_background_task_runs,
                    )

                    await prune_expired_messages()
                    await prune_expired_transcripts()
                    await prune_old_background_task_runs()
                except Exception:
                    logger.warning("Prune sweep error", exc_info=True)

        async def _periodic_presence_tick() -> None:
            while True:
                await asyncio.sleep(settings.presence_tick_interval_seconds)
                try:
                    from .db.session import (
                        SessionLocal,
                        get_user_session_factory,
                        is_sqlite_mode,
                    )
                    from .services.agent.inner_life.presence import run_presence_tick

                    def _soul_db_factory_for(user_id: int):
                        # The soul store is physically per-user in the desktop
                        # SQLite deployment; resolve each user's own factory so
                        # the initiative tick reads/writes that user's migrated
                        # database, never the shared (unmigrated) SessionLocal.
                        if is_sqlite_mode():
                            return get_user_session_factory(user_id)
                        return SessionLocal

                    await asyncio.to_thread(
                        run_presence_tick,
                        get_runtime_session_factory(),
                        soul_db_factory_for=_soul_db_factory_for,
                    )
                except Exception:
                    logger.warning("Presence tick error", exc_info=True)

        sweep_tasks.append(asyncio.create_task(_periodic_inactivity_sweep()))
        sweep_tasks.append(asyncio.create_task(_periodic_prune_sweep()))
        sweep_tasks.append(asyncio.create_task(_periodic_presence_tick()))

        from .services.agent.fastembed_backend import warm_up_retrieval_models

        # Load the bundled retrieval models (fastembed embeddings, and the
        # local reranker when enabled) off the chat/request path so the
        # first query after startup doesn't pay for the load. Cancelled on
        # shutdown like the sweep tasks above; never raises.
        sweep_tasks.append(
            asyncio.create_task(asyncio.to_thread(warm_up_retrieval_models))
        )

        # Install structured health event logger
        health_logger = get_event_logger()
        health_logger.cleanup_old_logs()
        health_handler = StructuredLogHandler(health_logger)
        health_handler.setLevel(logging.WARNING)
        logging.getLogger("anima_server").addHandler(health_handler)

        yield
    finally:
        from .services.agent.consolidation import drain_background_memory_tasks
        from .services.agent.reflection import cancel_pending_reflection
        try:
            # Flush pending Soul Writer candidates for all active users unless
            # background memory work was explicitly disabled for this process.
            if settings.agent_background_memory_enabled:
                try:
                    from sqlalchemy import select as _sel

                    from .models.runtime import RuntimeThread
                    from .services.agent.soul_writer import run_soul_writer

                    rt_factory = get_runtime_session_factory()
                    with rt_factory() as rt_db:
                        active_user_ids = list(rt_db.scalars(
                            _sel(RuntimeThread.user_id).where(
                                RuntimeThread.status == "active")
                        ).all())
                    for uid in set(active_user_ids):
                        try:
                            await run_soul_writer(uid)
                        except Exception:
                            logger.debug(
                                "Shutdown Soul Writer failed for user %s", uid)
                except Exception:
                    logger.debug(
                        "Shutdown Soul Writer sweep failed", exc_info=True)

            for task in sweep_tasks:
                task.cancel()
            await cancel_pending_reflection()
            await drain_background_memory_tasks()
        finally:
            try:
                await unlock_session_store.shutdown()
            finally:
                if health_handler is not None:
                    logging.getLogger("anima_server").removeHandler(health_handler)
                if health_logger is not None:
                    health_logger.flush()
                dispose_runtime_engine()
                if embedded_pg is not None:
                    embedded_pg.stop()
                _release_runtime_instance_claim()


class SidecarNonceMiddleware(BaseHTTPMiddleware):
    """Reject requests that do not carry the expected sidecar nonce.

    When ``ANIMA_SIDECAR_NONCE`` is set, every request (except the health
    endpoints) must include the header ``x-anima-nonce`` with the matching
    value.  This binds the desktop client to the exact sidecar process it
    launched, preventing rogue localhost processes from being trusted.

    The nonce is **not** exposed over HTTP — it is delivered to the
    desktop frontend via a trusted Tauri IPC command so that other
    local processes cannot obtain it.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        if not settings.sidecar_nonce and settings.app_env != "development":
            logger.warning(
                "Sidecar nonce is not configured in non-development environment")

    # type: ignore[override]
    async def dispatch(self, request: Request, call_next):
        nonce = settings.sidecar_nonce
        path = request.url.path
        if nonce and path not in _NONCE_EXEMPT_PATHS and not path.startswith(_NONCE_EXEMPT_PREFIXES):
            header_value = (request.headers.get("x-anima-nonce") or "").strip()
            if not hmac.compare_digest(header_value, nonce):
                return JSONResponse(
                    status_code=403,
                    content={"error": "Invalid or missing sidecar nonce."},
                )
        response = await call_next(request)
        return response


def create_app() -> FastAPI:
    if (
        settings.core_require_encryption
        and not settings.sidecar_nonce
        and settings.app_env != "development"
    ):
        raise RuntimeError(
            "Sidecar nonce must be configured when encryption is required.")
    if not settings.sidecar_nonce and settings.app_env != "development":
        logger.warning(
            "Sidecar nonce is not configured in non-development environment")
    if not acquire_core_lock():
        raise RuntimeError("Core is already open in another process")
    ensure_core_manifest()
    ensure_per_user_databases_ready()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # Sidecar nonce enforcement — added before CORSMiddleware so that
    # Starlette's reverse-add ordering makes CORS the outermost layer,
    # allowing OPTIONS preflights to succeed before the nonce check runs.
    app.add_middleware(SidecarNonceMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
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
        health_emit("http", "error_response", "warn", data={
            "status_code": exc.status_code,
            "detail": str(exc.detail)[:200],
        })
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = []
        for error in exc.errors():
            normalized = dict(error)
            ctx = normalized.get("ctx")
            if isinstance(ctx, dict):
                normalized["ctx"] = {
                    key: str(value) if isinstance(value, Exception) else value
                    for key, value in ctx.items()
                }
            details.append(normalized)
        return JSONResponse(
            status_code=422,
            content={
                "error": "Invalid request",
                "details": jsonable_encoder(details),
            },
        )

    @app.get("/health", tags=["system"])
    @app.get("/api/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "server",
            "environment": settings.app_env,
            "provisioned": is_provisioned(),
        }

    app.include_router(auth_router)
    app.include_router(capabilities_router)
    app.include_router(chat_router)
    app.include_router(config_router)
    app.include_router(consciousness_router)
    app.include_router(core_router)
    app.include_router(corefs_router)
    app.include_router(corefs_security_router)
    app.include_router(db_router)
    app.include_router(diary_router)
    app.include_router(documents_router)
    app.include_router(eval_router)
    app.include_router(forgetting_router)
    app.include_router(graph_router)
    app.include_router(health_router)
    app.include_router(images_router)
    app.include_router(knowledge_router)
    app.include_router(memory_router)
    app.include_router(presence_router)
    app.include_router(soul_router)
    app.include_router(tasks_router)
    app.include_router(telegram_router)
    app.include_router(threads_router)
    app.include_router(users_router)
    app.include_router(vault_router)
    app.include_router(ws_router)

    return app


app = create_app()
