import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.schemas import Answer, NewChat, SendMessage
from backend.storage import SESSION_TTL, Store, StoreError
from core.contracts import Consultant
from core.stub import StubConsultant

COOKIE = "energytool_session"
WEB = Path(__file__).parent / "web"


def create_app(db_path: str | None = None, consultant: Consultant | None = None, timeout: float | None = None):
    mode = os.getenv("CORE_MODE", "stub")
    if consultant is None and mode != "stub":
        raise RuntimeError("Only CORE_MODE=stub is wired at checkpoint 1; connect the participant #2 adapter first")
    store = Store(db_path or os.getenv("DATABASE_PATH", "var/energytool.sqlite3"))
    core = consultant or StubConsultant()
    seconds = timeout if timeout is not None else float(os.getenv("CORE_TIMEOUT_SECONDS", "20"))
    if not 0 < seconds <= 30:
        raise ValueError("CORE_TIMEOUT_SECONDS must be >0 and <=30")
    secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    app = FastAPI(title="MTS Energytool", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"error": "origin_not_allowed"}, status_code=403)
            # Our JSON requests cannot be submitted through a plain cross-site HTML form.
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"error": "json_required"}, status_code=415)
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"error": "invalid_content_length"}, status_code=400)
            if length > 40000:
                return JSONResponse({"error": "body_too_large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        return JSONResponse({"error": exc.code}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default payload can echo submitted contacts/messages.
        return JSONResponse({"error": "invalid_request"}, status_code=422)

    def owner(request: Request):
        return store.session(request.cookies.get(COOKIE))

    @app.get("/health")
    def health():
        with store.connection() as db:
            db.execute("SELECT 1").fetchone()
        return {"status": "ok", "core_mode": "stub" if isinstance(core, StubConsultant) else "custom"}

    @app.post("/api/session")
    def session(request: Request, response: Response):
        token = request.cookies.get(COOKIE)
        try:
            store.session(token)
        except StoreError:
            token = store.new_session()
            response.set_cookie(COOKIE, token, max_age=SESSION_TTL, httponly=True, secure=secure, samesite="strict")
        return {"status": "ready"}

    @app.post("/api/chats", status_code=201)
    def new_chat(body: NewChat, user=Depends(owner)):
        return store.new_chat(user, body.title)

    @app.get("/api/chats")
    def chats(offset: int = Query(0, ge=0), user=Depends(owner)):
        return {"chats": store.list_chats(user, offset), "offset": offset, "limit": 100}

    @app.get("/api/chats/{chat_id}")
    def chat(chat_id: str, user=Depends(owner)):
        return store.get_chat(user, chat_id)

    @app.post("/api/chats/{chat_id}/archive")
    def archive(chat_id: str, user=Depends(owner)):
        store.archive(user, chat_id)
        return {"status": "archived"}

    @app.post("/api/chats/{chat_id}/reopen", status_code=201)
    def reopen(chat_id: str, user=Depends(owner)):
        old = store.get_chat(user, chat_id)
        if not old["archived"]:
            raise StoreError(409, "chat_not_archived")
        return store.new_chat(user, old["title"])

    async def process(chat_id, body, user):
        turn_id, cached, history = store.reserve(user, chat_id, body.request_id, body.content)
        if cached is not None:
            return {"request_id": body.request_id, "result": cached, "replayed": True}
        try:
            raw = await asyncio.wait_for(core.answer(history), timeout=seconds)
            result = Answer.model_validate(raw).model_dump()
            if result["status"] == "stub" and not isinstance(core, StubConsultant):
                raise ValueError("Real core returned stub status")
            store.finish(turn_id, result)
            return {"request_id": body.request_id, "result": result, "replayed": False}
        except asyncio.CancelledError:
            store.finish(turn_id, None)
            raise
        except TimeoutError:
            store.finish(turn_id, None)
            raise StoreError(504, "core_timeout") from None
        except Exception:
            store.finish(turn_id, None)
            raise StoreError(502, "core_unavailable_or_invalid") from None

    @app.post("/api/chats/{chat_id}/messages")
    async def message(chat_id: str, body: SendMessage, user=Depends(owner)):
        return await process(chat_id, body, user)

    @app.post("/api/chats/{chat_id}/messages/stream")
    async def stream(chat_id: str, body: SendMessage, user=Depends(owner)):
        result = await process(chat_id, body, user)

        async def events():
            # Only validated, committed answers are emitted. Not live model tokens.
            yield "event: result\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/app.js", include_in_schema=False)
    def javascript():
        return FileResponse(WEB / "app.js", media_type="text/javascript")

    @app.get("/style.css", include_in_schema=False)
    def stylesheet():
        return FileResponse(WEB / "style.css", media_type="text/css")

    return app
