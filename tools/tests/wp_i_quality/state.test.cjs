'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

require('../../../static/quality-v1.js');
const { Reader, requestPath } = globalThis.ClinicQualityV1;

const primary = { view: 'page' };
const summary = {
    total_patients: 0, patients_with_issues: 0, total_issues: 0,
    critical_count: 0, warning_count: 0, info_count: 0,
    possible_duplicate_groups: 0, unresolved_patients: 0, average_completeness: 0,
};

function collection(items = [], offset = 0, total = items.length, nextCursor = null) {
    return {
        items,
        page: { limit: 50, offset, total, has_next: nextCursor !== null, next_cursor: nextCursor },
    };
}

function patient(id) {
    return {
        patient_id: id, patient_name: 'Синтетическая карточка',
        treating_doctor: { user_id: null, display_name: '' },
        departments: { preview: [], total: 0, has_more: false },
        completeness: { score: 100, filled: 6, total: 6, missing: [] },
        issue_count: 0, max_severity: null, top_issues: [], can_edit: false,
    };
}

function response(args, overrides = {}) {
    const { query, selector, snapshot } = args;
    const { view, group_id = null, owner_patient_id = null, facet = null } = selector;
    const scope = {
        mode: 'quality-v1',
        view,
        snapshot: {
            token: snapshot || 'synthetic-root', revision: 'a'.repeat(64), query_hash: 'b'.repeat(64),
            computed_at: '2026-11-19T12:00:00+03:00', expires_at: '2026-11-19T12:10:00+03:00',
            institution_day: '2026-11-19', timezone: 'Europe/Moscow',
        },
        query: {
            sort: query.sort || 'severity', has_search: Boolean(query.search?.trim()),
            active_filters: ['severity', 'category', 'code', 'patient_id', 'doctor_user_id',
                'department_id', 'search'].filter(key => query[key]),
        },
        scope_summary: { ...summary },
        totals: { patients: 0, findings: 0, duplicate_groups: 0 },
        bases: {
            scope_summary: 'authorized_scope', totals: 'matched_query', facets: 'authorized_scope',
            groups: 'groups_touching_at_least_one_matched_patient',
        },
    };
    if (view === 'page') {
        return {
            ...scope, patients: collection(), duplicate_groups: collection(),
            facets: { doctors: collection(), departments: collection() }, ...overrides,
        };
    }
    const body = { ...scope, selection: { group_id, owner_patient_id, facet }, collection: collection() };
    if (view === 'patient_issues') {
        body.patient = patient(owner_patient_id);
        body.detail_scope = 'all_patient_rules';
    }
    if (view === 'members') {
        body.group = {
            group_id, reason_codes: ['phone'], reason: 'Совпадает номер телефона',
            member_total: 2, matching_member_total: 1, manual_review_required: true,
        };
        body.collection = collection([
            { patient_id: 'member-one', patient_name: 'Синтетическая А', completeness_score: 100, matches_query: true },
            { patient_id: 'member-two', patient_name: 'Синтетическая Б', completeness_score: 100, matches_query: false },
        ]);
    }
    return { ...body, ...overrides };
}

function refusal(status, code) {
    return Object.assign(new Error('Синтетический отказ'), { status, data: code ? { code } : null });
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

function harness() {
    const active = { context: 'synthetic-user:initial-scope:1', visible: true };
    const events = [];
    const accessLosses = [];
    const reads = [];
    const pendingReaders = new Map();
    const reader = new Reader({
        context: () => active.context,
        visible: () => active.visible,
        read(args) {
            const pending = { args, ...deferred() };
            const index = reads.push(pending) - 1;
            pendingReaders.get(index)?.resolve(pending);
            pendingReaders.delete(index);
            return pending.promise;
        },
        publish: event => events.push(event),
        onAccessLost: error => accessLosses.push(error),
    });

    function readAt(index) {
        if (reads[index]) return Promise.resolve(reads[index]);
        if (!pendingReaders.has(index)) pendingReaders.set(index, deferred());
        return pendingReaders.get(index).promise;
    }

    return {
        reader, active, events, accessLosses, reads, readAt,
        last: key => events.findLast(event => event.key === key),
    };
}

async function openRoot(h, query = {}) {
    h.reader.reset(query);
    const opening = h.reader.request('patients', primary);
    const { args, resolve } = await h.readAt(0);
    assert.equal(args.snapshot, null);
    assert.equal(args.cursor, null);
    resolve(response(args));
    assert.equal(await opening, true);
    assert.equal(h.last('patients').state, 'ready');
}

test('R06 root and explicit patient cursor keep a single snapshot and do not prefetch', { timeout: 3000 }, async () => {
    const h = harness();
    h.reader.reset({ sort: 'issues', search: 'синтетика', department_id: 'department-one' });
    assert.equal(h.reads.length, 0);
    const first = h.reader.request('patients', primary);
    const pageOne = await h.readAt(0);
    assert.equal(pageOne.args.snapshot, null);
    assert.equal(pageOne.args.cursor, null);
    assert.equal(pageOne.args.query.search, 'синтетика');
    assert.equal(pageOne.args.query.department_id, 'department-one');
    const items = Array.from({ length: 50 }, (_, i) => patient('synthetic-' + i));
    pageOne.resolve(response(pageOne.args, {
        patients: collection(items, 0, 51, 'opaque-next'),
        scope_summary: { ...summary, total_patients: 93 }, totals: { patients: 51, findings: 0, duplicate_groups: 0 },
    }));
    assert.equal(await first, true);
    assert.equal(h.last('patients').data.patients.page.total, 51);
    assert.equal(h.last('patients').data.scope_summary.total_patients, 93);
    assert.equal(h.reads.length, 1);

    const second = h.reader.request('patients', primary, 'opaque-next');
    const pageTwo = await h.readAt(1);
    assert.equal(pageTwo.args.cursor, 'opaque-next');
    assert.equal(pageTwo.args.snapshot, 'synthetic-root');
    assert.equal(pageTwo.args.query.sort, 'issues');
    pageTwo.resolve(response(pageTwo.args, {
        patients: collection([patient('synthetic-50')], 50, 51),
        scope_summary: { ...summary, total_patients: 93 }, totals: { patients: 51, findings: 0, duplicate_groups: 0 },
    }));
    assert.equal(await second, true);
    assert.deepEqual(h.last('patients').data.patients.items.map(item => item.patient_id), ['synthetic-50']);
    assert.equal(h.reads.length, 2);
});

test('U08 independent group, member and facet tickets reject superseded search only', { timeout: 3000 }, async () => {
    const h = harness();
    await openRoot(h);
    const calls = [
        ['groups', { view: 'groups' }],
        ['members', { view: 'members', group_id: 'f'.repeat(64) }],
        ['departments', { view: 'facets', facet: 'departments', facet_search: 'третье отделение' }],
        ['doctors', { view: 'facets', facet: 'doctors', facet_search: 'предыдущий поиск' }],
        ['doctors', { view: 'facets', facet: 'doctors', facet_search: 'новый поиск' }],
    ];
    const requests = calls.map(([key, selector]) => h.reader.request(key, selector));
    const sent = await Promise.all(calls.map((_, i) => h.readAt(i + 1)));
    for (const [i, entry] of sent.entries()) {
        assert.equal(entry.args.snapshot, 'synthetic-root');
        assert.equal(entry.args.cursor, null);
        for (const [name, value] of Object.entries(calls[i][1])) {
            assert.equal(entry.args.selector[name], value);
        }
    }
    for (const i of [4, 2, 1, 0]) sent[i].resolve(response(sent[i].args));
    assert.deepEqual(await Promise.all([4, 2, 1, 0].map(i => requests[i])), [true, true, true, true]);
    const publications = h.events.length;
    sent[3].resolve(response(sent[3].args));
    assert.equal(await requests[3], false);
    assert.equal(h.events.length, publications);
    for (const key of ['patients', 'groups', 'members', 'doctors', 'departments']) {
        assert.equal(h.last(key).state, 'ready');
    }
});

test('U01 fresh error remains authoritative over delayed prior 200', { timeout: 3000 }, async () => {
    const h = harness();
    h.reader.reset({ severity: 'warning' });
    const old = h.reader.request('patients', primary);
    const fresh = h.reader.request('patients', primary);
    const oldRead = await h.readAt(0);
    const latest = await h.readAt(1);
    latest.reject(refusal(503, 'QUALITY_NOT_AVAILABLE'));
    assert.equal(await fresh, false);
    const error = h.last('patients');
    assert.equal(error.state, 'error');
    assert.equal(error.data ?? null, null);
    oldRead.resolve(response(oldRead.args));
    assert.equal(await old, false);
    assert.equal(h.last('patients'), error);
    assert.equal(h.reads.length, 2);
});

test('U07 U08 stale query, owner, access context and cleared tickets publish no results', { timeout: 3000 }, async () => {
    const h = harness();
    h.reader.reset({ search: 'предыдущий поиск' });
    const staleQuery = h.reader.request('patients', primary);
    const queryRead = await h.readAt(0);
    h.reader.reset({ search: 'новый поиск' }, 'owner-first');
    const staleOwner = h.reader.request('issues', { view: 'patient_issues', owner_patient_id: 'owner-first' });
    const ownerRead = await h.readAt(1);
    h.reader.reset({}, 'owner-second');
    const staleAccess = h.reader.request('issues', { view: 'patient_issues', owner_patient_id: 'owner-second' });
    const accessRead = await h.readAt(2);
    h.active.context = 'another-user:other-scope:2';
    const beforeStale = h.events.length;
    ownerRead.resolve(response(ownerRead.args));
    queryRead.reject(refusal(401));
    accessRead.resolve(response(accessRead.args));
    assert.deepEqual(await Promise.all([staleQuery, staleOwner, staleAccess]), [false, false, false]);
    assert.equal(h.events.length, beforeStale);
    assert.equal(h.accessLosses.length, 0);

    h.reader.reset({}, 'owner-second');
    const cancelled = h.reader.request('issues', { view: 'patient_issues', owner_patient_id: 'owner-second' });
    const beforeClose = await h.readAt(3);
    h.reader.clear();
    const afterClear = h.events.length;
    beforeClose.resolve(response(beforeClose.args));
    assert.equal(await cancelled, false);
    assert.equal(h.events.length, afterClear);
});

test('S02 hidden workspace and absent identity do not send medical requests', { timeout: 3000 }, async () => {
    const h = harness();
    h.reader.reset({});
    h.active.visible = false;
    assert.equal(await h.reader.request('patients', primary), false);
    h.active.visible = true;
    h.active.context = null;
    assert.equal(await h.reader.request('patients', primary), false);
    assert.equal(h.reads.length, 0);
});

for (const [status, code] of [[409, 'PATIENT_QUERY_CHANGED'], [410, 'CURSOR_EXPIRED']]) {
    test('U02 one benign ' + status + ' restart preserves query and owner, second refusal stops', { timeout: 3000 }, async () => {
        const h = harness();
        const owner = 'owner-first';
        const query = { search: 'синтетический фильтр', sort: 'issues', severity: 'warning' };
        h.reader.reset(query, owner);
        const reading = h.reader.request('issues', { view: 'patient_issues', owner_patient_id: owner });
        const original = await h.readAt(0);
        original.reject(refusal(status, code));
        const restarted = await h.readAt(1);
        assert.equal(restarted.args.cursor, null);
        assert.equal(restarted.args.snapshot, null);
        assert.equal(restarted.args.selector.view, 'patient_issues');
        assert.equal(restarted.args.selector.owner_patient_id, owner);
        for (const [key, value] of Object.entries(query)) assert.equal(restarted.args.query[key], value);
        restarted.reject(refusal(status, code));
        assert.equal(await reading, false);
        assert.equal(h.reads.length, 2);
        assert.equal(h.last('issues').state, 'error');
        assert.equal(h.last('issues').data ?? null, null);
    });
}

test('U02 simultaneous stale children can only launch one fresh primary', { timeout: 3000 }, async () => {
    const h = harness();
    await openRoot(h, { search: 'синтетический запрос' });
    const groups = h.reader.request('groups', { view: 'groups' });
    const facet = h.reader.request('departments', { view: 'facets', facet: 'departments' });
    const oldGroup = await h.readAt(1);
    const oldFacet = await h.readAt(2);
    oldGroup.reject(refusal(409, 'PATIENT_QUERY_CHANGED'));
    oldFacet.reject(refusal(410, 'CURSOR_EXPIRED'));
    const primaryRestart = await h.readAt(3);
    assert.equal(primaryRestart.args.selector.view, 'page');
    assert.equal(primaryRestart.args.snapshot, null);
    assert.equal(primaryRestart.args.query.search, 'синтетический запрос');
    primaryRestart.resolve(response(primaryRestart.args, {
        snapshot: { ...response(primaryRestart.args).snapshot, token: 'synthetic-root-renewed' },
    }));
    await Promise.all([groups, facet]);
    assert.equal(h.reads.length, 4);
    assert.equal(h.last('patients').state, 'ready');
    assert.equal(h.last('patients').data.snapshot.token, 'synthetic-root-renewed');
});

for (const [status, code, accessLost] of [
    [401, null, true],
    [403, null, true],
    [404, null, false],
    [409, 'ACCESS_CONTEXT_CHANGED', true],
    [429, null, false],
    [503, 'QUALITY_NOT_AVAILABLE', false],
]) {
    test('U03 no benign restart after ' + status + ' ' + (code || 'refusal'), { timeout: 3000 }, async () => {
        const h = harness();
        h.reader.reset({});
        const reading = h.reader.request('patients', primary);
        const { reject } = await h.readAt(0);
        reject(refusal(status, code));
        assert.equal(await reading, false);
        assert.equal(h.reads.length, 1);
        assert.equal(h.events.some(event => event.state === 'ready'), false);
        assert.equal(h.accessLosses.length, Number(accessLost));
    });
}

test('U01 U08 mismatched envelope, snapshot and selectors clear every dependent collection', { timeout: 3000 }, async () => {
    const facet = { view: 'facets', facet: 'doctors' };
    const contradictions = [
        body => { body.mode = 'legacy'; },
        body => { body.view = 'groups'; },
        body => { body.snapshot.token = 'foreign-token'; },
        body => { body.snapshot.revision = 'c'.repeat(64); },
        body => { body.snapshot.query_hash = 'd'.repeat(64); },
        body => { body.selection.facet = 'departments'; },
        body => { body.query.active_filters.push('severity'); },
        body => { delete body.scope_summary; },
        body => { body.totals.patients = '0'; },
        body => { body.collection.page.has_next = true; },
    ];
    for (const change of contradictions) {
        const h = harness();
        await openRoot(h);
        const before = h.events.length;
        const reading = h.reader.request('doctors', facet);
        const { args, resolve } = await h.readAt(1);
        const body = response(args);
        change(body);
        resolve(body);
        assert.equal(await reading, false);
        assert.deepEqual(h.events.slice(before).map(event => event.state), ['loading', 'reset', 'error']);
        assert.equal(h.last('patients').data ?? null, null);
        assert.equal(h.reads.length, 2);
        assert.equal(await h.reader.request('doctors', facet), false, 'a fresh root is required');
    }
});

test('U08 card root rejects foreign owner and finding identifiers', { timeout: 3000 }, async () => {
    const h = harness();
    const owner = 'owner-first';
    const selector = { view: 'patient_issues', owner_patient_id: owner };
    h.reader.reset({}, owner);
    const wrongOwner = h.reader.request('issues', selector);
    const first = await h.readAt(0);
    first.resolve(response(first.args, { patient: patient('foreign-patient') }));
    assert.equal(await wrongOwner, false);
    assert.equal(h.last('issues').state, 'error');

    const wrongFinding = h.reader.request('issues', selector);
    const second = await h.readAt(1);
    second.resolve(response(second.args, {
        collection: collection([{
            finding_key: 'e'.repeat(64), patient_id: 'foreign-patient',
            code: 'missing_diagnosis', category: 'completeness', severity: 'warning',
            title: 'Синтетическая проверка', description: '', field: 'diagnosis', details: {},
            suggested_action: { label: 'Проверить', tab: 'data', can_write: false },
        }]),
    }));
    assert.equal(await wrongFinding, false);
    assert.equal(h.last('issues').state, 'error');
    assert.equal(h.events.some(event => event.state === 'ready'), false);
});

test('U08 the card reader refuses a selector for a different owner before HTTP', { timeout: 3000 }, async () => {
    const h = harness();
    h.reader.reset({}, 'owner-first');
    const reading = h.reader.request('issues', { view: 'patient_issues', owner_patient_id: 'owner-second' });
    assert.equal(h.reads.length, 0);
    assert.equal(await reading, false);
    assert.equal(h.events.some(event => event.state === 'ready'), false);
});

test('U07 a neutral owner 404 invalidates previously admitted Quality collections', { timeout: 3000 }, async () => {
    const h = harness();
    const owner = 'owner-first';
    const issuesSelector = { view: 'patient_issues', owner_patient_id: owner };
    const groupsSelector = { view: 'groups', owner_patient_id: owner };
    h.reader.reset({}, owner);
    const issues = h.reader.request('issues', issuesSelector);
    const initial = await h.readAt(0);
    initial.resolve(response(initial.args));
    assert.equal(await issues, true);
    const groups = h.reader.request('groups', groupsSelector);
    const initialGroups = await h.readAt(1);
    initialGroups.resolve(response(initialGroups.args));
    assert.equal(await groups, true);

    const late = h.reader.request('groups', groupsSelector);
    const lateGroup = await h.readAt(2);
    const before = h.events.length;
    const unavailable = h.reader.request('issues', issuesSelector);
    const lostOwner = await h.readAt(3);
    lostOwner.reject(refusal(404));
    assert.equal(await unavailable, false);
    assert.deepEqual(h.events.slice(before).map(event => event.state), ['loading', 'reset', 'error']);
    assert.equal(h.last('issues').data ?? null, null);
    const accessLost = h.events.length;
    lateGroup.resolve(response(lateGroup.args));
    assert.equal(await late, false);
    assert.equal(h.events.length, accessLost);
    assert.equal(h.reads.length, 4);
});

test('F09 both search values are header-only and checked before transport', () => {
    const endpoint = '/api/data-quality/issues';
    const query = { search: 'Синтетическая карточка А', sort: 'issues', doctor_user_id: 'doctor-first' };
    const selector = { view: 'facets', facet: 'departments', facet_search: 'Синтетическое отделение' };
    const [path, options] = requestPath(endpoint, { query, selector, snapshot: 'opaque-root', cursor: 'opaque-next' });
    const parsed = new URL(path, 'https://fixture.invalid');
    assert.equal(parsed.pathname, endpoint);
    assert.deepEqual(Object.fromEntries(parsed.searchParams), {
        mode: 'quality-v1',
        limit: '50',
        view: 'facets',
        sort: 'issues',
        doctor_user_id: 'doctor-first',
        facet: 'departments',
        snapshot: 'opaque-root',
        cursor: 'opaque-next',
    });
    assert.deepEqual(options.headers, {
        'X-Search': encodeURIComponent(query.search),
        'X-Quality-Facet-Search': encodeURIComponent(selector.facet_search),
    });
    for (const invalid of ['А'.repeat(120), 'часть\nстроки']) {
        assert.throws(() => requestPath(endpoint, { query: { ...query, search: invalid }, selector }),
            error => error.status === 422 && !error.message.includes(invalid));
    }
});
