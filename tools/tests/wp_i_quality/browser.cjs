const assert = require('node:assert/strict');
const fs = require('node:fs');

const {
    api, chromium, fixture, getQuality, login, observe, qualityView, receipt, setupBounded, successful,
} = require('./browser_helpers.cjs');

const workspace = '#qualityV1Workspace';
const card = '#patientQualityBlock';
const roles = ['developer', 'doctor', 'head', 'caopHead', 'consiliumChair', 'deputy', 'emptyDoctor'];
const ownerName = 'Синтетика I8A3 00000';

function section(page, part, root = workspace) {
    return page.locator(`${root} [data-quality-part="${part}"]`);
}

function action(host, name) {
    return host.locator(`[data-quality-action="${name}"]`);
}

function searchBox(page) {
    return page.locator(`${workspace} [name="search"]`);
}

async function clickFor(page, button, view) {
    const response = getQuality(page, view);
    await button.click();
    return response;
}

async function enter(page) {
    const response = getQuality(page);
    await qualityView(page);
    const main = await successful(await response, 'page');
    await page.locator(workspace).waitFor({ state: 'visible' });
    return main;
}

async function changeSearch(page, value) {
    await searchBox(page).fill(value);
    const response = getQuality(page);
    await action(page.locator(workspace), 'apply').click();
    return response;
}

async function waitPatient(page, id, root = workspace, part = 'patients') {
    const target = section(page, part, root).locator(`[data-patient-id="${id}"]`);
    await target.waitFor({ state: 'visible' });
    return target;
}

async function waitEmptyActions(page, part = 'patients') {
    await page.waitForFunction(({ root, part }) => {
        const section = document.querySelector(`${root} [data-quality-part="${part}"]`);
        return section && ![...section.querySelectorAll('[data-quality-action="patient"],[data-quality-action="group"]')]
            .some(node => !node.disabled && node.getClientRects().length);
    }, { root: workspace, part });
}

function noLegacyOrWrites(ledger) {
    assert.equal(ledger.legacyQuality, 0);
    assert.equal(ledger.fullRegistry, 0);
    assert.equal(ledger.writes, 0);
}

async function publicGate(page) {
    const supported = [
        ['page', {}], ['groups', {}], ['members', { group_id: '0'.repeat(64) }],
        ['facets', { facet: 'doctors' }], ['patient_issues', { owner_patient_id: fixture.ownerPatientId }],
    ];
    for (const [view, extra] of supported) {
        const query = new URLSearchParams({ mode: 'quality-v1', view, ...extra });
        const response = await api(page.context(), 'GET', '/api/data-quality/issues?' + query);
        assert.equal(response.status(), 503);
        const result = await response.json();
        assert.equal(result.code ?? result.detail?.code, 'QUALITY_NOT_AVAILABLE');
        assert(!('patients' in result) && !('collection' in result));
    }
    const legacy = await api(page.context(), 'GET', '/api/data-quality/issues?limit=1');
    assert.equal(legacy.status(), 200);
    assert(Array.isArray((await legacy.json()).patients));
}

async function verifyPagedUi(page, first, ledger, results) {
    const rows = section(page, 'patients');
    assert.equal(first.patients.page.limit, 50);
    assert(first.patients.page.has_next, 'fixture must have a second real patient page');
    await waitPatient(page, first.patients.items[0].patient_id);
    assert.equal(await action(rows, 'patient').count(), first.patients.items.length);
    assert.equal(ledger.quality.length, 1, 'single composite GET before explicit navigation');
    assert.equal(await action(rows, 'previous').isDisabled(), true);

    const next = action(rows, 'next');
    await next.focus();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Shift+Tab');
    assert(await next.evaluate(node => document.activeElement === node && node.matches(':focus-visible')));
    let awaited = getQuality(page);
    await next.press('Enter');
    const nextResponse = await awaited;
    const second = await successful(nextResponse, 'page');
    const nextUrl = new URL(nextResponse.url());
    assert.equal(second.snapshot.token, first.snapshot.token, 'equal signed root');
    assert.equal(second.patients.page.offset, 50);
    assert.equal(nextUrl.searchParams.get('cursor'), first.patients.page.next_cursor, 'original opaque cursor forwarded');
    assert(second.patients.items.every(row => !first.patients.items.some(other => other.patient_id === row.patient_id)));
    await waitPatient(page, second.patients.items[0].patient_id);
    assert.equal(await action(rows, 'patient').count(), second.patients.items.length);
    assert.equal(ledger.quality.length, 2, 'one additional GET for next page');

    const previous = await successful(await clickFor(page, action(rows, 'previous'), 'page'), 'page');
    assert.equal(previous.patients.page.offset, 0);
    assert.equal(previous.snapshot.token, first.snapshot.token);
    await waitPatient(page, previous.patients.items[0].patient_id);
    assert.equal(ledger.quality.length, 3);
    results.checks.push('real composite first/next/previous, one request per click, keyboard focus, no hidden traversal');
}

async function verifyCollections(page, first, results) {
    const groups = section(page, 'groups');
    const group = first.duplicate_groups.items.find(row => row.member_total === fixture.largeGroupMemberCount);
    assert(group, 'root group preview must contain the seeded large group');
    const groupRoot = first.snapshot.token;
    if (first.duplicate_groups.page.has_next) {
        const next = await successful(await clickFor(page, action(groups, 'next'), 'groups'), 'groups');
        assert.equal(next.snapshot.token, groupRoot);
        assert.equal(next.collection.page.offset, first.duplicate_groups.page.limit);
        await groups.locator(`[data-group-id="${next.collection.items[0].group_id}"]`).waitFor({ state: 'visible' });
        assert.equal(await action(groups, 'group').count(), next.collection.items.length);
        const previous = await successful(await clickFor(page, action(groups, 'previous'), 'groups'), 'groups');
        assert.equal(previous.collection.page.offset, 0);
        await groups.locator(`[data-group-id="${group.group_id}"]`).waitFor({ state: 'visible' });
    } else {
        assert.fail('fixture must exercise a real second group page');
    }

    const memberReply = await clickFor(
        page, groups.locator(`[data-quality-action="group"][data-group-id="${group.group_id}"]`), 'members',
    );
    const members = await successful(memberReply, 'members');
    assert.equal(members.snapshot.token, groupRoot);
    assert.equal(members.selection.group_id, group.group_id);
    await waitPatient(page, members.collection.items[0].patient_id, workspace, 'members');
    const memberPanel = section(page, 'members');
    assert.equal(await action(memberPanel, 'patient').count(), members.collection.items.length);
    assert.equal(members.collection.page.total, fixture.largeGroupMemberCount);
    const nextMembers = await successful(await clickFor(page, action(memberPanel, 'next'), 'members'), 'members');
    assert.equal(nextMembers.snapshot.token, groupRoot);
    assert.equal(nextMembers.collection.page.offset, 50);
    await waitPatient(page, nextMembers.collection.items[0].patient_id, workspace, 'members');
    assert.equal(await action(memberPanel, 'patient').count(), nextMembers.collection.items.length);

    for (const facetName of ['doctors', 'departments']) {
        const host = section(page, facetName);
        const collection = first.facets[facetName];
        assert(collection.items.length, 'fixture must contain real facet options');
        if (collection.page.has_next) {
            const facetResponse = await successful(await clickFor(page, action(host, 'next'), 'facets'), 'facets');
            assert.equal(facetResponse.snapshot.token, groupRoot);
            assert.equal(facetResponse.selection.facet, facetName);
            assert.equal(facetResponse.collection.page.offset, collection.page.limit);
        } else {
            const response = getQuality(page, 'facets');
            const control = action(host, 'facet-search');
            await control.click();
            const facetResponse = await successful(await response, 'facets');
            assert.equal(facetResponse.selection.facet, facetName);
        }
    }
    results.checks.push('real group pager, members and both facet selectors bound to the original root');
}

async function verifyCard(page, first, ledger, results) {
    // The owner may be outside the first 50 rows. Use an explicit bounded search
    // or the existing guarded registry action to reach the known synthetic owner.
    const row = first.patients.items.find(item => item.patient_id === fixture.ownerPatientId);
    if (!row) {
        const response = await changeSearch(page, ownerName);
        const filtered = await successful(response, 'page');
        assert(filtered.patients.items.some(item => item.patient_id === fixture.ownerPatientId));
        assert.equal(new URL(response.url()).searchParams.has('search'), false);
        assert.equal(decodeURIComponent(await response.request().headerValue('x-search')), ownerName);
    }
    const button = await waitPatient(page, fixture.ownerPatientId);
    const objectRead = page.waitForResponse(response => new URL(response.url()).pathname ===
        '/api/patients/' + encodeURIComponent(fixture.ownerPatientId));
    const loaded = getQuality(page, 'patient_issues');
    const boundedGroups = getQuality(page, 'groups');
    await button.click();
    assert.equal((await objectRead).status(), 200);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id, fixture.ownerPatientId);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'crumbPatientName',
        'browser focused the new record heading after validated navigation');
    const reply = await successful(await loaded, 'patient_issues');
    assert.equal(reply.selection.owner_patient_id, fixture.ownerPatientId);
    assert.notEqual(reply.snapshot.token, first.snapshot.token, 'card has its own owner-bound root');
    const groups = await successful(await boundedGroups, 'groups');
    assert.equal(groups.snapshot.token, reply.snapshot.token);
    assert.equal(groups.selection.owner_patient_id, fixture.ownerPatientId);
    const departments = await successful(await clickFor(page,
        action(page.locator(card), 'show-departments'), 'facets'), 'facets');
    assert.equal(departments.snapshot.token, reply.snapshot.token);
    assert.equal(departments.selection.owner_patient_id, fixture.ownerPatientId);
    assert.equal(departments.collection.page.total, reply.patient.departments.total);
    await action(section(page, 'issues', card), 'finding').first().waitFor({ state: 'visible' });
    assert.equal(await action(section(page, 'issues', card), 'finding').count(), reply.collection.items.length);
    const next = await successful(await clickFor(page, action(section(page, 'issues', card), 'next'), 'patient_issues'),
        'patient_issues');
    assert.equal(next.snapshot.token, reply.snapshot.token);
    assert.equal(next.collection.page.offset, 50);
    await page.waitForFunction(({ expected, root }) => document.querySelectorAll(
        `${root} [data-quality-part="issues"] [data-quality-action="finding"]`,
    ).length === expected, { root: card, expected: next.collection.items.length });
    const finding = next.collection.items.findIndex(item =>
        item.suggested_action.can_write && item.suggested_action.tab === 'events');
    assert(finding >= 0, 'editable event finding seeded');
    const fresh = page.waitForResponse(response => new URL(response.url()).pathname ===
        '/api/patients/' + encodeURIComponent(fixture.ownerPatientId));
    await action(section(page, 'issues', card), 'finding').nth(finding).click();
    assert.equal((await fresh).status(), 200);
    await page.waitForFunction(() => app.currentTab === 'events');
    assert(ledger.objectReads > 1);
    assert.equal(ledger.writes, 0);
    results.checks.push('registry and finding actions use fresh object GET; owner issue/groups/departments stay bounded');
}

async function verifyFacetSelection(page, results) {
    const main = await enter(page);
    const doctors = section(page, 'doctors');
    const second = await successful(await clickFor(page, action(doctors, 'next'), 'facets'), 'facets');
    const doctor = second.collection.items[0];
    await doctors.locator(`[data-facet-id="${doctor.user_id}"]`).waitFor({ state: 'visible' });
    await doctors.locator('[name="facet_search"]').fill(doctor.display_name);
    const searchReply = await clickFor(page, action(doctors, 'facet-search'), 'facets');
    const found = await successful(searchReply, 'facets');
    assert.equal(found.snapshot.token, main.snapshot.token);
    assert.equal(found.collection.page.offset, 0);
    assert.equal(decodeURIComponent(await searchReply.request().headerValue('x-quality-facet-search')), doctor.display_name);
    assert.equal(new URL(searchReply.url()).searchParams.has('facet_search'), false);
    assert(found.collection.items.some(item => item.user_id === doctor.user_id));
    const choice = doctors.locator(`[data-facet-id="${doctor.user_id}"]`);
    const selectedReply = await clickFor(page, choice, 'page');
    const selected = await successful(selectedReply, 'page');
    assert.notEqual(selected.snapshot.token, main.snapshot.token);
    assert.equal(new URL(selectedReply.url()).searchParams.get('doctor_user_id'), doctor.user_id);
    assert.equal(selected.totals.patients, doctor.patient_count);
    assert.equal(selected.scope_summary.total_patients, main.scope_summary.total_patients);
    await waitPatient(page, selected.patients.items[0].patient_id);
    results.checks.push('explicit second-page facet selection uses technical ID and header search; scope totals separate');
}

async function captureAccessibility(page, results) {
    const axeSource = process.env.AXE_SOURCE;
    assert(axeSource, 'AXE_SOURCE is required for the browser suite');
    await page.evaluate(fs.readFileSync(axeSource, 'utf8'));
    for (const theme of ['light', 'dark']) {
        await page.evaluate(t => app.applyTheme(t), theme);
        for (const width of [1440, 375, 320]) {
            await page.setViewportSize({ width, height: 960 });
            const state = await page.evaluate(async () => {
                const violations = await axe.run(document, {
                    runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa'] },
                });
                return {
                    overflow: document.documentElement.scrollWidth > innerWidth,
                    violations: violations.violations.map(rule => rule.id),
                };
            });
            results.accessibility.push({ theme, width, ...state });
            assert.equal(state.overflow, false, 'no viewport horizontal overflow');
            assert.deepEqual(state.violations, [], 'bounded workspace WCAG A/AA checks');
        }
    }
    await page.setViewportSize({ width: 1440, height: 960 });
}

async function verifyRole(browser, actor, results) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
    try {
        const page = await context.newPage();
        await login(page, fixture.accounts[actor]);
        const nav = page.locator('[data-view="quality"]');
        const access = await api(context, 'GET', `${fixture.qualityPath}?mode=quality-v1&view=page&limit=50`);
        assert.equal(access.status(), 200, 'scope reads through real SQLite view');
        const body = await access.json();
        const scope = body.scope_summary.total_patients;
        const browserChecks = { actor, scopedPatientCount: scope, rows: body.patients.items.length, nav: await nav.isVisible() };
        if (actor === 'consiliumChair') {
            assert.equal(browserChecks.nav, false, 'preserve existing chair sidebar');
            await setupBounded(page);
            const detail = getQuality(page, 'patient_issues');
            await page.evaluate(id => app.openPatient(id), fixture.chairPatientId);
            await page.waitForFunction(id => app.currentPatientId === id && app.currentView === 'patient', fixture.chairPatientId);
            const scoped = await successful(await detail, 'patient_issues');
            assert.equal(scoped.selection.owner_patient_id, fixture.chairPatientId);
            results.roles.push({ ...browserChecks, scopedCard: true });
            return;
        }
        assert.equal(browserChecks.nav, true);
        const ledger = observe(page);
        await setupBounded(page);
        const uiBody = await enter(page);
        assert.equal(uiBody.scope_summary.total_patients, scope);
        if (uiBody.patients.items.length) await waitPatient(page, uiBody.patients.items[0].patient_id);
        assert.equal(await action(section(page, 'patients'), 'patient').count(), browserChecks.rows);
        if (actor === 'emptyDoctor') {
            assert.equal(scope, 0);
            assert.equal(uiBody.patients.items.length, 0);
            assert.equal(uiBody.duplicate_groups.items.length, 0);
            assert.equal(uiBody.facets.doctors.items.length, 0);
        }
        if (actor === 'doctor') {
            assert(!uiBody.patients.items.some(item => item.patient_id === fixture.hiddenPatientId));
            const unseen = await api(context, 'GET', `${fixture.qualityPath}?mode=quality-v1&view=patient_issues&owner_patient_id=${
                encodeURIComponent(fixture.hiddenPatientId)}`);
            const nonexistent = await api(context, 'GET', `${fixture.qualityPath}?mode=quality-v1&view=patient_issues&owner_patient_id=missing-synthetic-owner`);
            assert.equal(unseen.status(), 404);
            assert.equal(nonexistent.status(), 404);
            assert.deepEqual(await unseen.json(), await nonexistent.json());
        }
        noLegacyOrWrites(ledger);
        results.roles.push(browserChecks);
    } finally {
        await context.close();
    }
}

async function main() {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    const results = { realFastApi: true, syntheticOnly: true, chromium: browser.version(), checks: [],
        roles: [], accessibility: [], latency: [], faultChecks: [] };
    try {
        const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' });
        const page = await context.newPage();
        const pageErrors = [];
        page.on('pageerror', error => pageErrors.push(error.name));
        await login(page, fixture.accounts.developer);
        await publicGate(page);
        results.checks.push('five well-formed production requests retain coded 503; legacy remains available');

        const ledger = observe(page);
        await setupBounded(page);
        const start = performance.now();
        const first = await enter(page);
        await waitPatient(page, first.patients.items[0].patient_id);
        results.latency.push({ case: 'first render', elapsedMs: Math.round(performance.now() - start),
            syntheticPatientCount: fixture.count, displayedRows: first.patients.items.length });
        await verifyPagedUi(page, first, ledger, results);
        await verifyCollections(page, first, results);
        await captureAccessibility(page, results);
        await verifyCard(page, first, ledger, results);
        await verifyFacetSelection(page, results);
        noLegacyOrWrites(ledger);
        assert.deepEqual(pageErrors, []);
        results.transport = ledger;
        await context.close();

        for (const actor of roles) await verifyRole(browser, actor, results);
        await verifyLifecycleFaults(browser, results);
        receipt('browser-results.json', results);
        console.log(JSON.stringify({ checks: results.checks, roles: results.roles.length,
            faultChecks: results.faultChecks.length, accessibility: results.accessibility.length, chromium: results.chromium }));
    } finally {
        receipt('browser-partial-results.json', results);
        await browser.close();
    }
}

async function verifyLifecycleFaults(browser, results) {
    // Kept separate from the scope and final market DOM assertions. These stub
    // only browser transport while the rest of the application stays FastAPI.
    const runFaults = require('./browser_faults.cjs');
    await runFaults(browser, results, {
        enter, clickFor, changeSearch, searchBox, section, action,
        waitPatient, waitEmptyActions, noLegacyOrWrites,
    });
}

main().catch(error => {
    console.error(error.stack);
    process.exitCode = 1;
});
