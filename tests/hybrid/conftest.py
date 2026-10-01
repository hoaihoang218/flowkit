"""Synthetic local media and fresh real SQLite; never live acceptance."""
import base64
import io
from datetime import timedelta
from fractions import Fraction

import av
import pytest
from fastapi.testclient import TestClient

from agent.hybrid.api import create_app
from agent.hybrid.contracts import fingerprint, utc_now
from agent.hybrid.security import Settings

TOKEN = "synthetic-test-token-" + "x" * 40
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def video_bytes(width=720, height=1280, seconds=1, audio=False):
    buffer = io.BytesIO()
    with av.open(buffer, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"preset": "ultrafast", "crf": "40"}
        sound = container.add_stream("aac", rate=48000) if audio else None
        if sound:
            sound.layout = "mono"
        for index in range(seconds * 2):
            frame = av.VideoFrame(width, height, "rgb24")
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
        if audio:
            frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
            frame.sample_rate, frame.pts = 48000, 0
            frame.time_base = Fraction(1, 48000)
            frame.planes[0].update(bytes(frame.planes[0].buffer_size))
            for packet in sound.encode(frame):
                container.mux(packet)
            for packet in sound.encode():
                container.mux(packet)
    return buffer.getvalue()


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "AFF" / "flowkit"
    app = create_app(Settings(root, token=TOKEN))
    with TestClient(app, base_url="http://127.0.0.1:8100", client=("127.0.0.1", 22222)) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        session = client.post("/api/sessions", json={"actorId": "local-operator"})
        assert session.status_code == 200, session.text
        client.headers["X-Session-ID"] = session.json()["sessionId"]
        yield client, app.state.store, root


def upload(client, role="PRODUCT_IMAGE", data=PNG, project="project-1"):
    content = "PRODUCT_ONLY" if role == "PRODUCT_IMAGE" else "SOURCE_MOTION" if role == "SOURCE_MOTION" else "PERSON"
    response = client.post("/api/inputs/upload", data={"projectId": project, "role": role, "contentClass": content, "attestation": "Synthetic local fixture; required content classification reviewed."}, files={"file": ("ignored.png", data, "image/png")})
    assert response.status_code == 200, response.text
    return {key: response.json()[key] for key in ("assetId", "sha256", "role")}


def authorization(bindings, lane="lane-1", order=1, batch="batch-1", budget="budget-1", count=1, mode="I2V_PRODUCT_ONLY", cost=1):
    spec = {"action": "preview-generation", "prompt": "Show the exact supplied product with restrained movement.", "model": "manually-verified-model", "mode": mode, "resolution": "720x1280", "orientation": "VERTICAL", "audioPolicy": "SILENT", "durationSeconds": 1.0, "outputCount": count, "inputAuthority": bindings, "previewArtifactSha256": None, "previewRequestId": None, "previewArtifactId": None, "previewReceiptId": None, "qaReceiptId": None}
    scope = {"projectId": "project-1", "productId": "product-1", "contentId": "CID-1", "allocationId": "allocation-1", "topicId": "topic-1", "topicRevision": 1, "format": "A1", "jobId": "job-1", "jobRevision": 1, "batchId": batch, "batchRevision": 1, "laneId": lane, "laneRevision": 1, "order": order, "outfitPackId": "outfit-1", "outfitPackRevision": 1, "outfitHash": "1" * 64, "p02ReceiptId": "p02-1", "p02Fingerprint": "2" * 64, "p02Revision": 1, "inputHashes": bindings, "sourceWindow": {"startSeconds": 0.0, "endSeconds": 1.0} if mode.startswith("V2V") else None, "profileId": "profile-1", "flowProjectId": "flow-project-1", "budget": {"budgetId": budget, "maxGenerations": 4, "maxCostUnits": 4 * cost, "costUnitsPerGeneration": cost}}
    return {"authorizationId": "auth-" + batch + "-" + lane, "approvedBy": "local-operator", "approvalRef": "explicit-local-fixture-approval", "approvedAt": (utc_now() - timedelta(minutes=1)).isoformat(), "expiresAt": (utc_now() + timedelta(hours=1)).isoformat(), "scope": scope, "specification": spec}


def envelope(auth, key=None):
    scope = auth["scope"]
    result = {**auth["specification"], **{name: scope[name] for name in ("batchId", "batchRevision", "laneId", "laneRevision", "order")}, "authorization": auth}
    result["payloadSha256"] = fingerprint(result)
    result["idempotencyKey"] = key or "key-" + auth["authorizationId"]
    return result


def admit(client, auths):
    for auth in auths:
        response = client.post("/api/authorizations", json=auth)
        assert response.status_code == 200, response.text
    response = client.post("/api/requests/batch", json={"requests": [envelope(auth) for auth in auths]})
    assert response.status_code == 200, response.text
    return response.json()


def run(client, row):
    response = client.post(f"/api/requests/batches/{row['batch_id']}/run", json={"requestId": row["id"], "expectedStateVersion": row["stateVersion"]})
    assert response.status_code == 200, response.text
    return response.json()


def receipt_metadata(row, index=1, receipt="receipt-1"):
    return {"receiptId": receipt, "reservationId": row["handoff"]["reservationId"], "attempt": 1, "payloadSha256": row["payload_sha256"], "laneId": row["lane_id"], "laneRevision": row["lane_revision"], "expectedStateVersion": row["stateVersion"], "outputIndex": index, "performedAt": utc_now().isoformat(), "evidenceRef": "operator-provider-ui-observation", "profileId": "profile-1", "flowProjectId": "flow-project-1", "observedGenerations": row["specification"]["outputCount"], "costStatus": "KNOWN", "observedCostUnits": row["specification"]["outputCount"] * row["envelope"]["authorization"]["scope"]["budget"]["costUnitsPerGeneration"]}
