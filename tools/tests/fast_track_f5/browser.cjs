'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = JSON.parse(fs.readFileSync(process.env.F5_FIXTURE, 'utf8'));
const {origin, users, patients} = fixture;
assert(fixture.syntheticOnly);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const axeSource = fs.readFileSync(process.env.AXE_SOURCE, 'utf8');
const receipt = {checks: [], axe: [], nativeZoom: [], responseProofs: [], injectedTransportFaults: [],
    pageErrors: 0, unexpectedClinicalWrites: 0, legacyRegistryRequests: 0};
const output = path.dirname(process.env.F5_RECEIPT);
let active = 'initialization';
const clinicalWrites = [];
let csvBytes;
const dashboardPath = '/api/analytics/dashboard';
const detailsPath = '/api/analytics/details';
const exportPath = '/api/analytics/export';
function reply(page, pathname, matches = () => true, method = 'GET') {
    return page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === method && url.pathname === pathname && matches(url.searchParams);
    });
}

async function audited(response, kind) {
    assert.equal(response.status(), 200, kind);
    const raw = await response.body();
    receipt.responseProofs.push({kind, sha256: createHash('sha256').update(raw).digest('hex')});
    return JSON.parse(raw);
}

async function setup(context) {
    await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        return route.abort('blockedbyclient');
    });
    const page = await context.newPage();
    // Ordinary pagination is separated from deliberately concurrent scenarios.
    // Forward each actual loopback API request once; no retry or response edit.
    let queue = Promise.resolve();
    await page.route(origin + '/api/**', route => {
        const request = queue.then(async () => {
            const response = await route.fetch();
            if (new URL(route.request().url()).pathname === exportPath && response.ok()) {
                assert.equal(csvBytes, undefined, 'only one successful export in this browser suite');
                csvBytes = await response.body();
            }
            await route.fulfill({response});
        });
        queue = request.catch(() => {});
        return request;
    });
    page.setDefaultTimeout(15000);
    page.on('pageerror', () => { receipt.pageErrors++; });
    page.on('request', request => {
        const url = new URL(request.url());
        if (url.pathname === '/api/patients' && request.method() === 'GET' &&
            url.searchParams.get('view') !== 'page') receipt.legacyRegistryRequests++;
        if (url.pathname.startsWith('/api/patients') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method()))
            clinicalWrites.push({method: request.method(), path: url.pathname, body: request.postDataJSON()});
    });
    return page;
}

async function login(page, role) {
    await page.goto(origin);
    await page.locator('#username').fill(users[role]);
    await page.locator('#password').fill(fixture.password);
    await page.locator('#loginSubmit').click();
    await ready(page);
    assert.equal(await page.evaluate(() => app.user.username), users[role]);
}

async function ready(page, offset = null) {
    await page.waitForFunction(offset => app.appReady && !app.sessionExpired && !app.sessionReconciling &&
        app.currentView === 'patients' && app.patientListState === 'ready' &&
        (offset === null || app.patientPage.page.offset === offset), offset);
}

async function keyboard(locator) {
    await locator.waitFor({state: 'visible'});
    await locator.focus();
    assert(await locator.evaluate(el => el === document.activeElement && !el.disabled));
    await locator.press('Enter');
}

async function view(page, key) {
    await page.locator('[data-view="' + key + '"]').first().click();
    await page.locator('#view-' + key).waitFor({state: 'visible'});
}


async function axe(page) {
    return page.evaluate(async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']},
    })).violations.map(rule => ({id: rule.id, targets: rule.nodes.map(node => node.target)})));
}

async function theme(page, target) {
    const html = page.locator('html');
    if ((await html.getAttribute('data-theme')) !== target) await page.locator('#themeToggle').click();
    assert.equal(await html.getAttribute('data-theme'), target);
}

async function geometry(page, viewName) {
    return page.evaluate(viewName => {
        window.scrollTo(0, 0);
        const within = document.querySelector('#view-' + viewName);
        const title = within.querySelector('h1, #crumbPatientName');
        const heading = title.getBoundingClientRect();
        const bar = document.querySelector('.topbar').getBoundingClientRect();
        const hit = document.elementFromPoint(heading.left + Math.min(18, heading.width / 2), heading.top + heading.height / 2);
        return {width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth, scale: visualViewport.scale,
            titleVisible: heading.top + 1 >= bar.bottom && heading.bottom < innerHeight &&
                !!hit && (title === hit || title.contains(hit))};
    }, viewName);
}

async function narrower(page, viewName) {
    await page.evaluate(axeSource);
    for (const mode of ['light', 'dark']) {
        await theme(page, mode);
        for (const width of [320, 375, 768, 1440]) {
            await page.setViewportSize({width, height: 1000});
            const dimensions = await geometry(page, viewName);
            assert.equal(dimensions.width, width);
            assert.equal(dimensions.overflow, false);
            assert.equal(dimensions.titleVisible, true, viewName + ' ' + width + ' title must be above the fold');
            const violations = await axe(page);
            assert.deepEqual(violations, [], viewName + ' ' + mode + ' ' + width);
            receipt.axe.push({view: viewName, theme: mode, width, violations: 0, titleVisible: true});
            if (process.env.F5_SCREENSHOTS === '1' && (width === 1440 && mode === 'light' || width === 375 && mode === 'dark'))
                await page.screenshot({path: path.join(output, [viewName, mode, width].join('-') + '.png'), animations: 'disabled'});
        }
    }
}

async function nativeZoom() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clinic-f5-native-'));
    const extension = path.join(dir, 'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension, 'manifest.json'), JSON.stringify({manifest_version: 3,
        name: 'Local browser zoom verification', version: '1.0', permissions: ['tabs'],
        background: {service_worker: 'worker.js'}}));
    fs.writeFileSync(path.join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(()=>{});');
    let context;
    try {
        context = await chromium.launchPersistentContext(path.join(dir, 'profile'), {
            headless: true, viewport: null, reducedMotion: 'reduce', ignoreDefaultArgs: ['--disable-extensions'],
            executablePath: process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            args: ['--window-size=1280,1000', '--disable-background-networking',
                '--disable-extensions-except=' + extension, '--load-extension=' + extension],
        });
        const page = await setup(context);
        await login(page, 'doctor');
        const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout: 12000});
        const base = await page.evaluate(() => devicePixelRatio);
        for (const viewName of ['analytics']) {
            await worker.evaluate(async url => {
                const tab = (await chrome.tabs.query({})).find(item => item.url === url);
                if (!tab) throw new Error('missing synthetic browser tab');
                await chrome.tabs.setZoom(tab.id, 1);
            }, page.url());
            await page.waitForFunction(target => Math.abs(devicePixelRatio - target) < 0.01, base);
            await view(page, viewName);
            await analyticsReady(page);
            for (const mode of ['light', 'dark']) {
                await theme(page, mode);
                for (const factor of [1, 2, 4]) {
                    const zoom = await worker.evaluate(async ({url, factor}) => {
                        const tab = (await chrome.tabs.query({})).find(item => item.url === url);
                        if (!tab) throw new Error('missing synthetic browser tab');
                        await chrome.tabs.setZoom(tab.id, factor);
                        return chrome.tabs.getZoom(tab.id);
                    }, {url: page.url(), factor});
                    assert.equal(Math.round(zoom * 100), 100 * factor);
                    await page.waitForFunction(target => Math.abs(devicePixelRatio - target) < 0.01, base * factor);
                    const dimensions = await geometry(page, viewName);
                    assert.equal(dimensions.overflow, false);
                    assert.equal(dimensions.scale, 1);
                    assert.equal(dimensions.titleVisible, true);
                    receipt.nativeZoom.push({view: viewName, theme: mode, percent: factor * 100, viewport: dimensions.width});
                }
            }
        }
    } finally {
        await context?.close();
        fs.rmSync(dir, {recursive: true, force: true});
    }
}

const detailsPanel = page => page.locator('#analyticsDetailsPanel');
const dashboard = page => page.locator('.analytics-dashboard');
const metric = (payload, code) => payload.summary.cards.find(item => item.code === code);
const overdue = params => params.get('type') === 'overdue_events';
const detailReply = (page, offset = 0) => reply(page, detailsPath,
    params => overdue(params) && params.get('limit') === '50' && Number(params.get('offset') || 0) === offset);

async function analyticsReady(page) {
    await page.locator('#view-analytics').waitFor();
    await dashboard(page).and(page.locator('[aria-busy="false"]')).waitFor();
    await page.locator('#analyticsStatsRow').getByRole('button').first().waitFor();
}

async function openAnalytics(page, control) {
    const [dash, detail] = await Promise.all([reply(page, dashboardPath), detailReply(page), keyboard(control)]);
    assert.equal(dash.status(), 200);
    assert.equal(detail.status(), 200);
    await analyticsReady(page);
    await detailsPanel(page).getByRole('button', {name: 'Открыть пациента', exact: true}).first().waitFor();
    return {cards: await audited(dash, 'scoped analytics dashboard'),
        rows: await audited(detail, 'scoped analytics drilldown')};
}

function assertScoped(data, doctor = false) {
    assert(data.items.length > 0);
    assert(data.items.every(item => item.patient_id !== patients.foreign.id));
    assert(data.items.every(item => !doctor || item.patient_id !== patients.paged.id));
    assert(data.items.every(item => !doctor || item.patient_id !== patients.quality.id));
}

function assertOverdueRows(data) {
    assert.equal(data.detail_type, 'overdue_events');
    const count = new Set(data.items.map(item => item.patient_id)).size;
    assert(data.items.length > count, 'event rows must be more numerous than patients');
    assert.deepEqual(data.items.map(item => item.days_overdue),
        data.items.map(item => item.days_overdue).sort((left, right) => right - left));
}

async function selectPatient(page, patient) {
    const row = detailsPanel(page).locator('.analytics-detail').filter({hasText: patient.name}).first();
    const action = row.getByRole('button', {name: 'Открыть пациента', exact: true});
    const [read] = await Promise.all([reply(page, '/api/patients/' + patient.id), keyboard(action)]);
    const body = await audited(read, 'analytics object authorization');
    assert.equal(body.id, patient.id);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id, patient.id);
    await page.locator('#view-patient').getByText(patient.name, {exact: true}).first().waitFor();
    return body;
}

async function verifyCSV(page, context, dashboardResponse) {
    const button = page.locator('#analyticsExportBtn');
    await page.locator('#analyticsReportType').selectOption('data_quality');
    await page.locator('#analyticsReportDetail').selectOption('details');
    const [response, download] = await Promise.all([
        reply(page, exportPath, () => true, 'POST'), page.waitForEvent('download'), keyboard(button),
    ]);
    assert.equal(response.status(), 200);
    assert.deepEqual(response.request().postDataJSON(), {reportType: 'data_quality', detailLevel: 'details'});
    assert.equal(new URL(response.url()).searchParams.get('stage'), 'exam');
    assert.match(download.suggestedFilename(), /^analytics_data_quality_\d{8}_\d{6}\.csv$/);
    assert.match(response.headers()['content-disposition'], /^attachment; filename="analytics_data_quality_\d{8}_\d{6}\.csv"$/);
    assert.match(response.headers()['content-type'], /^text\/csv; charset=utf-8/);
    const bytes = await fs.promises.readFile(await download.path());
    assert.deepEqual(bytes, csvBytes);
    assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf]);
    const rows = bytes.subarray(3).toString('utf8').trim().split('\r\n');
    assert.equal(rows.shift(), 'severity,code,label,count');
    const counts = new Map();
    for (const row of rows) {
        const [severity, code, label, count] = row.split(',');
        assert.equal(label, severity || code);
        assert(/^[0-9]+$/.test(count));
        if (code) counts.set(code, Number(count));
    }
    assert(counts.size >= 2 && counts.has('overdue_event'));
    assert.equal([...counts.values()].reduce((sum, count) => sum + count, 0),
        dashboardResponse.data_quality.summary.total_issues);
    for (const patient of Object.values(patients)) assert(!bytes.includes(patient.name));
    const rejected = await context.request.post(origin + exportPath, {
        headers: {Origin: origin}, data: {reportType: 'data_quality', detailLevel: 'details'},
    });
    assert.equal(rejected.status(), 403);
    assert(!rejected.headers()['content-disposition']);
    receipt.csv = {rows: rows.length, detailCodes: counts.size, exactDownloadBytes: true,
        sha256: createHash('sha256').update(bytes).digest('hex'), bom: true, authControl: 403};
}

async function manager(page, context) {
    active = 'management Pulse to permitted analytics';
    await login(page, 'head');
    await view(page, 'pulse');
    await page.locator('#f1PulseStage').waitFor();
    const stageResponse = reply(page, '/api/analytics/summary',
        params => params.get('stage') === 'exam');
    await page.locator('#f1PulseStage').selectOption('exam');
    const selected = page.locator('#f1Pulse [data-f1-metric="overdue_events"]');
    await selected.waitFor();
    // The clinical attention summary and analytics use distinct contracts.
    await stageResponse;
    await keyboard(selected);
    const initial = await openAnalytics(page, page.locator('#f5PulseAnalytics'));
    assert.equal(initial.cards.meta.filters.stage, 'exam');
    assert.equal(initial.rows.total, metric(initial.cards, 'overdue_events').value);
    assert.equal(initial.rows.offset, 0);
    assert.equal(initial.rows.items.length, 50);
    assert(initial.rows.total > 50);
    assertScoped(initial.rows);
    assertOverdueRows(initial.rows);

    active = 'manager pagination and browser history';
    const [next] = await Promise.all([detailReply(page, initial.rows.items.length),
        keyboard(detailsPanel(page).getByRole('button', {name: 'Следующая страница', exact: true}))]);
    assert.equal(next.status(), 200);
    const pageTwo = await audited(next, 'scoped second analytics page');
    assert.equal(pageTwo.total, initial.rows.total);
    assertScoped(pageTwo);
    assertOverdueRows({ ...pageTwo, items: [...initial.rows.items, ...pageTwo.items] });
    const patient = patients.overdue;
    assert(pageTwo.items.some(row => row.patient_id === patient.id), 'fixture must place recent overdue events on second page');
    await selectPatient(page, patient);

    const [restored, returnedDash] = await Promise.all([
        detailReply(page, 50), reply(page, dashboardPath), page.goBack(),
    ]);
    assert.equal(restored.status(), 200);
    assert.equal(returnedDash.status(), 200);
    assert.deepEqual((await audited(restored, 'fresh back to drilldown')).items, pageTwo.items);
    await detailsPanel(page).getByRole('button', {name: 'Предыдущая страница', exact: true}).waitFor();
    await page.waitForFunction(id => document.activeElement?.dataset.analyticsPatient === id, patient.id);

    active = 'passive same-identity analytics reconciliation';
    const before = await page.evaluate(() => ({epoch: app.sessionEpoch || 0, user: app.user.id,
        position: document.activeElement.dataset.analyticsPosition}));
    // Trigger the public lifecycle event without re-focusing or modifying the app.
    // The real session, dashboard and details responses must pass the normal barrier.
    const [identity, newDashboard, newDetails] = await Promise.all([
        reply(page, '/api/auth/me'), reply(page, dashboardPath), detailReply(page, 50),
        page.evaluate(() => window.dispatchEvent(new FocusEvent('focus'))),
    ]);
    assert.equal(identity.status(), 200);
    assert.equal((await identity.json()).user.id, before.user);
    assert.equal((await audited(newDashboard, 'dashboard after passive session recheck')).meta.filters.stage, 'exam');
    assert.deepEqual((await audited(newDetails, 'page two after passive session recheck')).items, pageTwo.items);
    await page.waitForFunction(({user, epoch, position, patient}) => app.user?.id === user &&
        app.sessionEpoch > epoch && !app.sessionExpired && !app.sessionReconciling &&
        document.activeElement?.dataset.analyticsPatient === patient &&
        document.activeElement.dataset.analyticsPosition === position, {...before, patient: patient.id}).catch(async error => {
        const diagnostic = await page.evaluate(({user, epoch, position, patient}) => ({
            sameUser: app.user?.id === user, freshEpoch: app.sessionEpoch > epoch, expired: app.sessionExpired,
            reconciling: app.sessionReconciling, view: app.currentView, offset: app.analytics.details?.offset,
            tag: document.activeElement?.tagName, id: document.activeElement?.id,
            expectedPosition: position, activePosition: document.activeElement?.dataset.analyticsPosition,
            matchesPatient: document.activeElement?.dataset.analyticsPatient === patient,
        }), {...before, patient: patient.id});
        throw new Error('Passive focus restoration failed: ' + JSON.stringify(diagnostic), {cause: error});
    });
    assert.equal(clinicalWrites.length, 0);
    receipt.checks.push('passive same-user session recheck reloads real scoped dashboard and page two, restoring exact row focus and stage without writes');

    active = 'manager browser Forward and explicit return';
    const [forward] = await Promise.all([reply(page, '/api/patients/' + patient.id), page.goForward()]);
    assert.equal((await audited(forward, 'analytics history forwards object')).id, patient.id);
    const [explicit, fresh] = await Promise.all([
        detailReply(page, 50), reply(page, dashboardPath), keyboard(page.locator('#backToF1')),
    ]);
    assert.equal(explicit.status(), 200);
    assert.equal(fresh.status(), 200);
    await analyticsReady(page);

    active = 'CSV bytes, context and rejection';
    await verifyCSV(page, context, await audited(fresh, 'fresh dashboard before export'));
    receipt.checks.push('head Pulse, supported stage, full server pagination and event units, audited card, Back and Forward, fresh explicit return and real scoped CSV');
}

async function doctor(page, context) {
    active = 'doctor My Day to personal analytics';
    await login(page, 'doctor');
    await view(page, 'myDay');
    const eventMode = page.locator('#f1MyDay [data-f1-doctor-mode="events"]');
    await keyboard(eventMode);
    const initial = await openAnalytics(page, page.locator('#f5MyDayAnalytics'));
    assertScoped(initial.rows, true);
    assertOverdueRows(initial.rows);
    assert.equal(initial.cards.workload.rows.length, 1);
    assert.equal(initial.cards.workload.rows[0].doctor_user_id, fixture.accountIds.doctor);
    const outside = await context.request.get(origin + '/api/patients/' + patients.foreign.id);
    assert.equal(outside.status(), 404);
    assert(!(await outside.text()).includes(patients.foreign.name));
    const empty = await context.request.get(origin + detailsPath,
        {params: {type: 'overdue_events', doctor_user_id: fixture.accountIds.foreign}});
    assert.equal(empty.status(), 200);
    assert.equal((await empty.json()).total, 0);
    const forbidden = await context.request.get(origin + '/api/audit');
    assert.equal(forbidden.status(), 403);

    active = 'doctor real write and refreshed aggregate';
    const [next] = await Promise.all([detailReply(page, 50),
        keyboard(detailsPanel(page).getByRole('button', {name: 'Следующая страница', exact: true}))]);
    assert.equal(next.status(), 200);
    const patient = patients.overdue;
    const id = patient.eventIds.at(-1);
    const payload = await selectPatient(page, patient);
    const event = payload.history[0].events.find(item => item.id === id);
    assert(event && !event.actualDate);
    const controls = page.locator('#patient-event-' + id);
    await controls.getByRole('textbox', {name: 'Фактическая дата', exact: true}).evaluate((node, value) => {
        node.value = value;
        node.dispatchEvent(new Event('input', {bubbles: true}));
    }, fixture.today);
    assert.equal(clinicalWrites.length, 0);
    const eventPath = '/api/patients/' + patient.id + '/events/' + id;
    const [saved] = await Promise.all([reply(page, eventPath, () => true, 'PATCH'),
        keyboard(controls.getByRole('button', {name: 'Сохранить: Фактическая дата', exact: true}))]);
    assert.equal(saved.status(), 200);
    const savedEvent = (await saved.json()).history[0].events.find(item => item.id === id);
    assert.equal(savedEvent.actualDate, fixture.today);
    assert.equal(savedEvent.version, event.version + 1);
    assert.deepEqual(clinicalWrites, [{method: 'PATCH', path: eventPath,
        body: {field: 'actualDate', value: fixture.today, version: event.version}}]);
    await page.waitForFunction(() => !app.clinicalWriteInFlight);
    const [back, refreshed] = await Promise.all([detailReply(page, 50), reply(page, dashboardPath),
        keyboard(page.locator('#backToF1'))]);
    assert.equal(back.status(), 200);
    assert.equal(refreshed.status(), 200);
    assert.equal((await audited(back, 'fresh post-commit details')).total, initial.rows.total - 1);
    assert.equal(metric(await audited(refreshed, 'fresh post-commit dashboard'), 'overdue_events').value,
        metric(initial.cards, 'overdue_events').value - 1);
    await analyticsReady(page);
    const [period] = await Promise.all([reply(page, dashboardPath,
        params => params.has('date_from') && params.has('date_to')),
    keyboard(page.locator('[data-analytics-period="7"]'))]);
    assert.equal(period.status(), 200);
    const dated = await period.json();
    assert.equal(metric(dated, 'overdue_events').kind, 'current');
    assert.equal(metric(dated, 'overdue_events').value, metric(initial.cards, 'overdue_events').value - 1);
    assert.equal(dated.meta.timezone, initial.cards.meta.timezone);
    assert.equal(dated.trend.rows.length, 7);
    receipt.checks.push('doctor My Day, personal server scope including empty and foreign filters, real 404 and 403, only explicit versioned write, fresh aggregate drops one event');
}

async function authority(page, context, browser) {
    active = 'stale analytic row and live object-scope reassignment';
    await login(page, 'doctor');
    await view(page, 'myDay');
    await keyboard(page.locator('#f1MyDay [data-f1-doctor-mode="events"]'));
    await openAnalytics(page, page.locator('#f5MyDayAnalytics'));
    const [next] = await Promise.all([detailReply(page, 50),
        keyboard(detailsPanel(page).getByRole('button', {name: 'Следующая страница', exact: true}))]);
    assert.equal(next.status(), 200);
    const patient = patients.revoked;
    const action = detailsPanel(page).locator('.analytics-detail').filter({hasText: patient.name})
        .first().getByRole('button', {name: 'Открыть пациента', exact: true});
    await action.waitFor();
    const object = '/api/patients/' + patient.id;
    const admin = await browser.newContext({reducedMotion: 'reduce', viewport: {width: 1440, height: 1000}});
    try {
        const adminPage = await setup(admin);
        await login(adminPage, 'admin');
        const current = await admin.request.get(origin + object);
        assert.equal(current.status(), 200);
        const baseline = await current.json();
        const csrf = (await admin.cookies()).find(item => item.name === 'clinic_csrf');
        const changed = await admin.request.put(origin + object + '/treating-doctor', {
            data: {userId: fixture.accountIds.foreign, expectedVersion: baseline.version},
            headers: {'X-CSRF-Token': csrf.value},
        });
        assert.equal(changed.status(), 200);
        assert.equal((await changed.json()).treatingDoctorUserId, fixture.accountIds.foreign);
    } finally {
        await admin.close();
    }
    const [objectDenied] = await Promise.all([reply(page, object), keyboard(action)]);
    assert.equal(objectDenied.status(), 404);
    assert(!(await objectDenied.text()).includes(patient.name));
    await action.waitFor({state: 'detached'});
    assert(!await page.locator('#view-patient').isVisible());
    assert(!(await detailsPanel(page).textContent()).includes(patient.name));

    active = 'late real HTTP 200 after session revoke';
    let release;
    let received;
    let forwarded;
    const gate = new Promise(resolve => { release = resolve; });
    const fetched = new Promise(resolve => { received = resolve; });
    const delivered = new Promise(resolve => { forwarded = resolve; });
    const hold = async route => {
        const response = await route.fetch();
        received(response.status());
        await gate;
        await route.fulfill({response});
        forwarded();
    };
    await page.route(origin + dashboardPath + '?*', hold, {times: 1});
    const old = page.waitForResponse(response => response.status() === 200 &&
        new URL(response.url()).pathname === dashboardPath);
    try {
        await keyboard(page.locator('#analyticsRefreshBtn'));
        assert.equal(await fetched, 200);
        const csrf = (await context.cookies()).find(item => item.name === 'clinic_csrf');
        const logout = await context.request.post(origin + '/api/auth/logout', {
            headers: {'X-CSRF-Token': csrf.value},
        });
        assert.equal(logout.status(), 200);
        const [denied] = await Promise.all([reply(page, dashboardPath), keyboard(page.locator('#analyticsRefreshBtn'))]);
        assert.equal(denied.status(), 401);
        await page.locator('#loginScreen').waitFor();
        assert(await page.evaluate(() => app.sessionExpired || !app.user));
    } finally {
        release();
    }
    await Promise.all([old, delivered]);
    assert.equal(await page.locator('#analyticsStatsRow .analytics-kpi').count(), 0);
    assert.equal(await detailsPanel(page).locator('.analytics-detail').count(), 0);
    assert(!await page.locator('#view-patient').isVisible());
    receipt.checks.push('authorized reassignment causes real object 404 and removes stale patient actions; live session revoke returns 401 and held real 200 cannot restore data');
}


async function anonymous(browser) {
    const context = await browser.newContext();
    try {
        for (const resource of [dashboardPath, detailsPath + '?type=overdue_events']) {
            assert.equal((await context.request.get(origin + resource)).status(), 401);
        }
        const denied = await context.request.post(origin + exportPath, {
            headers: {Origin: origin}, data: {reportType: 'data_quality', detailLevel: 'details'},
        });
        assert.equal(denied.status(), 401);
        assert(!denied.headers()['content-disposition']);
        receipt.csv.anonymous = 401;
    } finally {
        await context.close();
    }
    receipt.checks.push('anonymous real 401 on dashboard, details and CSV; failed export delivers no file');
}

async function main() {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking']});
    receipt.chromium = browser.version();
    try {
        for (const exercise of [manager, doctor, authority]) {
            const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
            const page = await setup(context);
            await exercise(page, context, browser);
            process.stdout.write('Browser group complete: ' + exercise.name + '\n');
            await page.unrouteAll({behavior: 'wait'});
            await context.close();
        }
        await anonymous(browser);
        const visual = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const presentation = await setup(visual);
        await login(presentation, 'head');
        active = 'responsive analytics';
        await view(presentation, 'analytics');
        await analyticsReady(presentation);
        await narrower(presentation, 'analytics');
        process.stdout.write('Browser group complete: responsive Axe checks\n');
        await presentation.unrouteAll({behavior: 'wait'});
        await visual.close();
    } finally {
        await browser.close();
    }
    active = 'native zoom';
    await nativeZoom();
    assert.equal(clinicalWrites.length, 1);
    assert.equal(receipt.legacyRegistryRequests, 0);
    assert.equal(receipt.pageErrors, 0);
    receipt.checks.push('keyboard actions, light and dark responsive axe states and real Chrome native zoom at 100, 200 and 400 percent');
    fs.writeFileSync(process.env.F5_RECEIPT, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600});
}

main().catch(error => {
    process.stderr.write(active + '\n' + error.stack + '\n');
    process.exitCode = 1;
});
