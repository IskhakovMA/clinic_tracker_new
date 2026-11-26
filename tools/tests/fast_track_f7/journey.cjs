'use strict';

const assert = require('node:assert/strict');
const {createHash, randomUUID} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);
const {setup, login, password} = require('../wp_i_consilium/fixtures.cjs');

const origin = process.env.WP_H_ORIGIN;
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const patients = '/api/patients';
const appointments = '/api/consilium/appointments';
const schedule = '/api/consilium/schedule';
const dashboard = '/api/analytics/dashboard';
const checks = [];
const widths = [320, 375, 768, 1024, 1440];
const axe = [];
const readHashes = [];
const writes = [];
const transportFaults = [];
let phase = 'startup';
let pageErrors = 0;
let legacyReads = 0;

function response(page, method, pathname, filter = () => true) {
    return page.waitForResponse(r => {
        const url = new URL(r.url());
        return r.request().method() === method && url.pathname === pathname && filter(r, url);
    });
}

async function keyboard(control) {
    await control.and(control.page().locator(':not(:disabled)')).waitFor();
    await control.focus();
    assert(await control.evaluate(node => node === document.activeElement && !node.disabled));
    await control.press('Enter');
}

async function navigate(page, key) {
    await keyboard(page.locator('[data-view="' + key + '"]').first());
    await page.locator('#view-' + key).waitFor();
}

async function protectedRead(res) {
    assert.equal(res.status(), 200);
    const body = await res.body();
    readHashes.push(createHash('sha256').update(body).digest('hex'));
    return JSON.parse(body);
}

async function session(browser, f, role, viewport = {width:1440,height:1000}) {
    const context = await browser.newContext({reducedMotion: 'reduce', viewport});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort('blockedbyclient'));
    const page = await context.newPage();
    page.on('pageerror', () => { pageErrors++; });
    page.on('request', request => {
        const url = new URL(request.url());
        const method = request.method();
        if (url.pathname === patients && method === 'GET' && url.searchParams.get('view') !== 'page') legacyReads++;
        if ((url.pathname.startsWith(patients) || url.pathname.startsWith(appointments)) && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            writes.push({role, pathname: url.pathname, method, body: request.postData(), key: request.headers()['idempotency-key']});
        }
    });
    await login(page, origin, f.roles[role].username);
    return {context,page};
}

async function openForm(page) {
    await keyboard(page.locator('#openAddBtn'));
    await page.locator('#addModal').waitFor();
    assert.equal(await page.evaluate(() => document.activeElement.id), 'addModalTitle');
}

async function fill(page, doctor, name, day = '', policy = '') {
    await page.locator('#fio').fill(name);
    await page.locator('#age').fill('51');
    await page.locator('#phone').fill('+70000000123');
    await page.locator('#policyNumber').fill(policy);
    await page.locator('#diagnosisLocalization').selectOption('respiratory_thoracic');
    const diagnosis = page.locator('#diagnosisSearch');
    await diagnosis.fill('C34.1');
    await page.getByRole('option', {name: /C34.1/}).first().waitFor();
    await diagnosis.press('Enter');
    assert.equal(await page.locator('#diagnosis').inputValue(), 'C34.1');
    await page.locator('#stage').selectOption('exam');
    assert.equal(await page.locator('#doctor').isDisabled(), true);
    assert.equal(await page.locator('#doctor').inputValue(), doctor);
    await page.locator('#head').selectOption(await page.locator('#head option').evaluateAll(nodes => nodes.find(node => node.value)?.value));
    if (day) {
        await page.locator('#examDate').fill(day);
        await page.locator('#examTime').fill('13:15');
    }
}

async function responsive(page) {
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE, 'utf8'));
    for (const theme of ['light', 'dark']) {
        if ((await page.locator('html').getAttribute('data-theme')) !== theme) await page.locator('#themeToggle').click();
        assert.equal(await page.locator('html').getAttribute('data-theme'), theme);
        for (const width of widths) {
            await page.setViewportSize({width, height:1000});
            const geometry = await page.locator('#addModalTitle').evaluate(title => {
                window.scrollTo(0, 0);
                const box = title.getBoundingClientRect();
                const hit = document.elementFromPoint(box.left + 12, box.top + box.height / 2);
                return {width:innerWidth, overflow:document.documentElement.scrollWidth > innerWidth,
                    titleVisible:hit === title || title.contains(hit)};
            });
            assert.deepEqual(geometry, {width, overflow:false, titleVisible:true});
            const violations = await page.evaluate(async () => (await window.axe.run(document, {
                runOnly: {type:'tag', values:['wcag2a','wcag2aa','wcag21a','wcag21aa']},
            })).violations.map(rule => ({id:rule.id, targets:rule.nodes.map(node => node.target)})));
            assert.deepEqual(violations, [], theme + ' ' + width);
            axe.push({theme,width,violations:0,titleVisible:true});
            if (theme === 'light' && width === 1440 || theme === 'dark' && width === 375) {
                await page.screenshot({path:path.join(process.env.OUT, `f7-intake-${theme}-${width}.png`),animations:'disabled'});
            }
        }
    }
}

async function freshCard(page, id) {
    const res = await page.waitForResponse(r => new URL(r.url()).pathname === patients + '/' + id &&
        r.request().method() === 'GET');
    const card = await protectedRead(res);
    assert.equal(card.id, id);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id, id);
    return card;
}

async function post(page, pathname, request, status) {
    const [result] = await Promise.all([response(page, 'POST', pathname), keyboard(request)]);
    assert.equal(result.status(), status);
    return result;
}

async function exactCreate(page, name) {
    const loaded = page.waitForResponse(r => r.request().method() === 'GET' &&
        /^\/api\/patients\/[a-f0-9-]+$/i.test(new URL(r.url()).pathname));
    const created = await post(page, patients, page.locator('#submitAddBtn'), 201);
    const card = await created.json();
    assert(card.id && card.name === name);
    const confirmed = await protectedRead(await loaded);
    assert.equal(confirmed.id, card.id);
    assert.equal(confirmed.name, name);
    await page.waitForFunction(id => app.currentView === 'patient' && app.currentPatientId === id, card.id);
    return confirmed;
}

async function registry(page, card) {
    await page.locator('#view-patients').waitFor();
    const query = page.locator('#searchInput');
    const searched = response(page, 'GET', patients, (_, url) =>
        url.searchParams.get('view') === 'page' && url.searchParams.get('q') === card.name);
    await query.fill(card.name);
    const result = await protectedRead(await searched);
    assert.equal(result.page.total, 1);
    assert.deepEqual(result.patients.map(p => p.id), [card.id]);
    const item = page.getByRole('button', {name:'Открыть карточку: ' + card.name, exact:true});
    const ready = freshCard(page, card.id);
    await keyboard(item);
    return ready;
}

async function book(page, card, f) {
    phase = 'card to real bookable consilium slot';
    await keyboard(page.locator('#tab-overview'));
    const [freshSchedule] = await Promise.all([response(page, 'GET', schedule),
        keyboard(page.locator('#bookPatientConsiliumBtn'))]);
    const availability = await protectedRead(freshSchedule);
    assert(availability.slots.some(slot => slot.scheduledDate === f.day && slot.scheduledTime === '11:00' &&
        slot.consiliumGroup === 'thoracic' && slot.bookable && !slot.occupied));
    assert(availability.slots.some(slot => slot.scheduledDate === f.day && slot.scheduledTime === '10:30' &&
        slot.consiliumGroup === 'thoracic' && slot.occupied));
    const form = page.locator('#consiliumModal');
    await form.waitFor();
    assert.equal(await page.locator('#consiliumPatient').inputValue(), card.id);
    assert.equal(await page.locator('#consiliumPatient').isDisabled(), true);
    assert.equal(await page.locator('#consiliumGroup').getAttribute('readonly'), '');
    await page.locator('#consiliumDate').fill(f.day);
    await page.locator('#consiliumTime').selectOption('11:00');
    await page.locator('#consiliumComment').fill('Синтетическая запись из карточки F7');
    assert.equal(writes.filter(write => write.pathname === appointments).length,0);
    const committed = await post(page,appointments,page.locator('#submitConsiliumBtn'),201);
    const record = await committed.json();
    assert.equal(record.patientId,card.id);
    assert.equal(record.scheduledDate,f.day);
    assert.equal(record.scheduledTime,'11:00');
    assert.equal(record.consiliumGroup,'thoracic');
    assert.equal(record.status,'recorded');
    await form.waitFor({state:'hidden'});
    await page.locator('#patientConsiliumRecords').getByRole('button').filter({hasText:'11:00'}).waitFor();
    assert(await page.locator('#view-patient').isVisible());
    assert.equal(await page.locator('#crumbPatientName').textContent(),card.name);
    assert.equal(writes.filter(write => write.pathname === appointments).length,1);
    checks.push('pinned created patient, saved scoped slot and one explicit appointment POST; fresh same-card return without decision or stage change');
    return record;
}

async function denyAndVerifyChair(browser, f, card, appointment) {
    phase = 'real read-only and foreign-scope refusal, chair confirms booking';
    const readonly = await session(browser,f,'deputy_chief_oncology');
    assert(!await readonly.page.locator('#openAddBtn').isVisible());
    const csrf = (await readonly.context.cookies()).find(cookie=>cookie.name==='clinic_csrf');
    const rejected = await readonly.context.request.post(origin+patients,{
        headers:{'Idempotency-Key':randomUUID(),'X-CSRF-Token':csrf.value},
        data:{...JSON.parse(writes[0].body),name:'Синтетический запрещенный F7'},
    });
    assert.equal(rejected.status(),403);
    await readonly.context.close();

    const foreign = await browser.newContext();
    await f.call(foreign,'POST','/api/auth/login',{username:'i5a.outside',password});
    const hidden = await f.request(foreign,'GET',patients+'/'+card.id);
    assert.equal(hidden.status(),404);
    assert(!(await hidden.text()).includes(card.name));
    await foreign.close();

    const chair = await session(browser,f,'consilium_chair');
    assert(!await chair.page.locator('#openAddBtn').isVisible());
    const present = response(chair.page,'GET',appointments);
    await navigate(chair.page,'consilium');
    const table = await protectedRead(await present);
    assert(table.appointments.some(item => item.id === appointment.id && item.status === 'recorded'));
    await chair.page.locator('#consiliumDateFilter').fill(f.day);
    await chair.page.locator('#consilium-grid-row-' + appointment.id).waitFor();
    await chair.context.close();
    checks.push('genuine 403 for read-only creation, neutral 404 for a foreign doctor, real chair read finds the recorded booking');
}

async function replayAndDuplicate(page, doctor, f, names, policy) {
    phase = 'lost create reply with explicit exact replay';
    await navigate(page,'patients');
    await openForm(page);
    await fill(page,f.roles.doctor.id,names[1],'',policy);
    const prior = writes.length;
    let lost;
    await page.route(origin+patients,async route => {
        assert.equal(route.request().method(),'POST');
        const committed = await route.fetch();
        assert.equal(committed.status(),201);
        assert.equal(committed.headers()['idempotent-replayed'],undefined);
        lost = await committed.json();
        await route.abort('connectionreset');
    }, {times:1});
    await keyboard(page.locator('#submitAddBtn'));
    await page.getByRole('button',{name:'Проверить результат',exact:true}).waitFor();
    assert(lost && lost.name === names[1]);
    assert.equal(writes.length - prior,1);
    assert.equal(await page.locator('#fio').inputValue(),names[1]);
    assert.equal(await page.locator('#fio').isDisabled(),true);
    const initial = writes.at(-1);
    assert.match(initial.key,/^[a-f0-9-]{36}$/i);
    const ready = freshCard(page,lost.id);
    const replay = await post(page,patients,page.locator('#submitAddBtn'),201);
    assert.equal(replay.headers()['idempotent-replayed'],'true');
    assert.equal((await replay.json()).id,lost.id);
    assert.deepEqual(writes.at(-1),initial);
    assert.equal(writes.length - prior,2);
    await ready;
    const csrf = (await doctor.cookies()).find(cookie => cookie.name === 'clinic_csrf');
    const reused = await doctor.request.post(origin+patients,{
        headers:{'Idempotency-Key':initial.key,'X-CSRF-Token':csrf.value},
        data:{...JSON.parse(initial.body),name:names[1]+' Изменено'},
    });
    assert.equal(reused.status(),422);
    assert.equal((await reused.json()).code,'IDEMPOTENCY_KEY_REUSED');
    transportFaults.push('browser connection reset after genuine FastAPI 201 and COMMIT');
    checks.push('lost real 201 sends no automatic retry; explicit same exact key/body replays original ID; changed body is refused with 422');

    phase = 'genuine 409 duplicate with explicit confirmation';
    await navigate(page,'patients');
    await openForm(page);
    await fill(page,f.roles.doctor.id,names[2],'',policy);
    const duplicate = await post(page,patients,page.locator('#submitAddBtn'),409);
    const body = await duplicate.json();
    assert.equal(body.code,'POSSIBLE_DUPLICATE');
    assert(body.matches.some(match=>match.id===lost.id));
    const modal = page.locator('#duplicateModal');
    await modal.waitFor();
    const reason = 'Синтетическое основание отдельной карточки F7';
    await modal.locator('#duplicateReason').fill(reason);
    const read = page.waitForResponse(r => r.request().method()==='GET' &&
        /^\/api\/patients\/[a-f0-9-]+$/i.test(new URL(r.url()).pathname));
    const accepted = await post(page,patients,modal.locator('#confirmDuplicateBtn'),201);
    const newCard = await accepted.json();
    assert.notEqual(newCard.id,lost.id);
    assert.equal(newCard.name,names[2]);
    const confirmation = JSON.parse(accepted.request().postData()).duplicateConfirmation;
    assert.equal(confirmation.reason,reason);
    assert(confirmation.matchedPatientIds.includes(lost.id));
    assert.notEqual(accepted.request().headers()['idempotency-key'],initial.key);
    assert.equal((await protectedRead(await read)).id,newCard.id);
    await page.waitForFunction(id=>app.currentView==='patient'&&app.currentPatientId===id,newCard.id);
    checks.push('real duplicate 409 identifies scoped match; explicit reason and server-confirmed distinct patient preserve existing duplicate rules');
    return [lost.id,newCard.id];
}

async function workload(page,card,event,baseAmount) {
    phase = 'fresh working lists and personal aggregate';
    const analytics = response(page,'GET',dashboard);
    await navigate(page,'analytics');
    const snapshot = await protectedRead(await analytics);
    const count = snapshot.summary.cards.find(item=>item.code==='total_patients').value;
    assert.equal(count,baseAmount+1);
    const events = '/api/patients/workspace/events';
    await navigate(page,'calendar');
    const [calendar] = await Promise.all([response(page,'GET',events,(_,url)=>url.searchParams.get('q')===card.name),
        page.locator('#eventsWorkspaceQuery').fill(card.name).then(() =>
            keyboard(page.locator('#eventsWorkspaceSearch button[type=submit]')))]);
    const planned = await protectedRead(calendar);
    assert(planned.events.some(item=>item.id===event.id && item.patientId===card.id && !item.actualDate));
    await page.locator('#calendarPanel [data-workspace-row="'+event.id+'"]').waitFor();
    const day = response(page,'GET',patients,(_,url)=>url.searchParams.get('limit')==='20' &&
        url.searchParams.get('view')==='page');
    await navigate(page,'myDay');
    assert.equal((await protectedRead(await day)).page.total,baseAmount+1);
    const inbox = response(page,'GET','/api/internal-notifications');
    await navigate(page,'notifications');
    const notifications = await protectedRead(await inbox);
    const patientAlerts = notifications.items.filter(item=>item.patient_id===card.id);
    assert(patientAlerts.every(item=>!['event','consilium_revision'].includes(item.source_type)),
        'a far-future plan and recorded booking must not trigger event or revision alerts');
    checks.push('fresh personal dashboard and My Day add exactly one authorized patient; calendar shows unchanged plan and inbox has no spurious far-future alert');
}

async function main() {
    const browser = await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,
        args:['--disable-background-networking']});
    try {
        const f=await setup(browser,origin);
        const {context,page}=await session(browser,f,'doctor');
        const names=['Синтетический Новый Пациент F7','Синтетический Проверка Повтора F7','Синтетический Подтвержденный Дубль F7'];
        const base=await f.call(context,'GET',dashboard);
        const baseAmount=base.summary.cards.find(item=>item.code==='total_patients').value;

        phase='empty validation and keyboard-only intake';
        await openForm(page);
        assert.equal(await page.locator('#fio').inputValue(),'');
        await keyboard(page.locator('#submitAddBtn'));
        await page.locator('#addFormError').waitFor();
        assert.equal(writes.length,0);
        await page.locator('#fio').focus();
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(()=>document.activeElement.id),'age');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.evaluate(()=>document.activeElement.id),'fio');
        await fill(page,f.roles.doctor.id,names[0],f.day);
        await page.locator('#addFormError').waitFor({state:'hidden'});
        assert.equal(await page.locator('#createErrorSummaryList li').count(),0);
        await page.locator('#createEventPreview').getByText(/13:15/).waitFor();
        assert.equal(writes.length,0);
        await responsive(page);
        await keyboard(page.locator('#createBackBtn'));
        await page.locator('#confirmModal').waitFor();
        await page.keyboard.press('Escape');
        await page.locator('#confirmModal').waitFor({state:'hidden'});
        assert.equal(await page.locator('#fio').inputValue(),names[0]);
        assert.equal(await page.evaluate(()=>document.activeElement.id),'createBackBtn');
        checks.push('empty field error, real keyboard diagnosis, Tab and Shift+Tab, planned preview, Escape preserves intake draft, 10 axe checks and no writes');

        phase='authorized create plus fresh patient route and scoped list';
        const card=await exactCreate(page,names[0]);
        assert.equal(writes.length,1);
        assert.equal(card.age,51);
        assert.equal(card.diagnosis,'C34.1');
        assert.equal(card.stage,'exam');
        assert.equal(card.treatingDoctorUserId,f.roles.doctor.id);
        assert.equal(card.history.length,1);
        assert.equal(card.history[0].stage,'exam');
        assert.equal(card.history[0].events.length,1);
        const event=card.history[0].events[0];
        assert.equal(event.date,f.day);
        assert.equal(event.time,'13:15');
        assert.equal(event.actualDate,null);
        await keyboard(page.locator('#tab-events'));
        await page.locator('#patient-event-'+event.id).waitFor();
        await keyboard(page.locator('#backToList'));
        await registry(page,card);
        checks.push('single 201, server-created ID, fresh card GET, initial route and planned event, paginated server search and fresh scoped reopen');

        const appointment=await book(page,card,f);
        await denyAndVerifyChair(browser,f,card,appointment);
        await workload(page,card,event,baseAmount);
        const additional=await replayAndDuplicate(page,context,f,names,'7700000099997707');

        assert.equal(writes.filter(item=>item.pathname===patients).length,5);
        assert.equal(writes.filter(item=>item.pathname===appointments).length,1);
        assert.equal(legacyReads,0);
        assert.equal(pageErrors,0);
        fs.writeFileSync(path.join(process.env.OUT,'journey.local.json'),JSON.stringify({
            suite:'journey',browser:browser.version(),checks,axe,transportFaults,pageErrors,
            requests:{createPosts:5,appointmentPosts:1,legacyFullLists:0,unexpectedWrites:0},
            privateProof:{ids:[card.id,...additional],names,doctor:f.roles.doctor.id,readHashes,
                event:event.id,eventDate:event.date,eventTime:event.time,slotDay:f.day,appointment:appointment.id},
        },null,2));
    } finally {
        await browser.close();
    }
}

main().catch(error=>{
    process.stderr.write(phase+'\n'+error.stack+'\n');
    process.exitCode=1;
});
