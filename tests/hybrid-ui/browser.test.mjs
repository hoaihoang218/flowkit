// Synthetic route-intercepted browser evidence only; never standalone live acceptance.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
const require = createRequire(import.meta.url);
let chromium;
let browserDependencyError;
try { ({ chromium } = require('playwright')); }
catch (localError) {
  if (process.env.FLOWKIT_PLAYWRIGHT_MODULE) {
    try { ({ chromium } = require(process.env.FLOWKIT_PLAYWRIGHT_MODULE)); }
    catch (envError) { browserDependencyError = `Playwright không khả dụng qua FLOWKIT_PLAYWRIGHT_MODULE: ${envError.code ?? envError.message}`; }
  } else browserDependencyError = `Chưa có module playwright (${localError.code ?? localError.message}); cài dependency riêng hoặc đặt FLOWKIT_PLAYWRIGHT_MODULE.`;
}
const edgeCandidate = process.platform === 'win32' && process.env['PROGRAMFILES(X86)']
  ? join(process.env['PROGRAMFILES(X86)'], 'Microsoft', 'Edge', 'Application', 'msedge.exe') : null;
const browserExecutable = process.env.FLOWKIT_BROWSER_EXECUTABLE
  || (edgeCandidate && existsSync(edgeCandidate) ? edgeCandidate : undefined);
const origin = 'http://127.0.0.1:8100';
const sha = 'a'.repeat(64);
const scope = { projectId:'synthetic', productId:'synthetic-product', profileId:'synthetic-profile', flowProjectId:'synthetic-flow', budget:{ budgetId:'synthetic-budget', maxGenerations:1, maxCostUnits:10, costUnitsPerGeneration:10 }, sourceWindow:null, inputHashes:[{ assetId:'input', role:'PRODUCT_IMAGE', sha256:sha }] };
const spec = { action:'preview-generation', prompt:'Nội dung giả lập dùng để kiểm tra UI.', mode:'I2V_PRODUCT_ONLY', inputAuthority:scope.inputHashes, outputCount:1, previewArtifactSha256:null, previewRequestId:null, previewArtifactId:null, previewReceiptId:null, qaReceiptId:null };
const auth = { authorizationId:'synthetic-auth', approvedBy:'local-operator', specification:spec, scope };
const envelope = { ...spec, idempotencyKey:'synthetic-key', payloadSha256:sha, batchId:'synthetic-batch', laneId:'A1-product', laneRevision:1, order:1, authorization:auth };

test('operational controls, double click, CAS, reload, keyboard and mobile layout (mock HTTP)', async t => {
  if (!chromium) { t.skip(browserDependencyError); return; }
  let browser;
  try { browser = await chromium.launch({ ...(browserExecutable ? { executablePath:browserExecutable } : {}), headless:true }); }
  catch (error) {
    if (/executable doesn't exist|executable doesn't exist at|browser not found|no such file|enoent/i.test(error.message)) {
      t.skip('Browser executable chưa có; dùng Playwright browser đã cài hoặc đặt FLOWKIT_BROWSER_EXECUTABLE. Không tự cài browser.');
      return;
    }
    throw error;
  }
  const page = await browser.newPage({ viewport:{ width:1280,height:900 } });
  let row = { id:'r1', envelope, specification:spec, action:spec.action, executionState:'QUEUED', stateVersion:0, qaState:'NONE', artifacts:[] };
  const calls = []; const errors = [];
  let clarifyCalls = 0;
  page.on('pageerror',e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    if (url.origin !== origin) throw new Error('Unexpected non-local request');
    if (path.startsWith('/hybrid/') || path === '/protocol/canonical-json.mjs') {
      const rel = path === '/protocol/canonical-json.mjs' ? '../../protocol/canonical-json.mjs' : `../../dashboard/hybrid/${path.slice('/hybrid/'.length) || 'index.html'}`;
      const source = await readFile(fileURLToPath(new URL(rel,import.meta.url)));
      return route.fulfill({ body:source,contentType:path.endsWith('.css')?'text/css':path.endsWith('.mjs')?'text/javascript':'text/html' });
    }
    calls.push({ path,method:req.method(),headers:req.headers(),body:req.postData() });
    let response = {}; let status = 200;
    if (path === '/health') response = { mode:'manual-only',automatic_provider_transport:false };
    else if (path === '/api/sessions') response = { sessionId:'test-session',actorId:'local-operator' };
    else if (path === '/api/sessions/test-session/renew') response = { sessionId:'test-session',actorId:'local-operator' };
    else if (path === '/api/providers/status') response = { transport:{status:'unverified'},balance:{status:'unknown',value:null} };
    else if (path === '/api/requests') response = [row];
    else if (path === '/api/requests/batch-status') response = { requests:[row] };
    else if (path === '/api/requests/r1') response = row;
    else if (path === '/api/authorizations') response = JSON.parse(req.postData());
    else if (path === '/api/requests/batch') response = [row];
    else if (path === '/api/inputs/upload') response = { assetId:'uploaded',sha256:sha,metadata:{width:720,height:1280} };
    else if (path === '/api/requests/batches/synthetic-batch/run') {
      await new Promise(resolve => setTimeout(resolve,80));
      row = { ...row,executionState:'MANUAL_REQUIRED',stateVersion:1,handoff:{reservationId:'reservation',attempt:1,payloadSha256:sha,scope,specification:spec} }; response = row;
    } else if (path === '/api/requests/r1/reconcile') {
      const body = JSON.parse(req.postData());
      if (body.outcome === 'OUTPUT_RECEIVED') {
        clarifyCalls++;
        if (clarifyCalls === 1) { row = {...row,stateVersion:3}; status = 409; response = {detail:'State version changed; inspect current evidence'}; }
        else { row = {...row,executionState:'OUTPUT_RECEIVED',stateVersion:4,qaState:'NONE',artifacts:row.artifacts.map(a=>({...a,accepted:true}))}; response = row; }
      } else { status = 409; response = {detail:'State version changed; inspect current state'}; }
    }
    else if (path === '/api/requests/r1/receipts') {
      row = { ...row, executionState:'OUTCOME_UNKNOWN',stateVersion:2,artifacts:[{ artifactId:'synthetic-artifact',sha256:sha,receiptId:'synthetic-receipt',reservationId:'reservation',accepted:false,metadata:{width:720,height:1280,hasAudio:false} }] }; response = { artifact:row.artifacts[0],request:row };
    } else if (path === '/api/requests/r1/qa') { row = { ...row,qaState:'PASS',stateVersion:5 }; response = { ...JSON.parse(req.postData()),request:row }; }
    else { status = 404; response = {detail:'Unexpected mock route'}; }
    await route.fulfill({ status,contentType:'application/json',body:JSON.stringify(response) });
  });
  try {
    await page.goto(`${origin}/hybrid/`);
    assert.equal(await page.locator('#run').isDisabled(),true);
    await page.locator('#token').fill('synthetic-token-only-in-memory');
    await page.locator('#connect').click(); await page.getByText('Đã xác thực session và đọc snapshot server.',{exact:false}).waitFor();
    assert.equal(await page.locator('#token').inputValue(),'');
    await page.getByRole('button',{name:'Xem request r1'}).click();
    await page.locator('#run').waitFor(); await page.waitForFunction(() => !document.querySelector('#run').disabled);
    if (process.env.FLOWKIT_UI_SCREENSHOT_DIR) {
      await mkdir(process.env.FLOWKIT_UI_SCREENSHOT_DIR,{recursive:true});
      await page.screenshot({path:join(process.env.FLOWKIT_UI_SCREENSHOT_DIR,'desktop.png'),fullPage:true});
    }
    await page.locator('#authorization').fill(JSON.stringify(auth));
    assert.equal(await page.locator('#authorize').isDisabled(),true);
    await page.locator('#approval-confirm').check();
    await page.locator('#authorization').fill(JSON.stringify({...auth,approvalRef:'changed'}));
    assert.equal(await page.locator('#approval-confirm').isChecked(),false);
    await page.locator('#manifest').fill(JSON.stringify({ requests:[envelope] }));
    await page.locator('#fingerprint').click(); await page.getByText('Đã tính fingerprint, giữ nguyên',{exact:false}).waitFor();
    assert.equal(calls.filter(c=>c.path==='/api/authorizations').length,0);
    await page.locator('#approval-confirm').check(); await page.locator('#authorize').click();
    await page.getByText('Đã đăng ký đúng snapshot authorization',{exact:false}).waitFor();
    await page.locator('#run').evaluate(button => { button.click(); button.click(); });
    await page.getByText('Đã giữ một reservation.',{exact:false}).waitFor();
    assert.equal(calls.filter(c=>c.path.endsWith('/run')).length,1);
    assert.equal(await page.locator('#run').isDisabled(),true);
    await page.locator('#handoff').click(); assert.match(await page.locator('#receipt').inputValue(),/reservation/);
    await page.locator('#receipt').fill('{"operator":"preserve receipt editor"}');
    await page.locator('#reason').fill('Đối soát bằng chứng giả lập'); await page.locator('#evidence').fill('synthetic-evidence');
    await page.locator('#reconcile').click(); await page.getByRole('alert').waitFor();
    assert.match(await page.locator('#message').textContent(),/HTTP 409/);
    assert.equal(await page.locator('#reason').inputValue(),'Đối soát bằng chứng giả lập');
    assert.equal(await page.locator('#receipt').inputValue(),'{"operator":"preserve receipt editor"}');
    assert.equal(await page.locator('#reconcile').isDisabled(),true);
    await page.locator('#refresh').click(); await page.getByText('Đã đọc lại server.',{exact:false}).waitFor();
    await page.locator('#receipt').focus(); await page.keyboard.press('Tab'); assert.equal(await page.locator('#output-file').evaluate(el=>el===document.activeElement),true);
    await page.locator('[name="projectId"]').fill('synthetic');
    await page.locator('[name="role"]').selectOption('PRODUCT_IMAGE'); await page.locator('[name="contentClass"]').selectOption('PRODUCT_ONLY');
    await page.locator('[name="attestation"]').fill('Bằng chứng giả lập cho UI; chưa nghiệm thu input thật.');
    await page.locator('[name="file"]').setInputFiles({name:'synthetic.png',mimeType:'image/png',buffer:Buffer.from('synthetic input bytes')});
    await page.getByRole('button',{name:'Upload input và xem receipt thật'}).click();
    await page.getByText('Server đã nhận input bytes;',{exact:false}).waitFor();
    assert.match(await page.locator('#input-result').textContent(),/uploaded/);
    await page.locator('#receipt').fill(JSON.stringify({ receiptId:'synthetic-receipt',reservationId:'reservation',attempt:1,payloadSha256:sha,laneId:'A1-product',laneRevision:1,expectedStateVersion:1,outputIndex:1,performedAt:'2026-10-01T00:00:00Z',evidenceRef:'synthetic-evidence',profileId:scope.profileId,flowProjectId:scope.flowProjectId,observedGenerations:1,costStatus:'UNKNOWN',observedCostUnits:null,providerOperationId:null,providerWorkflowId:null }));
    await page.locator('#output-file').setInputFiles({name:'synthetic.mp4',mimeType:'video/mp4',buffer:Buffer.from('synthetic output bytes')});
    await page.getByRole('button',{name:'Nhập receipt/output',exact:true}).click(); await page.getByText('Server đã nhận receipt/output.',{exact:false}).waitFor();
    await page.locator('#artifact').selectOption('synthetic-artifact');
    assert.equal(await page.getByRole('button',{name:'Lưu QA thủ công'}).isDisabled(),true);
    assert.equal(await page.locator('#run').isDisabled(),true);
    await page.getByText('Xác nhận output đã giữ làm bằng chứng trong cùng attempt',{exact:true}).click();
    const clarification = {artifactId:'synthetic-artifact',artifactSha256:sha,receiptId:'synthetic-receipt',reservationId:'reservation',observedGenerations:1,observedCostUnits:4};
    await page.locator('#clarification').fill(JSON.stringify({...clarification,observedCostUnits:null}));
    await page.locator('#clarify-output').click(); await page.getByRole('alert').waitFor();
    assert.match(await page.locator('#message').textContent(),/chi phí/); assert.equal(clarifyCalls,0);
    await page.locator('#refresh').click(); await page.getByText('Đã đọc lại server.',{exact:false}).waitFor();
    await page.locator('#clarification').fill(JSON.stringify(clarification));
    await page.locator('#clarify-output').click(); await page.getByRole('alert').waitFor();
    assert.match(await page.locator('#message').textContent(),/HTTP 409/);
    assert.equal(await page.locator('#clarification').inputValue(),JSON.stringify(clarification)); assert.equal(clarifyCalls,1);
    await page.locator('#refresh').click(); await page.getByText('Đã đọc lại server.',{exact:false}).waitFor();
    await page.locator('#clarify-output').evaluate(button=>{button.click();button.click();});
    await page.getByText('Đã ghi evidence xác nhận output trong cùng attempt.',{exact:false}).waitFor();
    assert.equal(clarifyCalls,2); assert.equal(calls.filter(c=>c.path.endsWith('/run')).length,1);
    assert.equal(calls.filter(c=>c.path.endsWith('/receipts')).length,1); assert.equal(calls.filter(c=>c.path.endsWith('/qa')).length,0);
    const clarified = JSON.parse(calls.filter(c=>c.path.endsWith('/reconcile')&&JSON.parse(c.body).outcome==='OUTPUT_RECEIVED').at(-1).body);
    assert.deepEqual(clarified.clarification,clarification); assert.equal(clarified.expectedStateVersion,3);
    assert.equal(clarified.evidenceRef,'synthetic-evidence');
    assert.equal(await page.locator('#qa-verdict').inputValue(),'');
    await page.locator('#qa-id').fill('synthetic-qa'); await page.locator('#qa-verdict').selectOption('PASS');
    for (const check of await page.locator('[data-qa]').all()) await check.selectOption('PASS');
    await page.locator('#qa-notes').fill('Review giả lập cho contract UI, chưa QA output provider thật.');
    await page.getByRole('button',{name:'Lưu QA thủ công'}).click(); await page.getByText('Đã ghi review QA cho exact SHA.',{exact:false}).waitFor();
    const qaCall = calls.find(c=>c.path.endsWith('/qa')); const qaBody = JSON.parse(qaCall.body);
    assert.equal(qaBody.artifactSha256,sha); assert.equal(Object.keys(qaBody.checks).length,9);
    const receiptCall = calls.find(c=>c.path.endsWith('/receipts'));
    assert.match(receiptCall.headers['content-type'],/multipart\/form-data/); assert.match(receiptCall.body,/observedCostUnits/);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false);
    assert.ok(await page.locator('h1').isVisible());
    assert.ok(await page.locator('#run').boundingBox());
    assert.equal(await page.locator('#qa-checks select').count(),9);
    assert.ok(await page.locator('#message').getAttribute('role'));
    if (process.env.FLOWKIT_UI_SCREENSHOT_DIR) await page.screenshot({path:join(process.env.FLOWKIT_UI_SCREENSHOT_DIR,'mobile.png'),fullPage:true});
    await page.reload(); await page.locator('#token').fill('synthetic-token-only-in-memory'); await page.locator('#connect').click();
    await page.getByText('Đã xác thực session và đọc snapshot server.',{exact:false}).waitFor();
    assert.equal(calls.filter(c=>c.path==='/api/sessions').length,1);
    assert.equal(calls.filter(c=>c.path==='/api/sessions/test-session/renew').length,1);
    assert.equal(calls.filter(c=>c.path.endsWith('/run')).length,1);
    assert.equal(await page.evaluate(() => localStorage.length),0);
    assert.equal(await page.evaluate(() => sessionStorage.getItem('flowkit.sessionId')),'test-session');
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    assert.equal(await page.locator('#admit').isDisabled(),true);
    assert.deepEqual(errors,[]);
    assert.ok(calls.filter(c=>c.path.startsWith('/api/')&&!c.path.startsWith('/api/sessions')).every(c=>c.headers.authorization==='Bearer synthetic-token-only-in-memory'&&c.headers['x-session-id']==='test-session'));
  } finally { await browser.close(); }
});
