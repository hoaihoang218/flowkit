// RFC 8785 serialization for JSON values; identifiers remain strings.
export function canonicalJson(value) {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'string') {
    for (let i = 0; i < value.length; i++) {
      const code = value.charCodeAt(i);
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new TypeError('Invalid Unicode surrogate');
      } else if (code >= 0xdc00 && code <= 0xdfff) throw new TypeError('Invalid Unicode surrogate');
    }
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('Non-finite JSON number');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) if (!(i in value)) throw new TypeError('Sparse arrays are not JSON');
    return '[' + value.map(canonicalJson).join(',') + ']';
  }
  if (typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return '{' + Object.keys(value).sort().map(key => canonicalJson(key) + ':' + canonicalJson(value[key])).join(',') + '}';
  }
  throw new TypeError('Only plain JSON values are allowed');
}
export async function payloadSha256(value) {
  const hash = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonicalJson(value)));
  return Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('');
}
