'use strict';

const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);

const source = JSON.parse(fs.readFileSync(process.env.F11_SOURCE, 'utf8'));
const target = JSON.parse(fs.readFileSync(process.env.F11_TARGET, 'utf8'));
const checks = [];
const layout = [];
const nativeZoom = [];
let phase = 'startup';

function digest(value) {
    return createHash('sha256').update(value).digest('hex');
}

async function open(browser, fixture, role) {
    assert(fixture.syntheticOnly && fixture.schema === 33);
    assert.match(fixture.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
    const context = await browser.newContext({reducedMotion: 'reduce', viewport: {width: 1440, height: 940}});
    await context.route('**/*', route => new URL(route.request().url()).origin === fixture.origin
        ? route.continue() : route.abort('blockedbyclient'));
    const response = await context.request.post(fixture.origin + '/api/auth/login', {
        data: {username: fixture.users[role], password: fixture.password},
    });
    assert.equal(response.status(), 200, 'synthetic login');
    return {context, fixture};
}

async function request(session, url, {method = 'GET', data, csrf = true, headers = {}} = {}) {
    if (csrf) {
        const cookie = (await session.context.cookies()).find(cookie => cookie.name === 'clinic_csrf');
        assert(cookie);
        headers = {...headers, 'X-CSRF-Token': cookie.value};
    }
    return session.context.request.fetch(session.fixture.origin + url, {method, data, headers});
}

async function browserPage(session) {
    const page = await session.context.newPage();
    await page.goto(session.fixture.origin);
    await page.waitForFunction(() => app.appReady && !app.sessionExpired && app.user);
    return page;
}

const checkbox = page => page.locator('#exportPatientList input[type=checkbox]');
const transferPrefix = 'Синтетический Ф11 Перенос';

async function chooseExport(page, query) {
    await page.locator('#openExportBtn').click();
    await checkbox(page).first().waitFor();
    if (query) {
        const result = page.waitForResponse(response => {
            const url = new URL(response.url());
            return url.pathname === '/api/patients' && url.searchParams.get('q') === query && response.ok();
        });
        await page.locator('#exportSearchInput').fill(query);
        await result;
        await page.waitForFunction(value => app.exportQuery?.q === value, query);
    }
}

async function download(page, method) {
    const [attachment, response] = await Promise.all([
        page.waitForEvent('download'),
        page.waitForResponse(response => new URL(response.url()).pathname.startsWith('/api/export') &&
            response.request().method() === method),
        (async () => {
            await page.locator('#confirmExportBtn').click();
            await page.locator('#confirmOkBtn').click();
        })(),
    ]);
    assert.equal(response.status(), 200);
    assert.equal(response.headers()['cache-control'], 'no-store');
    assert.match(response.headers()['content-disposition'], /^attachment; filename="patients_export_\d{4}-\d{2}-\d{2}\.json"$/);
    assert.match(attachment.suggestedFilename(), /^patients_export_\d{4}-\d{2}-\d{2}\.json$/);
    const bytes = fs.readFileSync(await attachment.path());
    const doc = JSON.parse(bytes);
    assert.equal(doc.signature.algorithm, 'HMAC-SHA256');
    assert.match(doc.signature.value, /^[a-f0-9]{64}$/);
    await attachment.delete();
    return {bytes, doc};
}

async function exports(browser, sender) {
    phase = 'signed exports and scope';
    const page = await browserPage(sender);
    await chooseExport(page, source.transfer.name);
    await checkbox(page).first().check();
    const single = await download(page, 'GET');
    assert.deepEqual(single.doc.patients.map(p => p.id), [source.transfer.id]);
    assert.equal(single.doc.patients[0].history[0].events.length, 1);
    await chooseExport(page, transferPrefix);
    await checkbox(page).first().check();
    await page.locator('#exportPatientList').getByRole('button', {name: 'Далее', exact: true}).click();
    await page.waitForFunction(() => app.exportPage?.page.offset === 50);
    assert.equal(await checkbox(page).count(), 1);
    await checkbox(page).first().check();
    assert.equal(await page.locator('#exportSelectedCount').innerText(), '2');
    const multiple = await download(page, 'GET');
    assert.equal(multiple.doc.patients.length, 2);
    await chooseExport(page, transferPrefix);
    await page.locator('#exportSelectAllBtn').click();
    assert.equal(await page.locator('#exportSelectedCount').innerText(), '51');
    const selected = await download(page, 'POST');
    assert.equal(selected.doc.patients.length, 51);
    assert(selected.doc.patients.every(patient => patient.name.startsWith(transferPrefix)));
    const revision = await request(sender, '/api/export/selection', {
        method: 'POST', data: {query: {q: transferPrefix}, revision: '0'.repeat(64)},
    });
    assert.equal(revision.status(), 409);
    assert.match(JSON.stringify(await revision.json()), /PATIENT_QUERY_CHANGED/);
    assert.equal((await request(sender, '/api/export', {csrf: false})).status(), 403);
    const doctor = await open(browser, source, 'doctor');
    const doctorResponse = await request(doctor, '/api/export?ids=' + [source.transfer.id, source.patients.foreign.id].join(','));
    assert.equal(doctorResponse.status(), 200);
    assert.deepEqual((await doctorResponse.json()).patients.map(patient => patient.id), [source.transfer.id]);
    const head = await open(browser, source, 'head');
    const headResponse = await request(head, '/api/export?ids=' + [source.transfer.id, source.patients.foreign.id].join(','));
    assert.equal(headResponse.status(), 200);
    assert.deepEqual((await headResponse.json()).patients.map(patient => patient.id), [source.transfer.id]);
    for (const role of ['readonly', 'chair']) {
        const denied = await open(browser, source, role);
        assert.equal((await request(denied, '/api/export?ids=' + source.transfer.id)).status(), 403);
        assert.equal((await request(denied, '/api/import/preview',
            {method: 'POST', data: {raw: single.bytes.toString()}})).status(), 403);
        await denied.context.close();
    }
    await doctor.context.close();
    await head.context.close();

    const key = process.env.F11_SIGNING_FILE;
    fs.renameSync(key, key + '.absent');
    try {
        const refused = await request(sender, '/api/export?ids=' + source.transfer.id);
        assert.equal(refused.status(), 503);
        assert.equal(refused.headers()['content-disposition'], undefined);
    } finally {
        fs.renameSync(key + '.absent', key);
    }
    checks.push('signed one and two cross-page patient downloads, full 51-record server selection, transfer bytes checked',
        'doctor and head scope, readonly/chair denials, missing CSRF and stale revision denied, missing key fails closed');
    return single;
}

async function changedExportRevision(sender) {
    phase = 'real export revision conflict';
    const page = await browserPage(sender);
    await chooseExport(page, transferPrefix);
    const selection = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === '/api/patients' && url.searchParams.get('q') === transferPrefix && response.ok();
    });
    await page.locator('#exportSelectAllBtn').click();
    await selection;
    await page.waitForFunction(() => app.exportPage && app.exportAllSelection);
    const url = '/api/patients/' + source.revisionTarget.id;
    const original = await (await request(sender,url)).json();
    const changed = await request(sender,url,{method:'PATCH',data:{
        field:'name',value:original.name+' обновлен',expectedVersion:original.version,
    }});
    assert.equal(changed.status(),200);
    let downloads=0;
    page.on('download',()=>downloads++);
    const reload = page.waitForResponse(response => {
        const url = new URL(response.url());
        return url.pathname === '/api/patients' && url.searchParams.get('q') === transferPrefix && response.ok();
    });
    const denied = page.waitForResponse(response => new URL(response.url()).pathname === '/api/export/selection');
    await page.locator('#confirmExportBtn').click();
    await page.locator('#confirmOkBtn').click();
    const response = await denied;
    assert.equal(response.status(),409);
    assert.match(JSON.stringify(await response.json()),/PATIENT_QUERY_CHANGED/);
    await reload;
    await page.waitForFunction(() => app.exportPage && !app.exportAllSelection);
    assert.equal(downloads,0);
    assert.equal(await page.locator('#exportSelectedCount').textContent(),'0');
    await page.locator('#exportSelectAllBtn').click();
    const fresh=await download(page,'POST');
    assert.equal(fresh.doc.patients.length,51);
    assert(fresh.doc.patients.some(patient=>patient.id===source.revisionTarget.id && patient.name===original.name+' обновлен'));
    await page.close();
    checks.push('a real versioned source rename invalidates the selected revision, clears selection with no download, fresh confirmation succeeds');
}

async function obsoleteExport(sender) {
    phase = 'export response after expired session';
    const page = await browserPage(sender);
    await chooseExport(page, source.transfer.name);
    await checkbox(page).first().check();
    let admit, release;
    const ready = new Promise(resolve => { admit = resolve; });
    const barrier = new Promise(resolve => { release = resolve; });
    const route = url => url.pathname === '/api/export';
    await page.route(route, async interception => {
        const response = await interception.fetch();
        admit(response.status());
        await barrier;
        await interception.fulfill({response});
    });
    let downloads = 0;
    page.on('download', () => downloads++);
    try {
        await page.evaluate(() => { window.f11PendingExport = app.confirmExport(); });
        await page.locator('#confirmOkBtn').click();
        assert.equal(await ready, 200);
        const logout = await request(sender, '/api/auth/logout', {method:'POST'});
        assert([200,204].includes(logout.status()));
        const denied = await page.evaluate(async () => {
            try {await apiGetJson('/api/patients?view=page&limit=1');}
            catch (error) {return error.status;}
        });
        assert.equal(denied,401);
        await page.waitForFunction(() => app.sessionExpired);
        release();
        await page.evaluate(() => window.f11PendingExport);
        assert.equal(downloads,0);
        assert(await page.locator('#exportModal').isHidden());
    } finally {
        release();
        await page.unroute(route);
    }
    checks.push('already released export HTTP 200 cannot trigger a browser download after observed session expiry');
}

function file(bytes) {
    return {name: 'synthetic-f11.json', mimeType: 'application/json', buffer: Buffer.from(bytes)};
}

async function importPreview(page, bytes) {
    const response = page.waitForResponse(response => response.url().endsWith('/api/import/preview'));
    await page.locator('#importFileInput').setInputFiles(file(bytes));
    const received = await response;
    await page.locator('#importReviewModal').waitFor();
    return received;
}

async function replacePreview(page, valid, invalid) {
    phase = 'replace file during held preview';
    let admit, release;
    const ready = new Promise(resolve=>{admit=resolve;});
    const barrier = new Promise(resolve=>{release=resolve;});
    const endpoint='**/api/import/preview';
    await page.evaluate(()=>{
        const original=app.handleImportFile;
        window.f11PreviewRuns=[];
        app.handleImportFile=function(input) {
            const task=original.call(this,input);
            window.f11PreviewRuns.push(task);
            return task;
        };
    });
    await page.route(endpoint,async route=>{
        const response=await route.fetch();
        if (route.request().postDataJSON().raw===valid) {
            const result=await response.json();
            admit(response.status()===200 && result.valid);
            await barrier;
        }
        await route.fulfill({response});
    });
    try {
        await page.locator('#importFileInput').setInputFiles(file(valid));
        assert(await ready);
        const replacement = await importPreview(page,invalid);
        assert.equal(replacement.status(),200);
        const result=await replacement.json();
        assert.equal(result.valid,false);
        assert.equal(result.provenance.status,'invalid_signature');
        await page.evaluate(()=>window.f11PreviewRuns[1]);
        const error = page.locator('#importProvenance');
        await error.getByText('Подпись недействительна — импорт заблокирован',{exact:true}).waitFor();
        const before=await error.innerText();
        release();
        await page.evaluate(()=>window.f11PreviewRuns[0]);
        assert.equal(await error.innerText(),before);
        assert(await page.locator('#confirmImportCommitBtn').isDisabled());
    } finally {
        release();
        await page.unroute(endpoint);
    }
    checks.push('actual invalid replacement stays authoritative after older valid preview HTTP 200 is released');
}

async function modeConfirmations(page, receiver) {
    phase = 'mode resets separate user confirmations';
    const existing=await (await request(receiver,'/api/patients/'+target.patients.overdue.id)).json();
    const raw=JSON.stringify([{
        name:'Синтетический Ф11 Внешняя Запись',age:50,priority:'normal',stage:'exam',diagnosis:'C34.1',
        nosology:'lung34',head:'Синтетический заведующий Ф11',policyNumber:existing.policyNumber,events:[],
    }]);
    const response=await importPreview(page,raw);
    const review=await response.json();
    assert(review.valid && review.provenance.confirmationRequired);
    assert(review.possibleDuplicates.add.rows.length>0);
    await page.locator('#importUnsignedConfirm').check();
    await page.locator('#importDuplicateReason').fill('Синтетическое объяснение для независимой проверки');
    assert(!(await page.locator('#confirmImportCommitBtn').isDisabled()));
    await page.locator('#importModeSelect').selectOption('update');
    assert(!(await page.locator('#importUnsignedConfirm').isChecked()));
    assert.equal(await page.locator('#importDuplicateReason').inputValue(),'');
    await page.locator('#importModeSelect').selectOption('add');
    assert(await page.locator('#confirmImportCommitBtn').isDisabled());
    await page.keyboard.press('Escape');
    await page.locator('#importReviewModal').waitFor({state:'hidden'});
    checks.push('unsigned-file consent and duplicate reason reset on real mode change; Escape closes review without writing');
}

async function checkLayout(page) {
    if (!process.env.AXE_SOURCE) throw new Error('Required AXE_SOURCE missing');
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE, 'utf8'));
    for (const theme of ['light', 'dark']) {
        await page.evaluate(value => app.applyTheme(value), theme);
        for (const width of [320, 375, 768, 1024, 1440]) {
            await page.setViewportSize({width, height: 940});
            const data = await page.evaluate(async () => ({
                overflow: document.documentElement.scrollWidth > innerWidth,
                violations: (await axe.run(document, {runOnly: {type: 'tag',
                    values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(item =>
                    ({id: item.id, targets: item.nodes.map(node=>node.target)})),
            }));
            if (data.overflow || data.violations.length) process.stderr.write(JSON.stringify({theme, width, ...data}) + '\n');
            assert.deepEqual(data, {overflow: false, violations: []}, theme + '/' + width);
            layout.push({theme, width, ...data});
            if (width === 375 || width === 1440) {
                await page.screenshot({path: path.join(process.env.OUT, 'f11-preview-' + theme + '-' + width + '.png'),
                    animations: 'disabled'});
            }
        }
    }
}

async function importAndReplay(browser, receiver, exported) {
    phase = 'import preview';
    const raw = exported.bytes.toString();
    const before = await (await request(receiver, '/api/patients?view=page')).json();
    const invalid = await request(receiver, '/api/import/preview', {method: 'POST', data: {raw: 'not JSON'}});
    assert([200, 400, 422].includes(invalid.status()));
    if (invalid.ok()) assert.equal((await invalid.json()).valid, false);
    const altered = JSON.parse(raw);
    altered.patients[0].name += ' изменено';
    const tampered = await (await request(receiver, '/api/import/preview',
        {method: 'POST', data: {raw: JSON.stringify(altered)}})).json();
    assert.equal(tampered.valid, false);
    assert.equal(tampered.provenance.status, 'invalid_signature');
    const unsigned = {patients: [{name: 'Синтетический Ф11 Неподписанный', age: 50, diagnosis: 'C34.1'}]};
    const unsignedReview = await (await request(receiver, '/api/import/preview',
        {method: 'POST', data: {raw: JSON.stringify(unsigned)}})).json();
    assert.equal(unsignedReview.provenance.confirmationRequired, true);

    const page = await browserPage(receiver);
    await replacePreview(page,raw,JSON.stringify(altered));
    await modeConfirmations(page,receiver);
    const preview = await importPreview(page, raw);
    assert.equal(preview.status(), 200);
    const report = await preview.json();
    assert.equal(report.valid, true);
    assert.equal(report.provenance.status, 'verified_signed');
    assert.equal(report.totalRecords, 1);
    const afterPreview = await (await request(receiver, '/api/patients?view=page')).json();
    assert.equal(afterPreview.page.total, before.page.total);
    await checkLayout(page);
    await page.locator('#importModeSelect').selectOption('add');
    if (await page.locator('#importPreserveIdsConfirm').isVisible()) {
        assert(await page.locator('#confirmImportCommitBtn').isDisabled());
        await page.locator('#importPreserveIdsConfirm').check();
    }

    phase = 'lost response after real commit';
    const attempts = [];
    const parser = '**/api/import/commit';
    let firstResult;
    await page.route(parser, async route => {
        const req = route.request();
        attempts.push({key: req.headers()['idempotency-key'], body: req.postData()});
        const response = await route.fetch();
        assert.equal(response.status(), 200);
        if (attempts.length === 1) {
            firstResult = await response.json();
            await route.abort('failed');
        } else {
            await route.fulfill({response});
        }
    });
    await page.locator('#confirmImportCommitBtn').click();
    const retry = page.locator('#retryImportCommitBtn');
    await page.waitForFunction(()=>{
        const button=document.getElementById('retryImportCommitBtn');
        return !button.hidden && !button.disabled;
    });
    assert.equal(attempts.length, 1);
    assert.equal(firstResult.added, 1);
    assert(attempts[0].key);
    assert(await page.locator('#importModeSelect').isDisabled());
    assert.match(await page.locator('#importExecutionStatus').innerText(), /результат|подтвержд/i);
    const written = await (await request(receiver, '/api/patients?view=page')).json();
    assert.equal(written.page.total, before.page.total + 1);
    await retry.click();
    await page.locator('#importReviewModal').waitFor({state: 'hidden'});
    assert.equal(attempts.length, 2);
    assert.deepEqual(attempts[0], attempts[1]);
    await page.unroute(parser);
    await page.locator('#view-patients').waitFor();
    await page.locator('#searchInput').fill(source.transfer.name);
    await page.getByText(source.transfer.name, {exact: true}).first().waitFor();

    const modified = JSON.parse(attempts[0].body);
    modified.raw += ' ';
    if (modified.preserveIdsConfirmation) {
        modified.preserveIdsConfirmation.fileSha256 = digest(modified.raw);
    }
    const changed = await request(receiver, '/api/import/commit', {
        method: 'POST', headers: {'Idempotency-Key': attempts[0].key}, data: modified,
    });
    assert.equal(changed.status(), 422);
    assert.equal((await changed.json()).code,'IDEMPOTENCY_KEY_REUSED');
    const rereview = await (await request(receiver, '/api/import/preview', {method: 'POST', data: {raw}})).json();
    assert(rereview.previouslyImported);
    assert(rereview.duplicateImpact?.add?.blocked || rereview.possibleDuplicates?.add?.rows?.length);
    const final = await (await request(receiver, '/api/patients?view=page')).json();
    assert.equal(final.page.total, before.page.total + 1);
    checks.push('separate installation verifies exported signature, rejects invalid signature, previews unsigned and invalid files',
        'preview does not change medical records; five widths and two themes pass document axe',
        'transport abort after real 200 preserves unknown outcome, explicit exact body and key replay only',
        'changed body with old key rejected, imported file recognized, registry reread shows one new patient');

    const revoked = await open(browser, target, 'admin');
    const revokedPage = await browserPage(revoked);
    await importPreview(revokedPage, raw);
    const logout = await request(revoked, '/api/auth/logout', {method: 'POST'});
    assert([200, 204].includes(logout.status()));
    const denied = await request(revoked, '/api/import/commit', {method: 'POST', csrf: false,
        data: JSON.parse(attempts[0].body)});
    assert([401, 403].includes(denied.status()));
    await revoked.context.close();
    checks.push('expired session cannot commit reviewed import');
}

async function checkNativeZoom(exported) {
    phase = 'native Chromium zoom';
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'f11z-'));
    const extension = path.join(temporary, 'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension,'manifest.json'), JSON.stringify({
        manifest_version: 3, name: 'Synthetic localhost zoom', version: '1.0', permissions: ['tabs'],
        background: {service_worker: 'worker.js'},
    }));
    fs.writeFileSync(path.join(extension,'worker.js'), 'chrome.runtime.onInstalled.addListener(()=>{});');
    let context;
    try {
        context = await chromium.launchPersistentContext(path.join(temporary,'profile'), {
            headless: true, viewport: null, reducedMotion: 'reduce', ignoreDefaultArgs: ['--disable-extensions'],
            executablePath: process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            args: ['--window-size=1280,1000','--disable-background-networking',
                '--disable-extensions-except='+extension,'--load-extension='+extension],
        });
        await context.route('**/*', route => new URL(route.request().url()).origin === target.origin
            ? route.continue() : route.abort('blockedbyclient'));
        const login = await context.request.post(target.origin+'/api/auth/login', {
            data: {username: target.users.admin, password: target.password},
        });
        assert.equal(login.status(),200);
        const page = await browserPage({context,fixture: target});
        await importPreview(page,exported.bytes);
        const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
        const baseline = await page.evaluate(()=>devicePixelRatio);
        for (const theme of ['light','dark']) {
            await page.evaluate(value=>app.applyTheme(value),theme);
            for (const factor of [1,2,4]) {
                const zoom = await worker.evaluate(async ({url,factor})=>{
                    const tab=(await chrome.tabs.query({})).find(item=>item.url===url);
                    await chrome.tabs.setZoom(tab.id,factor);
                    return chrome.tabs.getZoom(tab.id);
                },{url:page.url(),factor});
                assert.equal(Math.round(zoom*100),factor*100);
                await page.waitForFunction(dpr=>Math.abs(devicePixelRatio-dpr)<0.01,baseline*factor);
                const shape=await page.evaluate(()=>({
                    width: innerWidth, scale: visualViewport.scale,
                    overflow: document.documentElement.scrollWidth > innerWidth,
                    visible: document.getElementById('importModeSelect').getBoundingClientRect().height > 0,
                }));
                assert(shape.visible && shape.scale===1 && !shape.overflow);
                nativeZoom.push({theme,percent:factor*100,width:shape.width});
            }
        }
    } finally {
        await context?.close();
        fs.rmSync(temporary,{recursive:true,force:true});
    }
    checks.push('native browser zoom through 400% holds import review in both themes');
}

async function main() {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking']});
    try {
        const sender = await open(browser, source, 'admin');
        const receiver = await open(browser, target, 'admin');
        const exported = await exports(browser, sender);
        await checkNativeZoom(exported);
        await importAndReplay(browser, receiver, exported);
        await changedExportRevision(sender);
        await obsoleteExport(sender);
        fs.writeFileSync(path.join(process.env.OUT, 'journey.local.json'), JSON.stringify({
            checks, layout, nativeZoom, exportSha256: digest(exported.bytes), browser: browser.version(),
        }));
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    const frames = error.stack?.match(/fast_track_f11\/journey\.cjs:\d+:\d+/g) || [];
    process.stderr.write(phase + ' ' + error.name + '\n' + frames.join('\n') + '\n');
    process.exitCode = 1;
});
