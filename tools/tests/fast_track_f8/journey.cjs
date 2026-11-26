'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {createHash} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);

const fixture = JSON.parse(fs.readFileSync(process.env.F8_FIXTURE, 'utf8'));
const {origin, patients, users} = fixture;
assert(fixture.syntheticOnly);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const issues = '/api/data-quality/issues';
const summary = '/api/data-quality/summary';
const dashboard = '/api/analytics/dashboard';
const events = '/api/patients/workspace/events';
const checklists = '/api/patients/workspace/checklists';
const checks = [];
const responseProofs = [];
const widthChecks = [];
const zoomChecks = [];
const writes = [];
let pageErrors = 0;
let legacyRequests = 0;
let phase = 'startup';
let ordinaryReads = Promise.resolve();

function response(page, route, matches = () => true, method = 'GET') {
    return page.waitForResponse(res => {
        const url = new URL(res.url());
        return url.pathname === route && res.request().method() === method && matches(url.searchParams, res);
    });
}

async function audited(res, kind) {
    assert.equal(res.status(), 200, kind);
    const bytes = await res.body();
    responseProofs.push({kind, sha256:createHash('sha256').update(bytes).digest('hex')});
    return JSON.parse(bytes);
}

async function keyboard(control) {
    await control.and(control.page().locator(':not(:disabled)')).waitFor();
    await control.focus();
    assert(await control.evaluate(node => node === document.activeElement && !node.disabled));
    await control.press('Enter');
}

async function view(page, key) {
    await keyboard(page.locator('[data-view="' + key + '"]').first());
    await page.locator('#view-' + key).waitFor();
}

async function session(browser, role) {
    const context = await browser.newContext({reducedMotion:'reduce',viewport:{width:1440,height:1000}});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    // Ordinary (non-race) steps forward each loopback request once. The explicit
    // supersession test below diverts only its designated held response.
    await page.route(origin + '/api/**',route=>{
        const request = ordinaryReads.then(async () => route.fulfill({response:await route.fetch()}));
        ordinaryReads = request.catch(()=>{});
        return request;
    });
    page.on('pageerror', () => { pageErrors++; });
    page.on('request', req => {
        const url = new URL(req.url());
        if (url.pathname === '/api/patients' && req.method() === 'GET' && url.searchParams.get('view') !== 'page') legacyRequests++;
        if (url.pathname.startsWith('/api/patients') && !['GET','HEAD','OPTIONS'].includes(req.method())) {
            writes.push({role, method:req.method(), path:url.pathname, body:req.postDataJSON()});
        }
        assert(!(url.pathname.startsWith('/api/data-quality') && url.searchParams.has('mode')),
            'ordinary application must not activate the experimental Quality API');
    });
    await page.goto(origin);
    await page.locator('#username').fill(users[role]);
    await page.locator('#password').fill(fixture.password);
    await keyboard(page.locator('#loginSubmit'));
    await page.waitForFunction(() => app.appReady && !app.sessionExpired && app.patientListState === 'ready');
    return {page,context};
}

function cardQuality(pid) {
    return '/api/data-quality/patients/' + pid;
}

function rows(page, patient) {
    return page.locator('#qualityBody tr').filter({hasText:patient.name});
}

function finding(page, patient, code) {
    return rows(page, patient).locator('[data-quality-action][data-quality-code="' + code + '"]');
}

function eventRow(page, patient) {
    return page.locator('#f1MyDay [data-f1-doctor-row=events]').filter({hasText:patient.name});
}

function checklistRow(page, patient) {
    return page.locator('#f1MyDay [data-f1-doctor-row=checklists]').filter({hasText:patient.name});
}

async function codeFilter(page, code) {
    const [selected] = await Promise.all([response(page,issues,params => (params.get('code') || '') === code),
        page.locator('#qualityCodeFilter').selectOption(code)]);
    return audited(selected,'normal public Quality filter');
}

async function openQuality(page) {
    const read = response(page,issues);
    if (await page.locator('#view-quality').isVisible()) await keyboard(page.locator('#qualityRefreshBtn'));
    else await view(page,'quality');
    return audited(await read,'ordinary public Quality console');
}

async function fromMyDay(page, mode, route) {
    const results = response(page,route,params => params.get('limit') === '20');
    const root = page.locator('#f1MyDay [data-f1-doctor-mode="' + mode + '"]');
    await keyboard(root);
    return audited(await results,'My Day ' + mode);
}

async function managerMetrics(manager, first) {
    const page = manager.page;
    const pulse = response(page,'/api/analytics/summary');
    await view(page,'pulse');
    const current = await audited(await pulse,'management Pulse');
    const analytic = response(page,dashboard);
    await view(page,'analytics');
    const aggregate = await audited(await analytic,'management analytics');
    const value = payload => payload.cards.find(card => card.code === 'overdue_events').value;
    assert.equal(value(current), value(aggregate.summary));
    if (first !== undefined) assert.equal(value(current),first - 1);
    return value(current);
}

async function exactStableMyDayLink(page) {
    phase = 'My Day stable event identification';
    await view(page,'myDay');
    const available = await fromMyDay(page,'events',events);
    const target = patients.repeated;
    const eventId = target.eventIds[0];
    assert(available.events.some(item => item.id === eventId));
    assert(available.events.every(item => item.patientId !== patients.foreign.id));
    const permission = response(page,'/api/patients/' + target.id);
    await keyboard(eventRow(page,target).getByRole('button',{name:/^К событию/}));
    const card = await audited(await permission,'My Day exact event object approval');
    await page.waitForFunction(id => document.activeElement?.id === 'patient-event-' + id,eventId);
    assert(card.history[0].events.some(item => item.id === eventId));
    assert.notEqual(card.history[0].id,card.history.at(-1).id);
    const back = response(page,events,params => params.get('limit') === '20');
    await keyboard(page.locator('#backToF1'));
    const fresh = await audited(await back,'fresh My Day after viewing repeated stage');
    assert(fresh.events.some(item => item.id === eventId));
    assert.equal(writes.length,0);
    checks.push('My Day uses the exact stable event in an earlier occurrence of the same stage and navigation performs no write');
}

async function qualityEvent(page) {
    phase = 'filtered Quality to manual event selection';
    await openQuality(page);
    const patient = patients.overdue;
    const id = patient.eventIds[0];
    const search = response(page,issues,(_,res)=>res.request().headers()['x-search']===encodeURIComponent(patient.name));
    await page.locator('#qualitySearchInput').fill(patient.name);
    const searched = await search;
    assert(!new URL(searched.url()).searchParams.has('q'));
    assert((await audited(searched,'authorized search via private header')).patients.every(item=>item.patient_id===patient.id));
    const filtered = await codeFilter(page,'overdue_event');
    const selected = filtered.issues.filter(issue => issue.patient_id === patient.id && issue.code === 'overdue_event');
    assert.equal(selected.length,2);
    assert(!filtered.issues.some(issue => issue.patient_id === patients.foreign.id));
    assert(selected.every(issue => !issue.details.event_id && !issue.details.history_entry_id));
    const permission = response(page,'/api/patients/' + patient.id);
    await keyboard(finding(page,patient,'overdue_event').first());
    const card = await audited(await permission,'Quality target authorized object read');
    await page.waitForFunction(() => app.currentView === 'patient' && app.currentTab === 'events');
    const target = page.locator('#patient-event-' + id);
    await target.waitFor();
    assert.equal(await page.evaluate(() => document.activeElement?.id?.startsWith('patient-event-')),false,
        'public legacy finding cannot guess a specific item from date or display position');
    const item = card.history[0].events.find(event => event.id === id);
    assert(item && !item.actualDate);
    await target.getByRole('textbox',{name:'Фактическая дата',exact:true}).evaluate((input,value) => {
        input.value = value;
        input.dispatchEvent(new Event('input',{bubbles:true}));
    },fixture.today);
    assert.equal(writes.length,0);
    const patch = '/api/patients/' + patient.id + '/events/' + id;
    const [saved] = await Promise.all([response(page,patch,()=>true,'PATCH'),
        keyboard(target.getByRole('button',{name:'Сохранить: Фактическая дата',exact:true}))]);
    assert.equal(saved.status(),200);
    const confirmed = (await saved.json()).history[0].events.find(event => event.id === id);
    assert.equal(confirmed.version,item.version + 1);
    assert.equal(confirmed.actualDate,fixture.today);
    assert.deepEqual(writes[0],{role:'doctor',method:'PATCH',path:patch,
        body:{field:'actualDate',value:fixture.today,version:item.version}});
    await page.waitForFunction(() => !app.clinicalWriteInFlight);
    const refresh = response(page,issues,params => params.get('code') === 'overdue_event');
    await keyboard(page.locator('#backToF1'));
    const current = await audited(await refresh,'fresh Quality after versioned event update');
    const remaining = current.issues.filter(issue => issue.patient_id === patient.id && issue.code === 'overdue_event');
    assert.equal(remaining.length,1);
    assert.notEqual(remaining[0].details.planned_date, item.date);
    assert.equal(await page.locator('#qualityCodeFilter').inputValue(),'overdue_event');
    assert.equal(await page.locator('#qualitySearchInput').inputValue(),patient.name);
    await finding(page,patient,'overdue_event').first().waitFor();
    const [rewind] = await Promise.all([response(page,'/api/patients/' + patient.id),page.goBack()]);
    const rechecked = await audited(rewind,'Back opens freshly authorized patient');
    assert.equal(rechecked.history[0].events.find(event => event.id === id).actualDate,fixture.today);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id &&
        browserNavigation.entry(history.state) === browserNavigation.committed,patient.id);
    const [forward] = await Promise.all([response(page,issues,params => params.get('code') === 'overdue_event'),page.goForward()]);
    const presented = await audited(forward,'Forward fetches current findings');
    assert.equal(presented.issues.filter(issue => issue.patient_id === patient.id && issue.code === 'overdue_event').length,1);
    assert.equal(writes.length,1);
    const clearSearch = response(page,issues,(_,res)=>!res.request().headers()['x-search']);
    await page.locator('#qualitySearchInput').fill('');
    await audited(await clearSearch,'restore unsearched public Quality');
    checks.push('Quality private-header search and public filters and neutral event-tab fallback, one authorized versioned save, refreshed Quality 2→1 retains filter; Back/Forward revalidate without replay');
    return id;
}

async function checklistAndWorklists(page, eventId) {
    phase = 'fresh calendar and My Day then checklist';
    await view(page,'calendar');
    await page.locator('#eventsWorkspaceQuery').fill(patients.overdue.name);
    const [newSchedule] = await Promise.all([response(page,events,params => params.get('q') === patients.overdue.name),
        keyboard(page.locator('#eventsWorkspaceSearch button[type=submit]'))]);
    const schedule = await audited(newSchedule,'fresh scoped schedule after fact');
    assert(!schedule.events.some(item => item.id === eventId));
    assert(schedule.events.some(item => item.id === patients.overdue.eventIds[1]));

    const work = response(page,events,params => params.get('limit') === '20');
    await view(page,'myDay');
    const day = page.locator('#f1MyDay');
    const eventControl = day.locator('[data-f1-doctor-mode=events]');
    if (await eventControl.getAttribute('aria-pressed') !== 'true') await keyboard(eventControl);
    const queue = await audited(await work,'My Day after fact');
    assert(!queue.events.some(item => item.id === eventId));
    assert(queue.events.some(item => item.id === patients.overdue.eventIds[1]));

    const patient = patients.checklist;
    const before = await fromMyDay(page,'checklists',checklists);
    assert.equal(before.checklists.find(row => row.patient_id === patient.id).incomplete_required,1);
    const open = response(page,'/api/patients/' + patient.id);
    await keyboard(checklistRow(page,patient).getByRole('button',{name:/^К чек-листу/}));
    await audited(await open,'fresh checklist owner authorization');
    const item = page.locator('#patientPanels').getByRole('checkbox',{name:fixture.item,exact:false});
    assert.equal(await item.isChecked(),false);
    const route = '/api/patients/' + patient.id + '/history/0/checklist';
    const afterWrite = response(page,'/api/patients/' + patient.id);
    const [patched] = await Promise.all([response(page,route,()=>true,'PATCH'),item.press('Space')]);
    assert.equal(patched.status(),200);
    assert.equal((await patched.json()).history[0].checklist[fixture.item],true);
    assert.deepEqual(writes[1],{role:'doctor',method:'PATCH',path:route,
        body:{item:fixture.item,checked:true,expected:false}});
    const freshCard = await audited(await afterWrite,'fresh checklist card after successful PATCH');
    assert.equal(freshCard.history[0].checklist[fixture.item],true);
    await page.waitForFunction(() => !app.clinicalWriteInFlight && !app.checklistPending);
    const refreshed = response(page,checklists,params => params.get('limit') === '20');
    await keyboard(page.locator('#backToF1'));
    const after = await audited(await refreshed,'updated current My Day checklist');
    assert.equal(after.checklists.find(row => row.patient_id === patient.id).incomplete_required,0);
    await checklistRow(page,patient).getByRole('button',{name:/^Посмотреть чек-лист/}).waitFor();
    const owner = await page.request.get(origin + cardQuality(patient.id));
    assert.equal(owner.status(),200);
    assert(!(await owner.json()).issues.some(issue => issue.code === 'incomplete_required_checklist'));
    checks.push('recorded event leaves real Calendar/My Day queues; explicit conditional final checkbox completes required checklist and refreshed Quality no longer returns it');
}

async function safeDuplicates(page, context) {
    phase = 'read-only duplicate display within current scope';
    await openQuality(page);
    const duplicates = await codeFilter(page,'possible_duplicate');
    const pair = duplicates.duplicate_groups.find(group => group.patient_ids.includes(patients.duplicateA.id));
    assert(pair);
    assert.deepEqual(new Set(pair.patient_ids),new Set([patients.duplicateA.id,patients.duplicateB.id]));
    assert(!JSON.stringify(duplicates).includes(patients.duplicateForeign.name));
    const panel = page.locator('#qualityDuplicatesPanel');
    const client = panel.getByRole('button',{name:new RegExp(patients.duplicateA.name)});
    await client.waitFor();
    assert(!(await panel.innerText()).includes(patients.duplicateForeign.name));
    assert.equal(await panel.getByRole('button',{name:/объединить|удалить|назначить главной/i}).count(),0);
    const before = writes.length;
    const owner = response(page,'/api/patients/' + patients.duplicateA.id);
    await keyboard(client);
    assert.equal((await audited(await owner,'duplicate matched patient object')).id,patients.duplicateA.id);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id,patients.duplicateA.id);
    const rendering = response(page,issues,params => params.get('code') === 'possible_duplicate');
    await keyboard(page.locator('#backToF1'));
    assert((await audited(await rendering,'fresh duplicate scope after open')).duplicate_groups
        .some(group => group.patient_ids.includes(patients.duplicateA.id)));
    assert.equal(writes.length,before);
    const forbidden = await context.request.get(origin + '/api/audit');
    assert.equal(forbidden.status(),403);
    const hidden = await context.request.get(origin + cardQuality(patients.duplicateForeign.id));
    assert.equal(hidden.status(),404);
    assert(!(await hidden.text()).includes(patients.duplicateForeign.name));
    checks.push('duplicate matches remain advisory: same-policy third patient outside scope hidden, fresh allowed navigation, no decisions, real 403 and neutral 404');
}

async function fieldCorrection(page) {
    phase = 'Quality finding to versioned standard Data field';
    const patient = patients.correction;
    await openQuality(page);
    const before = await codeFilter(page,'missing_required_field');
    assert(before.issues.some(issue=>issue.patient_id===patient.id && issue.field==='phone'));
    const owner = response(page,'/api/patients/' + patient.id);
    await keyboard(finding(page,patient,'missing_required_field').first());
    const original = await audited(await owner,'fresh missing-field owner approval');
    await page.waitForFunction(()=>app.currentView==='patient' && app.currentTab==='data');
    const field = page.locator('#patientPanels').getByRole('textbox',{name:'Телефон',exact:true});
    assert.equal(await field.inputValue(),'');
    await field.fill(fixture.correctedPhone);
    assert.equal(writes.length,2);
    const route = '/api/patients/' + patient.id;
    const [saved] = await Promise.all([response(page,route,()=>true,'PATCH'),field.press('Tab')]);
    assert.equal(saved.status(),200);
    const confirmed = await saved.json();
    assert.equal(confirmed.version,original.version + 1);
    assert.equal(confirmed.phone,fixture.correctedPhone);
    assert.deepEqual(writes[2],{role:'doctor',method:'PATCH',path:route,
        body:{field:'phone',value:fixture.correctedPhone,expectedVersion:original.version}});
    await page.waitForFunction(({id,phone})=>app.getPatient(id)?.phone===phone && !app.clinicalWriteInFlight,
        {id:patient.id,phone:fixture.correctedPhone});
    const updated = response(page,issues,params=>params.get('code')==='missing_required_field');
    await keyboard(page.locator('#backToF1'));
    assert(!(await audited(await updated,'fresh Quality after standard field correction')).issues
        .some(issue=>issue.patient_id===patient.id && issue.field==='phone'));
    checks.push('ordinary Data control saves one phone PATCH with expected version; mandatory audit omits value; fresh finding is gone');
}

async function reassignedScope(page,browser) {
    phase = 'stale Quality finding after real administrator reassignment';
    await codeFilter(page,'overdue_event');
    const patient = patients.revoked, route = '/api/patients/' + patient.id;
    const action = finding(page,patient,'overdue_event').first();
    await action.waitFor();
    const admin = await browser.newContext();
    try {
        assert.equal((await admin.request.post(origin+'/api/auth/login',{
            data:{username:users.admin,password:fixture.password},
        })).status(),200);
        const record = await admin.request.get(origin+route);
        assert.equal(record.status(),200);
        const current = await record.json();
        const csrf = (await admin.cookies()).find(cookie=>cookie.name==='clinic_csrf');
        const changed = await admin.request.put(origin+route+'/treating-doctor',{
            headers:{'X-CSRF-Token':csrf.value},
            data:{userId:fixture.accountIds.foreign,expectedVersion:current.version},
        });
        assert.equal(changed.status(),200);
        assert.equal((await changed.json()).treatingDoctorUserId,fixture.accountIds.foreign);
    } finally {
        await admin.close();
    }
    const denial = response(page,route);
    const fresh = response(page,issues,params=>params.get('code')==='overdue_event');
    await keyboard(action);
    const refused = await denial;
    assert.equal(refused.status(),404);
    assert(!(await refused.text()).includes(patient.name));
    assert(!(await audited(await fresh,'fresh Quality after actual scope withdrawal')).issues
        .some(issue=>issue.patient_id===patient.id));
    await action.waitFor({state:'detached'});
    assert(!await page.locator('#view-patient').isVisible());
    assert.equal(writes.length,3);
    checks.push('actual reassignment returns neutral 404 for formerly actionable row, removes it through a fresh Quality read, and leaves no unauthorized write');
}

async function delayedQuality(page) {
    phase = 'stale public Quality response superseded';
    let signal;
    let release;
    let completed;
    const started = new Promise(resolve => { signal = resolve; });
    const held = new Promise(resolve => { release = resolve; });
    const delivered = new Promise(resolve => { completed = resolve; });
    const trap = async route => {
        if (new URL(route.request().url()).searchParams.get('code') !== 'overdue_event') return route.fallback();
        const real = await route.fetch();
        signal(real.status());
        await held;
        await route.fulfill({response:real});
        completed();
    };
    await page.route(origin + issues + '?*',trap);
    const old = response(page,issues,params => params.get('code') === 'overdue_event');
    try {
        await page.locator('#qualityCodeFilter').selectOption('overdue_event');
        assert.equal(await Promise.race([started,old.then(() => null)]),200);
        const fresh = await codeFilter(page,'missing_policy_number');
        assert(fresh.issues.every(issue=>issue.code==='missing_policy_number'));
        assert.equal(await page.locator('#qualityCodeFilter').inputValue(),'missing_policy_number');
        await finding(page,patients.duplicateA,'missing_policy_number').waitFor({state:'hidden'});
        // Adopt the current request before the older body is released.
        await page.waitForFunction(() => app.quality.filters.code === 'missing_policy_number' &&
            app.quality.issues.every(issue => issue.code === 'missing_policy_number'));
    } finally {
        release();
    }
    assert.equal((await old).status(),200);
    await delivered;
    await page.unroute(origin + issues + '?*',trap);
    assert.equal(await page.locator('#qualityBody [data-quality-code=overdue_event]').count(),0);
    assert.equal(await page.locator('#qualityCodeFilter').inputValue(),'missing_policy_number');
    assert.equal(writes.length,3);
    checks.push('held real older Quality response delivers 200 after newer filter without restoring old actionable rows');
}

async function qualityVisual(page) {
    phase = 'public Quality both themes and responsive keyboard';
    await codeFilter(page,'overdue_event');
    await finding(page,patients.overdue,'overdue_event').first().waitFor();
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
    const title = page.locator('#view-quality h1').first();
    for (const theme of ['light','dark']) {
        if ((await page.locator('html').getAttribute('data-theme')) !== theme) await page.locator('#themeToggle').click();
        assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
        for (const width of [320,375,768,1024,1440]) {
            await page.setViewportSize({width,height:1000});
            const shape = await title.evaluate(node => {
                window.scrollTo(0,0);
                const position = node.getBoundingClientRect();
                const hit = document.elementFromPoint(position.x + 12,position.y + position.height / 2);
                return {width:innerWidth,overflow:document.documentElement.scrollWidth > innerWidth,
                    visible:hit === node || node.contains(hit)};
            });
            assert.deepEqual(shape,{width,overflow:false,visible:true});
            const violations = await page.evaluate(async () => (await window.axe.run(document,{
                runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},
            })).violations.map(rule=>({id:rule.id,targets:rule.nodes.map(node=>node.target)})));
            assert.deepEqual(violations,[],theme + ' ' + width);
            widthChecks.push({theme,width,violations:0,titleVisible:true});
            if (theme==='light' && width===1440 || theme==='dark' && width===375) {
                await page.screenshot({path:path.join(process.env.OUT,'f8-quality-'+theme+'-'+width+'.png'),animations:'disabled'});
            }
        }
    }
    checks.push('full-document WCAG A/AA axe on five widths and both themes with visible title and no page overflow');
}

async function liveRevocation(page,context) {
    phase = 'real session expiry and protected Quality clearance';
    const csrf = (await context.cookies()).find(item=>item.name==='clinic_csrf');
    const logout = await context.request.post(origin + '/api/auth/logout',{headers:{'X-CSRF-Token':csrf.value}});
    assert.equal(logout.status(),200);
    const denied = response(page,summary);
    await keyboard(page.locator('#qualityRefreshBtn'));
    assert.equal((await denied).status(),401);
    await page.locator('#loginScreen').waitFor();
    assert.equal(await page.locator('#qualityBody [data-quality-action]').count(),0);
    assert.equal(await page.locator('#qualityDuplicatesPanel button').count(),0);
    assert.equal(writes.length,3);
    checks.push('actual session revocation returns 401 and destroys former Quality actions without a medical write');
}

async function nativeZoom() {
    phase = 'native Chromium zoom on public Quality';
    const temp = fs.mkdtempSync(path.join(os.tmpdir(),'clinic-f8-zoom-'));
    const extension = path.join(temp,'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify({
        manifest_version:3,name:'Synthetic localhost zoom',version:'1.0',permissions:['tabs'],
        background:{service_worker:'worker.js'},
    }));
    fs.writeFileSync(path.join(extension,'worker.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
    let context;
    try {
        context = await chromium.launchPersistentContext(path.join(temp,'profile'),{
            headless:true,viewport:null,reducedMotion:'reduce',ignoreDefaultArgs:['--disable-extensions'],
            executablePath:process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            args:['--window-size=1280,1000','--disable-background-networking',
                '--disable-extensions-except='+extension,'--load-extension='+extension],
        });
        await context.route('**/*', route=>new URL(route.request().url()).origin===origin
            ? route.continue() : route.abort('blockedbyclient'));
        const page = context.pages()[0] || await context.newPage();
        page.on('request',request=>{
            if (!['GET','HEAD','OPTIONS'].includes(request.method()) &&
                !new URL(request.url()).pathname.startsWith('/api/auth/')) throw new Error('unexpected synthetic zoom write');
        });
        await page.goto(origin);
        await page.locator('#username').fill(users.doctor);
        await page.locator('#password').fill(fixture.password);
        await keyboard(page.locator('#loginSubmit'));
        await page.waitForFunction(()=>app.appReady && app.patientListState==='ready');
        await openQuality(page);
        await codeFilter(page,'overdue_event');
        await finding(page,patients.overdue,'overdue_event').first().waitFor();
        const control = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker',{timeout:10000});
        const baseline = await page.evaluate(()=>devicePixelRatio);
        for (const mode of ['light','dark']) {
            if ((await page.locator('html').getAttribute('data-theme'))!==mode) await page.locator('#themeToggle').click();
            for (const factor of [1,2,4]) {
                const actual = await control.evaluate(async ({url,factor})=>{
                    const tab = (await chrome.tabs.query({})).find(item=>item.url===url);
                    if (!tab) throw new Error('synthetic tab missing');
                    await chrome.tabs.setZoom(tab.id,factor);
                    return chrome.tabs.getZoom(tab.id);
                },{url:page.url(),factor});
                assert.equal(Math.round(actual*100),factor*100);
                await page.waitForFunction(target=>Math.abs(devicePixelRatio-target)<0.01,baseline*factor);
                const shape = await page.locator('#view-quality h1').first().evaluate(node=>{
                    window.scrollTo(0,0);
                    const rect=node.getBoundingClientRect();
                    const hit=document.elementFromPoint(rect.x+12,rect.y+rect.height/2);
                    return {width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth,
                        visible:hit===node || node.contains(hit),scale:visualViewport.scale};
                });
                assert.equal(shape.overflow,false);
                assert.equal(shape.visible,true);
                assert.equal(shape.scale,1);
                zoomChecks.push({theme:mode,percent:factor*100,width:shape.width,noPageOverflow:true});
            }
        }
    } finally {
        await context?.close();
        fs.rmSync(temp,{recursive:true,force:true});
    }
    checks.push('actual Chromium tab zoom at 100, 200 and 400 percent in both themes using a transient profile');
}

async function main() {
    const browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,
        args:['--disable-background-networking']});
    try {
        const manager = await session(browser,'head');
        const totals = await managerMetrics(manager);
        const doctor = await session(browser,'doctor');
        await exactStableMyDayLink(doctor.page);
        const eventId = await qualityEvent(doctor.page);
        await managerMetrics(manager,totals);
        checks.push('department manager receives exact fresh Pulse and Analytics metric decrement from server');
        await manager.page.unrouteAll({behavior:'wait'});
        await manager.context.close();
        await checklistAndWorklists(doctor.page,eventId);
        await fieldCorrection(doctor.page);
        await safeDuplicates(doctor.page,doctor.context);
        await delayedQuality(doctor.page);
        await reassignedScope(doctor.page,browser);
        await qualityVisual(doctor.page);
        await liveRevocation(doctor.page,doctor.context);
        await doctor.page.unrouteAll({behavior:'wait'});
        await doctor.context.close();
        await nativeZoom();
        assert.equal(writes.length,3);
        assert.equal(legacyRequests,0);
        assert.equal(pageErrors,0);
        fs.writeFileSync(path.join(process.env.OUT,'journey.local.json'),JSON.stringify({
            suite:'journey',browser:browser.version(),checks,axe:widthChecks,nativeZoom:zoomChecks,
            measurements:{explicitMedicalWrites:writes.length,legacyRequests,pageErrors},
            transportFaults:['held genuine HTTP 200 between two filter reads; no mocked API result'],
            responseProofs,
        },null,2));
    } finally {
        for (const context of browser.contexts()) {
            for (const page of context.pages()) await page.unrouteAll({behavior:'wait'});
        }
        await browser.close();
    }
}

main().catch(error => {
    process.stderr.write(phase + '\n' + error.stack + '\n');
    process.exitCode = 1;
});
