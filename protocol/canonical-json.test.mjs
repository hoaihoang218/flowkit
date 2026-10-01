import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {canonicalJson, payloadSha256} from './canonical-json.mjs';
const fixtures = JSON.parse(await readFile(new URL('../docs/fixtures/hybrid-canonical.json', import.meta.url), 'utf8')).fixtures;
for (const fixture of fixtures) test('JCS golden: ' + fixture.name, async () => {
  assert.equal(canonicalJson(fixture.payload), fixture.canonical);
  assert.equal(await payloadSha256(fixture.payload), fixture.sha256);
});
test('reject invalid JSON and malformed Unicode', () => {
  for (const invalid of [undefined, NaN, Infinity, new Date(), Array(1), '\ud800', {'\udc00': 'bad'}]) {
    assert.throws(() => canonicalJson(invalid), TypeError);
  }
});
