"""Authenticated local operator API; no automatic provider transport."""
import hashlib
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import TypeAdapter, ValidationError

from agent.hybrid.contracts import Authorization, Batch, Identifier, QAReceipt, ReceiptMetadata, Reconcile, RunBatch, SessionCreate, StateChange
from agent.hybrid.media import MediaError, inspect_input, probe_media, validate_output
from agent.hybrid.security import Settings
from agent.hybrid.store import Store, StoreError, new_id

CAPABILITIES = {
    "transport": {"status": "unverified", "automatic": False, "executionState": "MANUAL_REQUIRED", "evidence": "No verified legal provider transport is installed or enabled."},
    "V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON": {"status": "unverified", "evidence": "Exact source-motion-only with independent Before and After semantics requires manual provider verification; no fallback permitted."},
    "I2V_PRODUCT_ONLY": {"status": "unverified", "evidence": "Exact product-only mode and no person/face/outfit require manual provider and visual verification."},
    "1080-promotion": {"status": "unverified", "evidence": "Exact 1080x1920 preview promotion requires manual provider verification and immutable preview QA PASS."},
    "balance": {"status": "unknown", "value": None, "evidence": "No provider balance query is performed; configuration tier is not balance."},
}


def create_app(settings: Settings | None = None) -> FastAPI:
    selected = settings or Settings.from_environment()
    store = Store(selected.runtime_root)

    @asynccontextmanager
    async def lifespan(app):
        app.state.local_token = selected.provision()
        store.initialize()
        store.recover()
        yield

    app = FastAPI(title="Flow Kit AFF Hybrid", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None,
                  telemetry={"tracing": False, "metrics": False, "logs": False, "operation_spans": False, "auto_configure": False})
    app.state.store, app.state.settings = store, selected
    repo_root = Path(__file__).resolve().parents[2]
    shell_files = {
        "/": repo_root / "dashboard/hybrid/index.html",
        "/hybrid/": repo_root / "dashboard/hybrid/index.html",
        "/hybrid/styles.css": repo_root / "dashboard/hybrid/styles.css",
        "/hybrid/app.mjs": repo_root / "dashboard/hybrid/app.mjs",
        "/hybrid/client.mjs": repo_root / "dashboard/hybrid/client.mjs",
        "/protocol/canonical-json.mjs": repo_root / "protocol/canonical-json.mjs",
    }

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(MediaError)
    async def media_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def contract_error(request, exc):
        # Never echo prompt/media/body values, including invalid Unicode, into
        # errors. A malformed canonical domain must remain a structured 422.
        errors = [{"loc": error["loc"], "type": error["type"], "msg": error["msg"].encode("utf-8", errors="replace").decode("utf-8")} for error in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        peer = request.client.host if request.client else None
        if not selected.transport_allowed(request.headers.get("host", ""), request.headers.get("origin"), peer):
            return JSONResponse({"detail": "Exact loopback Host and Origin required"}, status_code=403)
        public_shell = request.method == "GET" and request.url.path in shell_files
        if request.url.path != "/health" and not public_shell:
            if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + app.state.local_token):
                return JSONResponse({"detail": "Local bearer authentication required"}, status_code=401)
            renewing = request.method == "POST" and request.url.path.startswith("/api/sessions/") and request.url.path.endswith("/renew")
            if request.url.path != "/api/sessions" and not renewing:
                try:
                    request.state.session = store.session(request.headers.get("x-session-id", ""))
                except StoreError as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status)
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) < 0 or int(length) > selected.max_upload_bytes + 65536:
                    return JSONResponse({"detail": "Request exceeds upload limit"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length"}, status_code=400)
        return await call_next(request)

    def actor(request: Request):
        return request.state.session["actor_id"]

    async def owned_upload(file: UploadFile, directory: str, identity: str):
        path = selected.runtime_root / directory / (identity + ".media")
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("xb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > selected.max_upload_bytes:
                        raise StoreError(413, "Upload exceeds limit")
                    digest.update(chunk)
                    stream.write(chunk)
            if size == 0:
                raise StoreError(422, "Actual media bytes are required")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        return path, digest.hexdigest()

    @app.get("/health")
    def health():
        return {"status": "ok", "version": app.version, "mode": "manual-only", "extension_connected": False, "automatic_provider_transport": False}

    def shell(request: Request):
        if request.url.path == "/":
            return RedirectResponse("/hybrid/", status_code=307)
        path = shell_files[request.url.path]
        if not path.is_file():
            raise StoreError(404, "Standalone operator shell is not installed")
        media = "text/javascript" if path.suffix == ".mjs" else "text/css" if path.suffix == ".css" else "text/html"
        return FileResponse(path, media_type=media, headers={"Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'", "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    for shell_path in shell_files:
        app.add_api_route(shell_path, shell, methods=["GET"], include_in_schema=False)

    @app.post("/api/sessions")
    def session(body: SessionCreate):
        if body.actorId != selected.operator_id:
            raise StoreError(403, "Session actor must match the server-configured operator principal")
        return store.create_session(selected.operator_id)

    @app.post("/api/sessions/{session_id}/renew")
    def renew_session(session_id: str):
        return store.renew_session(session_id, selected.operator_id)

    @app.get("/api/providers/status")
    def capabilities():
        return CAPABILITIES

    @app.post("/api/inputs/upload")
    async def upload_input(request: Request, projectId: str = Form(...), role: str = Form(...), contentClass: str = Form(...), attestation: str = Form(...), file: UploadFile = File(...)):
        try:
            TypeAdapter(Identifier).validate_python(projectId)
        except ValidationError as exc:
            raise StoreError(422, "Invalid project identity") from exc
        if role not in {"SOURCE_MOTION", "BEFORE", "AFTER", "PRODUCT_IMAGE"} or contentClass not in {"SOURCE_MOTION", "PERSON", "PRODUCT_ONLY"} or not 1 <= len(attestation) <= 2000:
            raise StoreError(422, "Exact role, content class and explicit operator attestation required")
        if (role == "SOURCE_MOTION") != (contentClass == "SOURCE_MOTION") or role == "PRODUCT_IMAGE" and contentClass != "PRODUCT_ONLY":
            raise StoreError(422, "Input content class conflicts with role")
        asset = new_id()
        path, digest = await owned_upload(file, "inputs", asset)
        try:
            metadata = inspect_input(path, role, selected.runtime_root)
            return store.add_input(actor(request), projectId, role, contentClass, attestation, asset, digest, path, metadata)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.get("/api/inputs/{asset_id}/bytes")
    def input_bytes(asset_id: str, request: Request):
        return FileResponse(store.owned_file("input", asset_id, actor(request)), media_type="application/octet-stream")

    @app.post("/api/authorizations")
    def authorize(body: Authorization, request: Request):
        return store.authorize(body, actor(request))

    @app.post("/api/requests/batch")
    def batch(body: Batch, request: Request):
        return store.admit(body, actor(request))

    @app.get("/api/requests/batch-status")
    def batch_status(request: Request, batchId: str | None = None, project_id: str | None = None, orientation: str | None = None, type: str | None = None):
        rows = store.list(actor(request), project_id, batchId)
        if orientation:
            rows = [r for r in rows if r["orientation"] == orientation]
        if type:
            rows = [r for r in rows if r["type"] == type]
        counts = {name: sum(r["status"] == name for r in rows) for name in ("PENDING", "PROCESSING", "COMPLETED", "FAILED")}
        return {"total": len(rows), "pending": counts["PENDING"], "processing": counts["PROCESSING"], "completed": counts["COMPLETED"], "failed": counts["FAILED"], "done": bool(rows) and counts["PENDING"] + counts["PROCESSING"] == 0, "all_succeeded": bool(rows) and counts["COMPLETED"] == len(rows), "orientation": orientation, "executionStates": {state: sum(r["executionState"] == state for r in rows) for state in {r["executionState"] for r in rows}}, "requests": rows}

    @app.get("/api/requests")
    def list_requests(request: Request, project_id: str | None = None, batchId: str | None = None):
        return store.list(actor(request), project_id, batchId)

    @app.post("/api/requests/batches/{batch_id}/run")
    def run(batch_id: str, body: RunBatch, request: Request):
        return store.reserve(batch_id, body.requestId, body.expectedStateVersion, request.state.session)

    @app.get("/api/requests/{rid}")
    def get_request(rid: str, request: Request):
        return store.get(rid, actor(request))

    @app.post("/api/requests/{rid}/cancel")
    def cancel(rid: str, body: StateChange, request: Request):
        return store.change_state(rid, actor(request), body.expectedStateVersion, "CANCELED", body.reason)

    @app.post("/api/requests/{rid}/reconcile")
    def reconcile(rid: str, body: Reconcile, request: Request):
        if body.outcome == "OUTPUT_RECEIVED":
            return store.clarify(rid, actor(request), request.state.session["id"], body)
        return store.change_state(rid, actor(request), body.expectedStateVersion, body.outcome, body.reason, body.evidenceRef)

    @app.post("/api/requests/{rid}/receipts")
    async def receipt(rid: str, request: Request, metadata: str = Form(...), file: UploadFile = File(...)):
        try:
            parsed = ReceiptMetadata.model_validate_json(metadata)
        except ValidationError as exc:
            raise StoreError(422, "Receipt metadata violates the locked contract") from exc
        context = store.receipt_context(rid, actor(request), request.state.session["id"], parsed)
        artifact = new_id()
        path, digest = await owned_upload(file, "output", artifact)
        try:
            actual = probe_media(path, selected.runtime_root)
            validate_output(actual, context["specification"])
            return store.attach_receipt(rid, actor(request), request.state.session["id"], parsed, artifact, digest, path, actual)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.post("/api/requests/{rid}/qa")
    def qa(rid: str, body: QAReceipt, request: Request):
        return store.qa(rid, actor(request), body)

    @app.get("/api/artifacts/{artifact_id}/bytes")
    def artifact_bytes(artifact_id: str, request: Request):
        return FileResponse(store.owned_file("artifact", artifact_id, actor(request)), media_type="application/octet-stream")

    @app.post("/api/ext/callback")
    def callback(request: Request):
        raise StoreError(410, "Extension transport is disabled; callbacks cannot complete requests")

    @app.websocket("/ws/dashboard")
    async def dashboard(websocket: WebSocket):
        peer = websocket.client.host if websocket.client else None
        if not selected.transport_allowed(websocket.headers.get("host", ""), websocket.headers.get("origin"), peer):
            await websocket.close(code=4403)
            return
        if not secrets.compare_digest(websocket.headers.get("authorization", ""), "Bearer " + app.state.local_token):
            await websocket.close(code=4401)
            return
        try:
            local_session = store.session(websocket.headers.get("x-session-id", ""))
        except StoreError:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            await websocket.send_json({"type": "snapshot", "requests": store.list(local_session["actor_id"]), "capabilities": CAPABILITIES})
            while True:
                message = await websocket.receive_text()
                store.session(local_session["id"])
                if message != "snapshot":
                    await websocket.send_json({"type": "error", "detail": "Status-only channel; provider commands and callbacks are disabled"})
                else:
                    await websocket.send_json({"type": "snapshot", "requests": store.list(local_session["actor_id"])})
        except WebSocketDisconnect:
            pass
        except StoreError:
            await websocket.close(code=4401)

    @app.api_route("/api/{legacy_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def reject_legacy(legacy_path: str):
        raise StoreError(410, "Unmanaged legacy route is disabled in the safe hybrid entrypoint")

    return app
