'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createHash, randomBytes} = require('node:crypto');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);

const fixture = JSON.parse(fs.readFileSync(process.env.F9_FIXTURE, 'utf8'));
const {origin, patients, users, accountIds} = fixture;
assert(fixture.syntheticOnly && fixture.schema === 33);
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const checks = [], responseProofs = [], accessibility = [], nativeZoom = [], toastProofs = [];
const eventPath = '/api/patients/workspace/events?limit=100';
const patientPath = '/api/patients/' + patients.overdue.id;
const transferPath = '/api/patients/' + patients.revoked.id;
const headLogin = 'f9.synthetic.' + 'h'.repeat(51);
const headName = ('Синтетический Заведующий F9 ' + 'ДлиннаяФамилия'.repeat(15)).slice(0,200);
const deptName = 'Синтетическое отделение F9 ' + 'ДлинноеНазвание'.repeat(10);
const randomPassword = () => randomBytes(24).toString('hex');
let phase = 'startup';
let reads = Promise.resolve();

async function context(browser) {
    const ctx = await browser.newContext({reducedMotion:'reduce',viewport:{width:1440,height:1000}});
    await ctx.route('**/*',route=>new URL(route.request().url()).origin===origin
        ? route.continue() : route.abort('blockedbyclient'));
    return ctx;
}

async function press(locator, key = 'Enter') {
    await locator.and(locator.page().locator(':not(:disabled)')).waitFor();
    await locator.focus();
    assert(await locator.evaluate(node=>node===document.activeElement && !node.disabled));
    await locator.press(key);
}

async function browseRole(ctx, username, password = fixture.password) {
    const page = await ctx.newPage();
    await page.route(origin + '/api/**', route => {
        const issued = reads.then(async()=>route.fulfill({response:await route.fetch()}));
        reads = issued.catch(()=>{});
        return issued;
    });
    await page.goto(origin);
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(password);
    await press(page.locator('#loginSubmit'));
    await page.waitForFunction(() => app.appReady && !app.sessionExpired && app.patientListState === 'ready');
    return page;
}

async function call(ctx, route, status, method='GET', data) {
    assert(route.startsWith('/api/'));
    const headers = {};
    if (method !== 'GET') {
        const csrf = (await ctx.cookies()).find(cookie=>cookie.name==='clinic_csrf');
        if (csrf) headers['X-CSRF-Token']=csrf.value;
    }
    const issued=reads.then(()=>ctx.request.fetch(origin+route,{method,headers,data}));
    reads=issued.catch(()=>{});
    const response=await issued;
    assert.equal(response.status(),status,method + ' ' + route.split('/').slice(0,4).join('/'));
    return response;
}

async function audited(response, kind) {
    const bytes = await response.body();
    responseProofs.push({kind,sha256:createHash('sha256').update(bytes).digest('hex')});
    return JSON.parse(bytes);
}

async function navigate(page, key) {
    await press(page.locator('[data-view="' + key + '"]').first());
    await page.locator('#view-' + key).waitFor();
}

function mutation(page, pathname, method) {
    return page.waitForResponse(response=>new URL(response.url()).pathname===pathname &&
        response.request().method()===method);
}

function dept(page, name) {
    return page.locator('#structurePanel .dept').filter({has:page.getByText(name,{exact:true})});
}

async function adminSetup(page, admin, temporary) {
    phase = 'department and head creation from UI';
    await navigate(page,'structure');
    await page.locator('#structurePanel .dept').first().waitFor();
    const base = (await (await call(admin,'/api/departments',200)).json()).departments;
    const source = base.find(department=>department.doctors.some(doc=>doc.id===accountIds.doctor));
    assert(source);
    await press(page.locator('#openDeptCreateBtn'));
    await page.locator('#textPromptInput').fill(deptName);
    const [, created] = await Promise.all([press(page.locator('#textPromptOkBtn')), mutation(page,'/api/departments','POST')]);
    assert.equal(created.status(),201);
    const target=await created.json();
    await dept(page,deptName).waitFor();
    checks.push('create department');

    await navigate(page,'staff');
    await page.locator('#staffBody tr').first().waitFor();
    const start=page.locator('#openStaffCreateBtn');
    await press(start);
    await page.locator('#staffModal').waitFor();
    for (const key of ['Tab','Shift+Tab']) {
        await page.keyboard.press(key);
        assert(await page.locator('#staffModal').evaluate(node=>node.contains(document.activeElement)));
    }
    await page.keyboard.press('Escape');
    await page.locator('#staffModal').waitFor({state:'hidden'});
    assert(await start.evaluate(node=>document.activeElement===node));
    await press(start);
    await page.locator('#staffFullName').fill(headName);
    await page.locator('#staffUsername').fill(headLogin);
    await page.locator('#staffRole').selectOption('department_head');
    await page.locator('#staffPassword').fill(temporary);
    await page.locator('#staffMustChange').check();
    const [, account] = await Promise.all([press(page.locator('#submitStaffBtn')),mutation(page,'/api/users','POST')]);
    assert.equal(account.status(),201);
    const newHead=await account.json();
    assert(!JSON.stringify(newHead).includes(temporary));
    await page.locator('#staffModal').waitFor({state:'hidden'});
    assert.equal(await page.locator('#staffPassword').inputValue(),'');
    const row=page.locator('#staffBody tr').filter({hasText:headName});
    await row.waitFor();
    await page.locator('#staffSearchInput').fill(headLogin);
    await row.waitFor();
    await page.waitForFunction(()=>app.staffReadState==='ready' && app.staffUsers.length===1);
    await page.locator('#staffRoleFilter').selectOption('department_head');
    await page.waitForFunction(()=>app.staffReadState==='ready' && app.staffUsers.length===1);
    await page.locator('#staffSearchInput').fill('');
    await page.locator('#staffRoleFilter').selectOption('');
    checks.push('create user and filter with server query', 'Escape and return focus in staff editor');

    await navigate(page,'structure');
    const card=dept(page,deptName);
    await card.getByRole('combobox',{name:/Заведующий/}).selectOption(newHead.id);
    const [, assigned] = await Promise.all([press(card.getByRole('button',{name:'Назначить',exact:true})),
        mutation(page,'/api/departments/'+target.id,'PATCH')]);
    assert.equal(assigned.status(),200);
    assert((await assigned.json()).heads.some(head=>head.id===newHead.id));
    checks.push('assign active department head');
    return {source,target,newHead};
}

async function staffNamespaces(admin, unchanged) {
    const overview = await audited(await call(admin,'/api/patients?view=page',200),'admin registry');
    assert(overview.patients.some(p=>p.id===patients.overdue.id));
    if (unchanged) checks.push('developer registry remains available after structure changes');
}

async function scope(ctx, allowed, ordinal) {
    const page = await audited(await call(ctx,'/api/patients?view=page',200),ordinal+' list');
    assert.equal(page.patients.some(p=>p.id===patients.overdue.id),allowed);
    const card = await call(ctx,patientPath,allowed?200:404);
    if (allowed) {
        assert.equal((await audited(card,ordinal+' patient')).id,patients.overdue.id);
    } else {
        const missing=await call(ctx,'/api/patients/nonexistent-f9-fixture',404);
        assert.deepEqual(await card.json(),await missing.json());
    }
    const events=await audited(await call(ctx,eventPath,200),ordinal+' schedule');
    assert.equal(events.events.some(row=>row.patientId===patients.overdue.id),allowed);
    const issues=await audited(await call(ctx,'/api/data-quality/issues',200),ordinal+' Quality');
    assert.equal(issues.patients.some(row=>row.patient_id===patients.overdue.id),allowed);
    const analytics=await audited(await call(ctx,'/api/analytics/summary',200),ordinal+' Analytics');
    const total=analytics.cards.find(x=>x.code==='total_patients').value;
    assert.equal(total,page.page.total);
    const candidates=await audited(await call(ctx,'/api/patients/workspace/patients?limit=100',200),ordinal+' consilium');
    assert.equal(candidates.patients.some(row=>row.id===patients.overdue.id),allowed);
}

async function headPassword(browser, temporary, current) {
    const head=await context(browser);
    const login=await call(head,'/api/auth/login',200,'POST',{username:headLogin,password:temporary});
    assert((await login.json()).user.mustChangePassword);
    await call(head,'/api/patients?view=page',403);
    await call(head,'/api/auth/change-password',200,'POST',{currentPassword:temporary,newPassword:current});
    return head;
}

async function physicianDay(page, doctor, label) {
    phase = label;
    if (await page.locator('#view-myDay').isVisible()) await navigate(page,'patients');
    await navigate(page,'myDay');
    await press(page.locator('#f1MyDay [data-f1-doctor-mode="events"]'));
    await page.locator('#f1MyDay [data-f1-doctor-row="events"]').filter({hasText:patients.overdue.name}).first().waitFor();
    const work = await audited(await call(doctor,eventPath,200),label);
    assert(work.events.some(row=>row.patientId===patients.overdue.id));
    assert(!work.events.some(row=>row.patientId===patients.foreign.id));
}

async function reauthenticateAffectedPhysician(page) {
    phase = 'require fresh identity after changed membership';
    if (!await page.evaluate(()=>app.sessionExpired)) {
        await press(page.locator('[data-view="patients"]').first());
    }
    await page.waitForFunction(()=>app.sessionExpired);
    await page.locator('#loginScreen').waitFor();
    assert.equal(await page.locator('#f1MyDay [data-f1-doctor-row]').count(),0);
    assert.equal(await page.locator('#username').inputValue(),users.doctor);
    await page.locator('#password').fill(fixture.password);
    await press(page.locator('#loginSubmit'));
    await page.waitForFunction(()=>app.appReady && !app.sessionExpired && app.patientListState==='ready');
}

async function membership(page, admin, {source,target}, a, b) {
    phase = 'membership and scope transitions';
    await scope(a,true,'A before');
    await scope(b,false,'B before');
    const targetCard=dept(page,deptName);
    await targetCard.getByRole('combobox',{name:/Добавить врача/}).selectOption(accountIds.doctor);
    const [, added] = await Promise.all([press(targetCard.getByRole('button',{name:'Добавить',exact:true})),
        mutation(page,'/api/departments/'+target.id+'/doctors','POST')]);
    assert.equal(added.status(),200);
    assert((await added.json()).doctors.some(doctor=>doctor.id===accountIds.doctor));
    await page.waitForFunction(()=>app.structureReadState==='ready');
    await scope(a,true,'A multi');
    await scope(b,true,'B multi');
    const sourceCard=dept(page,source.name);
    const member=source.doctors.find(person=>person.id===accountIds.doctor);
    const person=sourceCard.getByRole('listitem').filter({hasText:member.fullName});
    await press(person.getByRole('button',{name:'Убрать из отделения',exact:true}));
    const [, removed] = await Promise.all([press(page.locator('#confirmOkBtn')),
        mutation(page,'/api/departments/'+source.id+'/doctors/'+accountIds.doctor,'DELETE')]);
    assert.equal(removed.status(),200);
    assert(!(await removed.json()).doctors.some(doctor=>doctor.id===accountIds.doctor));
    await page.waitForFunction(()=>app.structureReadState==='ready');
    await scope(a,false,'A removed');
    await scope(b,true,'B only');
    checks.push('add membership', 'remove membership', 'single and multiple memberships produce exact server scope');
    await staffNamespaces(admin,true);
}

async function authority(browser, admin, a, b, page, head) {
    phase = 'versioned treating doctor and session revocation';
    const d1=await context(browser), d2=await context(browser), other=await context(browser);
    for (const [ctx,user] of [[d1,users.doctor],[d2,users.doctor],[other,users.foreign]]) {
        await call(ctx,'/api/auth/login',200,'POST',{username:user,password:fixture.password});
    }
    const owned=await audited(await call(d1,transferPath,200),'old physician before reassignment');
    await call(other,transferPath,404);
    await navigate(page,'patients');
    await press(page.locator('#patientsBody .patient-open').filter({hasText:patients.revoked.name}));
    await page.locator('#view-patient').waitFor();
    await press(page.getByRole('button',{name:'Сменить лечащего врача',exact:true}));
    await page.locator('#doctorModalSelect').selectOption(accountIds.foreign);
    const [,change]=await Promise.all([press(page.locator('#doctorOkBtn')),
        mutation(page,transferPath+'/treating-doctor','PUT')]);
    assert.equal(change.status(),200);
    assert.deepEqual(change.request().postDataJSON(),{userId:accountIds.foreign,expectedVersion:owned.version});
    assert.equal((await change.json()).treatingDoctorUserId,accountIds.foreign);
    await page.locator('#doctorModal').waitFor({state:'hidden'});
    await call(admin,transferPath+'/treating-doctor',409,'PUT',
        {userId:accountIds.doctor,expectedVersion:owned.version});
    await call(d1,transferPath,404);
    await call(b,transferPath,404);
    await audited(await call(other,transferPath,200),'new physician after reassignment');
    await call(a,'/api/users',403);
    await call(b,'/api/departments',403);
    checks.push('versioned explicit treating-doctor reassignment', 'role and object scope deny former access');

    await navigate(page,'staff');
    const staff=page.locator('#staffBody tr').filter({hasText:users.doctor});
    await staff.waitFor();
    await press(staff.getByRole('button',{name:'Завершить сессии',exact:true}));
    const [, ended] = await Promise.all([press(page.locator('#confirmOkBtn')),
        mutation(page,'/api/users/'+accountIds.doctor+'/terminate-sessions','POST')]);
    assert.equal(ended.status(),200);
    assert((await ended.json()).terminated>=2);
    for (const tab of [d1,d2]) await call(tab,'/api/patients?view=page',401);
    checks.push('terminate two isolated browser sessions');

    // The known synthetic password is generated in memory and never included in receipts.
    const next=randomPassword();
    const reset=await call(admin,'/api/users/'+head.id+'/reset-password',200,'POST',
        {newPassword:next,mustChangePassword:true});
    assert(!JSON.stringify(await reset.json()).includes(next));
    await call(b,'/api/patients?view=page',401);
    await call(b,'/api/auth/login',200,'POST',{username:headLogin,password:next});
    await call(b,'/api/patients?view=page',403);
    await call(b,'/api/auth/change-password',200,'POST',{currentPassword:next,newPassword:randomPassword()});
    await call(b,'/api/patients?view=page',200);
    checks.push('temporary password gates protected reads after reset');

    await call(d1,'/api/auth/login',200,'POST',{username:users.doctor,password:fixture.password});
    await call(d2,'/api/auth/login',200,'POST',{username:users.doctor,password:fixture.password});
    await press(staff.getByRole('button',{name:'Блокировать',exact:true}));
    const [, blocked] = await Promise.all([press(page.locator('#confirmOkBtn')),
        mutation(page,'/api/users/'+accountIds.doctor,'PATCH')]);
    assert.equal(blocked.status(),200);
    assert.equal((await blocked.json()).status,'blocked');
    await staff.getByText('Заблокирован',{exact:true}).waitFor();
    for (const tab of [d1,d2]) await call(tab,'/api/patients?view=page',401);
    await call(d1,'/api/auth/login',401,'POST',{username:users.doctor,password:fixture.password});
    checks.push('block user and revoke two live sessions');
    await call(admin,'/api/users/'+accountIds.doctor,200,'PATCH',{status:'deactivated'});
    assert.equal((await (await call(admin,'/api/users?status=deactivated',200)).json()).users
        .find(user=>user.id===accountIds.doctor).status,'deactivated');
    checks.push('deactivation reflected by status filter');
}

async function staleStructure(page) {
    phase = 'late real response after leaving management view';
    const routeMatch = origin + '/api/departments';
    let release, arrived, delivered;
    const allowed = new Promise(resolve=>{release=resolve;});
    const held = new Promise(resolve=>{arrived=resolve;});
    const complete = new Promise(resolve=>{delivered=resolve;});
    let first = true;
    const handler = async route=>{
        if (!first || route.request().method()!=='GET') return route.fallback();
        first=false;
        const value=await route.fetch();
        assert.equal(value.status(),200);
        arrived();
        await allowed;
        await route.fulfill({response:value});
        delivered();
    };
    await page.route(routeMatch,handler);
    try {
        await navigate(page,'structure');
        await held;
        await navigate(page,'staff');
        await page.waitForFunction(()=>app.staffReadState==='ready');
        release();
        await complete;
        await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
        assert.equal(await page.locator('#structurePanel .dept').count(),0);
        assert.equal(await page.locator('#unresolvedPanel button').count(),0);
        assert.equal(await page.locator('#openDeptCreateBtn').isEnabled(),false);
    } finally {
        release();
        await page.unroute(routeMatch,handler);
    }
    checks.push('held genuine Structure HTTP 200 cannot restore administrative controls after navigation');
}

async function compactStaff(page, width, theme) {
    const row = page.locator('#staffBody tr').filter({hasText:headLogin});
    const ordinary = page.locator('#staffBody tr').filter({hasText:users.admin});
    const toggle = row.locator('button.staff-expand');
    await toggle.waitFor({state:'visible'});
    assert.equal(await ordinary.locator('button.staff-expand:visible').count(),0);
    const values = row.locator('.staff-value');
    const exact = await values.allTextContents();
    assert.deepEqual(exact,[headName,headLogin,deptName]);
    const measure = target => target.evaluate(node=>Math.round(node.getBoundingClientRect().height));
    const shortHeight = await measure(ordinary), compactHeight = await measure(row);
    const context = `Staff ${theme} ${width}px: compact ${compactHeight}px, ordinary ${shortHeight}px`;
    process.stdout.write(context+'\n');
    assert(compactHeight <= shortHeight + 90,context);
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    assert.match(await toggle.textContent(),/Показать полностью/);
    let writes = 0;
    const countWrite = request => {
        if (['POST','PUT','PATCH','DELETE'].includes(request.method())) writes++;
    };
    page.on('request',countWrite);
    await press(toggle);
    assert.equal(await toggle.getAttribute('aria-expanded'),'true');
    assert.match(await toggle.textContent(),/Свернуть/);
    assert.deepEqual(await values.allTextContents(),exact);
    assert(await measure(row) > compactHeight);
    assert.equal(await values.evaluateAll(nodes=>nodes.every(node=>node.scrollHeight <= node.clientHeight + 1)),true);
    const block=row.getByRole('button',{name:'Блокировать',exact:true});
    const end=row.getByRole('button',{name:'Завершить сессии',exact:true});
    assert(await block.isEnabled() && await end.isEnabled());
    if (theme==='dark' && width===375) {
        const edit = row.getByRole('button',{name:'Изменить',exact:true});
        await page.keyboard.press('Tab');
        assert(await edit.evaluate(node=>node===document.activeElement));
        await page.keyboard.press('Shift+Tab');
        assert(await toggle.evaluate(node=>node===document.activeElement));
        await toggle.scrollIntoViewIfNeeded();
        await page.screenshot({path:path.join(process.env.OUT,'f9-staff-expanded-dark-375.png'),animations:'disabled'});
    }
    await press(toggle,'Space');
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    assert.equal(await measure(row),compactHeight);
    page.off('request',countWrite);
    assert.equal(writes,0);
    return {shortHeight,compactHeight};
}

async function layout(page) {
    phase = 'Stage 3.5 accessibility and viewport checks';
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
    for (const section of ['staff','structure']) {
        if (!await page.locator('#view-'+section).isVisible()) await navigate(page,section);
        await page.waitForFunction(section=> section==='staff' ? app.staffReadState==='ready' :
            document.querySelectorAll('#structurePanel .dept').length>0,section);
        for (const theme of ['light','dark']) {
            await page.evaluate(theme=>app.applyTheme(theme),theme);
            for (const width of [320,375,768,1024,1440]) {
                await page.setViewportSize({width,height:1000});
                const body=page.locator('#view-'+section);
                const checks=await body.evaluate(async node=>({
                    overflow:document.documentElement.scrollWidth>innerWidth,
                    violations:(await axe.run(node,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}))
                        .violations.map(row=>row.id)
                }));
                assert.deepEqual(checks,{overflow:false,violations:[]},section+'/'+theme+'/'+width);
                const heights = section === 'staff' ? await compactStaff(page,width,theme) : {};
                accessibility.push({section,theme,width,...checks,...heights});
                if ((theme==='light' && width===1440) || (theme==='dark' && width===375)) {
                    if (section==='staff') {
                        await page.locator('#staffBody tr').filter({hasText:headLogin})
                            .locator('button.staff-expand').scrollIntoViewIfNeeded();
                    }
                    await page.screenshot({path:path.join(process.env.OUT,'f9-'+section+'-'+theme+'-'+width+'.png'),
                        animations:'disabled'});
                }
            }
        }
        await page.setViewportSize({width:1440,height:1000});
    }
    await navigate(page,'staff');
    // Change only the display response to exercise multiple long departments
    // without changing the real membership and audit fixtures.
    const extraDepartment = '<b>Синтетическое отделение</b> ' + 'д'.repeat(100);
    const match = /\/api\/users\?.+$/;
    const fixture = async route => {
        const response = await route.fetch();
        assert.equal(response.status(),200);
        const data = await response.json();
        const head = data.users.find(user=>user.username===headLogin);
        assert(head);
        head.departments.push({id:'synthetic-render-only',name:extraDepartment});
        await route.fulfill({response,json:data});
    };
    await page.route(match,fixture);
    try {
        await page.locator('#staffSearchInput').fill(headLogin);
        await page.waitForFunction(()=>app.staffReadState==='ready' && app.staffUsers.length===1);
        const row=page.locator('#staffBody tr').filter({hasText:headLogin});
        const toggle=row.locator('button.staff-expand');
        const department=row.locator('.staff-value').last();
        await toggle.waitFor({state:'visible'});
        assert.equal(await toggle.getAttribute('aria-expanded'),'false');
        await press(toggle);
        assert.equal(await toggle.getAttribute('aria-expanded'),'true');
        assert.equal(await department.textContent(),deptName+', '+extraDepartment);
        assert.equal(await department.locator('b').count(),0);
        assert(await department.evaluate(node=>node.scrollHeight<=node.clientHeight+1));
        await press(toggle,'Space');
        assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    } finally {
        await page.unroute(match,fixture);
    }
    await page.locator('#staffSearchInput').fill('');
    await page.waitForFunction(()=>app.staffReadState==='ready' && app.staffUsers.length>1);
    checks.push('compact staff values expand and collapse on keyboard across widths and themes, including after filter');
    checks.push('Stage 3.5 desktop/mobile themes and scoped axe checks');
}

async function zoomViews() {
    phase = 'native Chromium zoom on staff and structure';
    const temporary = fs.mkdtempSync(path.join(os.tmpdir(),'f9z-'));
    const extension = path.join(temporary,'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify({
        manifest_version:3,name:'Synthetic localhost zoom',version:'1.0',permissions:['tabs'],
        background:{service_worker:'worker.js'},
    }));
    fs.writeFileSync(path.join(extension,'worker.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
    let ctx;
    try {
        ctx=await chromium.launchPersistentContext(path.join(temporary,'profile'),{
            headless:true,viewport:null,reducedMotion:'reduce',ignoreDefaultArgs:['--disable-extensions'],
            executablePath:process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            args:['--window-size=1280,1000','--disable-background-networking',
                '--disable-extensions-except='+extension,'--load-extension='+extension],
        });
        await ctx.route('**/*', route=>new URL(route.request().url()).origin===origin
            ? route.continue() : route.abort('blockedbyclient'));
        const page=await browseRole(ctx,users.admin);
        const control=ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker',{timeout:10000});
        const baseline=await page.evaluate(()=>devicePixelRatio);
        async function zoom(factor) {
            const actual=await control.evaluate(async ({url,factor})=>{
                const tab=(await chrome.tabs.query({})).find(item=>item.url===url);
                if (!tab) throw new Error('synthetic tab missing');
                await chrome.tabs.setZoom(tab.id,factor);
                return chrome.tabs.getZoom(tab.id);
            },{url:page.url(),factor});
            assert.equal(Math.round(actual*100),factor*100);
            await page.waitForFunction(target=>Math.abs(devicePixelRatio-target)<0.01,baseline*factor);
        }
        for (const section of ['staff','structure']) {
            await zoom(1);
            await navigate(page,section);
            await page.waitForFunction(section=>section==='staff'?app.staffReadState==='ready':app.structureReadState==='ready',section);
            for (const theme of ['light','dark']) {
                await page.evaluate(theme=>app.applyTheme(theme),theme);
                for (const factor of [1,2,4]) {
                    await zoom(factor);
                    const shape=await page.locator('#view-'+section+' h1').evaluate(node=>({
                        width:innerWidth,scale:visualViewport.scale,visible:node.getBoundingClientRect().height>0,
                        overflow:document.documentElement.scrollWidth>innerWidth,
                    }));
                    assert(shape.visible && shape.scale===1 && !shape.overflow,section+'/'+theme+'/'+factor);
                    nativeZoom.push({section,theme,percent:factor*100,width:shape.width,noPageOverflow:true});
                }
            }
        }
    } finally {
        try {
            if (ctx) {
                for (const page of ctx.pages()) await page.unrouteAll({behavior:'wait'});
                await ctx.close();
            }
        } finally {
            fs.rmSync(temporary,{recursive:true,force:true});
        }
    }
    checks.push('native Chromium zoom at 100, 200 and 400 percent on both views and themes');
}

async function editRoleAndArchive(page, admin, headContext, setup) {
    phase = 'edit role, revoke active session and archive department';
    await navigate(page,'staff');
    const row=page.locator('#staffBody tr').filter({hasText:headLogin});
    await press(row.getByRole('button',{name:'Изменить',exact:true}));
    await page.locator('#staffRole').selectOption('doctor');
    const [, changed]=await Promise.all([press(page.locator('#submitStaffBtn')),
        mutation(page,'/api/users/'+setup.newHead.id,'PATCH')]);
    assert.equal(changed.status(),200);
    assert.equal((await changed.json()).roleId,'doctor');
    await call(headContext,'/api/patients?view=page',401);
    await row.getByText('Врач',{exact:true}).waitFor();
    checks.push('edit role in staff UI revokes the old head session');

    await navigate(page,'structure');
    const target=dept(page,deptName);
    await press(target.getByRole('button',{name:'Архивировать',exact:true}));
    const [, archived]=await Promise.all([press(page.locator('#confirmOkBtn')),
        mutation(page,'/api/departments/'+setup.target.id,'DELETE')]);
    assert.equal(archived.status(),200);
    assert.equal((await archived.json()).archived,true);
    await target.getByText('Архив',{exact:true}).waitFor();
    assert.equal(await target.getByRole('button',{name:'Добавить',exact:true}).count(),0);
    await call(admin,'/api/departments/'+setup.target.id+'/doctors',422,'POST',{userId:accountIds.foreign});
    checks.push('archived department persists with no active membership controls');
}

async function toastLayout(page) {
    phase = 'responsive toast queue, priority and keyboard';
    await navigate(page,'staff');
    await page.waitForFunction(()=>app.staffReadState==='ready');
    await page.waitForFunction(()=>!document.querySelector('#toasts .toast'));
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
    const items = page.locator('#toasts .toast');
    const search = page.locator('#staffSearchInput');
    const single = 'Синтетическое подтверждение сохранения.';
    await search.focus();
    await page.evaluate(text=>ui.toast(text,'good'),single);
    await items.filter({hasText:single}).waitFor();
    assert.deepEqual(await items.allTextContents(),[single]);
    assert(await search.evaluate(node=>node===document.activeElement));
    await page.waitForFunction(()=>!document.querySelector('#toasts .toast'));

    await page.clock.install();
    const priority = [
        ['Синтетическое предупреждение.','warning'],
        ['Синтетический отказ.','error'],
        ['Результат синтетической записи неизвестен. Сначала проверьте сервер.','warning'],
        ['Синтетическая ошибка проверки.','danger'],
        ['Синтетическое предупреждение проверки.','warning'],
    ];
    const long = ('Ошибка проверки. Выполните безопасное контрольное чтение. ').repeat(20);
    for (const theme of ['light','dark']) {
        for (const width of [320,375,1440]) {
            const cap = width <= 560 ? 2 : 3;
            await page.setViewportSize({width,height:812});
            await page.evaluate(theme=>app.applyTheme(theme),theme);
            await search.scrollIntoViewIfNeeded();
            await search.focus();
            await page.evaluate(()=>{
                for (let i=0;i<6;i++) ui.toast('Синтетическое действие '+i,'success');
            });
            assert((await items.count()) <= cap);
            await page.clock.runFor(5000);
            const aggregate='Успешных действий: '+(6-cap);
            assert((await items.allTextContents()).includes(aggregate));

            // Install the receipt before enqueueing to observe queued as well as immediately visible failures.
            await page.evaluate(messages=>{
                const target=document.getElementById('toasts');
                const visible=new Set();
                const capture=()=>target.querySelectorAll('.toast').forEach(node=>visible.add(node.textContent));
                globalThis.f9ToastReceipt={visible,observer:new MutationObserver(capture)};
                globalThis.f9ToastReceipt.observer.observe(target,{childList:true,subtree:true,characterData:true});
                for (const [text,kind] of messages) ui.toast(text,kind);
            },priority);
            assert.equal(await items.count(),cap);
            const firstVisible = await items.allTextContents();
            assert(firstVisible.includes(priority[0][0]) && firstVisible.includes(priority[1][0]));
            assert(await search.evaluate(node=>node===document.activeElement));
            await page.evaluate(()=>{
                const bounds=document.getElementById('staffSearchInput').getBoundingClientRect();
                if (document.elementFromPoint(bounds.x+bounds.width/2,bounds.y+bounds.height/2)?.id!=='staffSearchInput') {
                    throw new Error('Toast covers the active staff search control');
                }
            });
            for (let batch=0;batch<priority.length+1 && await items.count();batch++) {
                await page.clock.runFor(8000);
                assert((await items.count()) <= cap);
            }
            const delivered = await page.evaluate(()=>{
                const {visible,observer}=globalThis.f9ToastReceipt;
                observer.disconnect();
                delete globalThis.f9ToastReceipt;
                return [...visible];
            });
            assert(priority.every(([text])=>delivered.includes(text)));
            assert.equal(await items.count(),0);

            await page.evaluate(text=>ui.toast(text,'danger'),long);
            const error = items.first();
            const geometry = await error.evaluate(node=>{
                const region=node.closest('#toasts');
                const rect=region.getBoundingClientRect();
                return {bounded:rect.height <= innerHeight*0.4,
                    inViewport:rect.left>=0 && rect.right<=innerWidth,
                    scrollable:node.scrollHeight>node.clientHeight,live:region.getAttribute('aria-live')};
            });
            assert.deepEqual(geometry,{bounded:true,inViewport:true,scrollable:true,live:'polite'});
            assert.equal(await error.textContent(),long);
            await error.and(page.locator('[tabindex="0"]')).waitFor();
            assert(await search.evaluate(node=>node===document.activeElement));
            await error.focus();
            await page.keyboard.press('End');
            await page.waitForFunction(()=>document.querySelector('#toasts .toast')?.scrollTop>0);
            await page.clock.runFor(8000);
            assert(await error.evaluate(node=>node===document.activeElement));
            await search.focus();
            const violations = await page.locator('#toasts').evaluate(async node=>
                (await axe.run(node,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}))
                    .violations.map(entry=>entry.id));
            assert.deepEqual(violations,[],theme+'/'+width);
            await page.clock.runFor(8000);
            await page.waitForFunction(()=>!document.querySelector('#toasts .toast'));
            if (width!==1440) {
                await page.locator('#staffBody tr').filter({hasText:headLogin}).scrollIntoViewIfNeeded();
                await page.screenshot({path:path.join(process.env.OUT,'f9-staff-clean-'+theme+'-'+width+'.png'),
                    animations:'disabled'});
            }
            toastProofs.push({theme,width,cap,importantDelivered:priority.length,geometry,violations});
        }
    }
    checks.push('toast single with real timer, six-success aggregation, priority delivery, viewport/axe and keyboard');
}

async function main() {
    const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,
        args:['--disable-background-networking']});
    try {
        const admin=await context(browser);
        const page=await browseRole(admin,users.admin);
        const temporary=randomPassword(), current=randomPassword();
        const setup=await adminSetup(page,admin,temporary);
        const a=await context(browser);
        await call(a,'/api/auth/login',200,'POST',{username:users.head,password:fixture.password});
        const b=await headPassword(browser,temporary,current);
        const doctor=await context(browser);
        const doctorPage=await browseRole(doctor,users.doctor);
        await physicianDay(doctorPage,doctor,'My Day before membership move');
        await membership(page,admin,setup,a,b);
        await reauthenticateAffectedPhysician(doctorPage);
        await physicianDay(doctorPage,doctor,'My Day after membership move');
        await doctorPage.unrouteAll({behavior:'wait'});
        await doctor.close();
        checks.push('My Day retains own scope before and after organizational membership change');
        await authority(browser,admin,a,b,page,setup.newHead);
        await staleStructure(page);
        await layout(page);
        await zoomViews();
        await editRoleAndArchive(page,admin,b,setup);
        await toastLayout(page);
        fs.writeFileSync(path.join(process.env.OUT,'journey.local.json'),JSON.stringify({
            checks,responseProofs,accessibility,nativeZoom,toastProofs,browser:browser.version(),
            auditTargets:{head:setup.newHead.id,department:setup.target.id},
        }));
    } finally {
        for (const ctx of browser.contexts()) {
            for (const page of ctx.pages()) await page.unrouteAll({behavior:'wait'});
            await ctx.close();
        }
        await browser.close();
    }
}

main().catch(error=>{
    const frames=String(error.stack || '').match(/fast_track_f9\/journey\.cjs:\d+:\d+/g) || [];
    const status=String(error.message).match(/\b(?:GET|POST|PATCH|PUT|DELETE) \/api\/[a-z-]+(?:\/[a-z-]+)?[\s\S]*?\b([1-5]\d{2}) !== ([1-5]\d{2})\b/);
    process.stderr.write(phase+' '+error.name+(status?' HTTP '+status[1]+' expected '+status[2]:'')+'\n'+frames.join('\n')+'\n');
    process.exitCode=1;
});
