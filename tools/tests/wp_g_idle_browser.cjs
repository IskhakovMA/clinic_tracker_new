/* Actual FastAPI + controlled synthetic UTC. No production clock endpoint. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const origin = process.env.WP_D_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('isolated WP-G synthetic origin required');
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const password = 'synthetic-browser-password';
    async function api(context, method, path, data) {
        const csrf = (await context.cookies()).find(c => c.name === 'clinic_csrf')?.value;
        const r = await context.request.fetch(origin + path, {method, data, headers: csrf ? {'X-CSRF-Token': csrf} : {}});
        assert(r.ok(), `${path}: ${r.status()} ${await r.text()}`); return r.json();
    }
    async function login(page) {
        await page.locator('#username').fill('synthetic.browser'); await page.locator('#password').fill(password);
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => app.appReady && !app.sessionExpired && !document.body.classList.contains('session-locked'));
    }
    try {
        const admin=await browser.newContext(); await api(admin,'POST','/api/auth/login',{username:'synthetic.browser',password});
        const patient=await api(admin,'POST','/api/patients',{name:'Синтетический idle пациент',diagnosis:'C34.1',priority:'normal',stage:'exam',head:'Синтетический Заведующий',complaint:'Исходное значение'});
        const context=await browser.newContext(), page=await context.newPage(); await page.goto(origin);await login(page);
        await page.evaluate(async id => {await app.openPatient(id);app.currentTab='complaints';app.renderTabs();app.renderTabPanel(app.getPatient(id));},patient.id);
        let writes=0;
        page.on('request',r=>{if(r.method()==='PATCH' && r.url().includes('/api/patients/')) writes++;});
        await api(admin,'POST','/synthetic/clock',{seconds:14*60});
        await page.evaluate(()=>{app.lastActivitySent=-Infinity;});
        const heartbeat=page.waitForResponse(r=>r.url().endsWith('/api/auth/activity') && r.status()===200);
        await page.getByRole('textbox',{name:'Комментарии',exact:true}).fill('Несохраненный idle черновик');
        await heartbeat;
        await api(admin,'POST','/synthetic/clock',{seconds:28*60});
        assert.equal(await page.evaluate(async()=>{const r=await apiGetJson('/api/internal-notifications/summary');return !!r;}),true);
        await api(admin,'POST','/synthetic/clock',{seconds:29*60});
        await page.evaluate(async()=>{try {await apiGetJson('/api/patients');}catch(e){if(e.status!==401)throw e;}});
        await page.waitForFunction(()=>app.sessionExpired);
        assert.equal(writes,0);
        const draft=await page.evaluate(id=>app.sessionFieldDrafts.get(id+':complaint'),patient.id);
        assert.equal(draft.body.value,'Несохраненный idle черновик');assert.equal(draft.body.expectedVersion,patient.version);
        await api(admin,'POST','/api/auth/login',{username:'synthetic.browser',password});
        const fresh=await api(admin,'PATCH','/api/patients/'+patient.id,{field:'diagnosis',value:'C34.9',expectedVersion:patient.version});
        await login(page);
        assert.equal(await page.evaluate(id=>app.getPatient(id).diagnosis,patient.id),'C34.9');
        assert.equal(await page.evaluate(id=>app.getPatient(id).version,patient.id),fresh.version);
        assert.equal(writes,0);
        const rejected=page.waitForResponse(r=>r.request().method()==='PATCH' && r.url().endsWith('/api/patients/'+patient.id));
        await page.getByRole('button',{name:'Сохранить правку',exact:true}).click();
        assert.equal((await rejected).status(),409);
        assert.equal((await api(admin,'GET','/api/patients/'+patient.id)).diagnosis,'C34.9');
        console.log('PASS trusted form input renews server activity; polling does not; exact idle boundary locks UI; draft survives with original version; fresh reauth and manual 409');
        await context.close();await admin.close();
    } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
