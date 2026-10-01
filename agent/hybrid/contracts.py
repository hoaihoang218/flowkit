"""Strict public hybrid-v1 bodies and RFC 8785 payload fingerprints."""
import hashlib
from datetime import datetime, timezone
from typing import Annotated, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Revision = Annotated[int, Field(strict=True, ge=1, le=2147483647)]
Role = Literal["SOURCE_MOTION", "BEFORE", "AFTER", "PRODUCT_IMAGE"]
ExecutionState = Literal["QUEUED", "SUBMITTING", "RUNNING", "OUTPUT_RECEIVED", "MANUAL_REQUIRED", "OUTCOME_UNKNOWN", "FAILED", "CANCELED"]


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    @model_validator(mode="after")
    def canonical_domain(self):
        try:
            canonical(self.model_dump())
        except (rfc8785.CanonicalizationError, UnicodeError, ValueError) as exc:
            raise ValueError("Contract values must be valid RFC8785 Unicode and safe numeric values") from exc
        return self


def canonical(value: dict | list) -> bytes:
    return rfc8785.dumps(value)


def fingerprint(value: dict | list) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


class InputBinding(StrictBody):
    assetId: Identifier
    sha256: Sha256
    role: Role


class SourceWindow(StrictBody):
    startSeconds: Annotated[float, Field(ge=0)]
    endSeconds: Annotated[float, Field(gt=0)]

    @model_validator(mode="after")
    def ordered(self):
        if self.endSeconds <= self.startSeconds:
            raise ValueError("Source window must have positive duration")
        return self


class Budget(StrictBody):
    budgetId: Identifier
    maxGenerations: Annotated[int, Field(strict=True, ge=1, le=100)]
    maxCostUnits: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
    costUnitsPerGeneration: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]


class Scope(StrictBody):
    projectId: Identifier
    productId: Identifier
    contentId: Identifier
    allocationId: Identifier
    topicId: Identifier
    topicRevision: Revision
    format: Identifier
    jobId: Identifier
    jobRevision: Revision
    batchId: Identifier
    batchRevision: Revision
    laneId: Identifier
    laneRevision: Revision
    order: Annotated[int, Field(strict=True, ge=1, le=100)]
    outfitPackId: Identifier
    outfitPackRevision: Revision
    outfitHash: Sha256
    p02ReceiptId: Identifier
    p02Fingerprint: Sha256
    p02Revision: Revision
    inputHashes: list[InputBinding] = Field(min_length=1, max_length=3)
    sourceWindow: SourceWindow | None
    profileId: Identifier
    flowProjectId: Identifier
    budget: Budget


class Specification(StrictBody):
    action: Literal["preview-generation", "1080-promotion"]
    prompt: Annotated[str, Field(min_length=1, max_length=12000)]
    model: Identifier
    mode: Literal["V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON", "I2V_PRODUCT_ONLY"]
    resolution: Literal["720x1280", "1080x1920"]
    orientation: Literal["VERTICAL"]
    audioPolicy: Literal["SILENT"]
    durationSeconds: Annotated[float, Field(gt=0, le=60)]
    outputCount: Annotated[int, Field(strict=True, ge=1, le=4)]
    inputAuthority: list[InputBinding] = Field(min_length=1, max_length=3)
    previewArtifactSha256: Sha256 | None
    previewRequestId: Identifier | None
    previewArtifactId: Identifier | None
    previewReceiptId: Identifier | None
    qaReceiptId: Identifier | None

    @model_validator(mode="after")
    def exact_mode(self):
        roles = [x.role for x in self.inputAuthority]
        expected = {"SOURCE_MOTION", "BEFORE", "AFTER"} if self.mode == "V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON" else {"PRODUCT_IMAGE"}
        if len(roles) != len(expected) or set(roles) != expected:
            raise ValueError("Input roles must exactly match mode; fallback is prohibited")
        if len({x.assetId for x in self.inputAuthority}) != len(roles):
            raise ValueError("Each input role needs its own immutable asset")
        if self.action == "preview-generation":
            if self.resolution != "720x1280" or any(x is not None for x in (self.previewArtifactSha256, self.previewRequestId, self.previewArtifactId, self.previewReceiptId, self.qaReceiptId)):
                raise ValueError("Preview must be 720x1280 silent without promotion references")
        elif self.resolution != "1080x1920" or not all((self.previewArtifactSha256, self.previewRequestId, self.previewArtifactId, self.previewReceiptId, self.qaReceiptId)):
            raise ValueError("Promotion requires 1080x1920 and exact preview SHA/QA receipt")
        return self


class Authorization(StrictBody):
    authorizationId: Identifier
    approvedBy: Identifier
    approvalRef: Identifier
    approvedAt: str
    expiresAt: str
    scope: Scope
    specification: Specification

    @model_validator(mode="after")
    def exact_scope(self):
        if timestamp(self.expiresAt) <= timestamp(self.approvedAt):
            raise ValueError("Authorization expiry must follow approval")
        if self.scope.inputHashes != self.specification.inputAuthority:
            raise ValueError("Authorization input hashes must match exact specification inputs")
        if self.specification.mode == "V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON" and self.scope.sourceWindow is None:
            raise ValueError("Source motion requires an approved source window")
        if self.specification.mode == "I2V_PRODUCT_ONLY" and self.scope.sourceWindow is not None:
            raise ValueError("Product-only mode cannot carry a source-motion window")
        return self


class Envelope(Specification):
    idempotencyKey: Identifier
    payloadSha256: Sha256
    batchId: Identifier
    batchRevision: Revision
    laneId: Identifier
    laneRevision: Revision
    order: Annotated[int, Field(strict=True, ge=1, le=100)]
    authorization: Authorization

    @model_validator(mode="after")
    def exact_authorization(self):
        data = self.model_dump()
        spec = {name: data[name] for name in Specification.model_fields}
        if spec != self.authorization.specification.model_dump():
            raise ValueError("Envelope specification differs from authorization")
        for name in ("batchId", "batchRevision", "laneId", "laneRevision", "order"):
            if data[name] != getattr(self.authorization.scope, name):
                raise ValueError(f"Envelope {name} differs from authorization")
        expected = fingerprint({k: v for k, v in data.items() if k not in {"payloadSha256", "idempotencyKey"}})
        if expected != self.payloadSha256:
            raise ValueError("payloadSha256 does not match RFC8785 canonical payload")
        return self


class Batch(StrictBody):
    requests: list[Envelope] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_product(self):
        first = self.requests[0]
        for item in self.requests:
            if (item.batchId, item.batchRevision, item.authorization.scope.projectId, item.authorization.scope.productId) != (first.batchId, first.batchRevision, first.authorization.scope.projectId, first.authorization.scope.productId):
                raise ValueError("A batch must have one project, product and revision")
        if [x.order for x in self.requests] != list(range(1, len(self.requests) + 1)):
            raise ValueError("Batch order must be contiguous and start at 1")
        identities = {(x.authorization.scope.projectId, x.authorization.scope.productId, x.authorization.scope.contentId, x.authorization.scope.jobId, x.authorization.scope.jobRevision, x.laneId, x.laneRevision, x.action) for x in self.requests}
        if len({x.idempotencyKey for x in self.requests}) != len(self.requests) or len(identities) != len(self.requests):
            raise ValueError("Duplicate batch identities are prohibited")
        return self


class SessionCreate(StrictBody):
    actorId: Identifier


class RunBatch(StrictBody):
    requestId: Identifier
    expectedStateVersion: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]


class StateChange(StrictBody):
    expectedStateVersion: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class Clarification(StrictBody):
    artifactId: Identifier
    artifactSha256: Sha256
    receiptId: Identifier
    reservationId: Identifier
    observedGenerations: Annotated[int, Field(strict=True, ge=1, le=4)]
    observedCostUnits: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]


class Reconcile(StateChange):
    outcome: Literal["FAILED", "OUTCOME_UNKNOWN", "OUTPUT_RECEIVED"]
    evidenceRef: Identifier
    clarification: Clarification | None = None

    @model_validator(mode="after")
    def exact_clarification(self):
        if (self.outcome == "OUTPUT_RECEIVED") != (self.clarification is not None):
            raise ValueError("OUTPUT_RECEIVED requires exact clarification; other outcomes cannot carry it")
        return self


class ReceiptMetadata(StrictBody):
    receiptId: Identifier
    reservationId: Identifier
    attempt: Literal[1]
    payloadSha256: Sha256
    laneId: Identifier
    laneRevision: Revision
    expectedStateVersion: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)]
    outputIndex: Annotated[int, Field(strict=True, ge=1, le=4)]
    performedAt: str
    evidenceRef: Identifier
    profileId: Identifier
    flowProjectId: Identifier
    observedGenerations: Annotated[int, Field(strict=True, ge=1, le=4)]
    costStatus: Literal["KNOWN", "UNKNOWN"]
    observedCostUnits: Annotated[int, Field(strict=True, ge=0, le=9007199254740991)] | None
    providerOperationId: Identifier | None = None
    providerWorkflowId: Identifier | None = None

    @model_validator(mode="after")
    def observed_cost(self):
        timestamp(self.performedAt)
        if (self.costStatus == "KNOWN") != (self.observedCostUnits is not None):
            raise ValueError("Unknown cost cannot be interpreted as zero")
        return self


QA_COMMON = {"productIdentity", "inputFidelity", "modeFidelity", "motionContinuity", "noTextWatermark", "silentAudio", "technicalDimensions"}
QA_BY_MODE = {
    "V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON": QA_COMMON | {"sourceMotionOnly", "beforeIdentity", "afterIdentity", "anatomy", "outfitFidelity"},
    "I2V_PRODUCT_ONLY": QA_COMMON | {"noPersonFaceOutfit", "productOnlyMotion"},
}
QA_CHECKS = set.union(*QA_BY_MODE.values())


class QAReceipt(StrictBody):
    qaReceiptId: Identifier
    artifactId: Identifier
    artifactSha256: Sha256
    verdict: Literal["PASS", "FAIL"]
    checks: dict[str, Literal["PASS", "FAIL", "NOT_APPLICABLE"]]
    notes: Annotated[str, Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def explicit_checks(self):
        if not self.checks or not set(self.checks).issubset(QA_CHECKS):
            raise ValueError("QA requires explicit known visual and technical checks")
        if self.verdict == "PASS" and any(v == "FAIL" for v in self.checks.values()):
            raise ValueError("QA PASS requires every check PASS")
        return self
