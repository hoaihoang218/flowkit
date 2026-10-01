import copy
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import ValidationError

from agent.hybrid.contracts import Batch, Envelope, canonical, fingerprint, utc_now
from agent.hybrid.store import StoreError
from .conftest import PNG, admit, authorization, envelope, receipt_metadata, run, upload, video_bytes


def test_actual_input_hash_and_tampering(harness):
    client, store, root = harness
    binding = upload(client)
    import hashlib
    assert binding["sha256"] == hashlib.sha256(PNG).hexdigest()
    auth = authorization([binding])
    assert client.post("/api/authorizations", json=auth).status_code == 200
    with store.connection() as db:
        path = db.execute("SELECT path FROM hybrid_input WHERE id=?", (binding["assetId"],)).fetchone()[0]
    from pathlib import Path
    Path(path).write_bytes(b"changed test evidence")
    assert client.post("/api/requests/batch", json={"requests": [envelope(auth)]}).status_code == 409
    assert not store.list("local-operator")


def test_sqlite_concurrent_admission_and_reservation(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    assert client.post("/api/authorizations", json=auth).status_code == 200
    batch = Batch.model_validate({"requests": [envelope(auth)]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: store.admit(batch, "local-operator"), range(2)))
    assert results[0][0]["id"] == results[1][0]["id"]
    session = store.session(client.headers["X-Session-ID"])
    row = results[0][0]
    def reserve(_):
        try:
            return store.reserve(row["batch_id"], row["id"], 0, session)
        except StoreError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        reserved = list(pool.map(reserve, range(2)))
    assert sum(isinstance(value, dict) for value in reserved) == 1
    assert 409 in reserved
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM request").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 1
        assert db.execute("SELECT output_count FROM hybrid_attempt").fetchone()[0] == 1


def test_batch_atomic_invalid_authority(harness):
    client, store, _ = harness
    binding = upload(client)
    one = authorization([binding])
    two = authorization([binding], lane="lane-2", order=2)
    assert client.post("/api/authorizations", json=one).status_code == 200
    response = client.post("/api/requests/batch", json={"requests": [envelope(one), envelope(two)]})
    assert response.status_code == 403
    assert not store.list("local-operator")


def test_key_conflict_spec_immutable_and_no_retry(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    row = admit(client, [auth])[0]
    changed = copy.deepcopy(auth)
    changed["authorizationId"] += "-changed"
    changed["specification"]["prompt"] += " Extra movement."
    assert client.post("/api/authorizations", json=changed).status_code == 200
    assert client.post("/api/requests/batch", json={"requests": [envelope(changed, envelope(auth)["idempotencyKey"])]}).status_code == 409
    with store.connection() as db:
        with pytest.raises(Exception):
            db.execute("UPDATE request SET orientation='HORIZONTAL' WHERE id=?", (row["id"],))
    reserved = run(client, row)
    assert reserved["executionState"] == "MANUAL_REQUIRED"
    assert reserved["status"] == "PROCESSING"
    assert reserved["handoff"]["specification"]["prompt"] == auth["specification"]["prompt"]
    assert client.post(f"/api/requests/batches/{row['batch_id']}/run", json={"requestId": row["id"], "expectedStateVersion": reserved["stateVersion"]}).status_code == 409


def test_recovery_never_requeues_and_cas(harness):
    client, store, _ = harness
    row = run(client, admit(client, [authorization([upload(client)])])[0])
    assert store.recover() == 1
    current = store.get(row["id"], "local-operator")
    assert current["executionState"] == "OUTCOME_UNKNOWN"
    assert client.post(f"/api/requests/{row['id']}/cancel", json={"expectedStateVersion": row["stateVersion"], "reason": "stale CAS"}).status_code == 409
    assert client.post(f"/api/requests/batches/{row['batch_id']}/run", json={"requestId": row["id"], "expectedStateVersion": current["stateVersion"]}).status_code == 409
    assert store.recover() == 0


def test_budget_counts_all_outputs_and_zero_cost(harness):
    client, store, _ = harness
    auth = authorization([upload(client)], count=3, cost=0)
    auth["scope"]["budget"]["maxGenerations"] = 2
    row = admit(client, [auth])[0]
    response = client.post(f"/api/requests/batches/{row['batch_id']}/run", json={"requestId": row["id"], "expectedStateVersion": 0})
    assert response.status_code == 409
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM hybrid_attempt").fetchone()[0] == 0


def test_sequential_waits_for_prior_qa(harness):
    client, _, _ = harness
    binding = upload(client)
    rows = admit(client, [authorization([binding]), authorization([binding], lane="lane-2", order=2)])
    response = client.post("/api/requests/batches/batch-1/run", json={"requestId": rows[1]["id"], "expectedStateVersion": 0})
    assert response.status_code == 409
    run(client, rows[0])
    assert client.post("/api/requests/batches/batch-1/run", json={"requestId": rows[1]["id"], "expectedStateVersion": 0}).status_code == 409


@pytest.mark.parametrize("field,value", [("orientation", "HORIZONTAL"), ("mode", "I2V_FALLBACK"), ("audioPolicy", "AUDIO"), ("resolution", "1080x1920")])
def test_exact_policy_rejects_degrade(harness, field, value):
    client, _, _ = harness
    auth = authorization([upload(client)])
    auth["specification"][field] = value
    with pytest.raises(ValidationError):
        Envelope.model_validate(envelope(auth))


def test_person_mode_requires_three_actual_assets(harness):
    client, _, _ = harness
    bindings = [upload(client, "SOURCE_MOTION", video_bytes(16, 16)), upload(client, "BEFORE"), upload(client, "AFTER")]
    auth = authorization(bindings, mode="V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON")
    row = admit(client, [auth])[0]
    assert row["specification"]["mode"].startswith("V2V")
    bad = copy.deepcopy(auth)
    bad["specification"]["inputAuthority"] = bindings[:2]
    bad["scope"]["inputHashes"] = bindings[:2]
    assert client.post("/api/authorizations", json=bad).status_code == 422


def test_expiry_checked_again_at_reservation(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    row = admit(client, [auth])[0]
    import agent.hybrid.store as store_module
    original = store_module.utc_now
    authenticated_session = store.session(client.headers["X-Session-ID"])
    store_module.utc_now = lambda: original() + timedelta(hours=2)
    try:
        with pytest.raises(StoreError) as error:
            store.reserve(row["batch_id"], row["id"], 0, authenticated_session)
        assert error.value.status == 403
    finally:
        store_module.utc_now = original


def test_immutable_replay_survives_authorization_expiry(harness):
    client, store, _ = harness
    auth = authorization([upload(client)])
    row = admit(client, [auth])[0]
    batch = Batch.model_validate({"requests": [envelope(auth)]})
    import agent.hybrid.store as module
    original = module.utc_now
    module.utc_now = lambda: original() + timedelta(hours=2)
    try:
        assert store.admit(batch, "local-operator")[0]["id"] == row["id"]
        conflict = copy.deepcopy(auth)
        conflict["specification"]["prompt"] += " Changed after expiry."
        with pytest.raises(StoreError) as error:
            store.admit(Batch.model_validate({"requests": [envelope(conflict)]}), "local-operator")
        assert error.value.status == 409
    finally:
        module.utc_now = original


def test_same_lane_slug_in_different_job_is_not_same_attempt(harness):
    client, store, _ = harness
    binding = upload(client)
    one = authorization([binding])
    first = admit(client, [one])[0]
    two = authorization([binding], batch="batch-other-job")
    two["scope"]["jobId"] = "job-2"
    two["scope"]["contentId"] = "CID-2"
    second = admit(client, [two])[0]
    assert first["id"] != second["id"]
    third = authorization([binding], batch="batch-same-job-new-key")
    assert client.post("/api/authorizations", json=third).status_code == 200
    assert client.post("/api/requests/batch", json={"requests": [envelope(third)]}).status_code == 409
    assert len(store.list("local-operator")) == 2


def test_same_lane_slug_in_distinct_jobs_inside_one_product_batch(harness):
    client, _, _ = harness
    binding = upload(client)
    one = authorization([binding])
    two = authorization([binding], order=2)
    two["authorizationId"] = "auth-distinct-job-same-lane"
    two["scope"]["jobId"] = "job-2"
    two["scope"]["contentId"] = "CID-2"
    rows = admit(client, [one, two])
    assert len(rows) == 2 and rows[0]["id"] != rows[1]["id"]


def test_shared_javascript_python_canonical_fixtures():
    fixture = Path(__file__).resolve().parents[2] / "docs/fixtures/hybrid-canonical.json"
    for item in json.loads(fixture.read_text(encoding="utf-8"))["fixtures"]:
        assert canonical(item["payload"]).decode("utf-8") == item["canonical"]
        assert fingerprint(item["payload"]) == item["sha256"]


@pytest.mark.parametrize("field", ["maxCostUnits", "costUnitsPerGeneration"])
def test_cost_domain_overflow_is_structured_422(harness, field):
    client, _, _ = harness
    auth = authorization([upload(client)])
    auth["scope"]["budget"][field] = 9007199254740992
    response = client.post("/api/authorizations", json=auth)
    assert response.status_code == 422


def test_invalid_unicode_and_nonfinite_rejected_without_500(harness):
    client, _, _ = harness
    auth = authorization([upload(client)])
    auth["specification"]["prompt"] = "bad surrogate \ud800"
    response = client.post("/api/authorizations", content=json.dumps(auth), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "surrogate" not in response.text
    auth["specification"]["prompt"] = "valid prompt"
    auth["specification"]["durationSeconds"] = float("inf")
    response = client.post("/api/authorizations", content=json.dumps(auth), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
