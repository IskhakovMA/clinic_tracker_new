const assert = require('node:assert/strict');
const {createHash, randomBytes} = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE);

const fixture = JSON.parse(fs.readFileSync(process.env.F10_FIXTURE,'utf8'));
const {origin, password, users, patients, accountIds} = fixture;
assert(fixture.syntheticOnly && fixture.schema === 33);
assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
const checks = [], layout = [], nativeZoom = [];
const auditList = /\/api\/audit\?/;
const complaint = 'F10_PRIVATE_SYNTHETIC_COMPLAINT';
const staffName = 'F10_PRIVATE_SYNTHETIC_STAFF';
const departmentName = 'F10_PRIVATE_SYNTHETIC_DEPARTMENT';
const newPassword = randomBytes(24).toString('hex');
let phase = 'startup';

async function createContext(browser) {
    const context = await browser.newContext({reducedMotion:'reduce', viewport:{width:1440,height:900}});
    await fence(context);
    return context;
}

async function fence(context) {
    await context.route('**/*',route=>new URL(route.request().url()).origin===origin
        ? route.continue() : route.abort('blockedbyclient'));
}

async function request(context, url, status, method='GET', data) {
    const csrf = (await context.cookies()).find(cookie=>cookie.name==='clinic_csrf');
    const headers = csrf && method!=='GET' ? {'X-CSRF-Token':csrf.value} : {};
    const response = await context.request.fetch(origin+url,{method,headers,data});
    assert.equal(response.status(),status,method+' '+url.split('/').slice(0,3).join('/'));
    return response;
}

async function openAudit(context) {
    const page = await context.newPage();
    await page.goto(origin);
    if (await page.locator('#loginForm').isVisible()) {
        await page.locator('#username').fill(users.admin);
        await page.locator('#password').fill(password);
        await page.locator('#loginSubmit').click();
    }
    await page.waitForFunction(()=>app.appReady && !app.sessionExpired);
    await page.locator('[data-view="audit"]').first().click();
    await ready(page);
    return page;
}

async function ready(page) {
    await page.waitForFunction(()=>app.auditReadState==='ready');
}

async function loadOffset(page, id, offset) {
    const promise = page.waitForResponse(response=>auditList.test(response.url()) &&
        Number(new URL(response.url()).searchParams.get('offset'))===offset);
    await page.locator('#'+id).click();
    const response = await promise;
    assert.equal(response.status(),200);
    await ready(page);
    return response.json();
}

async function compare(page, data) {
    assert.equal(data.limit,50);
    const rows=page.locator('#auditPanel [data-audit-event-id]');
    assert.equal(await rows.count(),data.events.length);
    const ids=await rows.evaluateAll(nodes=>nodes.map(node=>Number(node.dataset.auditEventId)));
    assert.deepEqual(ids,data.events.map(event=>event.id));
    assert(ids.every((id,index)=>index===0 || id<ids[index-1]));
    assert.match(await page.locator('#auditPageInfo').textContent(),new RegExp(String(data.total)));
}

async function executeWrites(admin,doctor) {
    const user = await (await request(admin,'/api/users',201,'POST',{
        username:'f10.synthetic.user',fullName:staffName,roleId:'doctor',
        password:newPassword,mustChangePassword:false,
    })).json();
    await request(admin,'/api/users/'+user.id,200,'PATCH',{roleId:'department_head'});
    await request(admin,'/api/users/'+user.id+'/terminate-sessions',200,'POST');
    const department = await (await request(admin,'/api/departments',201,'POST',{name:departmentName})).json();
    await request(admin,'/api/departments/'+department.id+'/doctors',200,'POST',{userId:accountIds.doctor});
    await request(admin,'/api/departments/'+department.id+'/doctors/'+accountIds.doctor,200,'DELETE');
    await request(doctor,'/api/auth/login',200,'POST',{username:users.doctor,password});
    const cardPath='/api/patients/'+patients.overdue.id;
    const before=await (await request(doctor,cardPath,200)).json();
    await request(doctor,cardPath,200,'PATCH',{field:'complaint',value:complaint,expectedVersion:before.version});
    const captured=await request(doctor,cardPath,200);
    assert.equal((await captured.json()).complaint,complaint);
    const sha256=createHash('sha256').update(await captured.body()).digest('hex');
    return {sha256,userId:user.id,departmentId:department.id};
}

function row(page,id) {
    return page.locator('[data-audit-event-id="'+id+'"]');
}

async function showManifest(page,event, status) {
    const toggle=row(page,event.id).locator('.audit-expand');
    await toggle.focus();
    await toggle.press('Space');
    assert.equal(await toggle.getAttribute('aria-expanded'),'true');
    const button=row(page,event.id).locator('.audit-manifest');
    const wait=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/audit/'+event.id+'/manifest');
    await button.click();
    const response=await wait;
    assert.equal(response.status(),status);
    return {response, toggle, details:row(page,event.id).locator('.audit-manifest-result')};
}

async function pagination(page,admin,doctor,anonymous) {
    phase='pagination and actual audit operations';
    let first=await loadOffset(page,'auditRefreshBtn',0);
    assert(first.total>200);
    assert.equal(first.events.length,50);
    await compare(page,first);
    assert(await page.locator('#auditPrev').isDisabled());
    const real=await executeWrites(admin,doctor);
    const current=await loadOffset(page,'auditRefreshBtn',0);
    assert(current.total>first.total);
    await compare(page,current);
    first=current;
    const second=await loadOffset(page,'auditNext',50);
    await compare(page,second);
    assert.equal(second.offset,50);
    const back=await loadOffset(page,'auditPrev',0);
    await compare(page,back);
    const visited=[...back.events];
    let offset=0;
    while (!(await page.locator('#auditNext').isDisabled())) {
        offset+=50;
        const data=await loadOffset(page,'auditNext',offset);
        await compare(page,data);
        visited.push(...data.events);
    }
    assert(offset>=200);
    assert(new Set(visited.map(event=>event.id)).size>200);
    await compare(page,await loadOffset(page,'auditFirst',0));
    const actualTypes = [
        ['user_created','target_user_id',real.userId],
        ['role_changed','target_user_id',real.userId],
        ['sessions_terminated','target_user_id',real.userId],
        ['department_created','target_department_id',real.departmentId],
        ['membership_changed','target_department_id',real.departmentId],
        ['patient_updated','patient_id',patients.overdue.id],
    ];
    for (const [type,key,id] of actualTypes) {
        const event=visited.find(item=>item.event_type===type && item[key]===id &&
            (type!=='patient_updated' || item.detail==='field=complaint'));
        assert(event,type);
        await row(page,event.id).waitFor();
        await row(page,event.id).locator('.audit-expand').press('Enter');
    }
    const allText=await page.locator('#auditPanel').textContent();
    for (const value of [complaint,staffName,departmentName,newPassword,'C34.1',patients.overdue.name]) {
        assert(!allText.includes(value));
    }
    const read=visited.find(event=>event.event_type==='medical_read_release' &&
        event.actor_user_id===accountIds.doctor && event.result==='ok');
    assert(read);
    const {response,toggle,details}=await showManifest(page,read,200);
    const manifest=await response.json();
    assert.equal(manifest.snapshotSha256,real.sha256);
    assert.equal(manifest.eventId,read.id);
    assert.equal(manifest.objectCount,manifest.objects.length);
    await details.getByText('Сохраненный SHA-256: '+manifest.snapshotSha256,{exact:true}).waitFor();
    const body=await details.textContent();
    assert(!body.includes(patients.overdue.id) && (!manifest.accessContext || !body.includes(manifest.accessContext)));
    await toggle.press('Enter');
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    assert(await toggle.evaluate(node=>node===document.activeElement));
    await request(doctor,'/api/audit/'+read.id+'/manifest',403);
    await request(anonymous,'/api/audit/'+read.id+'/manifest',401);
    await request(doctor,'/api/audit',403);
    await request(anonymous,'/api/audit',401);
    const empty=await (await request(admin,'/api/audit?limit=50&offset='+(first.total+50),200)).json();
    assert.equal(empty.events.length,0);
    const missing=visited.find(event=>event.event_type==='user_created' && event.target_user_id===real.userId);
    await request(admin,'/api/audit/'+missing.id+'/manifest',404);
    checks.push('real 225+ records and exact 50-record page contents; live count growth, all pages, forward/back/first',
        'actual staff creation, role update, session termination, department and membership changes, versioned clinical edit',
        'manifest exact saved digest from released HTTP bytes; no object IDs or access context in manifest view',
        'actual manifest 404 and overrun empty response; real 401/403 for list and manifest');
    return missing.id;
}

async function faults(page,admin,doctor,missing) {
    phase='manifest 404, empty data and denial';
    const readData=await (await request(admin,'/api/audit?limit=50&offset=0',200)).json();
    await row(page,missing).locator('.audit-expand').press('Enter');
    const absent=await showManifest(page,{id:missing},404);
    await absent.details.getByText('Нет связанного манифеста.',{exact:true}).waitFor();
    const contract = {...readData,total:0,events:[]};
    const empty=route=>route.fulfill({json:contract,status:200});
    await page.route(auditList,empty);
    await loadOffset(page,'auditRefreshBtn',0);
    assert.equal(await page.locator('#auditPanel [data-audit-event-id]').count(),0);
    assert(await page.locator('#auditPrev').isDisabled());
    assert(await page.locator('#auditNext').isDisabled());
    await page.unroute(auditList,empty);
    await loadOffset(page,'auditRefreshBtn',0);
    let nonzeroRequests=0;
    const outOfRange=async route=>{
        if (new URL(route.request().url()).searchParams.get('offset')==='50') {
            nonzeroRequests++;
            await route.fulfill({json:{...contract,offset:50},status:200});
        } else {
            await route.continue();
        }
    };
    await page.route(auditList,outOfRange);
    const fallback=page.waitForResponse(response=>auditList.test(response.url()) &&
        new URL(response.url()).searchParams.get('offset')==='0');
    await page.locator('#auditNext').click();
    await fallback;
    await ready(page);
    assert.equal(nonzeroRequests,1);
    assert(await page.locator('#auditPrev').isDisabled());
    await page.unroute(auditList,outOfRange);
    // Reuse the real doctor's denial body, omitting the other user's identity
    // header to isolate same-context 403 handling in the browser.
    const forbid=async route=>{
        const response=await request(doctor,'/api/audit',403);
        await route.fulfill({status:403,json:await response.json()});
    };
    await page.route(auditList,forbid);
    const rejected=page.waitForResponse(response=>auditList.test(response.url())&&response.status()===403);
    await page.locator('#auditRefreshBtn').click();
    await rejected;
    await page.waitForFunction(()=>app.auditReadState==='denied');
    assert.equal(await page.locator('#auditPanel [data-audit-event-id]').count(),0);
    await page.unroute(auditList,forbid);
    await loadOffset(page,'auditRefreshBtn',0);
    checks.push('controlled empty and truncated pages, one fallback; manifest absence and same-context denial recovery');
}

async function obsolete(page,context,admin) {
    phase='revocation during late response';
    const session = await page.evaluate(()=>app.sessionEpoch || 0);
    let unblock, admitted;
    const wait=new Promise(resolve=>{admitted=resolve;});
    const barrier=new Promise(resolve=>{unblock=resolve;});
    const hold=async route=>{
        const response=await route.fetch();
        assert.equal(response.status(),200);
        admitted();
        await barrier;
        await route.fulfill({response});
    };
    const heldList=/\/api\/audit\?limit=50&offset=0$/;
    await page.route(heldList,hold);
    try {
        const late=page.waitForResponse(response=>auditList.test(response.url())&&response.status()===200);
        void late.catch(()=>{});
        const read=page.evaluate(()=>app.renderAudit());
        void read.catch(()=>{});
        await wait;
        phase='revocation after admission';
        await request(admin,'/api/users/'+accountIds.admin+'/terminate-sessions',200,'POST');
        await request(context,'/api/audit',401);
        phase='401 observed by the browser';
        await page.evaluate(async()=>{try {await apiGetJson('/api/audit?limit=1');} catch {}});
        await page.waitForFunction(epoch=>app.sessionExpired && app.sessionEpoch>epoch,session);
        phase='late response after epoch change';
        unblock();
        await late;
        await read;
        assert.equal(await page.locator('#auditPanel [data-audit-event-id]').count(),0);
    } finally {
        unblock();
        await page.unroute(heldList,hold);
    }
    checks.push('real expired session with changed epoch fences an already admitted late HTTP 200');
}

async function checkLayout(page) {
    phase='Stage 3.5 layout and accessibility';
    await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
    const toggle=page.locator('#auditPanel .audit-expand').first();
    if (await toggle.getAttribute('aria-expanded')!=='true') await toggle.press('Enter');
    for (const theme of ['light','dark']) {
        await page.evaluate(t=>app.applyTheme(t),theme);
        for (const width of [320,375,768,1024,1440]) {
            await page.setViewportSize({width,height:900});
            const result=await page.evaluate(async ()=>{
                const violations=(await axe.run(document,{runOnly:{type:'tag',
                    values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}})).violations.map(row=>row.id);
                return {overflow:document.documentElement.scrollWidth>innerWidth,violations};
            });
            assert.deepEqual(result,{overflow:false,violations:[]},theme+'/'+width);
            layout.push({theme,width,...result});
            if (width===375||width===1440) {
                await page.screenshot({path:path.join(process.env.OUT,'f10-audit-'+theme+'-'+width+'.png'),
                    animations:'disabled'});
            }
        }
    }
    checks.push('Stage 3.5 at five widths and both themes, keyboard toggle, document axe WCAG A/AA');
}

async function zoomViews() {
    phase='native Chromium zoom';
    const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'f10z-'));
    const extension=path.join(temporary,'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension,'manifest.json'),JSON.stringify({
        manifest_version:3,name:'Synthetic localhost zoom',version:'1.0',permissions:['tabs'],
        background:{service_worker:'worker.js'},
    }));
    fs.writeFileSync(path.join(extension,'worker.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
    let context;
    try {
        context=await chromium.launchPersistentContext(path.join(temporary,'profile'),{
            headless:true,viewport:null,reducedMotion:'reduce',ignoreDefaultArgs:['--disable-extensions'],
            executablePath:process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            args:['--window-size=1280,1000','--disable-background-networking',
                '--disable-extensions-except='+extension,'--load-extension='+extension],
        });
        await fence(context);
        await request(context,'/api/auth/login',200,'POST',{username:users.admin,password});
        const page=await openAudit(context);
        const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
        const baseline=await page.evaluate(()=>devicePixelRatio);
        for (const theme of ['light','dark']) {
            await page.evaluate(t=>app.applyTheme(t),theme);
            for (const factor of [1,2,4]) {
                const zoom=await worker.evaluate(async ({url,factor})=>{
                    const tab=(await chrome.tabs.query({})).find(item=>item.url===url);
                    await chrome.tabs.setZoom(tab.id,factor);
                    return chrome.tabs.getZoom(tab.id);
                },{url:page.url(),factor});
                assert.equal(Math.round(zoom*100),factor*100);
                await page.waitForFunction(dpr=>Math.abs(devicePixelRatio-dpr)<0.01,baseline*factor);
                const shape=await page.evaluate(()=>({
                    width:innerWidth,scale:visualViewport.scale,overflow:document.documentElement.scrollWidth>innerWidth,
                    visible:document.getElementById('auditNext').getBoundingClientRect().height>0,
                }));
                assert(shape.visible && shape.scale===1 && !shape.overflow);
                nativeZoom.push({theme,percent:factor*100,width:shape.width});
            }
        }
    } finally {
        try { if(context) await context.close(); }
        finally { fs.rmSync(temporary,{recursive:true,force:true}); }
    }
    checks.push('native browser zoom through 400% for audit in both themes');
}

async function main() {
    const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE,
        args:['--disable-background-networking']});
    try {
        const admin=await createContext(browser);
        const doctor=await createContext(browser);
        const anonymous=await createContext(browser);
        const session=await createContext(browser);
        await request(admin,'/api/auth/login',200,'POST',{username:users.admin,password});
        await request(session,'/api/auth/login',200,'POST',{username:users.admin,password});
        const page=await openAudit(session);
        const missing=await pagination(page,admin,doctor,anonymous);
        await checkLayout(page);
        await zoomViews();
        await faults(page,admin,doctor,missing);
        await obsolete(page,session,admin);
        fs.writeFileSync(path.join(process.env.OUT,'journey.local.json'),
            JSON.stringify({checks,layout,nativeZoom,browser:browser.version()}));
    } finally {
        await browser.close();
    }
}
main().catch(error=>{
    process.stderr.write(phase+' '+error.name+'\n'+(error.stack.match(/fast_track_f10\/journey\.cjs:\d+:\d+/g)||[]).join('\n')+'\n');
    process.exitCode=1;
});
