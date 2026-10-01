import { Client, canMutate, fingerprintBatch, qaChecks, outputClarification } from './client.mjs';

// Calm operational workspace: green/neutral tokens, numbered steps, exact JSON inspectors.
// State and success always come from API responses; only the session identifier persists.
const $ = id => document.getElementById(id);
const client = new Client();
const state = { connected: false, busy: false, stale: true, online: navigator.onLine, refreshedAt: 0, requests: [], selected: null };
let mediaUrl = null;
const qaLabels = { productIdentity: 'Đúng sản phẩm', inputFidelity: 'Bám đúng input', modeFidelity: 'Đúng mode đã duyệt', motionContinuity: 'Chuyển động liên tục', noTextWatermark: 'Không chữ hoặc watermark', silentAudio: 'Không có âm thanh', technicalDimensions: 'Đúng kích thước kỹ thuật', sourceMotionOnly: 'Source chỉ cấp chuyển động', beforeIdentity: 'Đúng identity Before', afterIdentity: 'Đúng identity After', anatomy: 'Giải phẫu tự nhiên', outfitFidelity: 'Giữ đúng outfit', noPersonFaceOutfit: 'Không người, mặt hoặc outfit', productOnlyMotion: 'Chuyển động chỉ có sản phẩm' };
const savedSession = () => { try { return sessionStorage.getItem('flowkit.sessionId') ?? ''; } catch { return ''; } };
client.sessionId = savedSession();
const storeSession = id => { try { sessionStorage.setItem('flowkit.sessionId', id); } catch { /* Memory-only continuation remains available. */ } };
const pretty = value => JSON.stringify(value, null, 2);
const parse = id => { try { return JSON.parse($(id).value); } catch { throw new Error(`JSON trong ${id} chưa hợp lệ. Nội dung được giữ nguyên.`); } };
const selected = () => { if (!state.selected) throw new Error('Chọn request trước khi thao tác.'); return state.selected; };
const endpoint = suffix => `/api/requests/${encodeURIComponent(selected().id)}${suffix}`;

function message(text, error = false) {
  $('message').textContent = text;
  $('message').dataset.error = String(error);
  $('message').setAttribute('role', error ? 'alert' : 'status');
}
function controls() {
  const row = state.selected;
  for (const button of document.querySelectorAll('button')) button.disabled = state.busy;
  for (const button of document.querySelectorAll('[data-mutation]')) {
    const kind = button.dataset.mutation;
    let enabled = canMutate(state, kind);
    if (kind === 'authorize') enabled &&= $('approval-confirm').checked;
    if (['run','receipt','reconcile','cancel','qa','download'].includes(kind)) enabled &&= Boolean(row);
    if (kind === 'run') enabled &&= row?.executionState === 'QUEUED' && !state.requests.some(r => ['MANUAL_REQUIRED','OUTCOME_UNKNOWN','SUBMITTING','RUNNING'].includes(r.executionState));
    if (kind === 'receipt') enabled &&= ['MANUAL_REQUIRED','RUNNING','OUTCOME_UNKNOWN','CANCELED','FAILED'].includes(row?.executionState);
    if (kind === 'qa') enabled &&= row?.executionState === 'OUTPUT_RECEIVED' && Boolean($('artifact').value);
    if (kind === 'download') enabled &&= Boolean($('artifact').value);
    if (kind === 'reconcile') enabled &&= ['MANUAL_REQUIRED','RUNNING','SUBMITTING','OUTCOME_UNKNOWN'].includes(row?.executionState);
    if (button.id === 'clarify-output') enabled &&= row?.executionState === 'OUTCOME_UNKNOWN';
    if (kind === 'cancel') enabled &&= !['OUTPUT_RECEIVED','CANCELED','FAILED'].includes(row?.executionState);
    button.disabled = !enabled;
  }
  $('refresh').disabled = state.busy || !state.connected || !state.online;
  $('handoff').disabled = state.busy || !state.selected;
  $('connection').textContent = !state.online ? 'Offline · đã khóa' : !state.connected ? 'Chưa kết nối' : state.busy ? 'Đang xử lý…' : state.stale || Date.now() - state.refreshedAt > 60000 ? 'Cần đọc lại trạng thái' : 'Đã xác thực · manual-only';
  $('freshness').textContent = state.refreshedAt ? `Lần đọc server: ${new Date(state.refreshedAt).toLocaleString('vi-VN')}. Mutation khóa sau 60 giây; đọc lại trước khi thao tác.` : 'Chưa có snapshot server được xác thực.';
}
async function operation(task, { mutation = null, success = '', focusError = true } = {}) {
  // Lock before the first await: double click cannot issue a second mutation.
  if (state.busy) return;
  if (mutation && !canMutate(state, mutation)) { message('Thao tác đã khóa. Đọc lại trạng thái server và đối chiếu outcome trước khi tiếp tục.', true); return; }
  state.busy = true; controls();
  try { await task(); if (success) message(success); }
  catch (error) {
    if (error.status === 401 || error.status === 403) { state.connected = false; client.token = ''; }
    if (mutation || !error.status || error.status === 409) state.stale = true;
    const suffix = mutation ? ' Biểu mẫu được giữ nguyên. Không tự gửi lại; đọc lại trạng thái server để đối chiếu.' : '';
    message(`${error.status ? `HTTP ${error.status}: ` : ''}${error.message}${suffix}`, true);
    if (focusError) { $('message').tabIndex = -1; $('message').focus(); }
  } finally { state.busy = false; controls(); }
}

function renderRows() {
  const tbody = $('requests'); tbody.replaceChildren();
  if (!state.requests.length) { const tr = tbody.insertRow(); const td = tr.insertCell(); td.colSpan = 4; td.textContent = 'Server chưa có request trong phạm vi lọc.'; }
  for (const row of state.requests) {
    const tr = tbody.insertRow(); tr.setAttribute('aria-selected', String(state.selected?.id === row.id));
    for (const value of [`${row.lane_order ?? row.envelope.order} · ${row.lane_id ?? row.envelope.laneId}`, row.action, `${row.executionState} · QA ${row.qaState}`]) tr.insertCell().textContent = value;
    const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Xem'; button.setAttribute('aria-label', `Xem request ${row.id}`);
    button.addEventListener('click', () => operation(async () => {
      const detail = await client.request(`/api/requests/${encodeURIComponent(row.id)}`);
      showRequest(detail); renderRows();
    }));
    tr.insertCell().append(button);
  }
}
function showRequest(row) {
  const changed = state.selected?.id !== row.id;
  state.selected = row;
  $('request-detail').textContent = pretty(row);
  if (changed) { $('receipt').value = ''; $('qa-id').value = ''; $('qa-verdict').value = ''; $('qa-notes').value = ''; $('reason').value = ''; $('evidence').value = ''; $('clarification').value = ''; }
  const artifact = $('artifact'); const oldArtifact = artifact.value; artifact.replaceChildren();
  const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = 'Chọn exact artifact để review'; artifact.append(placeholder);
  for (const item of row.artifacts ?? []) { const option = document.createElement('option'); option.value = item.artifactId; option.textContent = `${item.artifactId} · ${item.accepted ? 'Đã nhận' : 'Chỉ bằng chứng'} · SHA ${item.sha256}`; artifact.append(option); }
  if (!changed && [...artifact.options].some(o => o.value === oldArtifact)) artifact.value = oldArtifact;
  const checks = $('qa-checks');
  if (changed || !checks.children.length) {
    checks.replaceChildren();
    for (const name of qaChecks(row.specification.mode)) {
      const label = document.createElement('label'); label.textContent = `${qaLabels[name]} · ${name}`;
      const select = document.createElement('select'); select.name = name; select.required = true; select.dataset.qa = name;
      for (const value of ['', 'PASS','FAIL']) { const option = document.createElement('option'); option.value = value; option.textContent = value || 'Chọn sau review'; select.append(option); }
      label.append(select); checks.append(label);
    }
  }
  const downloads = $('input-downloads'); downloads.replaceChildren();
  for (const binding of row.specification.inputAuthority) {
    const button = document.createElement('button'); button.type = 'button'; button.textContent = `Tải ${binding.role}`;
    button.addEventListener('click', () => operation(() => download(`/api/inputs/${encodeURIComponent(binding.assetId)}/bytes`, `${binding.role}.media`), { mutation: 'download' })); downloads.append(button);
  }
  if (changed && mediaUrl) { URL.revokeObjectURL(mediaUrl); mediaUrl = null; $('output-preview').removeAttribute('src'); $('output-preview').hidden = true; }
  controls();
}
async function refresh() {
  const filter = $('batch-filter').value.trim();
  const result = await client.request(`/api/requests/batch-status${filter ? `?batchId=${encodeURIComponent(filter)}` : ''}`);
  let detail = null;
  if (state.selected && result.requests.some(r => r.id === state.selected.id)) detail = await client.request(`/api/requests/${encodeURIComponent(state.selected.id)}`);
  state.requests = result.requests; state.stale = false; state.refreshedAt = Date.now();
  if (detail) showRequest(detail);
  else { state.selected = null; $('request-detail').textContent = 'Chọn request từ snapshot server.'; $('artifact').replaceChildren(); $('input-downloads').replaceChildren(); }
  renderRows();
}
async function download(path, filename) {
  const blob = await client.request(path, { blob: true });
  const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function inspectBatch(batch) {
  if (!Array.isArray(batch.requests) || !batch.requests.length) throw new Error('Manifest cần requests không rỗng.');
  $('batch-summary').textContent = pretty(batch.requests.map(r => {
    const s = r.authorization.scope;
    return { batchId: r.batchId, laneId: r.laneId, order: r.order, action: r.action, mode: r.mode, profileId: s.profileId, flowProjectId: s.flowProjectId, inputHashes: s.inputHashes, sourceWindow: s.sourceWindow, budget: s.budget, approvedRequestedGenerations: r.outputCount, reservedCostUnitsIfRun: r.outputCount * s.budget.costUnitsPerGeneration, providerBalance: 'UNKNOWN', authorizationId: r.authorization.authorizationId, approvedBy: r.authorization.approvedBy, expiresAt: r.authorization.expiresAt, idempotencyKey: r.idempotencyKey, payloadSha256: r.payloadSha256 };
  }));
}

$('connect-form').addEventListener('submit', event => {
  event.preventDefault(); operation(async () => {
    if (!['127.0.0.1','localhost','[::1]'].includes(location.hostname) || location.protocol !== 'http:') throw new Error('Mở shell trên cùng origin loopback HTTP của API server.');
    const health = await client.request('/health', { public: true });
    if (health.mode !== 'manual-only' || health.automatic_provider_transport !== false) throw new Error('Server không xác nhận manual-only; đã khóa thao tác.');
    client.token = $('token').value; $('token').value = '';
    if (client.sessionId) {
      // The operator explicitly reconnects: renew the same owned session, never a new attempt.
      await client.request(`/api/sessions/${encodeURIComponent(client.sessionId)}/renew`, { method: 'POST', session: false });
      await client.request('/api/requests');
    } else {
      const session = await client.request('/api/sessions', { method: 'POST', session: false, body: { actorId: $('actor').value.trim() } });
      client.sessionId = session.sessionId; storeSession(client.sessionId);
    }
    const providers = await client.request('/api/providers/status');
    $('provider').textContent = pretty({ sessionId: client.sessionId, health, providers });
    state.connected = true; await refresh();
  }, { success: 'Đã xác thực session và đọc snapshot server. Transport thủ công; số dư chưa biết.' });
});
$('forget-session').addEventListener('click', () => {
  if (state.busy) return;
  client.sessionId = ''; client.token = ''; storeSession(''); state.connected = false; state.stale = true;
  message('Đã bỏ session ID ở trang. Kết nối tiếp theo tạo session mới; session mới không nhập được receipt của reservation cũ. Không tạo attempt mới hoặc chạy lại batch.'); controls();
});
$('refresh').addEventListener('click', () => operation(refresh, { success: 'Đã đọc lại server. Đối chiếu stateVersion trong receipt trước khi gửi.' }));
$('input-form').addEventListener('submit', event => { event.preventDefault(); operation(async () => {
  const result = await client.request('/api/inputs/upload', { method: 'POST', body: new FormData(event.currentTarget) });
  $('input-result').textContent = pretty(result); await refresh();
}, { mutation: 'upload', success: 'Server đã nhận input bytes; xem SHA và metadata ở receipt.' }); });
$('manifest-file').addEventListener('change', event => operation(async () => { const file = event.target.files[0]; if (file) { $('manifest').value = await file.text(); $('approval-confirm').checked = false; inspectBatch(parse('manifest')); } }));
$('fingerprint').addEventListener('click', () => operation(async () => { const batch = await fingerprintBatch(parse('manifest')); $('manifest').value = pretty(batch); inspectBatch(batch); }, { success: 'Đã tính fingerprint, giữ nguyên idempotencyKey. Chưa đăng ký approval hoặc gửi batch.' }));
$('inspect').addEventListener('click', () => operation(async () => inspectBatch(parse('manifest'))));
$('authorization').addEventListener('input', () => { $('approval-confirm').checked = false; controls(); });
$('approval-confirm').addEventListener('change', controls);
$('authorize').addEventListener('click', () => operation(async () => {
  if (!$('approval-confirm').checked) throw new Error('Cần operator xác nhận riêng snapshot này.');
  const auth = parse('authorization');
  const result = await client.request('/api/authorizations', { method: 'POST', body: auth });
  $('approval-confirm').checked = false; $('batch-summary').textContent = `Server đã đăng ký authorization:\n${pretty(result)}`; await refresh();
}, { mutation: 'authorize', success: 'Đã đăng ký đúng snapshot authorization; batch chưa chạy.' }));
$('admit').addEventListener('click', () => operation(async () => {
  const batch = parse('manifest');
  // Submission sends the reviewed bytes as-is: no new key or fingerprint during reattempt.
  const rows = await client.request('/api/requests/batch', { method: 'POST', body: batch });
  $('batch-summary').textContent = pretty({ serverRequests: rows });
  $('batch-filter').value = batch.requests[0].batchId; await refresh();
}, { mutation: 'admit', success: 'Server đã nhận batch. Chọn lane kế tiếp để giữ reservation thủ công.' }));
$('run').addEventListener('click', () => operation(async () => {
  const row = selected(); if (row.executionState !== 'QUEUED') throw new Error('Chỉ request QUEUED mới được reserve.');
  const result = await client.request(`/api/requests/batches/${encodeURIComponent(row.envelope.batchId)}/run`, { method: 'POST', body: { requestId: row.id, expectedStateVersion: row.stateVersion } });
  showRequest(result);
  state.stale = true;
  if (result.executionState !== 'MANUAL_REQUIRED') throw new Error('Phản hồi không phải MANUAL_REQUIRED. Đối soát trước thao tác khác.');
  await refresh();
}, { mutation: 'run', success: 'Đã giữ một reservation. MANUAL_REQUIRED: xem exact handoff; chưa có output hoặc QA.' }));
$('handoff').addEventListener('click', () => {
  const row = selected(); $('request-detail').textContent = pretty({ requestId: row.id, executionState: row.executionState, stateVersion: row.stateVersion, payloadSha256: row.payload_sha256 ?? row.envelope.payloadSha256, authorization: row.envelope.authorization, specification: row.specification, handoff: row.handoff ?? null });
  if (row.handoff && !$('receipt').value.trim()) {
    const h = row.handoff; const scope = row.envelope.authorization.scope;
    $('receipt').value = pretty({ receiptId: '', reservationId: h.reservationId, attempt: 1, payloadSha256: h.payloadSha256, laneId: row.envelope.laneId, laneRevision: row.envelope.laneRevision, expectedStateVersion: row.stateVersion, outputIndex: 1, performedAt: '', evidenceRef: '', profileId: scope.profileId, flowProjectId: scope.flowProjectId, observedGenerations: row.specification.outputCount, costStatus: 'UNKNOWN', observedCostUnits: null, providerOperationId: null, providerWorkflowId: null });
  }
  message('Đối chiếu role, prompt, hash, source window, profile, Flow project và ngân sách trước thao tác thủ công. Receipt cần giờ thao tác và bằng chứng thật.');
});
$('receipt-form').addEventListener('submit', event => { event.preventDefault(); operation(async () => {
  const metadata = parse('receipt'); const file = $('output-file').files[0]; if (!file) throw new Error('Cần tệp output bytes thật.');
  const form = new FormData(); form.set('metadata', JSON.stringify(metadata)); form.set('file', file);
  const result = await client.request(endpoint('/receipts'), { method: 'POST', body: form });
  $('request-detail').textContent = pretty(result); await refresh();
}, { mutation: 'receipt', success: 'Server đã nhận receipt/output. Kiểm tra accepted và exact SHA; chưa phải QA PASS.' }); });
$('artifact').addEventListener('change', () => {
  for (const check of document.querySelectorAll('[data-qa]')) check.value = '';
  $('qa-verdict').value = ''; $('qa-id').value = ''; $('qa-notes').value = '';
  if (mediaUrl) { URL.revokeObjectURL(mediaUrl); mediaUrl = null; $('output-preview').removeAttribute('src'); $('output-preview').hidden = true; }
  controls();
});
const currentArtifact = () => { const artifact = selected().artifacts?.find(a => a.artifactId === $('artifact').value); if (!artifact) throw new Error('Chọn artifact từ server.'); return artifact; };
$('view-output').addEventListener('click', () => operation(async () => {
  const artifact = currentArtifact(); const bytes = await client.request(`/api/artifacts/${encodeURIComponent(artifact.artifactId)}/bytes`, { blob: true });
  if (mediaUrl) URL.revokeObjectURL(mediaUrl); mediaUrl = URL.createObjectURL(new Blob([bytes], { type: 'video/mp4' })); $('output-preview').src = mediaUrl; $('output-preview').hidden = false;
}, { mutation: 'download', success: 'Đã tải bytes server cho review; metadata và xem video không tự tạo QA PASS.' }));
$('download-output').addEventListener('click', () => operation(() => { const artifact = currentArtifact(); return download(`/api/artifacts/${encodeURIComponent(artifact.artifactId)}/bytes`, `${artifact.artifactId}.media`); }, { mutation: 'download' }));
$('qa-form').addEventListener('submit', event => { event.preventDefault(); operation(async () => {
  const artifact = currentArtifact(); const checks = Object.fromEntries([...document.querySelectorAll('[data-qa]')].map(el => [el.dataset.qa, el.value]));
  if (Object.values(checks).some(value => !['PASS','FAIL'].includes(value))) throw new Error('Chọn rõ từng check sau review.');
  if ($('qa-verdict').value === 'PASS' && Object.values(checks).some(value => value !== 'PASS')) throw new Error('Verdict PASS cần mọi check PASS.');
  await client.request(endpoint('/qa'), { method: 'POST', body: { qaReceiptId: $('qa-id').value.trim(), artifactId: artifact.artifactId, artifactSha256: artifact.sha256, verdict: $('qa-verdict').value, checks, notes: $('qa-notes').value.trim() } });
  await refresh();
}, { mutation: 'qa', success: 'Đã ghi review QA cho exact SHA. Promotion cần authorization riêng và lineage preview.' }); });
for (const action of ['reconcile','cancel']) $(action).addEventListener('click', () => operation(async () => {
  const reason = $('reason').value.trim(); if (!reason) throw new Error('Cần lý do rõ ràng.');
  const body = { expectedStateVersion: selected().stateVersion, reason };
  if (action === 'reconcile') { body.outcome = $('outcome').value; body.evidenceRef = $('evidence').value.trim(); if (!body.evidenceRef) throw new Error('Cần evidenceRef cho đối soát.'); }
  await client.request(endpoint(`/${action}`), { method: 'POST', body }); await refresh();
}, { mutation: action, success: action === 'cancel' ? 'Đã ghi cancel local. Provider chưa được hủy; receipt muộn chỉ làm bằng chứng, không mở attempt mới.' : 'Đã ghi bằng chứng đối soát. Không mở lại generation hoặc retry.' }));
$('clarify-output').addEventListener('click', () => operation(async () => {
  if (selected().executionState !== 'OUTCOME_UNKNOWN') throw new Error('Chỉ OUTCOME_UNKNOWN được xác nhận output bằng evidence.');
  const body = outputClarification(parse('clarification'), { expectedStateVersion:selected().stateVersion, reason:$('reason').value.trim(), evidenceRef:$('evidence').value.trim() });
  await client.request(endpoint('/reconcile'), { method:'POST', body }); await refresh();
}, { mutation:'reconcile', success:'Đã ghi evidence xác nhận output trong cùng attempt. Xem trạng thái server; đủ output mới mở QA thủ công, chưa tự QA PASS.' }));
window.addEventListener('offline', () => { state.online = false; state.stale = true; message('Offline. Đã khóa thao tác; dữ liệu và biểu mẫu được giữ nguyên.', true); controls(); });
window.addEventListener('online', () => { state.online = true; state.stale = true; message('Mạng trở lại. Đọc lại trạng thái server trước khi thao tác.'); controls(); });
window.addEventListener('pagehide', () => { client.token = ''; if (mediaUrl) URL.revokeObjectURL(mediaUrl); });
setInterval(controls, 1000); // Clock-only freshness guard; no network polling or generation.
controls();
