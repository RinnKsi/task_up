from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as fastapi_http_exception_handler
from fastapi.exceptions import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes.api import router as api_router
from app.api.routes.auth import router as auth_router
from app.api.routes.web import router as web_router
from app.core.config import settings
from app.core.paths import STATIC_ROOT
from app.db.auto_migrate import ensure_schema
from app.scheduler.jobs import start_scheduler


app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")
app.add_middleware(
    SessionMiddleware,
    secret_key="smart-tracker-demo-secret",
    max_age=60 * 60 * 24 * 30,  # 30 days; remember-me логика внутри get_current_user
    same_site="lax",
)

app.include_router(web_router)
app.include_router(auth_router)
app.include_router(api_router)


@app.on_event("startup")
def startup_event() -> None:
    ensure_schema()
    start_scheduler()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401 and not request.url.path.startswith("/api"):
        return RedirectResponse("/login", status_code=303)
    return await fastapi_http_exception_handler(request, exc)


if __name__ == "__main__":
    import os
    import uvicorn

    _host = os.environ.get("HOST", "0.0.0.0")
    _port = int(os.environ.get("PORT", "8000"))
    print(
        f"Smart Tracker: http://127.0.0.1:{_port}/  "
        f"(если «localhost» не открывается в браузере — используйте именно 127.0.0.1; хост сервера: {_host})"
    )
    uvicorn.run("app.main:app", host=_host, port=_port, reload=True)
