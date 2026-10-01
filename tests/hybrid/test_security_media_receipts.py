import copy
import json
import sys
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from agent.hybrid.contracts import QA_BY_MODE
from .conftest import PNG, TOKEN, admit, authorization, envelope, receipt_metadata, run, upload, video_bytes


def post_receipt(client, row, data=None, metadata=None):
    return client.post(f"/api/requests/{row['id']}/receipts", data={"metadata": json.dumps(metadata or receipt_metadata(row))}, files={"file": ("untrusted-filename.mp4", data if data is not None else video_bytes(), "video/mp4")})


def post_qa(client, rid, artifact, verdict="PASS", checks=None):
    return client.post(f"/api/requests/{rid}/qa", json={"qaReceiptId": "qa-" + artifact["artifactId"], "artifactId": artifact["artifactId"], "artifactSha256": artifact["sha256"], "verdict": verdict, "checks": checks if checks is not None else {name: verdict for name in QA_BY_MODE["I2V_PRODUCT_ONLY"]}, "notes": "Explicit review of synthetic fixture; no live acceptance claim."})


@pytest.mark.parametrize("headers,status", [({"Authorization": "Bearer wrong"}, 401), ({"Origin": "http://localhost.evil:8100"}, 403), ({"Origin": "http://127.0.0.1:8100.evil"}, 403), ({"Host": "localhost.evil:8100"}, 403), ({"X-Session-ID": "unknown-session"}, 401)])
def test_http_guards(harness, headers, status):
    client, _, _ = harness
    assert client.get("/api/requests", headers=headers).status_code == status
    assert client.post("/api/ext/callback", headers=headers, json={"id": "anything", "status": "COMPLETED"}).status_code == status


def test_session_principal_and_no_callback_completion(harness):
    client, store, _ = harness
    assert client.post("/api/sessions", json={"actorId": "Boss"}).status_code == 403
    row = run(client, admit(client, [authorization([upload(client)])])[0])
    assert client.post("/api/ext/callback", json={"id": row["id"], "status": "COMPLETED", "data": {"url": "forged"}}).status_code == 410
    assert store.get(row["id"], "local-operator")["executionState"] == "MANUAL_REQUIRED"
    assert client.post("/api/flow/generate-video", json={}).status_code == 410
    assert client.post("/api/requests", json={"type": "GENERATE_VIDEO"}).status_code == 410


@pytest.mark.parametrize("headers,code", [({"Authorization": "Bearer wrong"}, 4401), ({"Origin": "http://localhost.evil:8100"}, 4403), ({"X-Session-ID": "wrong-session"}, 4401)])
def test_websocket_guard(harness, headers, code):
    client, _, _ = harness
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("ws://127.0.0.1:8100/ws/dashboard", headers=headers):
            pass
    assert error.value.code == code


def test_ws_status_only_no_callback_secret(harness):
    client, _, _ = harness
    with client.websocket_connect("ws://127.0.0.1:8100/ws/dashboard") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert "callback_secret" not in json.dumps(snapshot)
        assert TOKEN not in json.dumps(snapshot)
        ws.send_text("provider_complete")
        assert ws.receive_json()["type"] == "error"
        ws.send_text("snapshot")
        assert ws.receive_json()["type"] == "snapshot"


def test_static_exemptions_are_explicit(harness):
    client, _, _ = harness
    no_auth = {"Authorization": "", "X-Session-ID": ""}
    assert client.get("/protocol/canonical-json.mjs", headers=no_auth).status_code == 200
    assert client.get("/protocol/canonical-json.test.mjs", headers=no_auth).status_code == 401
    assert client.get("/hybrid/../agent/main.py", headers=no_auth).status_code == 401
    assert client.post("/hybrid/app.mjs", headers=no_auth).status_code == 401
    assert client.get("/protocol/canonical-json.mjs", headers={"Origin": "http://evil.invalid"}).status_code == 403


def test_actual_decode_and_private_input_read(harness):
    client, _, _ = harness
    response = client.post("/api/inputs/upload", data={"projectId": "project-1", "role": "PRODUCT_IMAGE", "contentClass": "PRODUCT_ONLY", "attestation": "Classified explicitly"}, files={"file": ("fake.png", b"\x89PNG\r\n\x1a\nnot-an-image", "image/png")})
    assert response.status_code == 422
    assert client.get("/api/inputs/unregistered/bytes", headers={"Authorization": ""}).status_code == 401


@pytest.mark.parametrize("kind", ["invalid", "dimensions", "audio", "duration"])
def test_output_byte_probe_rejects_false_metadata(harness, kind):
    client, store, root = harness
    data = b"not-a-video" if kind == "invalid" else video_bytes(16, 16) if kind == "dimensions" else video_bytes(audio=True) if kind == "audio" else video_bytes(seconds=2)
    row = run(client, admit(client, [authorization([upload(client)])])[0])
    response = post_receipt(client, row, data)
    assert response.status_code == 422, response.text
    assert store.get(row["id"], "local-operator")["executionState"] == "MANUAL_REQUIRED"
    assert not list((root / "output").iterdir())


def test_receipt_session_lane_replay_and_cancel_late_result(harness):
    client, store, _ = harness
    row = run(client, admit(client, [authorization([upload(client)])])[0])
    metadata = receipt_metadata(row)
    wrong = copy.deepcopy(metadata)
    wrong["laneRevision"] = 2
    assert post_receipt(client, row, metadata=wrong).status_code == 409
    original_session = client.headers["X-Session-ID"]
    replacement = client.post("/api/sessions", json={"actorId": "local-operator"}).json()["sessionId"]
    client.headers["X-Session-ID"] = replacement
    assert post_receipt(client, row, metadata=metadata).status_code == 403
    client.headers["X-Session-ID"] = original_session
    canceled = client.post(f"/api/requests/{row['id']}/cancel", json={"expectedStateVersion": row["stateVersion"], "reason": "Explicit fixture cancellation"}).json()
    metadata["expectedStateVersion"] = canceled["stateVersion"]
    response = post_receipt(client, row, metadata=metadata)
    assert response.status_code == 200, response.text
    assert not response.json()["artifact"]["accepted"]
    assert response.json()["request"]["executionState"] == "CANCELED"
    assert response.json()["request"]["status"] == "FAILED"
    metadata["expectedStateVersion"] = response.json()["request"]["stateVersion"]
    assert post_receipt(client, row, metadata=metadata).status_code == 409
    assert store.get(row["id"], "local-operator")["status"] != "COMPLETED"


def test_unknown_cost_is_evidence_not_zero(harness):
    client, _, _ = harness
    row = run(client, admit(client, [authorization([upload(client)], cost=0)])[0])
    metadata = receipt_metadata(row)
    metadata.update(costStatus="UNKNOWN", observedCostUnits=None)
    response = post_receipt(client, row, metadata=metadata)
    assert response.status_code == 200
    assert response.json()["request"]["executionState"] == "OUTCOME_UNKNOWN"
    assert not response.json()["artifact"]["accepted"]
    assert client.get("/api/providers/status").json()["balance"]["status"] == "unknown"


def test_complete_preview_qa_and_exact_promotion(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    reserved = run(client, admit(client, [auth])[0])
    response = post_receipt(client, reserved)
    assert response.status_code == 200, response.text
    artifact, row = response.json()["artifact"], response.json()["request"]
    assert row["executionState"] == "OUTPUT_RECEIVED" and row["qaState"] == "NONE"
    assert artifact["metadata"]["width"] == 720 and artifact["metadata"]["height"] == 1280
    assert artifact["metadata"]["decodedFrames"] == 2
    assert client.get(artifact["bytesUrl"], headers={"Authorization": ""}).status_code == 401
    assert client.get(artifact["bytesUrl"]).status_code == 200
    assert store.get(row["id"], "local-operator")["handoff"]["reservationId"] == reserved["handoff"]["reservationId"]
    promotion = copy.deepcopy(auth)
    promotion["authorizationId"] = "auth-promotion"
    promotion["scope"]["batchId"] = "batch-promotion"
    promotion["scope"]["budget"] = {"budgetId": "promotion-budget", "maxGenerations": 1, "maxCostUnits": 0, "costUnitsPerGeneration": 0}
    promotion["specification"].update(action="1080-promotion", resolution="1080x1920", previewRequestId=row["id"], previewArtifactId=artifact["artifactId"], previewReceiptId=artifact["receiptId"], previewArtifactSha256=artifact["sha256"], qaReceiptId="qa-" + artifact["artifactId"])
    assert client.post("/api/authorizations", json=promotion).status_code == 409
    assert post_qa(client, row["id"], artifact, checks={"productIdentity": "PASS"}).status_code == 422
    qa = post_qa(client, row["id"], artifact)
    assert qa.status_code == 200, qa.text
    assert qa.json()["reviewedBy"] == "local-operator"
    assert qa.json()["request"]["qaState"] == "PASS"
    assert post_qa(client, row["id"], artifact).status_code == 409
    wrong = copy.deepcopy(promotion)
    wrong["scope"]["topicId"] = "other-topic"
    assert client.post("/api/authorizations", json=wrong).status_code == 409
    promoted = run(client, admit(client, [promotion])[0])
    result = post_receipt(client, promoted, video_bytes(1080, 1920), receipt_metadata(promoted, receipt="promotion-receipt"))
    assert result.status_code == 200, result.text
    assert result.json()["artifact"]["metadata"]["width"] == 1080
    assert result.json()["artifact"]["metadata"]["height"] == 1920
    assert result.json()["request"]["executionState"] == "OUTPUT_RECEIVED"


def test_safe_import_and_inert_extension():
    import agent.main
    assert not any(name.startswith(("agent.worker", "agent.sdk", "agent.services", "agent.config")) for name in sys.modules)
    assert all(agent.main.app._telemetry[key] is False for key in ("tracing", "metrics", "logs", "operation_spans", "auto_configure"))
    manifest = json.loads((Path(__file__).resolve().parents[2] / "extension/manifest.json").read_text(encoding="utf-8"))
    assert manifest["permissions"] == [] and manifest["host_permissions"] == []
    assert not set(manifest).intersection({"background", "content_scripts", "web_accessible_resources", "declarative_net_request", "side_panel", "action"})


def test_session_renewal_preserves_existing_attempt(harness, monkeypatch):
    client, store, _ = harness
    row = run(client, admit(client, [authorization([upload(client)])])[0])
    import agent.hybrid.store as module
    from datetime import timedelta
    real_now = module.utc_now
    monkeypatch.setattr(module, "utc_now", lambda: real_now() + timedelta(hours=2))
    session_id = client.headers["X-Session-ID"]
    assert client.get("/api/requests").status_code == 401
    assert client.post(f"/api/sessions/{session_id}/renew", headers={"Authorization": "Bearer wrong"}).status_code == 401
    renewed = client.post(f"/api/sessions/{session_id}/renew")
    assert renewed.status_code == 200
    assert renewed.json()["sessionId"] == session_id
    assert client.get(f"/api/requests/{row['id']}").json()["handoff"]["reservationId"] == row["handoff"]["reservationId"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1


def test_qa_fail_cannot_report_batch_success(harness):
    client, _, _ = harness
    reserved = run(client, admit(client, [authorization([upload(client)])])[0])
    response = post_receipt(client, reserved)
    assert response.status_code == 200
    artifact, row = response.json()["artifact"], response.json()["request"]
    before = client.get("/api/requests/batch-status?batchId=batch-1").json()
    assert not before["done"] and not before["all_succeeded"]
    assert row["status"] == "PROCESSING"
    failed = post_qa(client, row["id"], artifact, verdict="FAIL")
    assert failed.status_code == 200
    after = client.get("/api/requests/batch-status?batchId=batch-1").json()
    assert after["done"] and not after["all_succeeded"]
    assert failed.json()["request"]["executionState"] == "FAILED"


def test_input_drift_after_reservation_keeps_unassociated_evidence(harness):
    client, store, _ = harness
    binding = upload(client)
    row = run(client, admit(client, [authorization([binding])])[0])
    with store.connection() as db:
        path = Path(db.execute("SELECT path FROM hybrid_input WHERE id=?", (binding["assetId"],)).fetchone()[0])
    path.write_bytes(b"drift after reservation")
    response = post_receipt(client, row)
    assert response.status_code == 200
    assert not response.json()["artifact"]["accepted"]
    assert response.json()["request"]["executionState"] == "OUTCOME_UNKNOWN"
    assert "Input bytes changed" in response.json()["request"]["error_message"]
    assert client.get(response.json()["artifact"]["bytesUrl"]).status_code == 200


def test_preview_byte_drift_blocks_promotion(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    row = run(client, admit(client, [auth])[0])
    received = post_receipt(client, row).json()
    artifact = received["artifact"]
    assert post_qa(client, row["id"], artifact).status_code == 200
    promotion = copy.deepcopy(auth)
    promotion["authorizationId"] = "drift-promotion"
    promotion["scope"]["batchId"] = "drift-promotion-batch"
    promotion["specification"].update(action="1080-promotion", resolution="1080x1920", previewRequestId=row["id"], previewArtifactId=artifact["artifactId"], previewReceiptId=artifact["receiptId"], previewArtifactSha256=artifact["sha256"], qaReceiptId="qa-" + artifact["artifactId"])
    with store.connection() as db:
        path = Path(db.execute("SELECT path FROM hybrid_artifact WHERE id=?", (artifact["artifactId"],)).fetchone()[0])
    path.write_bytes(b"drift after QA")
    response = client.post("/api/authorizations", json=promotion)
    assert response.status_code == 409
    assert "preview bytes changed" in response.json()["detail"]


@pytest.mark.parametrize("target", ["artifact", "input"])
def test_qa_cannot_complete_after_actual_byte_drift(harness, target):
    client, store, _ = harness
    binding = upload(client)
    row = run(client, admit(client, [authorization([binding])])[0])
    received = post_receipt(client, row).json()
    artifact = received["artifact"]
    with store.connection() as db:
        table, identity = ("hybrid_artifact", artifact["artifactId"]) if target == "artifact" else ("hybrid_input", binding["assetId"])
        path = Path(db.execute(f"SELECT path FROM {table} WHERE id=?", (identity,)).fetchone()[0])
    path.write_bytes(b"changed before QA")
    response = post_qa(client, row["id"], artifact)
    assert response.status_code == 409
    current = store.get(row["id"], "local-operator")
    assert current["status"] == "PROCESSING" and current["qaState"] == "NONE"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_qa").fetchone()[0] == 0


def clarification_body(row, artifact, cost=1):
    return {"expectedStateVersion": row["stateVersion"], "reason": "Cost and exact output confirmed from operator evidence.", "outcome": "OUTPUT_RECEIVED", "evidenceRef": "explicit-cost-clarification", "clarification": {"artifactId": artifact["artifactId"], "artifactSha256": artifact["sha256"], "receiptId": artifact["receiptId"], "reservationId": artifact["reservationId"], "observedGenerations": row["specification"]["outputCount"], "observedCostUnits": cost}}


def unknown_receipt(client, row, index=1):
    metadata = receipt_metadata(row, index=index, receipt=f"unknown-receipt-{index}")
    metadata.update(costStatus="UNKNOWN", observedCostUnits=None)
    response = post_receipt(client, row, metadata=metadata)
    assert response.status_code == 200, response.text
    return response.json()


def test_unknown_cost_reconcile_same_attempt_then_explicit_qa(harness):
    client, store, _ = harness
    reserved = run(client, admit(client, [authorization([upload(client)])])[0])
    retained = unknown_receipt(client, reserved)
    body = clarification_body(retained["request"], retained["artifact"])
    response = client.post(f"/api/requests/{reserved['id']}/reconcile", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["executionState"] == "OUTPUT_RECEIVED"
    assert response.json()["status"] == "PROCESSING" and response.json()["qaState"] == "NONE"
    assert response.json()["artifact"]["accepted"] and not response.json()["artifact"]["originalReceiptAccepted"]
    assert client.post(f"/api/requests/{reserved['id']}/reconcile", json=body).status_code == 409
    assert post_qa(client, reserved["id"], retained["artifact"]).status_code == 200
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1
        original = db.execute("SELECT accepted,receipt FROM hybrid_artifact").fetchone()
        assert original["accepted"] == 0 and json.loads(original["receipt"])["costStatus"] == "UNKNOWN"
        assert db.execute("SELECT COUNT(*) FROM hybrid_event WHERE kind='ASSOCIATION'").fetchone()[0] == 1


@pytest.mark.parametrize("invalid", ["session", "sha", "cost", "artifact-drift", "input-drift", "cancel"])
def test_clarification_rejects_wrong_association_without_new_attempt(harness, invalid):
    client, store, _ = harness
    binding = upload(client)
    reserved = run(client, admit(client, [authorization([binding])])[0])
    retained = unknown_receipt(client, reserved)
    body = clarification_body(retained["request"], retained["artifact"])
    status = 403 if invalid == "session" else 409
    if invalid == "session":
        client.headers["X-Session-ID"] = client.post("/api/sessions", json={"actorId": "local-operator"}).json()["sessionId"]
    elif invalid == "sha":
        body["clarification"]["artifactSha256"] = "0" * 64
    elif invalid == "cost":
        body["clarification"]["observedCostUnits"] = 2
    elif invalid in {"artifact-drift", "input-drift"}:
        table, identity = ("hybrid_artifact", retained["artifact"]["artifactId"]) if invalid == "artifact-drift" else ("hybrid_input", binding["assetId"])
        with store.connection() as db:
            path = Path(db.execute(f"SELECT path FROM {table} WHERE id=?", (identity,)).fetchone()[0])
        path.write_bytes(b"clarification byte drift")
    else:
        canceled = client.post(f"/api/requests/{reserved['id']}/cancel", json={"expectedStateVersion": retained["request"]["stateVersion"], "reason": "Cancel unresolved fixture"}).json()
        body["expectedStateVersion"] = canceled["stateVersion"]
    response = client.post(f"/api/requests/{reserved['id']}/reconcile", json=body)
    assert response.status_code == status, response.text
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM hybrid_event WHERE kind='ASSOCIATION'").fetchone()[0] == 0


def test_multi_output_clarification_waits_for_all_indices(harness):
    client, store, _ = harness
    reserved = run(client, admit(client, [authorization([upload(client)], count=2)])[0])
    one = unknown_receipt(client, reserved)
    current = {**reserved, "stateVersion": one["request"]["stateVersion"]}
    two = unknown_receipt(client, current, index=2)
    body_one = clarification_body(two["request"], one["artifact"], cost=2)
    first = client.post(f"/api/requests/{reserved['id']}/reconcile", json=body_one)
    assert first.status_code == 200, first.text
    assert first.json()["executionState"] == "OUTCOME_UNKNOWN"
    assert first.json()["status"] == "PROCESSING"
    assert post_qa(client, reserved["id"], one["artifact"]).status_code == 409
    second = client.post(f"/api/requests/{reserved['id']}/reconcile", json=clarification_body(first.json(), two["artifact"], cost=2))
    assert second.status_code == 200, second.text
    assert second.json()["executionState"] == "OUTPUT_RECEIVED" and second.json()["qaState"] == "NONE"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1


def test_aggregate_qa_rehashes_already_reviewed_output(harness):
    client, store, _ = harness
    reserved = run(client, admit(client, [authorization([upload(client)], count=2)])[0])
    one = post_receipt(client, reserved).json()
    next_row = {**reserved, "stateVersion": one["request"]["stateVersion"]}
    two = post_receipt(client, next_row, metadata=receipt_metadata(next_row, index=2, receipt="receipt-two")).json()
    assert two["request"]["executionState"] == "OUTPUT_RECEIVED"
    first_qa = post_qa(client, reserved["id"], one["artifact"])
    assert first_qa.status_code == 200 and first_qa.json()["request"]["qaState"] == "NONE"
    with store.connection() as db:
        path = Path(db.execute("SELECT path FROM hybrid_artifact WHERE id=?", (one["artifact"]["artifactId"],)).fetchone()[0])
    path.write_bytes(b"drift in earlier reviewed output")
    second_qa = post_qa(client, reserved["id"], two["artifact"])
    assert second_qa.status_code == 409
    assert "Aggregate QA artifact bytes changed" in second_qa.json()["detail"]
    current = store.get(reserved["id"], "local-operator")
    assert current["status"] == "PROCESSING" and current["qaState"] == "NONE"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_qa").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1


def test_review_after_expiry_uses_original_performed_authority(harness, monkeypatch):
    client, store, _ = harness
    reserved = run(client, admit(client, [authorization([upload(client)])])[0])
    metadata = receipt_metadata(reserved)
    from datetime import timedelta
    import agent.hybrid.store as module
    original = module.utc_now
    monkeypatch.setattr(module, "utc_now", lambda: original() + timedelta(hours=2))
    session_id = client.headers["X-Session-ID"]
    assert client.post(f"/api/sessions/{session_id}/renew").status_code == 200
    response = post_receipt(client, reserved, metadata=metadata)
    assert response.status_code == 200, response.text
    assert response.json()["artifact"]["accepted"]
    assert post_qa(client, reserved["id"], response.json()["artifact"]).status_code == 200
    assert store.get(reserved["id"], "local-operator")["status"] == "COMPLETED"


def test_promotion_rehashes_all_preview_outputs(harness):
    client, store, _ = harness
    auth = authorization([upload(client)], count=2)
    reserved = run(client, admit(client, [auth])[0])
    one = post_receipt(client, reserved).json()
    next_row = {**reserved, "stateVersion": one["request"]["stateVersion"]}
    two = post_receipt(client, next_row, metadata=receipt_metadata(next_row, index=2, receipt="second-preview-receipt")).json()
    assert post_qa(client, reserved["id"], one["artifact"]).status_code == 200
    assert post_qa(client, reserved["id"], two["artifact"]).status_code == 200
    promotion = copy.deepcopy(auth)
    promotion["authorizationId"] = "multi-output-promotion"
    promotion["scope"]["batchId"] = "multi-output-promotion-batch"
    promotion["specification"].update(action="1080-promotion", resolution="1080x1920", previewRequestId=reserved["id"], previewArtifactId=two["artifact"]["artifactId"], previewReceiptId=two["artifact"]["receiptId"], previewArtifactSha256=two["artifact"]["sha256"], qaReceiptId="qa-" + two["artifact"]["artifactId"])
    with store.connection() as db:
        path = Path(db.execute("SELECT path FROM hybrid_artifact WHERE id=?", (one["artifact"]["artifactId"],)).fetchone()[0])
    path.write_bytes(b"drift in other preview after aggregate QA")
    response = client.post("/api/authorizations", json=promotion)
    assert response.status_code == 409, response.text
    assert "aggregate preview bytes changed" in response.json()["detail"]
