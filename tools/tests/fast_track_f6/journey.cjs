'use strict';

const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const {setup, login, password} = require('../wp_i_consilium/fixtures.cjs');

const origin = process.env.WP_H_ORIGIN;
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const appointments = '/api/consilium/appointments';
const checks = [];
const writes = [];
const readHashes = [];
const axeStates = [];
const transportFaults = [];
let phase = 'startup';
let pageErrors = 0;

async function keyboard(control) {
    await control.and(control.page().locator(':not(:disabled)')).waitFor();
    await control.focus();
    assert(await control.evaluate(node => node === document.activeElement && !node.disabled));
    await control.press('Enter');
}

function response(page, method, pathname) {
    return page.waitForResponse(res => new URL(res.url()).pathname === pathname && res.request().method() === method);
}

async function medicalWrite(page, method, pathname, control, status = 200) {
    const [res] = await Promise.all([response(page, method, pathname), keyboard(control)]);
    assert.equal(res.status(), status);
    return res.json();
}

async function authenticated(browser, fixture, role) {
    const context = await browser.newContext({reducedMotion: 'reduce', viewport: {width: 1440, height: 1000}});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.on('pageerror', () => { pageErrors++; });
    page.on('request', req => {
        const pathname = new URL(req.url()).pathname;
        if (pathname.startsWith(appointments) && !['GET', 'HEAD'].includes(req.method())) {
            writes.push({role, method: req.method(), pathname, body: req.postDataJSON()});
        }
    });
    await login(page, origin, fixture.roles[role].username);
    await keyboard(page.locator('[data-view=consilium]').first());
    await page.waitForFunction(() => app.consiliumGridReady());
    const date = page.locator(role === 'consilium_chair' ? '#consiliumDateFilter' : '#consiliumDoctorDate');
    await date.fill(fixture.day);
    await page.locator('.consilium-slot').first().waitFor();
    await page.waitForFunction(() => app.consiliumGridReady());
    return {context, page};
}

function slot(page, time) {
    return page.locator('.consilium-slot').filter({has: page.locator('.consilium-slot-time', {hasText: time})});
}

function row(page, id) {
    return page.locator('#consilium-grid-row-' + id);
}

async function refresh(page) {
    const [res] = await Promise.all([response(page, 'GET', appointments), keyboard(page.locator('#consiliumRefreshBtn'))]);
    assert.equal(res.status(), 200);
    const bytes = await res.body();
    readHashes.push(createHash('sha256').update(bytes).digest('hex'));
    await page.waitForFunction(() => app.consiliumGridReady());
    return JSON.parse(bytes).appointments;
}

async function detail(page, appointment, patient) {
    const trigger = row(page, appointment.id).getByRole('button', {name: patient.name, exact: true});
    const [fresh] = await Promise.all([response(page, 'GET', appointments), keyboard(trigger)]);
    assert.equal(fresh.status(), 200);
    const bytes = await fresh.body();
    readHashes.push(createHash('sha256').update(bytes).digest('hex'));
    const verified = JSON.parse(bytes).appointments.find(item => item.id === appointment.id);
    assert.equal(verified.version, appointment.version);
    await page.locator('#consiliumDetailsModal').waitFor();
    await page.locator('#consiliumDetailsBody').getByText(patient.name, {exact: true}).waitFor();
    const modal = page.locator('#consiliumDetailsModal');
    await page.keyboard.press('Shift+Tab');
    assert(await modal.evaluate(node => node.contains(document.activeElement)));
    await page.keyboard.press('Tab');
    await page.keyboard.press('Escape');
    await modal.waitFor({state: 'hidden'});
    assert(await trigger.evaluate(node => node === document.activeElement));
    return verified;
}

async function appearance(page) {
    const script = fs.readFileSync(process.env.AXE_SOURCE, 'utf8');
    await page.evaluate(script);
    const view = page.locator('#view-consilium');
    for (const theme of ['light', 'dark']) {
        if ((await page.locator('html').getAttribute('data-theme')) !== theme) {
            await page.locator('#themeToggle').click();
        }
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        for (const width of [320, 375, 768, 1440]) {
            await page.setViewportSize({width, height: 1000});
            const geometry = await view.evaluate(node => {
                window.scrollTo(0, 0);
                const title = node.querySelector('h1');
                const box = title.getBoundingClientRect();
                const x = box.left + Math.min(12, box.width / 2);
                const y = box.top + box.height / 2;
                const hit = document.elementFromPoint(x, y);
                return {overflow: document.documentElement.scrollWidth > innerWidth,
                    titleVisible: box.top >= 0 && (hit === title || title.contains(hit))};
            });
            assert.deepEqual(geometry, {overflow: false, titleVisible: true});
            const violations = await page.evaluate(async () => (await axe.run(document, {
                runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']},
            })).violations.map(item => ({id: item.id, targets: item.nodes.map(node => node.target)})));
            assert.deepEqual(violations, []);
            axeStates.push({theme, width, violations: 0, titleVisible: true});
            if (theme === 'light' && width === 1440 || theme === 'dark' && width === 375) {
                await page.screenshot({path: path.join(process.env.OUT, 'f6-consilium-' + theme + '-' + width + '.png'),
                    animations: 'disabled'});
            }
        }
    }
}

async function run() {
    const browser = await chromium.launch({headless: true, executablePath: process.env.BROWSER_EXECUTABLE,
        args: ['--disable-background-networking']});
    try {
        const f = await setup(browser, origin);
        const settings = {returnComment: 'Синтетическое основание F6', original: 'Синтетическая запись F6',
            corrected: 'Синтетическое уточнение F6'};
        phase = 'doctor booking slot and draft';
        const doctor = await authenticated(browser, f, 'doctor');
        const dp = doctor.page;
        // The foreign appointment's time is occupied, with no identity or action.
        const foreignSlot = slot(dp, '10:30');
        assert((await foreignSlot.innerText()).includes('Занято'));
        assert(!(await foreignSlot.innerText()).includes(f.foreign.name));
        assert.equal(await foreignSlot.getByRole('button').count(), 0);
        const selectedPatient = f.patients[0];
        const patientPath = '/api/patients/' + selectedPatient.id;
        const patientBefore = await f.call(doctor.context, 'GET', patientPath);
        await keyboard(slot(dp, '10:00').getByRole('button', {name: /записать/i}));
        await dp.locator('#consiliumModal').waitFor();
        await dp.locator('#consiliumPatient').selectOption(f.patients[1].id);
        await dp.waitForFunction(id => app.consilium.verifiedPatient?.id === id, f.patients[1].id);
        await dp.locator('#consiliumComment').fill('Синтетический ввод другого пациента');
        let fetched;
        let release;
        const arrived = new Promise(resolve => { fetched = resolve; });
        const held = new Promise(resolve => { release = resolve; });
        await dp.route(origin + patientPath, async route => {
            const result = await route.fetch();
            fetched(result.status());
            await held;
            await route.fulfill({response: result});
        }, {times: 1});
        const freshPatient = response(dp, 'GET', patientPath);
        try {
            await dp.locator('#consiliumPatient').selectOption(selectedPatient.id);
            assert.equal(await Promise.race([arrived, freshPatient.then(() => null)]), 200);
            assert.equal(await dp.locator('#submitConsiliumBtn').isDisabled(), true);
            assert.equal(await dp.locator('#consiliumComment').inputValue(), '');
            assert.equal(await dp.locator('#consiliumPrimaryDiagnosis').inputValue(), '');
            assert.equal(writes.length, 0);
        } finally {
            release();
        }
        assert.equal((await freshPatient).status(), 200);
        await dp.waitForFunction(() => document.querySelector('#consiliumPrimaryDiagnosis').value === 'C34.1');
        checks.push('switching booked patient clears prior clinical fields and blocks writes until a fresh successful object read');
        await dp.locator('#consiliumComment').fill(settings.original);
        assert.equal(writes.length, 0);

        // A real 201/commit is deliberately withheld to create an unknown result.
        phase = 'actual booking COMMIT with lost reply';
        let record;
        await dp.route(origin + appointments, async route => {
            assert.equal(route.request().method(), 'POST');
            const committed = await route.fetch();
            assert.equal(committed.status(), 201);
            record = await committed.json();
            await route.abort('connectionreset');
        }, {times: 1});
        await keyboard(dp.locator('#submitConsiliumBtn'));
        await dp.locator('#consiliumConflict').waitFor();
        assert(record && record.status === 'recorded' && record.patientId === selectedPatient.id);
        assert.equal(await dp.locator('#consiliumComment').inputValue(), settings.original);
        assert.equal(await dp.locator('#submitConsiliumBtn').isDisabled(), true);
        assert.equal(writes.length, 1);
        const [reconciled] = await Promise.all([response(dp, 'GET', appointments),
            keyboard(dp.locator('#consiliumConflictRefreshBtn'))]);
        assert.equal(reconciled.status(), 200);
        const matching = (await reconciled.json()).appointments.filter(item => item.patientId === selectedPatient.id);
        assert.equal(matching.length, 1);
        assert.equal(matching[0].id, record.id);
        await dp.locator('#consiliumConflictText').getByText(/Сервер вернул запись/).waitFor();
        assert.equal(await dp.locator('#consiliumComment').inputValue(), settings.original);
        assert.equal(await dp.locator('#submitConsiliumBtn').isDisabled(), true);
        assert.equal(writes.length, 1);
        await keyboard(dp.locator('#closeConsiliumBtn'));
        await dp.locator('#consiliumModal').waitFor({state: 'hidden'});
        await refresh(dp);
        await row(dp, record.id).waitFor();
        assert.equal(writes.length, 1);
        transportFaults.push('browser connection reset after actual FastAPI 201 and COMMIT');
        checks.push('saved free slot, masked foreign occupation, one real booking COMMIT with dropped reply, retained input until explicit authorized reconciliation');

        phase = 'doctor detail authorization and transfer';
        await detail(dp, record, selectedPatient);
        await keyboard(row(dp, record.id).getByRole('button', {name: 'Изменить', exact: true}));
        await dp.locator('#consiliumTime').selectOption('11:00');
        const moved = await medicalWrite(dp, 'PUT', appointments + '/' + record.id, dp.locator('#submitConsiliumBtn'));
        assert.equal(moved.scheduledTime, '11:00');
        assert.equal(moved.version, record.version + 1);
        assert.equal(writes.at(-1).body.version, record.version);
        assert.equal(moved.patientId, record.patientId);
        assert.equal(moved.consiliumGroup, record.consiliumGroup);
        await dp.locator('#consiliumModal').waitFor({state: 'hidden'});
        checks.push('fresh authorized detail with keyboard focus trap and restoration; one versioned transfer preserving patient and group');

        phase = 'chair review and revision';
        const chair = await authenticated(browser, f, 'consilium_chair');
        const cp = chair.page;
        const queueRow = cp.locator('#consiliumReviewList li').filter({hasText: selectedPatient.name});
        const openReview = name => keyboard(queueRow.getByRole('button', {name, exact: true}));
        await openReview('Рассмотреть');
        const reviewing = await medicalWrite(cp, 'POST', appointments + '/' + record.id + '/review',
            cp.locator('#startConsiliumReviewBtn'));
        assert.equal(reviewing.status, 'in_review');
        await keyboard(cp.locator('#returnConsiliumBtn'));
        await cp.locator('#consiliumRevisionComment').fill(settings.returnComment);
        const returned = await medicalWrite(cp, 'POST', appointments + '/' + record.id + '/return',
            cp.locator('#confirmConsiliumRevisionBtn'));
        assert.equal(returned.status, 'needs_revision');
        await cp.locator('#consiliumDecisionModal').waitFor({state: 'hidden'});

        phase = 'doctor correction and explicit resubmit';
        assert.equal((await refresh(dp)).find(item => item.id === record.id).status, 'needs_revision');
        await keyboard(row(dp, record.id).getByRole('button', {name: 'Изменить', exact: true}));
        await dp.locator('#consiliumComment').fill(settings.corrected);
        const resubmitted = await medicalWrite(dp, 'POST', appointments + '/' + record.id + '/resubmit',
            dp.locator('#resubmitConsiliumBtn'));
        assert.equal(resubmitted.status, 'revised');
        assert.equal(resubmitted.comment, settings.corrected);
        const correction = writes.find(item => item.role === 'doctor' && item.method === 'PUT' &&
            item.body.comment === settings.corrected);
        assert.equal(correction.body.version, returned.version);
        await dp.locator('#consiliumModal').waitFor({state: 'hidden'});
        checks.push('real review, one return round with reason, doctor observed versioned correction and explicit resubmit');

        phase = 'chair fresh second review and sign confirmation';
        assert.equal((await refresh(cp)).find(item => item.id === record.id).status, 'revised');
        await openReview('Рассмотреть повторно');
        const renewed = await medicalWrite(cp, 'POST', appointments + '/' + record.id + '/review',
            cp.locator('#startConsiliumReviewBtn'));
        assert.equal(renewed.status, 'in_review');
        await cp.locator('#consiliumTreatmentType').selectOption('surgery');
        await keyboard(cp.locator('#signConsiliumBtn'));
        await cp.locator('#confirmModal').waitFor();
        assert.equal(writes.filter(item => item.pathname.endsWith('/sign')).length, 0);
        await keyboard(cp.locator('#confirmCancelBtn'));
        await cp.locator('#confirmModal').waitFor({state: 'hidden'});
        assert.equal(writes.filter(item => item.pathname.endsWith('/sign')).length, 0);
        assert.equal(await cp.locator('#consiliumTreatmentType').inputValue(), 'surgery');
        await keyboard(cp.locator('#signConsiliumBtn'));
        const signed = await medicalWrite(cp, 'POST', appointments + '/' + record.id + '/sign',
            cp.locator('#confirmOkBtn'));
        assert.equal(signed.status, 'signed');
        assert.equal(signed.treatmentType, 'surgery');
        await cp.locator('#consiliumDecisionModal').waitFor({state: 'hidden'});

        phase = 'immutability, denied write and true synthetic rendering';
        const after = (await refresh(dp)).find(item => item.id === record.id);
        assert.equal(after.status, 'signed');
        assert.equal(after.version, signed.version);
        await detail(dp, signed, selectedPatient);
        assert.equal(await row(dp, record.id).getByRole('button', {name: 'Изменить', exact: true}).count(), 0);
        const illegal = await f.request(doctor.context, 'POST', appointments + '/' + record.id + '/sign', {treatmentType: 'surgery'});
        assert.equal(illegal.status(), 403);
        const secondSign = await f.request(chair.context, 'POST', appointments + '/' + record.id + '/sign', {treatmentType: 'surgery'});
        assert.equal(secondSign.status(), 409);
        const patientAfter = await f.call(doctor.context, 'GET', patientPath);
        assert.equal(patientAfter.stage, patientBefore.stage);
        assert.equal(patientAfter.version, patientBefore.version);
        assert.deepEqual((await refresh(dp)).find(item => item.id === record.id), after);
        phase = 'patient card authorized signed history read';
        await keyboard(dp.locator('[data-view=patients]').first());
        const card = dp.locator('[data-registry-patient="' + selectedPatient.id + '"]');
        const [cardRead] = await Promise.all([response(dp, 'GET', patientPath), keyboard(card)]);
        assert.equal(cardRead.status(), 200);
        readHashes.push(createHash('sha256').update(await cardRead.body()).digest('hex'));
        const [cardHistory] = await Promise.all([response(dp, 'GET', appointments),
            keyboard(dp.locator('#loadPatientConsiliumBtn'))]);
        assert.equal(cardHistory.status(), 200);
        readHashes.push(createHash('sha256').update(await cardHistory.body()).digest('hex'));
        const cardRecord = dp.locator('#patientConsiliumRecords').getByRole('button', {name: /Подписан$/});
        await keyboard(cardRecord);
        const cardDetails = dp.locator('#consiliumDetailsModal');
        await cardDetails.getByText(/Подписанное решение/).waitFor();
        assert.equal(await dp.locator('#consiliumArchiveToggleBtn').isVisible(), false);
        assert.equal(await cardDetails.getByRole('button', {name: /Изменить|Рассмотреть/}).count(), 0);
        await dp.keyboard.press('Escape');
        await cardDetails.waitFor({state: 'hidden'});
        assert(await cardRecord.evaluate(node => node === document.activeElement));
        assert(await dp.locator('#view-patient').isVisible());
        assert.equal(writes.length, 8);
        checks.push('patient overview opens authorized signed snapshot by stable patient ID, no mutation controls and Escape restores exact link focus');
        phase = 'presentational checks';
        checks.push('second review and explicit confirm; cancel causes no write, one sign only, signed state immutable, 403 for doctor and 409 for repeat chair, patient stage unchanged');
        await appearance(cp);
        checks.push('both approved themes at 320/375/768/1440, full-document axe A/AA, no horizontal page overflow, title remains visible');
        assert.equal(pageErrors, 0);
        const privateProof = {id: record.id, patient: selectedPatient.id, doctor: f.roles.doctor.id,
            chair: f.roles.consilium_chair.id, version: signed.version, readHashes, stage: patientBefore.stage,
            returnComment: settings.returnComment};
        fs.writeFileSync(path.join(process.env.OUT, 'journey.local.json'), JSON.stringify({
            suite: 'journey', browser: browser.version(), checks, axe: axeStates, transportFaults,
            requests: {medicalWrites: writes.length, unexpectedMedicalWrites: 0}, pageErrors, privateProof,
        }, null, 2));
    } finally {
        await browser.close();
    }
}

run().catch(error => {
    process.stderr.write(phase + '\n' + error.stack + '\n');
    process.exitCode = 1;
});
