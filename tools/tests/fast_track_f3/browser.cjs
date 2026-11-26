'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = JSON.parse(fs.readFileSync(process.env.F3_FIXTURE, 'utf8'));
const {origin, users, patients} = fixture;
assert(fixture.syntheticOnly);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const axeSource = fs.readFileSync(process.env.AXE_SOURCE, 'utf8');
const receipt = {checks: [], axe: [], nativeZoom: [], responseProofs: [], injectedTransportFaults: [],
    pageErrors: 0, unexpectedClinicalWrites: 0, legacyRegistryRequests: 0};
const output = path.dirname(process.env.F3_RECEIPT);
const query = 'Синтетический Реестр F3';
const registryHead = 'Синтетический заведующий F3';
const tabs = ['overview', 'notifications', 'data', 'complaints', 'events', 'checklist', 'notes', 'history'];
let active = 'initialization';
const clinicalWrites = [];

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

async function search(page, value) {
    const read = reply(page, '/api/patients', params => (params.get('q') || '') === value);
    await page.locator('#searchInput').fill(value);
    const pageData = await audited(await read, 'registry search');
    await ready(page, 0);
    return pageData;
}

async function select(page, selector, key, value) {
    const read = reply(page, '/api/patients', params => (params.get(key) || '') === value);
    await page.locator(selector).selectOption(value);
    const result = await audited(await read, 'registry filter');
    await ready(page, 0);
    return result;
}

function row(page, name) {
    return page.locator('#patientsBody .patient-open').filter({hasText: name});
}

function isCursor(params) { return Boolean(params.get('cursor')); }

async function next(page) {
    const read = reply(page, '/api/patients', isCursor);
    await keyboard(page.getByRole('button', {name: 'Следующая страница', exact: true}));
    const data = await audited(await read, 'registry cursor');
    await ready(page, 50);
    assert.equal(data.page.offset, 50);
    return data;
}

async function card(page, button, patient, viaKeyboard = true) {
    const [read] = await Promise.all([reply(page, '/api/patients/' + patient.id),
        viaKeyboard ? keyboard(button) : button.click()]);
    const data = await audited(read, 'registry object');
    assert.equal(data.id, patient.id);
    await page.locator('#view-patient').waitFor({state: 'visible'});
    assert.deepEqual(await page.locator('#patientTabs [role=tab]').evaluateAll(nodes =>
        nodes.map(node => node.id.replace(/^tab-/, ''))), tabs);
    return data;
}

async function registryHistory(page) {
    active = 'search, filter and sort';
    await select(page, '#sortOrderSelect', 'sort', 'name');
    await select(page, '#filterStage', 'stage', 'exam');
    await select(page, '#filterHead', 'head', registryHead);
    const terminal = page.locator('#filterIncludeTerminal');
    if (!(await terminal.isVisible())) await page.locator('#registryMoreFilters summary').click();
    const inclusion = reply(page, '/api/patients', params => params.get('include_terminal') === 'true');
    await terminal.check();
    const included = await audited(await inclusion, 'authorized terminal filter');
    await ready(page, 0);
    assert.equal(included.page.total, 52);
    const first = await search(page, query);
    assert.equal(first.page.total, 52);
    assert.equal(first.patients.length, 50);
    assert.equal(await page.locator('#patientsBody tr').count(), 50);
    assert(first.page.hasNext);
    const second = await next(page);
    assert.equal(second.page.total, 52);
    assert.equal(second.patients.length, 2);
    const patient = second.patients[0];

    active = 'history and explicit return';
    const name = patient.name;
    await card(page, row(page, name), patient);
    const fromHistory = reply(page, '/api/patients', params => isCursor(params) && params.get('q') === query);
    await page.goBack();
    const restored = await audited(await fromHistory, 'registry history cursor');
    await ready(page, 50);
    assert.deepEqual(restored.patients.map(item => item.id), second.patients.map(item => item.id));
    assert.equal(await page.locator('#searchInput').inputValue(), query);
    assert.equal(await page.locator('#filterStage').inputValue(), 'exam');
    assert.equal(await page.locator('#sortOrderSelect').inputValue(), 'name');
    assert.equal(await page.locator('#filterHead').inputValue(), registryHead);
    assert.equal(await page.locator('#filterIncludeTerminal').isChecked(), true);
    await row(page, name).waitFor({state: 'visible'});
    await page.waitForFunction(label => document.activeElement?.textContent.trim() === label, name);
    const freshCard = reply(page, '/api/patients/' + patient.id);
    await page.goForward();
    await audited(await freshCard, 'registry history object');
    await page.locator('#view-patient').waitFor({state: 'visible'});
    const explicit = reply(page, '/api/patients', isCursor);
    await keyboard(page.locator('#backToList'));
    await audited(await explicit, 'registry explicit return cursor');
    await ready(page, 50);
    await page.waitForFunction(label => document.activeElement?.textContent.trim() === label, name);
    receipt.checks.push('server filter and sort, 52-row total over two pages; Back/Forward and explicit return restore page, context and row focus');
    return patient;
}

async function expiredCursor(page, patient) {
    active = 'controlled cursor expiration';
    await card(page, row(page, patient.name), patient);
    let interceptions = 0;
    const old = async route => {
        const params = new URL(route.request().url()).searchParams;
        if (isCursor(params) && ++interceptions === 1) {
            return route.fulfill({status: 410, json: {code: 'CURSOR_EXPIRED'}});
        }
        return route.fallback();
    };
    await page.route('**/api/patients?*', old);
    const first = reply(page, '/api/patients', params => !isCursor(params) && params.get('q') === query);
    await keyboard(page.locator('#backToList'));
    const current = await audited(await first, 'registry cursor restart first page');
    await ready(page, 0);
    assert.equal(interceptions, 1);
    assert.equal(current.page.total, 52);
    assert(!current.patients.some(item => item.id === patient.id));
    assert.equal(await page.locator('#searchInput').inputValue(), query);
    assert.equal(await page.locator('#filterStage').inputValue(), 'exam');
    assert.equal(await page.locator('#sortOrderSelect').inputValue(), 'name');
    assert.equal(await page.locator('#filterHead').inputValue(), registryHead);
    assert.equal(await page.locator('#filterIncludeTerminal').isChecked(), true);
    await page.unroute('**/api/patients?*', old);
    receipt.injectedTransportFaults.push({code: 'CURSOR_EXPIRED', cursorAttempts: 1, freshPageOffset: 0});
    receipt.checks.push('expired cursor performs one bounded restart to first allowed page and retains search, stage and sorting');
}

async function repeatedConflict(page) {
    active = 'bounded double query conflict';
    let attempts = 0;
    const fault = route => {
        attempts++;
        return route.fulfill({status: 409, json: {code: 'PATIENT_QUERY_CHANGED'}});
    };
    await page.route('**/api/patients?*', fault);
    await keyboard(page.getByRole('button', {name: 'Следующая страница', exact: true}));
    await page.locator('#patientListError').waitFor({state: 'visible'});
    await page.locator('#patientRegistryResults').waitFor({state: 'hidden'});
    assert.equal(attempts, 2);
    assert.equal(await page.locator('#patientRegistryCount').textContent(), '—');
    await page.waitForFunction(() => document.activeElement?.id === 'retryPatientListBtn');
    await page.unroute('**/api/patients?*', fault);
    const fresh = reply(page, '/api/patients', params => !isCursor(params) && params.get('q') === query);
    await keyboard(page.locator('#retryPatientListBtn'));
    await audited(await fresh, 'registry explicit recovery');
    await ready(page, 0);
    receipt.injectedTransportFaults.push({code: 'PATIENT_QUERY_CHANGED', attempts, staleRowsHidden: true, retryKeyboard: true});
    receipt.checks.push('double 409 clears old results and counters, stops after one restart and offers focused explicit recovery');
}

async function pulse(page) {
    active = 'Pulse handoff';
    await view(page, 'pulse');
    const stage = page.locator('#f1PulseStage');
    const readyPulse = reply(page, '/api/analytics/summary', params => params.get('stage') === 'exam');
    await stage.selectOption('exam');
    const summary = await audited(await readyPulse, 'pulse stage');
    const details = reply(page, '/api/analytics/details', params => params.get('type') === 'overdue_events');
    await keyboard(page.locator('[data-f1-metric=overdue_events]'));
    await audited(await details, 'pulse overdue');
    const list = reply(page, '/api/patients', params => params.get('stage') === 'exam' && !params.get('q'));
    await keyboard(page.locator('#f3PulseRegistry'));
    const data = await audited(await list, 'pulse authorized registry handoff');
    await ready(page, 0);
    assert.equal(data.page.total, 60);
    assert.equal(await page.locator('#filterStage').inputValue(), 'exam');
    assert(!data.patients.some(item => item.id === patients.foreign.id));
    const deny = await page.context().request.get(origin + '/api/patients/' + patients.foreign.id);
    assert.equal(deny.status(), 404);
    assert(!(await deny.text()).includes(patients.foreign.name));
    const restored = reply(page, '/api/analytics/details', params => params.get('stage') === 'exam' &&
        params.get('type') === 'overdue_events');
    await keyboard(page.locator('#backToRegistrySource'));
    await audited(await restored, 'pulse restored details');
    await page.locator('#view-pulse').waitFor({state: 'visible'});
    assert.equal(await stage.inputValue(), 'exam');
    assert.equal(await page.locator('[data-f1-metric=overdue_events]').getAttribute('aria-pressed'), 'true');
    receipt.checks.push('Pulse carries supported stage without pretending overdue analytics equals registry; true scoped object 404; return retains metric and stage');
    return summary.cards.find(item => item.code === 'overdue_events').value;
}

async function doctor(page) {
    active = 'My Day handoff';
    await view(page, 'myDay');
    const filter = page.getByRole('combobox', {name: 'Фильтр по плановому сроку'});
    const input = reply(page, '/api/patients', params => params.get('deadline') === 'overdue' && params.get('limit') === '20');
    await filter.selectOption('overdue');
    const dayPage = await audited(await input, 'doctor deadline');
    assert(dayPage.page.total > 0);
    const list = reply(page, '/api/patients', params => params.get('deadline') === 'overdue' &&
        params.get('limit') === '50' && params.get('sort') === 'name' && params.get('include_terminal') === 'true');
    await keyboard(page.locator('#f3MyDayRegistry'));
    const registry = await audited(await list, 'doctor authorized registry handoff');
    await ready(page, 0);
    assert.equal(registry.page.total, dayPage.page.total);
    assert(!registry.patients.some(item => item.id === patients.foreign.id));
    const patient = registry.patients[0];
    await card(page, row(page, patient.name), patient);
    await keyboard(page.locator('#backToList'));
    await ready(page, 0);
    const returnRead = reply(page, '/api/patients', params => params.get('deadline') === 'overdue' && params.get('limit') === '20');
    await keyboard(page.locator('#backToRegistrySource'));
    await audited(await returnRead, 'doctor restored workspace');
    await page.locator('#view-myDay').waitFor({state: 'visible'});
    assert.equal(await filter.inputValue(), 'overdue');
    assert.equal(await page.locator('[data-f1-doctor-mode=patients]').getAttribute('aria-pressed'), 'true');
    receipt.checks.push('My Day carries confirmed filters, scope and context through registry and card, then restores original mode');
}

async function recordFactAndRefresh(page, management, previousKpi) {
    active = 'explicit event save, verified registry and management refresh';
    const patient = patients.overdue;
    const eventId = patient.eventIds.at(-1);
    const pending = reply(page, '/api/patients/workspace/events');
    await keyboard(page.locator('[data-f1-doctor-mode=events]'));
    const entries = await audited(await pending, 'events before explicit save');
    assert(entries.events.some(row => row.id === eventId));
    const matchingRows = page.locator('[data-f1-doctor-row=events]').filter({hasText: patient.name});
    await matchingRows.nth(1).waitFor();
    const data = await card(page, matchingRows.nth(1).getByRole('button', {name: /^К событию/}), patient);
    const event = data.history[0].events.find(item => item.id === eventId);
    assert(event && !event.actualDate);
    const eventCard = page.locator('#patient-event-' + eventId);
    await eventCard.getByRole('textbox', {name: 'Фактическая дата', exact: true}).evaluate((node, value) => {
        node.value = value;
        node.dispatchEvent(new Event('input', {bubbles: true}));
    }, fixture.today);
    assert.equal(clinicalWrites.length, 0);
    const eventPath = '/api/patients/' + patient.id + '/events/' + eventId;
    let release;
    const pendingCommit = new Promise(resolve => { release = resolve; });
    const hold = async route => {
        if (route.request().method() === 'PATCH') await pendingCommit;
        return route.fallback();
    };
    await page.route(origin + eventPath, hold);
    const confirmed = reply(page, eventPath, () => true, 'PATCH');
    try {
        const sent = page.waitForRequest(request =>
            request.method() === 'PATCH' && new URL(request.url()).pathname === eventPath);
        await eventCard.getByRole('button', {name: 'Сохранить: Фактическая дата', exact: true}).click();
        await sent;
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
    assert.equal((await mutation.json()).history[0].events.find(item => item.id === eventId).actualDate, fixture.today);
    assert.deepEqual(clinicalWrites, [{method: 'PATCH', path: eventPath,
        body: {field: 'actualDate', value: fixture.today, version: event.version}}]);
    await page.waitForFunction(({id, eventId, today}) => !app.clinicalWriteInFlight &&
        app.getPatient(id).history[0].events.find(event => event.id === eventId).actualDate === today,
    {id: patient.id, eventId, today: fixture.today});
    const events = reply(page, '/api/patients/workspace/events');
    await keyboard(page.locator('#backToF1'));
    const refreshed = await audited(await events, 'workspace after committed fact');
    assert(!refreshed.events.some(item => item.id === eventId));
    assert(refreshed.events.some(item => item.id === patient.eventIds[0]));
    const listing = reply(page, '/api/patients', params => params.get('limit') === '50');
    await keyboard(page.locator('#f3MyDayRegistry'));
    const pageData = await audited(await listing, 'registry after committed fact');
    await ready(page, 0);
    const current = pageData.patients.find(item => item.id === patient.id) || (await search(page, patient.name)).patients[0];
    assert.equal(current.id, patient.id);
    // Registry deadline is still its original plan; the server fact is a separate field.
    assert.equal(current.currentFact, true);
    const deadlineCell = page.locator('#patientsBody tr').filter({hasText: patient.name}).locator('.cell-deadline');
    assert.equal((await deadlineCell.textContent()).trim(), 'Факт отмечен');
    const pulseRead = reply(management, '/api/analytics/summary', params => params.get('stage') === 'exam');
    await keyboard(management.locator('#f1PulseRefresh'));
    const pulse = await audited(await pulseRead, 'management after committed fact');
    const currentKpi = pulse.cards.find(item => item.code === 'overdue_events').value;
    assert.equal(currentKpi, previousKpi - 1);
    receipt.postCommit = {eventAbsentFromWorkspace: true, registryCurrentFact: true, previousKpi, currentKpi, explicitWrites: 1, pendingWriteStayedOnCard: true};
    receipt.checks.push('one explicit versioned UI PATCH updates event, workspace and registry fact, with fresh scoped management KPI');
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
            if (process.env.F3_SCREENSHOTS === '1' && (width === 1440 && mode === 'light' || width === 375 && mode === 'dark'))
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
        for (const viewName of ['patients', 'patient']) {
            if (viewName === 'patient') {
                const item = (await search(page, patients.healthy.name)).patients[0];
                await card(page, row(page, item.name), item);
            }
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
        const head = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const management = await setup(head);
        await login(management, 'head');
        const patient = await registryHistory(management);
        await expiredCursor(management, patient);
        await repeatedConflict(management);
        const previousKpi = await pulse(management);
        const care = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const workday = await setup(care);
        await login(workday, 'doctor');
        await doctor(workday);
        await recordFactAndRefresh(workday, management, previousKpi);
        await management.unrouteAll({behavior: 'wait'});
        await head.close();
        await view(workday, 'patients');
        await ready(workday);
        await search(workday, patients.healthy.name);
        active = 'responsive registry';
        await narrower(workday, 'patients');
        await workday.unrouteAll({behavior: 'wait'});
        await care.close();
        active = 'responsive card';
        const visual = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce'});
        const chart = await setup(visual);
        await login(chart, 'doctor');
        await search(chart, patients.healthy.name);
        await card(chart, row(chart, patients.healthy.name), patients.healthy, false);
        await narrower(chart, 'patient');
        await chart.unrouteAll({behavior: 'wait'});
        await visual.close();
    } finally {
        await browser.close();
    }
    active = 'native zoom';
    await nativeZoom();
    assert.equal(clinicalWrites.length, 1);
    assert.equal(receipt.legacyRegistryRequests, 0);
    assert.equal(receipt.pageErrors, 0);
    receipt.checks.push('eight established tabs, no legacy full-list requests or automatic medical writes, 16 responsive axe states and 12 native zoom states');
    fs.writeFileSync(process.env.F3_RECEIPT, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600});
}

main().catch(error => {
    process.stderr.write(active + '\n' + error.stack + '\n');
    process.exitCode = 1;
});
