'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const fixture = JSON.parse(fs.readFileSync(process.env.F2_FIXTURE, 'utf8'));
const {origin, patients, users} = fixture;
assert.equal(fixture.syntheticOnly, true);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
assert(process.env.AXE_SOURCE, 'The pinned axe browser source is required');
const axeSource = fs.readFileSync(process.env.AXE_SOURCE, 'utf8');
const checkTags = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const output = path.dirname(process.env.F2_RECEIPT);
const receipt = {checks: [], axe: [], nativeZoom: [], responseProofs: [], injectedTransportFaults: [], pageErrors: 0};
const uiWrites = [];
let activeCheck = 'initialization';

function check(label) {
    receipt.checks.push(label);
}

function response(page, pathname, {method = 'GET', params = {}} = {}) {
    return page.waitForResponse(reply => {
        const url = new URL(reply.url());
        return reply.request().method() === method && url.pathname === pathname &&
            Object.entries(params).every(([key, value]) => url.searchParams.get(key) === value);
    });
}

async function auditedRead(reply, kind) {
    assert.equal(reply.status(), 200, kind);
    const raw = await reply.body();
    receipt.responseProofs.push({kind, sha256: createHash('sha256').update(raw).digest('hex')});
    return JSON.parse(raw.toString('utf8'));
}

async function setupContext(context) {
    await context.route('**/*', route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        return route.abort('blockedbyclient');
    });
    context.on('request', request => {
        const pathname = new URL(request.url()).pathname;
        if (pathname.startsWith('/api/') && !pathname.startsWith('/api/auth/') &&
            !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
            uiWrites.push({path: pathname, method: request.method(), body: request.postDataJSON()});
        }
    });
}

async function setupPage(context) {
    const page = await context.newPage();
    page.on('pageerror', () => { receipt.pageErrors++; });
    page.setDefaultTimeout(12000);
    return page;
}

async function sessionReady(page, role) {
    await page.waitForFunction(username => app.appReady && !app.sessionExpired && !app.sessionReconciling &&
        app.user?.username === username && app.patientListState === 'ready', users[role]);
}

async function login(page, role, reload = true) {
    if (reload) await page.goto(origin);
    await page.locator('#username').fill(users[role]);
    await page.locator('#password').fill(fixture.password);
    await page.locator('#loginSubmit').click();
    await sessionReady(page, role);
}

async function actor(browser, role) {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}, reducedMotion: 'reduce',
        serviceWorkers: 'block'});
    await setupContext(context);
    const page = await setupPage(context);
    await login(page, role);
    return {context, page};
}

async function controlRequest(context, method, pathname, data) {
    const csrf = (await context.cookies()).find(item => item.name === 'clinic_csrf');
    return context.request.fetch(origin + pathname, {method, data, headers: csrf ? {'X-CSRF-Token': csrf.value} : {}});
}

async function activate(locator, viaKeyboard = false) {
    if (!viaKeyboard) return locator.click();
    await locator.focus();
    assert(await locator.evaluate(node => node === document.activeElement && !node.disabled));
    await locator.press('Enter');
}

async function navigate(page, view, viaKeyboard = false) {
    await activate(page.locator(`[data-view="${view}"]`), viaKeyboard);
    await page.locator(`#view-${view}`).waitFor();
}

function listed(page, groupSelector, patient) {
    return page.locator(groupSelector).filter({hasText: patient.name});
}

async function absent(container, patient) {
    assert(!(await container.textContent()).includes(patient.name));
}

async function card(page, target, patient, tab, byKeyboard = false) {
    const read = response(page, '/api/patients/' + patient.id);
    await activate(target, byKeyboard);
    const payload = await auditedRead(await read, 'fresh object');
    assert.equal(payload.id, patient.id);
    await page.waitForFunction(({id, tab}) => app.currentView === 'patient' && app.currentPatientId === id &&
        app.currentTab === tab, {id: patient.id, tab});
    await page.locator('#view-patient').getByText(patient.name, {exact: true}).first().waitFor();
    return payload;
}

function cards(summary) {
    return Object.fromEntries(summary.cards.map(item => [item.code, item.value]));
}

function metric(panel, key) {
    return panel.locator(`[data-f1-metric="${key}"]`);
}

async function detail(page, metricLabel, type, {offset = 0, stage = 'exam', keyboard = false} = {}) {
    const read = response(page, '/api/analytics/details', {params: {stage, type, offset: String(offset), limit: '20'}});
    await activate(metricLabel, keyboard);
    return auditedRead(await read, 'scoped management details');
}

async function managerScenario(manager) {
    const {page, context} = manager;
    const pulse = page.locator('#f1Pulse');
    const details = pulse.locator('#f1PulseDetails');
    const first = response(page, '/api/analytics/summary');
    await navigate(page, 'pulse', true);
    const initial = cards(await auditedRead(await first, 'management summary'));
    assert.equal(initial.total_patients, 8);
    assert(initial.overdue_events > 20);
    await metric(pulse, 'overdue_events').waitFor();
    const filtered = response(page, '/api/analytics/summary', {params: {stage: 'exam'}});
    await pulse.locator('#f1PulseStage').selectOption('exam');
    const filteredCounts = cards(await auditedRead(await filtered, 'filtered management summary'));
    await absent(pulse, patients.foreign);

    const firstPage = await detail(page, metric(pulse, 'overdue_events'), 'overdue_events', {keyboard: true});
    assert.equal(firstPage.total, filteredCounts.overdue_events);
    assert.equal(firstPage.items.length, 20);
    const secondPage = await detail(page, details.getByRole('button', {name: 'Следующая страница'}),
        'overdue_events', {offset: 20});
    assert.equal(secondPage.offset, 20);
    assert(secondPage.items.some(row => row.patient_id === patients.overdue.id && row.planned_date));
    assert(!('event_id' in secondPage.items[0]));
    const target = listed(page, '#f1PulseDetails li', patients.overdue).first();
    await target.waitFor();
    const action = target.locator('[data-f1-open-patient]');
    await card(page, action, patients.overdue, 'events', true);
    await page.goBack();
    await target.waitFor();
    assert.equal(await pulse.locator('#f1PulseStage').inputValue(), 'exam');
    assert.match(await details.getByRole('status').textContent(), /21–/);
    await page.goForward();
    await page.waitForFunction(id => app.currentPatientId === id && app.currentView === 'patient', patients.overdue.id);
    await page.goBack();
    await target.waitFor();
    check('management: real server scope, 20-row pagination, event tab without false exact match, retained page/stage on Back/Forward');

    const quality = await detail(page, metric(pulse, 'data_quality_issues'), 'quality_actionable');
    assert(quality.items.some(row => row.patient_id === patients.quality.id && row.code === 'missing_required_field'));
    const qualityAction = listed(page, '#f1PulseDetails li', patients.quality)
        .filter({hasText: 'Не заполнен телефон'}).locator('[data-f1-open-patient]');
    await card(page, qualityAction, patients.quality, 'data');
    await page.locator('#backToF1').click();
    await metric(pulse, 'data_quality_issues').waitFor();

    const checklist = await detail(page, metric(pulse, 'incomplete_required_checklists'), 'incomplete_checklists');
    assert(checklist.total > 0 && checklist.total < filteredCounts.incomplete_required_checklists);
    assert.equal(checklist.items.find(row => row.patient_id === patients.checklist.id).incomplete_required, 1);
    const checkRow = listed(page, '#f1PulseDetails li', patients.checklist).first();
    await card(page, checkRow.locator('[data-f1-open-patient]'), patients.checklist, 'checklist');
    assert.equal(await page.locator('#patientPanels').getByRole('checkbox', {name: fixture.item, exact: false}).isChecked(), false);
    await page.locator('#backToF1').click();
    await checkRow.waitFor();
    assert.match(await details.getByRole('status').textContent(), /пациент/i);
    const outside = await context.request.get(origin + '/api/patients/' + patients.foreign.id);
    assert.equal(outside.status(), 404);
    assert(!(await outside.text()).includes(patients.foreign.name));
    check('management: correct Quality data section; checklist summary counts items, detail counts patients; out-of-scope real 404');

    await detail(page, metric(pulse, 'overdue_events'), 'overdue_events');
    await detail(page, details.getByRole('button', {name: 'Следующая страница'}), 'overdue_events', {offset: 20});
    await card(page, action, patients.overdue, 'events');
    return filteredCounts;
}

async function exactEvent(page, action, patient, event) {
    const payload = await card(page, action, patient, 'events', true);
    const eventCard = page.locator('#patient-event-' + event);
    await eventCard.waitFor();
    await page.waitForFunction(id => document.activeElement?.id === 'patient-event-' + id, event);
    assert(payload.history.some(occurrence => occurrence.events.some(current => current.id === event)));
    return {payload, eventCard};
}

async function doctorScenario(doctor, admin) {
    const {page, context} = doctor;
    const day = page.locator('#f1MyDay');
    const registry = response(page, '/api/patients', {params: {view: 'page', limit: '20'}});
    await navigate(page, 'myDay', true);
    const patientsPage = await auditedRead(await registry, 'doctor list');
    assert.equal(patientsPage.page.total, 6);
    const noPlan = listed(page, '[data-f1-doctor-row=patients]', patients.noDue);
    await noPlan.getByText('Плановый срок не указан', {exact: true}).waitFor();
    await absent(day, patients.foreign);
    const forbidden = await context.request.get(origin + '/api/audit');
    assert.equal(forbidden.status(), 403);
    const eventsRead = response(page, '/api/patients/workspace/events');
    await day.locator('[data-f1-doctor-mode=events]').click();
    const initialEvents = await auditedRead(await eventsRead, 'doctor events');
    const repeated = initialEvents.events.find(item => item.id === patients.repeated.eventIds[0]);
    assert(repeated && repeated.stage === 'exam');
    const historicalRow = listed(page, '[data-f1-doctor-row=events]', patients.repeated);
    const historical = await exactEvent(page, historicalRow.getByRole('button', {name: /^К событию/}),
        patients.repeated, repeated.id);
    assert.equal(historical.payload.history[0].id, repeated.historyEntryId);
    assert.equal(historical.payload.history.at(-1).stage, 'exam');
    assert.notEqual(historical.payload.history.at(-1).id, repeated.historyEntryId);
    await page.goBack();
    await historicalRow.waitFor();
    await page.goForward();
    await page.waitForFunction(id => app.currentPatientId === id && app.currentView === 'patient', patients.repeated.id);
    await page.locator('#backToF1').click();
    await historicalRow.waitFor();
    check('doctor: actual own rows, explicit missing deadline, real 403, exact matching event in an earlier recurrence of the current stage');

    const overdue = listed(page, '[data-f1-doctor-row=events]', patients.overdue);
    const firstId = patients.overdue.eventIds[0];
    const {payload, eventCard} = await exactEvent(page, overdue.first().getByRole('button', {name: /^К событию/}),
        patients.overdue, firstId);
    const event = payload.history[0].events.find(item => item.id === firstId);
    const eventPath = '/api/patients/' + patients.overdue.id + '/events/' + firstId;
    // Edit the existing input without firing change. The visible button is the sole write trigger.
    await eventCard.getByRole('textbox', {name: 'Фактическая дата', exact: true}).evaluate((node, value) => {
        node.value = value;
        node.dispatchEvent(new Event('input', {bubbles: true}));
    }, fixture.today);
    assert.equal(uiWrites.length, 0);
    const save = response(page, eventPath, {method: 'PATCH'});
    await eventCard.getByRole('button', {name: 'Сохранить: Фактическая дата', exact: true}).click();
    const saved = await save;
    assert.equal(saved.status(), 200);
    assert.equal((await saved.json()).history[0].events.find(item => item.id === firstId).actualDate, fixture.today);
    assert.deepEqual(uiWrites[0], {path: eventPath, method: 'PATCH', body: {field: 'actualDate', value: fixture.today, version: event.version}});
    await page.waitForFunction(({id, eventId, date}) => !app.clinicalWriteInFlight &&
        app.getPatient(id).history[0].events.find(event => event.id === eventId).actualDate === date,
    {id: patients.overdue.id, eventId: firstId, date: fixture.today});
    const refreshed = response(page, '/api/patients/workspace/events');
    await page.locator('#backToF1').click();
    const nextEvents = await auditedRead(await refreshed, 'events after authorized write');
    assert(!nextEvents.events.some(item => item.id === firstId));
    assert(nextEvents.events.some(item => item.id === patients.overdue.eventIds[1]));
    await overdue.waitFor();
    assert.equal(await overdue.count(), 1);
    const previousCard = response(page, '/api/patients/' + patients.overdue.id);
    const previousList = response(page, '/api/patients/workspace/events');
    await page.goBack();
    const settledCard = await auditedRead(await previousCard, 'event history replay after completion');
    assert.equal(settledCard.history[0].events.find(item => item.id === firstId).actualDate, fixture.today);
    const settledList = await auditedRead(await previousList, 'events after rejecting stale history');
    assert(!settledList.events.some(item => item.id === firstId));
    await page.waitForFunction(() => app.currentView === 'myDay' && !browserNavigation.correction &&
        browserNavigation.entry(history.state) === browserNavigation.committed);
    assert.equal(await page.locator('#view-patient').isVisible(), false);
    assert.equal(uiWrites.length, 1);
    check('doctor: one versioned UI event write; return and Back revalidate, completed event stays absent with no repeat write');

    const checkRead = response(page, '/api/patients/workspace/checklists');
    await day.locator('[data-f1-doctor-mode=checklists]').click();
    const checkData = await auditedRead(await checkRead, 'doctor checklists');
    assert.equal(checkData.checklists.find(row => row.patient_id === patients.checklist.id).incomplete_required, 1);
    const checklist = listed(page, '[data-f1-doctor-row=checklists]', patients.checklist);
    await card(page, checklist.getByRole('button', {name: /^К чек-листу/}), patients.checklist, 'checklist');
    const item = page.locator('#patientPanels').getByRole('checkbox', {name: fixture.item, exact: false});
    assert.equal(await item.isChecked(), false);
    const checkPath = '/api/patients/' + patients.checklist.id + '/history/0/checklist';
    const patch = response(page, checkPath, {method: 'PATCH'});
    await item.press('Space');
    const checkSaved = await patch;
    assert.equal(checkSaved.status(), 200);
    assert.equal((await checkSaved.json()).history[0].checklist[fixture.item], true);
    assert.deepEqual(uiWrites[1], {path: checkPath, method: 'PATCH', body: {item: fixture.item, checked: true, expected: false}});
    await page.waitForFunction(({id, item}) => app.getPatient(id).history[0].checklist[item] === true, {id: patients.checklist.id, item: fixture.item});
    const checkFresh = response(page, '/api/patients/workspace/checklists');
    await page.locator('#backToF1').click();
    const checkList = await auditedRead(await checkFresh, 'checklist after authorized write');
    assert.equal(checkList.checklists.find(row => row.patient_id === patients.checklist.id).incomplete_required, 0);
    await checklist.getByRole('button', {name: /^Посмотреть чек-лист/}).waitFor();
    check('doctor: existing checklist checkbox sends one conditional PATCH and reflects server completion on return');

    const eventsAgain = response(page, '/api/patients/workspace/events');
    await day.locator('[data-f1-doctor-mode=events]').click();
    assert.equal((await eventsAgain).status(), 200);
    await overdue.waitFor();
    const otherId = patients.overdue.eventIds[1];
    const latest = await admin.context.request.get(origin + '/api/patients/' + patients.overdue.id);
    const old = (await latest.json()).history[0].events.find(item => item.id === otherId);
    const otherPath = '/api/patients/' + patients.overdue.id + '/events/' + otherId;
    assert.equal((await controlRequest(admin.context, 'PATCH', otherPath,
        {field: 'actualDate', value: fixture.today, version: old.version})).status(), 200);
    const staleObject = response(page, '/api/patients/' + patients.overdue.id);
    const staleRefresh = response(page, '/api/patients/workspace/events');
    await overdue.getByRole('button', {name: /^К событию/}).click();
    assert.equal((await staleObject).status(), 200);
    const fresh = await auditedRead(await staleRefresh, 'events after other session commit');
    assert(!fresh.events.some(item => item.id === otherId));
    await day.getByRole('status').filter({hasText: 'Факт события уже отмечен.'}).waitFor();
    assert.equal(await overdue.count(), 0);
    assert.equal(await page.evaluate(() => app.currentView), 'myDay');
    check('doctor: another real authorized session completes a visible task; the stale button triggers fresh validation and disappears');
}

async function managerReturn(manager, before) {
    const {page} = manager;
    const pulse = page.locator('#f1Pulse');
    const detailRefetch = response(page, '/api/analytics/details', {params: {stage: 'exam', type: 'overdue_events', offset: '20'}});
    const summaryRefetch = response(page, '/api/analytics/summary', {params: {stage: 'exam'}});
    await page.locator('#backToF1').click();
    const freshSummary = cards(await auditedRead(await summaryRefetch, 'manager summary after confirmed writes'));
    const details = await auditedRead(await detailRefetch, 'same manager page after confirmed writes');
    assert.equal(freshSummary.overdue_events, before.overdue_events - 2);
    assert.equal(freshSummary.incomplete_required_checklists, before.incomplete_required_checklists - 1);
    assert.equal(details.total, freshSummary.overdue_events);
    assert(!details.items.some(item => item.patient_id === patients.overdue.id));
    const overdue = metric(pulse, 'overdue_events');
    await overdue.waitFor();
    assert.equal(await overdue.getAttribute('aria-pressed'), 'true');
    assert.equal(await pulse.locator('#f1PulseStage').inputValue(), 'exam');
    const value = (await overdue.getAttribute('aria-label')).match(/\d+/g).join('');
    assert.equal(Number(value), freshSummary.overdue_events);
    receipt.kpis = {countsBefore: {overdueEvents: before.overdue_events,
        incompleteItems: before.incomplete_required_checklists}, countsAfter: {overdueEvents: freshSummary.overdue_events,
        incompleteItems: freshSummary.incomplete_required_checklists}, stagePreserved: true, offsetPreserved: 20};
    check('management: return from card after confirmed updates elsewhere refetches summary and prior details; KPI matches fresh backend');
}

async function readOnlyScenario(viewer) {
    const {page} = viewer;
    await navigate(page, 'pulse');
    const pulse = page.locator('#f1Pulse');
    const overdue = metric(pulse, 'overdue_events');
    await overdue.click();
    const task = listed(page, '#f1PulseDetails li', patients.paged).first();
    await card(page, task.locator('[data-f1-open-patient]'), patients.paged, 'events');
    assert.equal(await page.locator('#readOnlyChip').isVisible(), true);
    assert.equal(await page.locator('#patientPanels [data-card-save]:visible').count(), 0);
    assert.equal(await page.locator('#patientPanels input:enabled').count(), 0);
    check('management: global read-only role reaches the correct card section without writable controls');
}

async function revokedScenario(doctor, admin) {
    const {page} = doctor;
    const day = page.locator('#f1MyDay');
    const target = listed(page, '[data-f1-doctor-row=events]', patients.revoked);
    const pathname = '/api/patients/' + patients.revoked.id;
    await target.waitFor();
    const gated = async route => {
        try {
            const current = await admin.context.request.get(origin + pathname);
            assert.equal(current.status(), 200);
            const {version} = await current.json();
            const updated = await controlRequest(admin.context, 'PUT', pathname + '/treating-doctor', {
                userId: fixture.accountIds.foreign, expectedVersion: version,
            });
            assert.equal(updated.status(), 200);
            assert.equal((await updated.json()).treatingDoctorUserId, fixture.accountIds.foreign);
            await route.continue();
        } catch (error) {
            await route.abort();
            throw error;
        }
    };
    const request = origin + pathname;
    await page.route(request, gated, {times: 1});
    const objectRead = response(page, pathname);
    const refresh = response(page, '/api/patients/workspace/events');
    await target.getByRole('button', {name: /^К событию/}).click();
    const denied = await objectRead;
    assert.equal(denied.status(), 404);
    assert(!(await denied.text()).includes(patients.revoked.name));
    const events = await auditedRead(await refresh, 'events after authorized reassignment');
    assert(!events.events.some(item => item.patientId === patients.revoked.id));
    await day.getByRole('status').filter({hasText: 'Запись больше недоступна.'}).waitFor();
    await absent(day, patients.revoked);
    assert.equal(await page.evaluate(() => app.currentView), 'myDay');
    check('doctor: a real other-session reassignment before object navigation yields neutral 404 and removes the old action');
}

async function draftScenario(doctor) {
    const {page} = doctor;
    const day = page.locator('#f1MyDay');
    const modes = day.locator('[data-f1-doctor-mode=patients]');
    await modes.click();
    const row = listed(page, '[data-f1-doctor-row=patients]', patients.healthy);
    await card(page, row.getByRole('button', {name: /^Открыть карточку/}), patients.healthy, 'overview');
    await page.getByRole('tab', {name: 'Данные', exact: true}).click();
    const input = page.locator('#patientPanels input[type=tel]');
    const value = '+70000000991';
    await input.fill(value);
    await page.getByRole('tab', {name: 'История', exact: true}).click();
    const modal = page.locator('#confirmModal');
    await modal.waitFor();
    await page.locator('#confirmCancelBtn').click();
    assert.equal(await input.inputValue(), value);
    assert.equal(await page.evaluate(() => app.currentTab), 'data');
    await page.locator('#backToF1').click();
    await modal.waitFor();
    await page.locator('#confirmCancelBtn').click();
    assert.equal(await input.inputValue(), value);
    assert.equal(uiWrites.length, 2);
    await page.locator('#backToF1').click();
    await modal.waitFor();
    await page.locator('#confirmOkBtn').click();
    await row.waitFor();
    assert.equal(await page.locator('#view-patient').isVisible(), false);
    assert.equal(await page.evaluate(() => app.sessionFieldDrafts.size), 1);
    assert((await page.locator('#sessionFieldDrafts').textContent()).includes(value));
    await page.locator('#sessionFieldDrafts').getByRole('button', {name: 'Отменить правку'}).click();
    await page.locator('#confirmOkBtn').click();
    await page.waitForFunction(() => app.sessionFieldDrafts.size === 0);
    assert.equal(await page.evaluate(() => app.currentView), 'myDay');
    await row.waitFor();
    check('doctor: tab change and return protect a dirty card field; confirmed return preserves the local draft until explicit discard, with no writes');
}

function injected(code) {
    return {status: 409, contentType: 'application/json', body: JSON.stringify({code})};
}

async function conflicts(doctor) {
    const {page, context} = doctor;
    const day = page.locator('#f1MyDay');
    const eventRoute = '**/api/patients/workspace/events**';
    let dispatches = 0;
    const singleConflict = route => {
        dispatches++;
        return dispatches === 1 ? route.fulfill(injected('PATIENT_QUERY_CHANGED')) : route.continue();
    };
    await page.route(eventRoute, singleConflict);
    const rejected = response(page, '/api/patients/workspace/events');
    await day.locator('[data-f1-doctor-mode=events]').click();
    assert.equal((await rejected).status(), 409);
    await day.getByRole('status').filter({hasText: 'Список изменился.'}).waitFor();
    assert.equal(dispatches, 2);
    await page.unroute(eventRoute, singleConflict);
    receipt.injectedTransportFaults.push({code: 'PATIENT_QUERY_CHANGED', automaticGets: 2, successfulRetry: true});

    const checklistRoute = '**/api/patients/workspace/checklists**';
    dispatches = 0;
    const doubleConflict = route => {
        dispatches++;
        return route.fulfill(injected('PATIENT_QUERY_CHANGED'));
    };
    await page.route(checklistRoute, doubleConflict);
    await day.locator('[data-f1-doctor-mode=checklists]').click();
    await day.getByRole('alert').waitFor();
    assert.equal(dispatches, 2);
    assert.equal(await day.locator('[data-f1-doctor-row]').count(), 0);
    const refresh = day.getByRole('button', {name: 'Обновить список', exact: true});
    await page.unroute(checklistRoute, doubleConflict);
    const recovered = response(page, '/api/patients/workspace/checklists');
    await activate(refresh, true);
    assert.equal((await recovered).status(), 200);
    await listed(page, '[data-f1-doctor-row=checklists]', patients.checklist).waitFor();
    receipt.injectedTransportFaults.push({code: 'PATIENT_QUERY_CHANGED', automaticGets: 2, explicitKeyboardRecovery: true});

    dispatches = 0;
    const accessChange = route => {
        dispatches++;
        return route.fulfill(injected('ACCESS_CONTEXT_CHANGED'));
    };
    await page.route(eventRoute, accessChange);
    await day.locator('[data-f1-doctor-mode=events]').click();
    await page.locator('#loginScreen').waitFor();
    assert.equal(dispatches, 1);
    assert.equal(await day.locator('[data-f1-doctor-row]').count(), 0);
    await page.unroute(eventRoute, accessChange);
    await login(page, 'doctor', false);
    receipt.injectedTransportFaults.push({code: 'ACCESS_CONTEXT_CHANGED', automaticGets: 1, locksSession: true});
    check('doctor: bounded ordinary 409 restart, terminal double 409 with keyboard recovery, access change goes to login without ordinary retry');

    if (!(await day.isVisible())) await navigate(page, 'myDay');
    await day.locator('[data-f1-doctor-mode=patients]').click();
    await listed(page, '[data-f1-doctor-row=patients]', patients.healthy).waitFor();
    assert.equal((await controlRequest(context, 'POST', '/api/auth/logout')).status(), 200);
    const gone = response(page, '/api/patients/workspace/events');
    await day.locator('[data-f1-doctor-mode=events]').click();
    assert.equal((await gone).status(), 401);
    await page.locator('#loginScreen').waitFor();
    assert.equal(await day.locator('[data-f1-doctor-row]').count(), 0);
    await login(page, 'doctor', false);
    check('doctor: real revoked session receives 401; locked screen has no former route data; fresh same-user login works');
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return {promise, resolve, reject};
}

function bounded(promise, message) {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), 12000); })])
        .finally(() => clearTimeout(timer));
}

async function lateResponse(doctor) {
    const {page, context} = doctor;
    const day = page.locator('#f1MyDay');
    if (!(await day.isVisible())) await navigate(page, 'myDay');
    await day.locator('[data-f1-doctor-mode=patients]').click();
    await listed(page, '[data-f1-doctor-row=patients]', patients.healthy).waitFor();
    const peer = await setupPage(context);
    await peer.goto(origin);
    await sessionReady(peer, 'doctor');
    await page.bringToFront();
    await sessionReady(page, 'doctor');
    const eventRoute = '**/api/patients/workspace/events**';
    const hold = deferred();
    const ready = deferred();
    const complete = deferred();
    const handler = async route => {
        try {
            const reply = await route.fetch();
            assert.equal(reply.status(), 200);
            assert((await reply.json()).events.some(item => item.patientId === patients.repeated.id));
            ready.resolve();
            await hold.promise;
            try {
                await route.fulfill({response: reply});
                complete.resolve('delivered');
            } catch {
                complete.resolve('discarded with old document');
            }
        } catch (error) {
            ready.reject(error);
        }
    };
    await page.route(eventRoute, handler, {times: 1});
    try {
        await day.locator('[data-f1-doctor-mode=events]').click();
        await bounded(ready.promise, 'previous identity response was not intercepted');
        await Promise.all([peer.waitForEvent('load'), peer.getByRole('button', {name: 'Выйти', exact: true}).click()]);
        await login(peer, 'foreign', false);
        await sessionReady(page, 'foreign');
        hold.resolve();
        receipt.oldResponseDisposition = await bounded(complete.promise, 'previous identity response was not finalized');
        if (!(await day.isVisible())) await navigate(page, 'myDay');
        if (await day.locator('[data-f1-doctor-mode=patients]').getAttribute('aria-pressed') !== 'true') {
            await day.locator('[data-f1-doctor-mode=patients]').click();
        }
        await listed(page, '[data-f1-doctor-row=patients]', patients.foreign).waitFor();
        await absent(day, patients.repeated);
        await absent(day, patients.healthy);
        assert.equal(await page.evaluate(() => app.sessionFieldDrafts.size), 0);
        check('doctor: late actual HTTP 200 after real peer-tab identity change never adds old scoped cards or drafts');
    } finally {
        hold.resolve();
        await peer.close();
    }
}

async function setTheme(page, theme) {
    if ((await page.locator('html').getAttribute('data-theme')) !== theme) await page.locator('#themeToggle').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
}

async function geometry(page, view) {
    return page.evaluate(view => {
        window.scrollTo(0, 0);
        const heading = document.querySelector(`#view-${view} h1`);
        const topbar = document.querySelector('.topbar').getBoundingClientRect();
        const rect = heading.getBoundingClientRect();
        const visible = document.elementFromPoint(rect.left + Math.min(rect.width / 2, 20), rect.top + rect.height / 2);
        return {width: innerWidth, overflow: document.documentElement.scrollWidth > innerWidth,
            headingClearOfTopbar: rect.top + 1 >= topbar.bottom && rect.bottom < innerHeight &&
                visible && (visible === heading || heading.contains(visible)),
            browserScale: visualViewport.scale, pixelRatio: devicePixelRatio};
    }, view);
}

async function assessAxe(page, label) {
    const violations = await page.evaluate(async values => (await axe.run(document, {
        runOnly: {type: 'tag', values},
    })).violations.map(rule => ({id: rule.id, targets: rule.nodes.map(node => node.target)})), checkTags);
    if (violations.length) fs.writeFileSync(path.join(output, 'axe-failure.json'), JSON.stringify({label, violations}, null, 2));
    assert.deepEqual(violations, [], label);
}

async function responsive(page, view) {
    await page.evaluate(axeSource);
    for (const theme of ['light', 'dark']) {
        await setTheme(page, theme);
        for (const width of [320, 375, 768, 1440]) {
            await page.setViewportSize({width, height: 1000});
            const dimensions = await geometry(page, view);
            assert.equal(dimensions.width, width);
            assert.equal(dimensions.overflow, false, `${view}: horizontal overflow at ${width}`);
            assert.equal(dimensions.headingClearOfTopbar, true, `${view}: topbar overlaps the heading at ${width}`);
            await assessAxe(page, `${view}/${theme}/${width}`);
            receipt.axe.push({view, theme, width, violations: 0, headingClearOfTopbar: true});
            if (process.env.F2_SCREENSHOTS === '1' && (theme === 'light' && width === 1440 || theme === 'dark' && width === 375)) {
                await page.evaluate(() => { document.activeElement.blur(); window.scrollTo(0, 0); });
                await page.screenshot({path: path.join(output, `${view}-${theme}-${width}.png`), animations: 'disabled'});
            }
        }
    }
}

async function nativeZoom() {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'clinic-f2-native-'));
    const extension = path.join(dir, 'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension, 'manifest.json'), JSON.stringify({manifest_version: 3,
        name: 'Synthetic localhost zoom verification', version: '1.0', permissions: ['tabs'],
        background: {service_worker: 'worker.js'}}));
    fs.writeFileSync(path.join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(()=>{});');
    try {
        for (const [view, role] of [['pulse', 'head'], ['myDay', 'doctor']]) {
            const context = await chromium.launchPersistentContext(path.join(dir, role), {
                headless: true, reducedMotion: 'reduce', viewport: null, ignoreDefaultArgs: ['--disable-extensions'],
                executablePath: process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
                args: ['--window-size=1280,1000', '--disable-background-networking',
                    '--disable-extensions-except=' + extension, '--load-extension=' + extension],
            });
            try {
                await setupContext(context);
                const page = await setupPage(context);
                await login(page, role);
                await navigate(page, view);
                const ready = view === 'pulse' ? '[data-f1-metric=overdue_events]' : '[data-f1-doctor-row=patients]';
                await page.locator(ready).first().waitFor();
                await page.evaluate(axeSource);
                const serviceWorker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker', {timeout: 12000});
                const base = await page.evaluate(() => devicePixelRatio);
                for (const theme of ['light', 'dark']) {
                    await setTheme(page, theme);
                    for (const factor of [1, 2, 4]) {
                        const zoom = await serviceWorker.evaluate(async ({url, factor}) => {
                            const [tab] = (await chrome.tabs.query({})).filter(item => item.url === url);
                            if (!tab) throw new Error('the synthetic tab must exist');
                            await chrome.tabs.setZoom(tab.id, factor);
                            return chrome.tabs.getZoom(tab.id);
                        }, {url: page.url(), factor});
                        assert.equal(Math.round(zoom * 100), factor * 100);
                        await page.waitForFunction(target => Math.abs(devicePixelRatio - target) < 0.01, base * factor);
                        const dimensions = await geometry(page, view);
                        assert.equal(dimensions.overflow, false);
                        assert.equal(dimensions.headingClearOfTopbar, true);
                        assert.equal(dimensions.browserScale, 1);
                        await assessAxe(page, `${view}/${theme}/native-${factor * 100}`);
                        receipt.nativeZoom.push({view, theme, percent: factor * 100, viewport: dimensions.width,
                            violations: 0, headingClearOfTopbar: true});
                    }
                }
            } finally {
                await context.close();
            }
        }
        check('layout: two themes at 320/375/768/1440px and native Chromium 100/200/400%; no clipping, overflow or axe A/AA findings');
    } finally {
        fs.rmSync(dir, {recursive: true, force: true});
    }
}

async function main() {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking']});
    receipt.chromium = browser.version();
    try {
        activeCheck = 'manager selection, card and back';
        const manager = await actor(browser, 'head');
        const kpis = await managerScenario(manager);
        activeCheck = 'doctor browser writes, return and cross-session completion';
        const doctor = await actor(browser, 'doctor');
        const admin = await actor(browser, 'admin');
        await doctorScenario(doctor, admin);
        activeCheck = 'responsive My Day with authoritative event list';
        await responsive(doctor.page, 'myDay');
        activeCheck = 'manager return after audited writes';
        await managerReturn(manager, kpis);
        await responsive(manager.page, 'pulse');
        activeCheck = 'read-only manager';
        const readonly = await actor(browser, 'readonly');
        await readOnlyScenario(readonly);
        await readonly.context.close();
        activeCheck = 'real object reassignment during navigation';
        await revokedScenario(doctor, admin);
        await admin.context.close();
        activeCheck = 'draft and close guards';
        await draftScenario(doctor);
        activeCheck = 'bounded conflicts and real 401';
        await conflicts(doctor);
        activeCheck = 'late response and real identity change';
        await lateResponse(doctor);
    } finally {
        await browser.close();
    }
    activeCheck = 'native Chromium zoom';
    await nativeZoom();
    assert.equal(uiWrites.length, 2);
    assert.equal(receipt.pageErrors, 0);
    receipt.explicitBrowserClinicalWrites = uiWrites.length;
    receipt.noAutomaticClinicalWrites = true;
    fs.writeFileSync(process.env.F2_RECEIPT, JSON.stringify(receipt, null, 2) + '\n', {flag: 'wx', mode: 0o600});
}

main().catch(error => {
    process.stderr.write(`${activeCheck}\n${error.stack}\n`);
    process.exitCode = 1;
});
