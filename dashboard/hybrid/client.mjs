import { payloadSha256 } from '../../protocol/canonical-json.mjs';

export const QA_COMMON = ['productIdentity','inputFidelity','modeFidelity','motionContinuity','noTextWatermark','silentAudio','technicalDimensions'];
export function qaChecks(mode) {
  return [...QA_COMMON, ...(mode === 'V2V_SOURCE_MOTION_BEFORE_AFTER_PERSON'
    ? ['sourceMotionOnly','beforeIdentity','afterIdentity','anatomy','outfitFidelity']
    : ['noPersonFaceOutfit','productOnlyMotion'])];
}
const nullable = ['previewArtifactSha256','previewRequestId','previewArtifactId','previewReceiptId','qaReceiptId'];
export async function fingerprintBatch(batch) {
  if (!Array.isArray(batch?.requests) || !batch.requests.length) throw new Error('Manifest cần requests không rỗng.');
  for (const envelope of batch.requests) {
    if (!envelope.idempotencyKey) throw new Error('Giữ idempotencyKey cố định và khác rỗng cho mỗi request.');
    for (const name of nullable) {
      if (!(name in envelope) || !(name in (envelope.authorization?.specification ?? {}))) throw new Error(`Phải khai báo ${name}, dùng null khi không áp dụng.`);
    }
    if (!('sourceWindow' in (envelope.authorization?.scope ?? {}))) throw new Error('Phải khai báo sourceWindow, dùng null cho product-only.');
    const { idempotencyKey, payloadSha256: oldHash, ...payload } = envelope;
    envelope.payloadSha256 = await payloadSha256(payload);
  }
  return batch;
}

export class ApiError extends Error {
  constructor(status, detail) { super(typeof detail === 'string' ? detail : JSON.stringify(detail)); this.status = status; }
}
export class Client {
  constructor(fetcher = globalThis.fetch.bind(globalThis)) { this.fetcher = fetcher; this.token = ''; this.sessionId = ''; }
  async request(path, { method = 'GET', body, public: isPublic = false, session = true, blob = false } = {}) {
    if (!path.startsWith('/api/') && path !== '/health') throw new Error('Chỉ chấp nhận đường dẫn API local.');
    const headers = {};
    if (!isPublic) {
      if (!this.token) throw new Error('Cần token local.');
      headers.Authorization = `Bearer ${this.token}`;
      if (session) { if (!this.sessionId) throw new Error('Cần session đã xác thực.'); headers['X-Session-ID'] = this.sessionId; }
    }
    if (body !== undefined && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
    const response = await this.fetcher(path, { method, headers, body, cache: 'no-store', credentials: 'omit', redirect: 'error' });
    if (!response.ok) {
      const data = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
      throw new ApiError(response.status, data.detail ?? data);
    }
    return blob ? response.blob() : response.json();
  }
}

export function canMutate(state, kind, now = Date.now()) {
  if (state.busy || !state.connected || state.stale || !state.online || now - state.refreshedAt > 60000) return false;
  if (state.requests.some(r => r.executionState === 'OUTCOME_UNKNOWN') && !['receipt','reconcile','download'].includes(kind)) return false;
  return true;
}

export function outputClarification(clarification, { expectedStateVersion, reason, evidenceRef }) {
  const fields = ['artifactId','artifactSha256','receiptId','reservationId','observedGenerations','observedCostUnits'];
  if (!clarification || typeof clarification !== 'object' || Array.isArray(clarification)
      || Object.keys(clarification).length !== fields.length || fields.some(name => !(name in clarification))) {
    throw new Error('Clarification cần đúng sáu field: artifactId, artifactSha256, receiptId, reservationId, observedGenerations, observedCostUnits.');
  }
  for (const name of ['artifactId','receiptId','reservationId']) {
    if (typeof clarification[name] !== 'string' || !clarification[name].trim()) throw new Error(`Cần ${name} đúng evidence gốc.`);
  }
  if (!/^[0-9a-f]{64}$/.test(clarification.artifactSha256)) throw new Error('Cần exact SHA-256 của artifact gốc.');
  if (!Number.isInteger(clarification.observedGenerations) || clarification.observedGenerations < 1) throw new Error('Nhập rõ observedGenerations theo cùng attempt.');
  if (!Number.isInteger(clarification.observedCostUnits) || clarification.observedCostUnits < 0) throw new Error('Nhập rõ chi phí đã biết; observedCostUnits không được thiếu/null hoặc tự mặc định 0.');
  if (!reason?.trim() || !evidenceRef?.trim()) throw new Error('Cần lý do và evidenceRef để xác nhận output.');
  return { expectedStateVersion, reason, outcome:'OUTPUT_RECEIVED', evidenceRef, clarification };
}
