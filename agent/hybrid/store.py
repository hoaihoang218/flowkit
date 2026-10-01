"""SQLite request queue with atomic admission, reservation and evidence binding."""
import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from agent.hybrid.contracts import Authorization, Batch, QA_BY_MODE, QAReceipt, ReceiptMetadata, Reconcile, canonical, fingerprint, timestamp, utc_now
from agent.hybrid.media import probe_media, validate_output


class StoreError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


def new_id() -> str:
    return str(uuid.uuid4())


SCHEMA = """
CREATE TABLE IF NOT EXISTS project(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS request(
 id TEXT PRIMARY KEY, project_id TEXT REFERENCES project(id), video_id TEXT,
 scene_id TEXT, character_id TEXT, type TEXT NOT NULL,
 orientation TEXT CHECK(orientation IN ('VERTICAL','HORIZONTAL')),
 status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROCESSING','COMPLETED','FAILED')),
 request_id TEXT, media_id TEXT, output_url TEXT, error_message TEXT,
 retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, edit_prompt TEXT,
 source_media_id TEXT, provider TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_project(project_id TEXT PRIMARY KEY REFERENCES project(id), actor_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hybrid_session(id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hybrid_input(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), actor_id TEXT NOT NULL,
 role TEXT NOT NULL, content_class TEXT NOT NULL, attestation TEXT NOT NULL,
 sha256 TEXT NOT NULL, path TEXT NOT NULL, metadata TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_authorization(
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), actor_id TEXT NOT NULL,
 body TEXT NOT NULL, body_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_budget(
 id TEXT PRIMARY KEY, body TEXT NOT NULL, project_id TEXT NOT NULL, product_id TEXT NOT NULL,
 profile_id TEXT NOT NULL, flow_project_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_batch(
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL, project_id TEXT NOT NULL REFERENCES project(id),
 product_id TEXT NOT NULL, actor_id TEXT NOT NULL, body_sha256 TEXT NOT NULL, request_ids TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_attempt(
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE REFERENCES request(id), attempt INTEGER NOT NULL CHECK(attempt=1),
 session_id TEXT NOT NULL REFERENCES hybrid_session(id), actor_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL, budget_id TEXT NOT NULL REFERENCES hybrid_budget(id),
 output_count INTEGER NOT NULL, cost_units INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hybrid_artifact(
 id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL REFERENCES request(id),
 reservation_id TEXT NOT NULL REFERENCES hybrid_attempt(id), session_id TEXT NOT NULL,
 actor_id TEXT NOT NULL, output_index INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL,
 metadata TEXT NOT NULL, receipt TEXT NOT NULL, accepted INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(request_id,output_index)
);
CREATE TABLE IF NOT EXISTS hybrid_qa(
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES request(id), artifact_id TEXT NOT NULL REFERENCES hybrid_artifact(id),
 actor_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(artifact_id)
);
CREATE TABLE IF NOT EXISTS hybrid_event(
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES request(id), actor_id TEXT NOT NULL,
 kind TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

REQUEST_COLUMNS = {
    "hybrid_version": "INTEGER", "execution_state": "TEXT", "state_version": "INTEGER NOT NULL DEFAULT 0",
    "idempotency_key": "TEXT", "payload_sha256": "TEXT", "batch_id": "TEXT", "batch_revision": "INTEGER",
    "lane_id": "TEXT", "lane_revision": "INTEGER", "lane_order": "INTEGER", "action": "TEXT",
    "specification": "TEXT", "authorization_id": "TEXT", "envelope": "TEXT", "actor_id": "TEXT",
    "qa_state": "TEXT NOT NULL DEFAULT 'NONE'",
}


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "flow_agent.db"

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except sqlite3.IntegrityError as exc:
            if write:
                db.rollback()
            raise StoreError(409, "Immutable identity or association conflicts") from exc
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            existing = {r["name"] for r in db.execute("PRAGMA table_info(request)")}
            for name, definition in REQUEST_COLUMNS.items():
                if name not in existing:
                    db.execute(f"ALTER TABLE request ADD COLUMN {name} {definition}")
            db.executescript("""
            CREATE UNIQUE INDEX IF NOT EXISTS hybrid_request_key ON request(project_id,idempotency_key) WHERE hybrid_version=1;
            CREATE UNIQUE INDEX IF NOT EXISTS hybrid_lane_action ON request(project_id,json_extract(envelope,'$.authorization.scope.productId'),json_extract(envelope,'$.authorization.scope.contentId'),json_extract(envelope,'$.authorization.scope.jobId'),json_extract(envelope,'$.authorization.scope.jobRevision'),lane_id,lane_revision,action) WHERE hybrid_version=1;
            CREATE INDEX IF NOT EXISTS hybrid_request_batch ON request(batch_id,lane_order) WHERE hybrid_version=1;
            CREATE TRIGGER IF NOT EXISTS hybrid_request_immutable BEFORE UPDATE OF hybrid_version,project_id,idempotency_key,payload_sha256,batch_id,batch_revision,lane_id,lane_revision,lane_order,action,specification,authorization_id,envelope,actor_id,orientation,type,provider ON request WHEN OLD.hybrid_version=1
            BEGIN SELECT RAISE(ABORT,'Immutable hybrid request specification'); END;
            """)
            for table in ("hybrid_input", "hybrid_authorization", "hybrid_budget", "hybrid_batch", "hybrid_attempt", "hybrid_artifact", "hybrid_qa", "hybrid_event"):
                for verb in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{verb.lower()}_immutable BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'Append-only evidence'); END")

    def recover(self):
        with self.connection(write=True) as db:
            rows = db.execute("SELECT id FROM request WHERE hybrid_version=1 AND execution_state IN ('SUBMITTING','RUNNING','MANUAL_REQUIRED')").fetchall()
            for row in rows:
                self._transition(db, row["id"], "OUTCOME_UNKNOWN", "PROCESSING", "Restart requires reconciliation; no resubmission")
                self._event(db, row["id"], "system", "RECOVERY", {"reason": "Unresolved manual handoff at restart"})
        return len(rows)

    def create_session(self, actor: str):
        session = {"sessionId": new_id(), "actorId": actor, "expiresAt": (utc_now() + timedelta(hours=1)).isoformat()}
        with self.connection(write=True) as db:
            db.execute("INSERT INTO hybrid_session VALUES (?,?,?)", (session["sessionId"], actor, session["expiresAt"]))
        return session

    def session(self, session_id: str):
        with self.connection() as db:
            row = db.execute("SELECT * FROM hybrid_session WHERE id=?", (session_id,)).fetchone()
        if row is None or timestamp(row["expires_at"]) <= utc_now():
            raise StoreError(401, "Valid authenticated local session required")
        return dict(row)

    def renew_session(self, session_id: str, actor: str):
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM hybrid_session WHERE id=?", (session_id,)).fetchone()
            if row is None or row["actor_id"] != actor:
                raise StoreError(403, "Session renewal requires the same server-authenticated principal")
            expiry = (utc_now() + timedelta(hours=1)).isoformat()
            db.execute("UPDATE hybrid_session SET expires_at=? WHERE id=?", (expiry, session_id))
            return {"sessionId": session_id, "actorId": actor, "expiresAt": expiry}

    def _project(self, db, project: str, actor: str, create=False):
        row = db.execute("SELECT actor_id FROM hybrid_project WHERE project_id=?", (project,)).fetchone()
        if row is None and create:
            db.execute("INSERT OR IGNORE INTO project(id,name) VALUES (?,?)", (project, project))
            db.execute("INSERT INTO hybrid_project VALUES (?,?)", (project, actor))
        elif row is None or row["actor_id"] != actor:
            raise StoreError(403, "Project is outside this actor's ownership")

    def add_input(self, actor: str, project: str, role: str, content_class: str, attestation: str, asset_id: str, digest: str, path: Path, metadata: dict):
        with self.connection(write=True) as db:
            self._project(db, project, actor, create=True)
            db.execute("INSERT INTO hybrid_input VALUES (?,?,?,?,?,?,?,?,?,?)", (asset_id, project, actor, role, content_class, attestation, digest, str(path), canonical(metadata).decode(), utc_now().isoformat()))
        return {"assetId": asset_id, "sha256": digest, "role": role, "contentClass": content_class, "metadata": metadata}

    def _inputs(self, db, auth: Authorization, actor: str):
        scope = auth.scope
        for binding in auth.specification.inputAuthority:
            row = db.execute("SELECT * FROM hybrid_input WHERE id=?", (binding.assetId,)).fetchone()
            if row is None or (row["project_id"], row["actor_id"], row["role"], row["sha256"]) != (scope.projectId, actor, binding.role, binding.sha256):
                raise StoreError(409, "Input ownership, role or actual-byte SHA differs from authority")
            if auth.specification.mode == "I2V_PRODUCT_ONLY" and row["content_class"] != "PRODUCT_ONLY":
                raise StoreError(422, "Product-only requires explicit no-person/face/outfit input attestation")
            if binding.role in {"BEFORE", "AFTER"} and row["content_class"] != "PERSON":
                raise StoreError(422, "Person mode requires person BEFORE and AFTER assets")
            if binding.role == "SOURCE_MOTION":
                meta = json.loads(row["metadata"])
                if scope.sourceWindow.endSeconds > meta["durationSeconds"]:
                    raise StoreError(422, "Approved source window exceeds actual source duration")
            path = Path(row["path"])
            if not path.resolve().is_relative_to(self.root) or not path.is_file():
                raise StoreError(409, "Owned immutable input bytes are unavailable")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != binding.sha256:
                raise StoreError(409, "Input bytes changed after registration")

    def _promotion(self, db, auth: Authorization, actor: str):
        spec, scope = auth.specification, auth.scope
        if spec.action != "1080-promotion":
            return
        row = db.execute("SELECT q.body,q.actor_id,a.id artifact_id,a.receipt_id,a.request_id,a.sha256,a.path,a.accepted,r.envelope,r.status,r.qa_state FROM hybrid_qa q JOIN hybrid_artifact a ON a.id=q.artifact_id JOIN request r ON r.id=q.request_id WHERE q.id=?", (spec.qaReceiptId,)).fetchone()
        if row is None or row["actor_id"] != actor or (row["request_id"], row["artifact_id"], row["receipt_id"], row["sha256"]) != (spec.previewRequestId, spec.previewArtifactId, spec.previewReceiptId, spec.previewArtifactSha256) or not self._effective_acceptance(db, row["artifact_id"], row["accepted"]) or row["status"] != "COMPLETED":
            raise StoreError(409, "Promotion requires an owned completed preview with exact immutable QA receipt/SHA")
        path = Path(row["path"])
        if not path.resolve().is_relative_to(self.root) or not path.is_file():
            raise StoreError(409, "Promotion preview bytes are unavailable")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != spec.previewArtifactSha256:
                raise StoreError(409, "Promotion preview bytes changed after immutable QA")
        qa = json.loads(row["body"])
        preview = json.loads(row["envelope"])
        prior_scope = preview["authorization"]["scope"]
        all_previews = db.execute("SELECT * FROM hybrid_artifact WHERE request_id=?", (spec.previewRequestId,)).fetchall()
        if row["qa_state"] != "PASS" or len(all_previews) != preview["outputCount"]:
            raise StoreError(409, "Promotion requires aggregate QA PASS for every preview output")
        preview_auth = Authorization.model_validate(preview["authorization"])
        for prior in all_previews:
            if not self._effective_acceptance(db, prior["id"], prior["accepted"]):
                raise StoreError(409, "Promotion preview output lacks exact association")
            self._validate_authorization(db, preview_auth, actor, performed_at=json.loads(prior["receipt"])["performedAt"])
            prior_path = Path(prior["path"])
            if not prior_path.resolve().is_relative_to(self.root) or not prior_path.is_file():
                raise StoreError(409, "Promotion aggregate preview bytes are unavailable")
            with prior_path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != prior["sha256"]:
                    raise StoreError(409, "Promotion aggregate preview bytes changed after QA")
        comparison = ("projectId", "productId", "contentId", "allocationId", "topicId", "topicRevision", "format", "jobId", "jobRevision", "laneId", "laneRevision", "outfitPackId", "outfitPackRevision", "outfitHash", "p02ReceiptId", "p02Fingerprint", "p02Revision", "inputHashes", "sourceWindow", "profileId", "flowProjectId")
        if qa["verdict"] != "PASS" or preview["action"] != "preview-generation" or preview["resolution"] != "720x1280" or any(prior_scope[name] != scope.model_dump()[name] for name in comparison):
            raise StoreError(409, "Promotion scope or explicit preview QA PASS differs")
        for name in ("prompt", "model", "mode", "orientation", "audioPolicy", "durationSeconds", "inputAuthority"):
            if preview[name] != spec.model_dump()[name]:
                raise StoreError(409, "Promotion cannot change approved preview specification")

    def _validate_authorization(self, db, auth: Authorization, actor: str, registered=True, performed_at=None):
        authority_time = timestamp(performed_at) if performed_at is not None else utc_now()
        if auth.approvedBy != actor or timestamp(auth.approvedAt) > authority_time or timestamp(auth.expiresAt) <= authority_time:
            raise StoreError(403, "Exact actor approval is missing, future-dated or expired")
        self._project(db, auth.scope.projectId, actor)
        if registered:
            row = db.execute("SELECT body_sha256,actor_id FROM hybrid_authorization WHERE id=?", (auth.authorizationId,)).fetchone()
            if row is None or row["actor_id"] != actor or row["body_sha256"] != fingerprint(auth.model_dump()):
                raise StoreError(403, "Immutable registered authorization snapshot required")
        self._inputs(db, auth, actor)
        self._promotion(db, auth, actor)

    def authorize(self, auth: Authorization, actor: str):
        body = auth.model_dump()
        with self.connection(write=True) as db:
            self._validate_authorization(db, auth, actor, registered=False)
            existing = db.execute("SELECT body_sha256 FROM hybrid_authorization WHERE id=?", (auth.authorizationId,)).fetchone()
            if existing:
                if existing["body_sha256"] != fingerprint(body):
                    raise StoreError(409, "Authorization identity already locks a different snapshot")
                return body
            scope = auth.scope
            budget = db.execute("SELECT * FROM hybrid_budget WHERE id=?", (scope.budget.budgetId,)).fetchone()
            budget_values = (canonical(scope.budget.model_dump()).decode(), scope.projectId, scope.productId, scope.profileId, scope.flowProjectId)
            if budget and tuple(budget[k] for k in ("body", "project_id", "product_id", "profile_id", "flow_project_id")) != budget_values:
                raise StoreError(409, "Budget identity already locks a different allowance or scope")
            if not budget:
                db.execute("INSERT INTO hybrid_budget VALUES (?,?,?,?,?,?)", (scope.budget.budgetId, *budget_values))
            db.execute("INSERT INTO hybrid_authorization VALUES (?,?,?,?,?,?)", (auth.authorizationId, scope.projectId, actor, canonical(body).decode(), fingerprint(body), utc_now().isoformat()))
        return body

    def admit(self, batch: Batch, actor: str):
        body, first = batch.model_dump(), batch.requests[0]
        with self.connection(write=True) as db:
            existing_batch = db.execute("SELECT * FROM hybrid_batch WHERE id=?", (first.batchId,)).fetchone()
            if existing_batch:
                if existing_batch["actor_id"] != actor or existing_batch["body_sha256"] != fingerprint(body):
                    raise StoreError(409, "Batch identity already locks a different all-or-nothing request list")
                return [self._public(self._owned_request(db, rid, actor)) for rid in json.loads(existing_batch["request_ids"])]
            # Identity conflicts and immutable retries are resolved before current
            # authority checks. New admissions still require fresh authority.
            for item in batch.requests:
                prior = db.execute("SELECT payload_sha256,actor_id FROM request WHERE project_id=? AND idempotency_key=? AND hybrid_version=1", (item.authorization.scope.projectId, item.idempotencyKey)).fetchone()
                if prior and (prior["payload_sha256"] != item.payloadSha256 or prior["actor_id"] != actor):
                    raise StoreError(409, "Idempotency key already locks a different payload or owner")
            for item in batch.requests:
                self._validate_authorization(db, item.authorization, actor)
            ids = []
            for item in batch.requests:
                project = item.authorization.scope.projectId
                existing = db.execute("SELECT * FROM request WHERE project_id=? AND idempotency_key=? AND hybrid_version=1", (project, item.idempotencyKey)).fetchone()
                if existing:
                    if existing["payload_sha256"] != item.payloadSha256:
                        raise StoreError(409, "Idempotency key already locks a different payload")
                    ids.append(existing["id"])
                    continue
                rid = new_id()
                spec = item.authorization.specification.model_dump()
                now = utc_now().isoformat()
                db.execute("""INSERT INTO request(id,project_id,type,orientation,status,provider,created_at,updated_at,hybrid_version,execution_state,state_version,idempotency_key,payload_sha256,batch_id,batch_revision,lane_id,lane_revision,lane_order,action,specification,authorization_id,envelope,actor_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (rid, project, "UPSCALE_VIDEO" if item.action == "1080-promotion" else "GENERATE_VIDEO", item.orientation, "PENDING", "flow-manual", now, now, 1, "QUEUED", 0, item.idempotencyKey, item.payloadSha256, item.batchId, item.batchRevision, item.laneId, item.laneRevision, item.order, item.action, canonical(spec).decode(), item.authorization.authorizationId, canonical(item.model_dump()).decode(), actor))
                ids.append(rid)
            db.execute("INSERT INTO hybrid_batch VALUES (?,?,?,?,?,?,?)", (first.batchId, first.batchRevision, first.authorization.scope.projectId, first.authorization.scope.productId, actor, fingerprint(body), canonical(ids).decode()))
            return [self._public(self._owned_request(db, rid, actor)) for rid in ids]

    def _owned_request(self, db, rid: str, actor: str):
        row = db.execute("SELECT * FROM request WHERE id=? AND hybrid_version=1", (rid,)).fetchone()
        if row is None:
            raise StoreError(404, "Managed hybrid request not found")
        if row["actor_id"] != actor:
            raise StoreError(403, "Request is outside this actor's ownership")
        return row

    def _public(self, row):
        data = dict(row)
        data["executionState"] = data.pop("execution_state")
        data["stateVersion"] = data.pop("state_version")
        data["qaState"] = data.pop("qa_state")
        data["specification"] = json.loads(data["specification"])
        data["envelope"] = json.loads(data["envelope"])
        return data

    def get(self, rid: str, actor: str):
        with self.connection() as db:
            row = self._owned_request(db, rid, actor)
            result = self._public(row)
            result["artifacts"] = [self._artifact_public(x, db) for x in db.execute("SELECT * FROM hybrid_artifact WHERE request_id=? ORDER BY output_index", (rid,))]
            attempt = db.execute("SELECT * FROM hybrid_attempt WHERE request_id=?", (rid,)).fetchone()
            if attempt:
                spec, scope = result["specification"], result["envelope"]["authorization"]["scope"]
                result["handoff"] = {"reservationId": attempt["id"], "attempt": 1, "sessionId": attempt["session_id"], "actorId": attempt["actor_id"], "payloadSha256": attempt["payload_sha256"], "inputAuthority": spec["inputAuthority"], "specification": spec, "scope": scope, "inputBytesUrls": {binding["role"]: f"/api/inputs/{binding['assetId']}/bytes" for binding in spec["inputAuthority"]}, "providerTransport": "MANUAL_REQUIRED", "capabilityEvidence": "Persisted existing reservation; inspect/reconcile only. No new attempt or retry."}
            return result

    def list(self, actor: str, project: str | None = None, batch: str | None = None):
        with self.connection() as db:
            if project:
                self._project(db, project, actor)
            rows = db.execute("SELECT * FROM request WHERE hybrid_version=1 AND actor_id=? AND (? IS NULL OR project_id=?) AND (? IS NULL OR batch_id=?) ORDER BY created_at,lane_order", (actor, project, project, batch, batch)).fetchall()
            return [self._public(row) for row in rows]

    def _event(self, db, rid, actor, kind, body):
        db.execute("INSERT INTO hybrid_event VALUES (?,?,?,?,?,?)", (new_id(), rid, actor, kind, canonical(body).decode(), utc_now().isoformat()))

    def _transition(self, db, rid, state, status, reason=None):
        db.execute("UPDATE request SET execution_state=?,status=?,state_version=state_version+1,error_message=?,updated_at=? WHERE id=?", (state, status, reason, utc_now().isoformat(), rid))

    def _cas(self, row, version):
        if row["state_version"] != version:
            raise StoreError(409, "State version changed; inspect current state before acting")

    def reserve(self, batch_id: str, rid: str, version: int, session: dict):
        actor = session["actor_id"]
        with self.connection(write=True) as db:
            row = self._owned_request(db, rid, actor)
            self._cas(row, version)
            if row["batch_id"] != batch_id or row["execution_state"] != "QUEUED":
                raise StoreError(409, "Only the next queued request in this approved batch can be reserved")
            earlier = db.execute("SELECT status,qa_state FROM request WHERE hybrid_version=1 AND batch_id=? AND lane_order<?", (batch_id, row["lane_order"])).fetchall()
            if any(x["status"] != "COMPLETED" or x["qa_state"] != "PASS" for x in earlier):
                raise StoreError(409, "Sequential batch is waiting for prior lane output and explicit QA PASS")
            auth = Authorization.model_validate(json.loads(row["envelope"])["authorization"])
            self._validate_authorization(db, auth, actor)
            # One live handoff per approved provider profile/project, across batches.
            active = db.execute("SELECT envelope FROM request WHERE hybrid_version=1 AND execution_state IN ('SUBMITTING','RUNNING','MANUAL_REQUIRED','OUTCOME_UNKNOWN')").fetchall()
            if any((json.loads(x["envelope"])["authorization"]["scope"]["profileId"], json.loads(x["envelope"])["authorization"]["scope"]["flowProjectId"]) == (auth.scope.profileId, auth.scope.flowProjectId) for x in active):
                raise StoreError(409, "Provider scope has an unresolved handoff; reconcile before another reservation")
            spec, budget = auth.specification, auth.scope.budget
            used = db.execute("SELECT COALESCE(SUM(output_count),0),COALESCE(SUM(cost_units),0) FROM hybrid_attempt WHERE budget_id=?", (budget.budgetId,)).fetchone()
            cost = spec.outputCount * budget.costUnitsPerGeneration
            if used[0] + spec.outputCount > budget.maxGenerations or used[1] + cost > budget.maxCostUnits:
                raise StoreError(409, "Approved generation or cost budget is exhausted")
            reservation = new_id()
            db.execute("INSERT INTO hybrid_attempt VALUES (?,?,?,?,?,?,?,?,?,?)", (reservation, rid, 1, session["id"], actor, row["payload_sha256"], budget.budgetId, spec.outputCount, cost, utc_now().isoformat()))
            self._transition(db, rid, "MANUAL_REQUIRED", "PROCESSING", "No verified legal provider transport; explicit operator handoff required")
            self._event(db, rid, actor, "RESERVATION", {"reservationId": reservation, "attempt": 1, "outputCount": spec.outputCount, "sessionId": session["id"]})
            result = self._public(self._owned_request(db, rid, actor))
            result["handoff"] = {"reservationId": reservation, "attempt": 1, "sessionId": session["id"], "actorId": actor, "payloadSha256": row["payload_sha256"], "inputAuthority": spec.model_dump()["inputAuthority"], "specification": spec.model_dump(), "scope": auth.scope.model_dump(), "inputBytesUrls": {binding.role: f"/api/inputs/{binding.assetId}/bytes" for binding in spec.inputAuthority}, "providerTransport": "MANUAL_REQUIRED", "capability": {"model": spec.model, "mode": spec.mode, "action": spec.action, "resolution": spec.resolution, "orientation": spec.orientation, "audioPolicy": spec.audioPolicy, "status": "unverified", "automatic": False, "evidence": "No verified legal transport or provider evidence for this exact tuple."}, "capabilityEvidence": "No verified legal automatic transport; exact mode/model/resolution must be verified manually. No fallback or retry."}
            return result

    def change_state(self, rid: str, actor: str, version: int, target: str, reason: str, evidence=None):
        with self.connection(write=True) as db:
            row = self._owned_request(db, rid, actor)
            self._cas(row, version)
            if row["execution_state"] in {"CANCELED", "FAILED", "OUTPUT_RECEIVED"}:
                raise StoreError(409, "Terminal request state cannot be changed")
            if target == "OUTCOME_UNKNOWN" and row["execution_state"] == "QUEUED":
                raise StoreError(409, "Queued request has no handoff outcome to reconcile")
            self._transition(db, rid, target, "FAILED" if target in {"CANCELED", "FAILED"} else "PROCESSING", reason)
            self._event(db, rid, actor, "CANCEL" if target == "CANCELED" else "RECONCILE", {"reason": reason, "evidenceRef": evidence, "outcome": target})
            return self._public(self._owned_request(db, rid, actor))

    def clarify(self, rid: str, actor: str, session_id: str, body: Reconcile):
        with self.connection(write=True) as db:
            row = self._owned_request(db, rid, actor)
            self._cas(row, body.expectedStateVersion)
            if row["execution_state"] != "OUTCOME_UNKNOWN":
                raise StoreError(409, "Only an unresolved existing outcome can accept clarification")
            exact = body.clarification
            attempt = db.execute("SELECT * FROM hybrid_attempt WHERE request_id=?", (rid,)).fetchone()
            if attempt is None or (attempt["id"], attempt["actor_id"], attempt["session_id"]) != (exact.reservationId, actor, session_id):
                raise StoreError(403, "Clarification requires the same authenticated original reservation session")
            artifact = db.execute("SELECT * FROM hybrid_artifact WHERE id=? AND request_id=?", (exact.artifactId, rid)).fetchone()
            if artifact is None or (artifact["receipt_id"], artifact["reservation_id"], artifact["sha256"], artifact["session_id"]) != (exact.receiptId, exact.reservationId, exact.artifactSha256, session_id):
                raise StoreError(409, "Clarification must bind exact immutable receipt/artifact/hash/reservation")
            if self._effective_acceptance(db, artifact["id"], artifact["accepted"]):
                raise StoreError(409, "Association clarification replay is prohibited")
            if exact.observedGenerations != attempt["output_count"] or exact.observedCostUnits > attempt["cost_units"]:
                raise StoreError(409, "Clarified generation count or cost exceeds exact approved reservation")
            original = ReceiptMetadata.model_validate_json(artifact["receipt"])
            if timestamp(original.performedAt) < timestamp(attempt["created_at"]) or timestamp(original.performedAt) > utc_now():
                raise StoreError(409, "Original performed time does not belong to this reserved attempt")
            auth = Authorization.model_validate(json.loads(row["envelope"])["authorization"])
            self._validate_authorization(db, auth, actor, performed_at=original.performedAt)
            path = Path(artifact["path"])
            if not path.resolve().is_relative_to(self.root) or not path.is_file():
                raise StoreError(409, "Clarification output bytes are unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != exact.artifactSha256:
                    raise StoreError(409, "Clarification output bytes changed after receipt")
            measured = probe_media(path, self.root)
            validate_output(measured, auth.specification.model_dump())
            self._event(db, rid, actor, "ASSOCIATION", {**exact.model_dump(), "evidenceRef": body.evidenceRef, "reason": body.reason, "sessionId": session_id, "payloadSha256": row["payload_sha256"], "performedAt": original.performedAt, "metadata": measured})
            count = self._accepted_count(db, rid)
            if count == auth.specification.outputCount:
                self._transition(db, rid, "OUTPUT_RECEIVED", "PROCESSING", "Reconciled existing output bytes; explicit visual QA is pending")
                db.execute("UPDATE request SET media_id=?,output_url=? WHERE id=?", (artifact["id"], f"/api/artifacts/{artifact['id']}/bytes", rid))
            else:
                self._transition(db, rid, "OUTCOME_UNKNOWN", "PROCESSING", "Awaiting valid association for every reserved output index")
            result = self._public(self._owned_request(db, rid, actor))
            result["artifact"] = self._artifact_public(artifact, db)
            return result

    def receipt_context(self, rid: str, actor: str, session_id: str, receipt: ReceiptMetadata):
        with self.connection() as db:
            return self._receipt_context(db, rid, actor, session_id, receipt)

    def _receipt_context(self, db, rid, actor, session_id, receipt):
        row = self._owned_request(db, rid, actor)
        self._cas(row, receipt.expectedStateVersion)
        attempt = db.execute("SELECT * FROM hybrid_attempt WHERE request_id=?", (rid,)).fetchone()
        if attempt is None or (attempt["id"], attempt["session_id"], attempt["actor_id"], attempt["payload_sha256"]) != (receipt.reservationId, session_id, actor, receipt.payloadSha256):
            raise StoreError(403, "Receipt is not bound to this authenticated session/reservation/fingerprint")
        if (row["lane_id"], row["lane_revision"], row["payload_sha256"]) != (receipt.laneId, receipt.laneRevision, receipt.payloadSha256) or receipt.outputIndex > attempt["output_count"]:
            raise StoreError(409, "Receipt lane revision, fingerprint or output index differs from reservation")
        scope = json.loads(row["envelope"])["authorization"]["scope"]
        if (receipt.profileId, receipt.flowProjectId, receipt.observedGenerations) != (scope["profileId"], scope["flowProjectId"], attempt["output_count"]):
            raise StoreError(409, "Observed provider profile/project/generation count differs from reservation")
        if timestamp(receipt.performedAt) < timestamp(attempt["created_at"]) or timestamp(receipt.performedAt) > utc_now():
            raise StoreError(409, "Performed time must belong to this reserved attempt")
        if receipt.observedCostUnits is not None and receipt.observedCostUnits > attempt["cost_units"]:
            raise StoreError(409, "Observed cost exceeds exact approved reservation")
        if row["execution_state"] not in {"MANUAL_REQUIRED", "RUNNING", "OUTCOME_UNKNOWN", "CANCELED", "FAILED"}:
            raise StoreError(409, "Request cannot accept provider receipt in current state")
        if db.execute("SELECT 1 FROM hybrid_artifact WHERE receipt_id=? OR (request_id=? AND output_index=?)", (receipt.receiptId, rid, receipt.outputIndex)).fetchone():
            raise StoreError(409, "Receipt replay or output replacement is prohibited")
        return self._public(row)

    def attach_receipt(self, rid: str, actor: str, session_id: str, receipt: ReceiptMetadata, artifact_id: str, digest: str, path: Path, metadata: dict):
        with self.connection(write=True) as db:
            row = self._receipt_context(db, rid, actor, session_id, receipt)
            accepted = row["executionState"] not in {"CANCELED", "FAILED"}
            auth = Authorization.model_validate(row["envelope"]["authorization"])
            association_issue = None
            try:
                self._validate_authorization(db, auth, actor, performed_at=receipt.performedAt)
            except StoreError as exc:
                accepted = False
                association_issue = exc.detail
            if receipt.costStatus == "UNKNOWN":
                accepted = False
                association_issue = "Observed provider cost remains unknown"
            if not path.resolve().is_relative_to(self.root) or not path.is_file():
                raise StoreError(409, "Uploaded output bytes are unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise StoreError(409, "Uploaded output bytes changed before atomic association")
            db.execute("INSERT INTO hybrid_artifact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (artifact_id, receipt.receiptId, rid, receipt.reservationId, session_id, actor, receipt.outputIndex, digest, str(path), canonical(metadata).decode(), canonical(receipt.model_dump()).decode(), int(accepted), utc_now().isoformat()))
            self._event(db, rid, actor, "RECEIPT", {"artifactId": artifact_id, "sha256": digest, "accepted": accepted, "associationIssue": association_issue})
            count = self._accepted_count(db, rid)
            if accepted and count == row["specification"]["outputCount"]:
                self._transition(db, rid, "OUTPUT_RECEIVED", "PROCESSING", "Output bytes received; explicit visual QA is pending")
                db.execute("UPDATE request SET media_id=?,output_url=? WHERE id=?", (artifact_id, f"/api/artifacts/{artifact_id}/bytes", rid))
            elif not accepted and row["executionState"] not in {"CANCELED", "FAILED"}:
                self._transition(db, rid, "OUTCOME_UNKNOWN", "PROCESSING", association_issue or "Receipt retained as evidence; exact authority association is unresolved")
            else:
                db.execute("UPDATE request SET state_version=state_version+1,updated_at=? WHERE id=?", (utc_now().isoformat(), rid))
            return {"artifact": self._artifact_public(db.execute("SELECT * FROM hybrid_artifact WHERE id=?", (artifact_id,)).fetchone(), db), "request": self._public(self._owned_request(db, rid, actor))}

    def _effective_acceptance(self, db, artifact_id, original):
        return bool(original) or db.execute("SELECT 1 FROM hybrid_event WHERE kind='ASSOCIATION' AND json_extract(body,'$.artifactId')=?", (artifact_id,)).fetchone() is not None

    def _accepted_count(self, db, rid):
        return sum(self._effective_acceptance(db, row["id"], row["accepted"]) for row in db.execute("SELECT id,accepted FROM hybrid_artifact WHERE request_id=?", (rid,)))

    def _artifact_public(self, row, db=None):
        accepted = self._effective_acceptance(db, row["id"], row["accepted"]) if db is not None else bool(row["accepted"])
        return {"artifactId": row["id"], "receiptId": row["receipt_id"], "requestId": row["request_id"], "reservationId": row["reservation_id"], "sha256": row["sha256"], "outputIndex": row["output_index"], "accepted": accepted, "originalReceiptAccepted": bool(row["accepted"]), "metadata": json.loads(row["metadata"]), "bytesUrl": f"/api/artifacts/{row['id']}/bytes"}

    def qa(self, rid: str, actor: str, receipt: QAReceipt):
        with self.connection(write=True) as db:
            row = self._owned_request(db, rid, actor)
            artifact = db.execute("SELECT * FROM hybrid_artifact WHERE id=? AND request_id=?", (receipt.artifactId, rid)).fetchone()
            if row["execution_state"] != "OUTPUT_RECEIVED" or artifact is None or not self._effective_acceptance(db, artifact["id"], artifact["accepted"]) or artifact["sha256"] != receipt.artifactSha256:
                raise StoreError(409, "QA must bind exact owned accepted output bytes")
            auth = Authorization.model_validate(json.loads(row["envelope"])["authorization"])
            original_receipt = json.loads(artifact["receipt"])
            self._validate_authorization(db, auth, actor, performed_at=original_receipt["performedAt"])
            path = Path(artifact["path"])
            if not path.resolve().is_relative_to(self.root) or not path.is_file():
                raise StoreError(409, "QA output bytes are unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != receipt.artifactSha256:
                    raise StoreError(409, "QA artifact bytes changed after provider receipt")
            mode = json.loads(row["specification"])["mode"]
            if set(receipt.checks) != QA_BY_MODE[mode] or any(value == "NOT_APPLICABLE" for value in receipt.checks.values()):
                raise StoreError(422, "QA must cover the exact checks applicable to the approved mode; no implied PASS")
            db.execute("INSERT INTO hybrid_qa VALUES (?,?,?,?,?,?)", (receipt.qaReceiptId, rid, receipt.artifactId, actor, canonical(receipt.model_dump()).decode(), utc_now().isoformat()))
            results = [json.loads(x["body"])["verdict"] for x in db.execute("SELECT body FROM hybrid_qa WHERE request_id=?", (rid,))]
            verdict = "FAIL" if "FAIL" in results else "PASS" if len(results) == json.loads(row["specification"])["outputCount"] else "NONE"
            if verdict == "PASS":
                # Final success belongs to every reserved index, including bytes
                # reviewed earlier; recheck inside the same transaction.
                all_artifacts = db.execute("SELECT * FROM hybrid_artifact WHERE request_id=?", (rid,)).fetchall()
                if len(all_artifacts) != auth.specification.outputCount:
                    raise StoreError(409, "Aggregate QA requires every reserved output index")
                for prior in all_artifacts:
                    if not self._effective_acceptance(db, prior["id"], prior["accepted"]):
                        raise StoreError(409, "Aggregate QA requires valid association for every output")
                    self._validate_authorization(db, auth, actor, performed_at=json.loads(prior["receipt"])["performedAt"])
                    prior_path = Path(prior["path"])
                    if not prior_path.resolve().is_relative_to(self.root) or not prior_path.is_file():
                        raise StoreError(409, "Aggregate QA output bytes are unavailable")
                    with prior_path.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != prior["sha256"]:
                            raise StoreError(409, "Aggregate QA artifact bytes changed after earlier review")
            db.execute("UPDATE request SET qa_state=?,state_version=state_version+1,updated_at=? WHERE id=?", (verdict, utc_now().isoformat(), rid))
            if verdict == "PASS":
                db.execute("UPDATE request SET status='COMPLETED',error_message=NULL WHERE id=?", (rid,))
            elif verdict == "FAIL":
                db.execute("UPDATE request SET status='FAILED',execution_state='FAILED',error_message='Explicit visual QA FAIL; no retry is authorized' WHERE id=?", (rid,))
            self._event(db, rid, actor, "QA", receipt.model_dump())
            return {**receipt.model_dump(), "reviewedBy": actor, "request": self._public(self._owned_request(db, rid, actor))}

    def owned_file(self, kind: str, identity: str, actor: str):
        with self.connection() as db:
            table = "hybrid_input" if kind == "input" else "hybrid_artifact"
            row = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
            if row is None:
                raise StoreError(404, "Owned media not found")
            if row["actor_id"] != actor:
                raise StoreError(403, "Media is outside this actor's ownership")
            path = Path(row["path"])
            if not path.resolve().is_relative_to(self.root) or not path.is_file():
                raise StoreError(409, "Owned media bytes are unavailable")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != row["sha256"]:
                    raise StoreError(409, "Immutable artifact bytes changed")
            return path
