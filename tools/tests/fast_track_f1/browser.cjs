'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodePath = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = JSON.parse(fs.readFileSync(process.env.F1_FIXTURE, 'utf8'));
const { origin, patients, users } = fixture;
assert.equal(fixture.syntheticOnly, true);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const receipt = {
    checks: [],
    responseProofs: [],
    injectedTransportFaults: [],
    axe: [],
    unexpectedMedicalWrites: 0,
    pageErrors: 0,
};

function check(label) {
    receipt.checks.push(label);
}

function apiResponse(page, path, params = {}) {
    return page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === 'GET' && url.pathname === path
            && Object.entries(params).every(([key, value]) => url.searchParams.get(key) === value);
    });
}

async function captureRead(response, kind) {
    assert.equal(response.status(), 200, kind);
    const raw = await response.body();
    receipt.responseProofs.push({kind, sha256: createHash('sha256').update(raw).digest('hex')});
    return JSON.parse(raw.toString('utf8'));
}

async function newContext(browser, options = {}) {
    const context = await browser.newContext({
        viewport: {width: 1440, height: 1000},
        reducedMotion: 'reduce',
        serviceWorkers: 'block',
        ...options,
    });
    await context.route('**/*', async route => {
        if (new URL(route.request().url()).origin === origin) await route.continue();
        else await route.abort('blockedbyclient');
    });
    context.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (path.startsWith('/api/') && !path.startsWith('/api/auth/') &&
            !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) receipt.unexpectedMedicalWrites++;
    });
    return context;
}

function monitor(page) {
    page.on('pageerror', () => receipt.pageErrors++);
    page.setDefaultTimeout(10000);
}

async function login(page, role, fromHome = false) {
    if (!fromHome) await page.goto(origin);
    await page.locator('#username').fill(users[role]);
    await page.locator('#password').fill(fixture.password);
    await page.locator('#loginSubmit').click();
    await page.waitForFunction(user => app.appReady && !app.sessionExpired && !app.sessionReconciling
        && app.user?.username === user && app.patientListState === 'ready', users[role]);
}

async function go(page, view, keyboard = false) {
    const selector = page.locator(`[data-view="${view}"]`);
    await selector.waitFor({state: 'visible'});
    if (keyboard) {
        await selector.focus();
        assert(await selector.evaluate(el => document.activeElement === el));
        await page.keyboard.press('Enter');
    } else {
        await selector.click();
    }
    await page.locator(`#view-${view}`).waitFor({state: 'visible'});
}

async function withoutForeign(page, selector = 'main') {
    assert(!(await page.locator(selector).textContent()).includes(patients.foreign.name),
        'foreign patient unexpectedly rendered');
}

async function openDetail(page, open, patient, tab = null) {
    const response = apiResponse(page, `/api/patients/${patient.id}`);
    await open();
    const body = await captureRead(await response, 'patient-object');
    assert.equal(body.id, patient.id);
    await page.waitForFunction(({id, tab}) => app.currentView === 'patient'
        && app.currentPatientId === id && (!tab || app.currentTab === tab), {id: patient.id, tab});
    await page.locator('#view-patient').getByText(patient.name, {exact: true}).first().waitFor();
}

async function management(browser) {
    const context = await newContext(browser);
    try {
        const page = await context.newPage();
        monitor(page);
        await login(page, 'head');
        const summaryResponse = apiResponse(page, '/api/analytics/summary');
        await go(page, 'pulse', true);
        const summary = await captureRead(await summaryResponse, 'pulse-summary-head');
        const values = Object.fromEntries(summary.cards.map(card => [card.code, card.value]));
        assert.equal(values.total_patients, 4);
        assert(values.overdue_events >= 2);
        assert(values.data_quality_issues > 0);
        const pulse = page.locator('#f1Pulse');
        await pulse.locator('[data-f1-metric=overdue_events]').waitFor();
        assert(await pulse.locator('[data-f1-metric]').count() >= 5);

        const filtered = apiResponse(page, '/api/analytics/summary', {stage: 'exam'});
        await pulse.locator('#f1PulseStage').selectOption('exam');
        assert.equal((await filtered).status(), 200);
        const overdueResponse = apiResponse(page, '/api/analytics/details', {type: 'overdue_events', stage: 'exam'});
        await pulse.locator('[data-f1-metric=overdue_events]').click();
        const overdue = await captureRead(await overdueResponse, 'pulse-overdue-head');
        assert(overdue.items.some(i => i.patient_id === patients.overdue.id));
        assert(!overdue.items.some(i => i.patient_id === patients.foreign.id));
        await pulse.locator('#f1PulseDetails').getByText(patients.overdue.name, {exact: true}).first().waitFor();
        await withoutForeign(page, '#f1PulseDetails');

        const detailButtons = pulse.locator('#f1PulseDetails [data-f1-open-patient]');
        await openDetail(page, () => detailButtons.first().click(), patients.overdue);
        await page.goBack();
        await pulse.waitFor({state: 'visible'});
        assert.equal(await pulse.locator('#f1PulseStage').inputValue(), 'exam');
        await pulse.locator('#f1PulseDetails').getByText(patients.overdue.name, {exact: true}).first().waitFor();
        await page.goForward();
        await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id, patients.overdue.id);
        await page.goBack();
        await pulse.waitFor({state: 'visible'});
        await pulse.locator('#f1PulseDetails').getByText(patients.overdue.name, {exact: true}).first().waitFor();
        await openDetail(page, () => detailButtons.first().click(), patients.overdue);
        await page.locator('#backToF1').click();
        await pulse.locator('#f1PulseDetails').getByText(patients.overdue.name, {exact: true}).first().waitFor();
        assert.equal(await pulse.locator('#f1PulseStage').inputValue(), 'exam');
        check('pulse head: real summary, stage/overdue drilldown, exact object, Back/Forward retains filters');

        const qualityResponse = apiResponse(page, '/api/analytics/details', {type: 'quality_actionable', stage: 'exam'});
        await pulse.locator('[data-f1-metric=data_quality_issues]').click();
        const quality = await captureRead(await qualityResponse, 'pulse-data-quality-head');
        assert(quality.items.some(i => i.patient_id === patients.missing.id && i.code === 'missing_required_field'));
        assert(!quality.items.some(i => i.patient_id === patients.foreign.id));
        await pulse.locator('#f1PulseDetails').getByText(patients.missing.name, {exact: true}).first().waitFor();
        await withoutForeign(page, '#f1PulseDetails');
        const denied = await context.request.get(origin + '/api/patients/' + patients.foreign.id);
        assert.equal(denied.status(), 404);
        assert(!(await denied.text()).includes(patients.foreign.name));
        check('pulse: server Quality warning for missing registration field and neutral real out-of-scope 404');

        if (process.env.AXE_SOURCE) {
            await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE, 'utf8'));
        }
        for (const theme of ['light', 'dark']) {
            await page.evaluate(value => app.applyTheme(value), theme);
            for (const width of [1440, 375]) {
                await page.setViewportSize({width, height: 850});
                assert(await pulse.isVisible());
                assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
                if (process.env.AXE_SOURCE) {
                    const violations = await page.evaluate(async () => (await axe.run('#view-pulse', {
                        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']},
                    })).violations.map(row => row.id));
                    assert.deepEqual(violations, [], `pulse ${theme}/${width}`);
                    receipt.axe.push({route: 'pulse', theme, width, violations: 0});
                }
                if (process.env.F1_SCREENSHOTS === '1' && ((theme === 'light' && width === 1440) || (theme === 'dark' && width === 375))) {
                    await page.locator('#view-pulse').screenshot({
                        path: nodePath.join(nodePath.dirname(process.env.F1_RECEIPT), `pulse-${theme}-${width}.png`),
                        animations: 'disabled',
                    });
                }
            }
        }
        check('pulse: both themes, desktop/narrow layout, keyboard activation');
    } finally {
        await context.close();
    }
}

async function doctor(browser) {
    const context = await newContext(browser);
    try {
        const page = await context.newPage();
        monitor(page);
        await login(page, 'doctor');
        assert(!(await page.locator('[data-view=pulse]').isVisible()));
        const analytics = await context.request.get(origin + '/api/analytics/summary');
        if (analytics.status() === 403) {
            assert(!(await analytics.text()).includes(patients.foreign.name));
        } else {
            // The existing API may expose role-scoped aggregates independent of F1 nav.
            assert.equal(analytics.status(), 200);
            const data = await analytics.json();
            assert.equal(data.cards.find(card => card.code === 'total_patients').value, 3);
        }
        const listingResponse = apiResponse(page, '/api/patients', {view: 'page'});
        await go(page, 'myDay', true);
        const listing = await captureRead(await listingResponse, 'my-day-patients-doctor');
        assert.equal(listing.page.total, 3);
        assert(listing.patients.every(p => [patients.scheduled.id, patients.overdue.id, patients.withoutPlan.id].includes(p.id)));
        const day = page.locator('#f1MyDay');
        const noPlan = day.locator('[data-f1-doctor-row]').filter({hasText: patients.withoutPlan.name});
        await noPlan.waitFor();
        assert.match(await noPlan.textContent(), /срок не указан|нет данных|не указана|нет даты/i);
        await withoutForeign(page, '#f1MyDay');
        assert(!(await day.textContent()).includes(patients.missing.name));
        check('my-day: scoped patients, actual stage/assignee, no-plan state and no foreign data');

        const eventsResponse = apiResponse(page, '/api/patients/workspace/events');
        await day.locator('[data-f1-doctor-mode=events]').click();
        const events = await captureRead(await eventsResponse, 'my-day-events-doctor');
        assert.equal(events.events.filter(row => row.patientId === patients.overdue.id).length, 2);
        assert(!events.events.some(row => row.patientId === patients.foreign.id));
        const row = day.locator('[data-f1-doctor-row]').filter({hasText: patients.overdue.name}).first();
        await openDetail(page, () => row.getByRole('button', {name: /^К событию/}).click(), patients.overdue, 'events');
        await page.goBack();
        await day.waitFor({state: 'visible'});
        await row.waitFor();
        check('my-day: two real overdue events, event card action and Back preserve task mode');

        let status = 409;
        let attempts = 0;
        const pending = [];
        const handler = async route => {
            attempts++;
            if (status) {
                const code = status;
                status = 0;
                await route.fulfill({status: code, contentType: 'application/json',
                    body: JSON.stringify({code: code === 409 ? 'PATIENT_QUERY_CHANGED' : 'UNAVAILABLE'})});
            } else {
                await route.continue();
            }
        };
        await page.route('**/api/patients/workspace/checklists**', handler);
        pending.push(apiResponse(page, '/api/patients/workspace/checklists'));
        await day.locator('[data-f1-doctor-mode=checklists]').click();
        assert.equal((await pending[0]).status(), 409);
        await page.waitForFunction(() => !document.querySelector('#f1MyDay [aria-busy=true]')
            && document.getElementById('f1MyDay').getAttribute('aria-busy') !== 'true');
        assert.equal(attempts, 2);
        receipt.injectedTransportFaults.push('my-day CHECKLISTS 409 PATIENT_QUERY_CHANGED -> real retry');
        check('my-day: one controlled GET retry after injected 409');
        await page.unroute('**/api/patients/workspace/checklists**', handler);
        await withoutForeign(page, '#f1MyDay');

        if (process.env.AXE_SOURCE) await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE, 'utf8'));
        for (const theme of ['light', 'dark']) {
            await page.evaluate(value => app.applyTheme(value), theme);
            for (const width of [1440, 375]) {
                await page.setViewportSize({width, height: 850});
                assert(await day.isVisible());
                assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
                if (process.env.AXE_SOURCE) {
                    const violations = await page.evaluate(async () => (await axe.run('#view-myDay', {
                        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']},
                    })).violations.map(row => row.id));
                    assert.deepEqual(violations, [], `my-day ${theme}/${width}`);
                    receipt.axe.push({route: 'my-day', theme, width, violations: 0});
                }
                if (process.env.F1_SCREENSHOTS === '1' && ((theme === 'light' && width === 1440) || (theme === 'dark' && width === 375))) {
                    await page.locator('#view-myDay').screenshot({
                        path: nodePath.join(nodePath.dirname(process.env.F1_RECEIPT), `my-day-${theme}-${width}.png`),
                        animations: 'disabled',
                    });
                }
            }
        }
        check('my-day: both themes, desktop/narrow layout, keyboard activation');
    } finally {
        await context.close();
    }
}

async function emptyAndIdentity(browser) {
    const context = await newContext(browser);
    try {
        const page = await context.newPage();
        monitor(page);
        await login(page, 'empty');
        const emptyResponse = apiResponse(page, '/api/patients', {view: 'page'});
        await go(page, 'myDay');
        assert.equal((await captureRead(await emptyResponse, 'my-day-empty')).page.total, 0);
        assert.equal(await page.locator('#f1MyDay [data-f1-doctor-row]').count(), 0);
        check('my-day: empty authorization scope using real zero-row response');

        const c = (await context.cookies()).find(cookie => cookie.name === 'clinic_csrf');
        assert(c);
        assert((await context.request.post(origin + '/api/auth/logout', {'headers': {'X-CSRF-Token': c.value}})).ok());
        const denied = apiResponse(page, '/api/patients/workspace/events');
        await page.locator('#f1MyDay [data-f1-doctor-mode=events]').click();
        assert.equal((await denied).status(), 401);
        await page.waitForFunction(() => app.sessionExpired);
        check('my-day: actual revoked session receives 401 and locks view');

        await login(page, 'foreign', true);
        const fresh = apiResponse(page, '/api/patients', {view: 'page'});
        await go(page, 'myDay');
        const listed = await captureRead(await fresh, 'my-day-after-identity-change');
        assert.equal(listed.page.total, 1);
        assert.equal(listed.patients[0].id, patients.foreign.id);
        await page.locator('#f1MyDay').getByText(patients.foreign.name, {exact: true}).first().waitFor();
        assert(!(await page.locator('#f1MyDay').textContent()).includes(patients.overdue.name));
        check('my-day: real same-profile identity changes scope on fresh login');
    } finally {
        await context.close();
    }
}

async function main() {
    const browser = await chromium.launch({
        headless: true,
        executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking'],
    });
    try {
        receipt.chromium = browser.version();
        receipt.syntheticOnly = true;
        await management(browser);
        await doctor(browser);
        await emptyAndIdentity(browser);
        assert.equal(receipt.pageErrors, 0);
        assert.equal(receipt.unexpectedMedicalWrites, 0);
        check('zero page errors and zero automatic non-auth writes');
        fs.writeFileSync(process.env.F1_RECEIPT, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600});
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    // Do not print Playwright call logs. They can include temporary fixture credentials.
    process.stderr.write(`${error.name}: ${receipt.checks.length} behavioral groups completed\n`);
    const location = error.stack?.split('\n').find(line => line.includes('browser.cjs'));
    if (location) process.stderr.write(location.replace(/.*browser\.cjs:/, 'browser.cjs:') + '\n');
    process.exitCode = 1;
});
