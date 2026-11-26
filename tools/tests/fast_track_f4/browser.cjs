'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = JSON.parse(fs.readFileSync(process.env.F4_FIXTURE, 'utf8'));
const {origin, users, patients} = fixture;
assert(fixture.syntheticOnly);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const axeSource = fs.readFileSync(process.env.AXE_SOURCE, 'utf8');
const receipt = {checks: [], axe: [], nativeZoom: [], responseProofs: [], injectedTransportFaults: [],
    pageErrors: 0, unexpectedClinicalWrites: 0, legacyRegistryRequests: 0};
const output = path.dirname(process.env.F4_RECEIPT);
let active = 'initialization';
const clinicalWrites = [];
const readWrites = [];
const schedulePath = '/api/patients/workspace/events';
const inboxPath = '/api/internal-notifications';
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
        if (url.pathname.startsWith(inboxPath) && request.method() === 'POST')
            readWrites.push(url.pathname);
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
    await locator.focus();
    assert(await locator.evaluate(el => el === document.activeElement && !el.disabled));
    await locator.press('Enter');
}

async function view(page, key) {
    await page.locator('[data-view="' + key + '"]').first().click();
    await page.locator('#view-' + key).waitFor({state: 'visible'});
}


function eventRow(page, id) { return page.locator('#calendarPanel [data-workspace-row="' + id + '"]'); }

async function calendarReady(page) {
    await page.locator('#calendarPanel .workspace-result').waitFor();
}

async function scheduleOpen(page, locator, match = () => true) {
    const [read] = await Promise.all([reply(page, schedulePath, match), keyboard(locator)]);
    const data = await audited(read, 'schedule read');
    await calendarReady(page);
    return data;
}

async function scheduleSearch(page, name) {
    await page.locator('#eventsWorkspaceQuery').fill(name);
    return scheduleOpen(page, page.locator('#eventsWorkspaceSearch button[type=submit]'),
        params => (params.get('q') || '') === name && !params.has('cursor'));
}

async function openEvent(page, button, patient, eventId) {
    const [read] = await Promise.all([reply(page, '/api/patients/' + patient.id), keyboard(button)]);
    const data = await audited(read, 'fresh event object');
    assert.equal(data.id, patient.id);
    assert(data.history.some(occ => occ.events.some(event => event.id === eventId && !event.actualDate)));
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentTab === 'events' &&
        document.activeElement?.id === 'patient-event-' + id, eventId);
    return data;
}

async function schedule(page, context) {
    active = 'schedule completeness and return';
    await view(page, 'myDay');
    const first = await scheduleOpen(page, page.locator('#f4MyDaySchedule'));
    assert(first.page.total > 50 && first.events.length === 50 && first.page.hasNext);
    const more = page.locator('#calendarPanel .pagination').getByRole('button', {name: 'Далее', exact: true});
    const second = await scheduleOpen(page, more, params => params.has('cursor'));
    assert.equal(second.page.offset, first.events.length);
    assert.equal(second.page.total, first.page.total);
    const combined = [...first.events, ...second.events];
    assert.equal(new Set(combined.map(row => row.id)).size, first.page.total);
    assert(patients.schedule.eventIds.every(id => combined.some(row => row.id === id)));
    assert(combined.every(row => row.patientId !== patients.foreign.id));
    assert.deepEqual(combined.map(row => [row.date, row.time || ''].join(' ')),
        combined.map(row => [row.date, row.time || ''].join(' ')).sort());
    // Exercise the route on the second page to cover cursor-backed history.
    const later = second.events.find(row => row.patientId === patients.schedule.id);
    await openEvent(page, eventRow(page, later.id).getByRole('button'), patients.schedule, later.id);
    const [restoredReply] = await Promise.all([reply(page, schedulePath, params => params.has('cursor')), page.goBack()]);
    const restored = await audited(restoredReply, 'schedule back to second page');
    assert.equal(restored.page.offset, second.page.offset);
    await eventRow(page, later.id).waitFor();
    await page.goForward();
    await page.locator('#view-patient').waitFor();
    await scheduleOpen(page, page.locator('#backToF1'), params => params.has('cursor'));
    await page.locator('#calendarPanel [data-workspace-row]').first().waitFor();

    const denied = await context.request.get(origin + '/api/patients/' + patients.foreign.id);
    assert.equal(denied.status(), 404);
    assert(!(await denied.text()).includes(patients.foreign.name));

    const today = await scheduleSearch(page, patients.schedule.name);
    const firstId = patients.schedule.eventIds[0];
    assert.equal(today.events.find(event => event.id === firstId).date, fixture.today);
    assert.equal(today.events.find(event => event.id === firstId).time || '', '');
    await eventRow(page, firstId).getByText('Время не указано', {exact: false}).waitFor();
    assert.equal(today.page.total, patients.schedule.eventIds.length);
    receipt.schedule = {total: first.page.total, pageSizes: [first.events.length, second.events.length],
        uniqueComplete: true, sortedDatesAndTime: true, syntheticExplicitNoTime: true};
    receipt.checks.push('real scoped 50+ events, exhaustive two-page IDs, chronology, no-time and second-page Back/Forward + explicit return');
}

async function saveFact(page) {
    active = 'explicit versioned schedule action';
    const patient = patients.overdue;
    const id = patient.eventIds.at(-1);
    await scheduleSearch(page, patient.name);
    const data = await openEvent(page, eventRow(page, id).getByRole('button'), patient, id);
    const event = data.history[0].events.find(item => item.id === id);
    const controls = page.locator('#patient-event-' + id);
    await controls.getByRole('textbox', {name: 'Фактическая дата', exact: true}).evaluate((node, value) => {
        node.value = value;
        node.dispatchEvent(new Event('input', {bubbles: true}));
    }, fixture.today);
    assert.equal(clinicalWrites.length, 0);
    const eventPath = '/api/patients/' + patient.id + '/events/' + id;
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const hold = async route => {
        if (route.request().method() === 'PATCH') await held;
        await route.fallback();
    };
    await page.route(origin + eventPath, hold);
    const confirmed = reply(page, eventPath, () => true, 'PATCH');
    try {
        const sent = page.waitForRequest(request =>
            request.method() === 'PATCH' && new URL(request.url()).pathname === eventPath);
        await Promise.all([sent, controls.getByRole('button', {name: 'Сохранить: Фактическая дата', exact: true}).click()]);
        assert(await page.evaluate(() => !!app.clinicalWriteInFlight));
        await keyboard(page.locator('#backToF1'));
        await page.getByText('Дождитесь результата сохранения.', {exact: false}).first().waitFor();
        assert.equal(await page.evaluate(() => app.currentView), 'patient');
        assert.equal(clinicalWrites.length, 1);
    } finally {
        release();
    }
    const mutation = await confirmed;
    await page.unroute(origin + eventPath, hold);
    assert.equal(mutation.status(), 200);
    assert.equal((await mutation.json()).history[0].events.find(item => item.id === id).actualDate, fixture.today);
    assert.deepEqual(clinicalWrites, [{method: 'PATCH', path: eventPath,
        body: {field: 'actualDate', value: fixture.today, version: event.version}}]);
    await page.waitForFunction(({patientId, id, today}) => !app.clinicalWriteInFlight &&
        app.getPatient(patientId).history[0].events.find(item => item.id === id).actualDate === today,
    {patientId: patient.id, id, today: fixture.today});
    const fresh = await scheduleOpen(page, page.locator('#backToF1'), params => params.get('q') === patient.name);
    assert(!fresh.events.some(item => item.id === id));
    assert(fresh.events.some(item => item.id === patient.eventIds[0]));
    await eventRow(page, id).waitFor({state: 'hidden'});
    await keyboard(page.locator('#backToScheduleSource'));
    await page.locator('#view-myDay').waitFor();
    const [workspace] = await Promise.all([reply(page, schedulePath),
        keyboard(page.locator('[data-f1-doctor-mode=events]'))]);
    assert(!(await audited(workspace, 'My Day after confirmed fact')).events.some(item => item.id === id));
    await scheduleOpen(page, page.locator('#f4MyDaySchedule'));
    receipt.postCommit = {absentFromSchedule: true, absentFromMyDay: true,
        pendingWriteStayedOnCard: true, explicitWrites: 1};
    receipt.checks.push('explicit versioned event fact, pending navigation blocked, real schedule and My Day refresh without completed event');
}

function notificationArticle(page, item) {
    return page.locator('#internalNotificationsFullList article').filter({hasText: item.patient_name})
        .filter({has: page.getByRole('heading', {name: item.title, exact: true})});
}

async function searchInbox(page, patient) {
    const inboxRead = reply(page, inboxPath);
    await page.locator('#internalNotificationsSearchInput').fill(patient.name);
    const response = await inboxRead;
    assert.equal(response.status(), 200);
    const data = await response.json();
    assert(data.items.length && data.items.every(row => row.patient_id === patient.id));
    await notificationArticle(page, data.items[0]).first().waitFor();
    return data;
}

async function notifications(page) {
    active = 'notification object and explicit read audit';
    await keyboard(page.locator('#scheduleNotifications'));
    await page.locator('#notificationsInboxPanel').waitFor();
    await page.locator('#internalNotificationsFullList article').first().waitFor();
    const patient = patients.overdue;
    const initial = await searchInbox(page, patient);
    assert(!initial.items.some(item => item.source_type === 'event' && item.source_id === patient.eventIds.at(-1)));
    const item = initial.items.find(item => item.source_type === 'event' && item.source_id === patient.eventIds[0]);
    assert(item && !item.read_at);
    const row = notificationArticle(page, item);
    await openEvent(page, row.locator('[data-internal-notification-open]'), patient, item.source_id);

    const historyBefore = await page.evaluate(() => ({length: history.length, state: history.state}));
    await keyboard(page.locator('#internalNotificationsBell'));
    const drawer = page.locator('#internalNotificationsDrawer');
    const drawerRow = drawer.locator('article').filter({hasText: item.patient_name})
        .filter({has: page.getByRole('heading', {name: item.title, exact: true})});
    await openEvent(page, drawerRow.locator('[data-internal-notification-open]'), patient, item.source_id);
    await drawer.waitFor({state: 'hidden'});
    assert.deepEqual(await page.evaluate(() => ({length: history.length, state: history.state})), historyBefore);
    assert.equal(readWrites.length, 0);
    const [returnedReply] = await Promise.all([reply(page, inboxPath), keyboard(page.locator('#backToF1'))]);
    assert.equal(returnedReply.status(), 200);
    assert(!(await returnedReply.json()).items.find(i => i.id === item.id).read_at);
    await row.getByRole('button', {name: 'Прочитано', exact: true}).waitFor();

    const [marked, refreshedReply] = await Promise.all([
        reply(page, inboxPath + '/' + item.id + '/read', () => true, 'POST'),
        reply(page, inboxPath),
        keyboard(row.getByRole('button', {name: 'Прочитано', exact: true})),
    ]);
    assert.equal(marked.status(), 200);
    assert.equal(refreshedReply.status(), 200);
    const refreshed = await refreshedReply.json();
    assert(refreshed.items.find(i => i.id === item.id).read_at);
    assert.equal(refreshed.summary.unread_count, initial.summary.unread_count - 1);
    await row.getByRole('button', {name: 'Прочитано', exact: true}).waitFor({state: 'hidden'});
    assert.deepEqual(readWrites, [inboxPath + '/' + item.id + '/read']);
    receipt.notifications = {exactEventFocus: true, openDidNotMarkRead: true, serverSummaryUpdated: true,
        completedEventNoticeAbsent: true, explicitReadRequests: 1, sameRouteDrawerVerifiedWithoutNewHistory: true};
    receipt.checks.push('server-generated internal event notifications, fresh exact event/return, one explicit read and server summary');
}

async function conflicts(page) {
    active = 'calendar bounded cursor and query recovery';
    await view(page, 'calendar');
    await scheduleSearch(page, '');
    const more = page.locator('#calendarPanel .pagination').getByRole('button', {name: 'Далее', exact: true});
    let expired = 0;
    const cursor = async route => {
        if (new URL(route.request().url()).searchParams.has('cursor')) {
            expired++;
            return route.fulfill({status: 410, json: {code: 'CURSOR_EXPIRED'}});
        }
        return route.fallback();
    };
    await page.route(origin + schedulePath + '?*', cursor);
    const reset = await scheduleOpen(page, more, params => !params.has('cursor'));
    assert.equal(reset.page.offset, 0);
    assert.equal(expired, 1);
    await page.unroute(origin + schedulePath + '?*', cursor);
    let attempts = 0;
    const conflict = route => {
        attempts++;
        return route.fulfill({status: 409, json: {code: 'PATIENT_QUERY_CHANGED'}});
    };
    await page.route(origin + schedulePath + '?*', conflict);
    await keyboard(more);
    await page.locator('#calendarPanel').getByRole('alert').waitFor();
    assert.equal(await page.locator('#calendarPanel [data-workspace-row]').count(), 0);
    assert.equal(attempts, 2);
    await page.unroute(origin + schedulePath + '?*', conflict);
    await scheduleOpen(page, page.locator('#calendarPanel').getByRole('button', {name: 'Повторить загрузку'}));
    receipt.injectedTransportFaults.push({scenario: 'calendar cursor 410', attempts: expired},
        {scenario: 'calendar repeated ordinary 409', attempts});
    receipt.checks.push('bounded 410 and repeated 409, old actions removed, keyboard recovery');
}


async function revokedNavigation(page, browser) {
    active = 'authorized reassignment during schedule navigation';
    const patient = patients.revoked;
    const eventId = patient.eventIds[0];
    const data = await scheduleSearch(page, patient.name);
    assert(data.events.some(row => row.id === eventId));
    const admin = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
    try {
        const adminPage = await setup(admin);
        await login(adminPage, 'admin');
        const objectPath = '/api/patients/' + patient.id;
        const current = await admin.request.get(origin + objectPath);
        assert.equal(current.status(), 200);
        const baseline = await current.json();
        const csrf = (await admin.cookies()).find(item => item.name === 'clinic_csrf');
        await page.route(origin + objectPath, async route => {
            try {
                const changed = await admin.request.put(origin + objectPath + '/treating-doctor', {
                    data: {userId: fixture.accountIds.foreign, expectedVersion: baseline.version},
                    headers: {'X-CSRF-Token': csrf.value},
                });
                assert.equal(changed.status(), 200);
                assert.equal((await changed.json()).treatingDoctorUserId, fixture.accountIds.foreign);
                await route.fallback();
            } catch (error) {
                await route.abort();
                throw error;
            }
        }, {times: 1});
        const [denied] = await Promise.all([reply(page, objectPath), keyboard(eventRow(page, eventId).getByRole('button'))]);
        assert.equal(denied.status(), 404);
        assert(!(await denied.text()).includes(patient.name));
        await eventRow(page, eventId).waitFor({state: 'hidden'});
        assert.equal(await page.locator('#view-patient').isVisible(), false);
        await view(page, 'calendar');
        await calendarReady(page);
        await eventRow(page, eventId).waitFor({state: 'hidden'});
    } finally {
        await admin.close();
    }
    receipt.checks.push('actual neutral 404 after authorized reassignment during navigation; inaccessible appointment row no longer visible');
}

async function expiredSession(page, context) {
    active = 'real calendar session revoke';
    const csrf = (await context.cookies()).find(item => item.name === 'clinic_csrf');
    const logout = await context.request.post(origin + '/api/auth/logout', {headers: {'X-CSRF-Token': csrf.value}});
    assert.equal(logout.status(), 200);
    const [denied] = await Promise.all([reply(page, schedulePath), keyboard(page.locator('#eventsWorkspaceRefresh'))]);
    assert.equal(denied.status(), 401);
    await page.locator('#loginScreen').waitFor();
    assert(!await page.locator('#view-calendar').isVisible() || await page.evaluate(() => app.sessionExpired));
    assert.equal(await page.locator('#calendarPanel [data-workspace-row]').count(), 0);
    receipt.checks.push('real session revoke returns 401 and clears old schedule actions');
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
            if (process.env.F4_SCREENSHOTS === '1' && (width === 1440 && mode === 'light' || width === 375 && mode === 'dark'))
                await page.screenshot({path: path.join(output, [viewName, mode, width].join('-') + '.png'), animations: 'disabled'});
        }
    }
}

async function nativeZoom() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clinic-f3-native-'));
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
        for (const viewName of ['calendar', 'notifications']) {
            await worker.evaluate(async url => {
                const tab = (await chrome.tabs.query({})).find(item => item.url === url);
                if (!tab) throw new Error('missing synthetic browser tab');
                await chrome.tabs.setZoom(tab.id, 1);
            }, page.url());
            await page.waitForFunction(target => Math.abs(devicePixelRatio - target) < 0.01, base);
            await view(page, viewName);
            await page.locator(viewName === 'calendar' ? '#calendarPanel [data-workspace-row]' : '#internalNotificationsFullList article').first().waitFor();
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

async function main() {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking']});
    receipt.chromium = browser.version();
    try {
        const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const page = await setup(context);
        await login(page, 'doctor');
        await schedule(page, context);
        await saveFact(page);
        await notifications(page);
        await conflicts(page);
        await revokedNavigation(page, browser);
        await expiredSession(page, context);
        await page.unrouteAll({behavior: 'wait'});
        await context.close();

        const visual = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const presentation = await setup(visual);
        await login(presentation, 'doctor');
        active = 'responsive schedule';
        await view(presentation, 'calendar');
        await calendarReady(presentation);
        await narrower(presentation, 'calendar');
        active = 'responsive notifications';
        await view(presentation, 'notifications');
        await presentation.locator('#internalNotificationsFullList article').first().waitFor();
        await narrower(presentation, 'notifications');
        await presentation.unrouteAll({behavior: 'wait'});
        await visual.close();
    } finally {
        await browser.close();
    }
    active = 'native zoom';
    await nativeZoom();
    assert.equal(clinicalWrites.length, 1);
    assert.equal(readWrites.length, 1);
    assert.equal(receipt.legacyRegistryRequests, 0);
    assert.equal(receipt.pageErrors, 0);
    receipt.checks.push('no implicit clinical or read-state mutation, no legacy list, 16 responsive axe states and 12 native zoom states');
    fs.writeFileSync(process.env.F4_RECEIPT, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600});
}

main().catch(error => {
    process.stderr.write(active + '\n' + error.stack + '\n');
    process.exitCode = 1;
});
