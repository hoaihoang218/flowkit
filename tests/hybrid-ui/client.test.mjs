import test from 'node:test';
import assert from 'node:assert/strict';
import { Client, ApiError, canMutate, fingerprintBatch, qaChecks, outputClarification } from '../../dashboard/hybrid/client.mjs';
import { payloadSha256 } from '../../protocol/canonical-json.mjs';

const envelope = () => ({ idempotencyKey: 'fixed-key', payloadSha256: '', prompt: 'Dữ liệu giả lập', previewArtifactSha256: null, previewRequestId: null, previewArtifactId: null, previewReceiptId: null, qaReceiptId: null, authorization: { scope: { sourceWindow: null }, specification: { previewArtifactSha256: null, previewRequestId: null, previewArtifactId: null, previewReceiptId: null, qaReceiptId: null } } });
test('fingerprint excludes exactly two fields, preserves null and stable key', async () => {
  const batch = await fingerprintBatch({ requests: [envelope()] }); const row = batch.requests[0];
  const { idempotencyKey, payloadSha256: hash, ...payload } = row;
  assert.equal(hash, await payloadSha256(payload)); assert.equal(idempotencyKey, 'fixed-key');
  assert.equal((await fingerprintBatch(batch)).requests[0].payloadSha256, hash);
  delete row.previewReceiptId; await assert.rejects(() => fingerprintBatch(batch), /null/);
});
test('client sends bearer/session only in headers and multipart without JSON content type', async () => {
  const calls = []; const client = new Client(async (path, options) => { calls.push({ path, options }); return new Response('{}', { status: 200 }); });
  client.token = 'memory-token'; client.sessionId = 'session';
  await client.request('/health', { public: true });
  await client.request('/api/sessions', { method: 'POST', session: false, body: { actorId: 'local-operator' } });
  const form = new FormData(); form.set('metadata','{}'); await client.request('/api/requests/r/receipts', { method: 'POST', body: form });
  assert.deepEqual(calls[0].options.headers, {});
  assert.equal(calls[1].options.headers.Authorization, 'Bearer memory-token'); assert.equal(calls[1].options.headers['X-Session-ID'], undefined);
  assert.equal(calls[2].options.headers['X-Session-ID'], 'session'); assert.equal(calls[2].options.headers['Content-Type'], undefined);
  assert.ok(calls.every(call => !call.path.includes('token') && call.options.cache === 'no-store'));
});
test('conflict surfaces API detail with no retry and invalid routes are refused', async () => {
  let calls = 0; const client = new Client(async () => { calls++; return new Response('{"detail":"State version changed"}', { status: 409 }); }); client.token = 'x'; client.sessionId = 's';
  await assert.rejects(() => client.request('/api/requests/r/cancel', { method: 'POST', body: {} }), e => e instanceof ApiError && e.status === 409);
  assert.equal(calls, 1); await assert.rejects(() => client.request('https://provider.example'), /API local/);
});
test('disconnected, busy, stale, offline and old snapshots block mutations; unknown only permits evidence', () => {
  const ready = { connected: true, busy: false, stale: false, online: true, refreshedAt: 100000, requests: [] };
  assert.equal(canMutate(ready,'run',100010),true);
  for (const change of [{ connected:false },{ busy:true },{ stale:true },{ online:false },{ refreshedAt:0 }]) assert.equal(canMutate({ ...ready,...change },'run',100010),false);
  const unknown = { ...ready, requests: [{ executionState:'OUTCOME_UNKNOWN' }] };
  assert.equal(canMutate(unknown,'run',100010),false); assert.equal(canMutate(unknown,'authorize',100010),false);
  assert.equal(canMutate(unknown,'receipt',100010),true); assert.equal(canMutate(unknown,'reconcile',100010),true);
});
test('QA exact applicable checks keep person and product separate', () => {
  assert.equal(qaChecks('I2V_PRODUCT_ONLY').length,9); assert.equal(qaChecks('V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON').length,12);
  assert.ok(!qaChecks('I2V_PRODUCT_ONLY').includes('beforeIdentity'));
});
test('same-attempt output clarification requires explicit known cost and exact evidence bindings', () => {
  const context = { expectedStateVersion:2,reason:'Đối soát receipt gốc',evidenceRef:'evidence-1' };
  const evidence = { artifactId:'artifact-1',artifactSha256:'a'.repeat(64),receiptId:'receipt-1',reservationId:'reservation-1',observedGenerations:1,observedCostUnits:4 };
  const body = outputClarification(evidence,context);
  assert.deepEqual(body,{...context,outcome:'OUTPUT_RECEIVED',clarification:evidence});
  assert.equal('idempotencyKey' in body,false); assert.equal('attempt' in body,false);
  for (const value of [null,undefined,'0',-1,1.5]) assert.throws(() => outputClarification({...evidence,observedCostUnits:value},context),/chi phí/);
  const missing = {...evidence}; delete missing.observedCostUnits; assert.throws(() => outputClarification(missing,context),/sáu field/);
  assert.throws(() => outputClarification({...evidence,expectedStateVersion:0},context),/sáu field/);
});
