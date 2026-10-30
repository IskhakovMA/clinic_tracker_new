/* WP-F FIX2: real FastAPI, synthetic accounts, Chromium; no medical web storage. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const origin = process.env.WP_D_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('Set WP_D_SYNTHETIC_ORIGIN to an isolated synthetic server');
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const password = 'synthetic-browser-password';
    async function api(context, method, path, data) {
        const csrf = (await context.cookies()).find(c => c.name === 'clinic_csrf')?.value;
        const r = await context.request.fetch(origin + path, {method, data, headers: csrf ? {'X-CSRF-Token': csrf} : {}});
        assert(r.ok(), `${method} ${path}: ${r.status()}`); return r.json();
    }
    try {
        const admin = await browser.newContext();
        await api(admin, 'POST', '/api/auth/login', {username: 'synthetic.browser', password});
        const suffix = Date.now().toString(36);
        const dept = await api(admin, 'POST', '/api/departments', {name: `Синтетическое FIX2 ${suffix}`});
        const create = (name, roleId) => api(admin, 'POST', '/api/users', {username: `fix2.${name}.${suffix}`, fullName: `Синтетический ${name}`, roleId, departmentIds: [dept.id], password, mustChangePassword: false});
        const head = await create('head', 'department_head'); const doctor = await create('doctor', 'doctor');
        const context = await browser.newContext(); const page = await context.newPage();
        page.on('pageerror', error => { throw error; });
        async function login() {
            await page.locator('#username').fill(head.username); await page.locator('#password').fill(password);
            await page.locator('#loginSubmit').click(); await page.waitForFunction(() => app.appReady && !app.sessionExpired);
        }
        await page.goto(origin); await login();
        assert.equal(await page.evaluate(() => app.can('importData')), true);
        assert.equal(await page.locator('#triggerImportBtn').evaluate(e => e.classList.contains('is-hidden')), false);
        const before = await api(admin, 'GET', '/api/patients');
        await api(admin, 'PATCH', '/api/users/' + head.id, {departmentIds: []});
        await api(admin, 'POST', '/api/users/' + head.id + '/terminate-sessions', {});
        await page.evaluate(() => apiGetJson('/api/patients').catch(() => null));
        await page.waitForFunction(() => app.sessionExpired); await login();
        assert.equal(await page.evaluate(() => app.user.roleId), 'department_head');
        assert.equal(await page.evaluate(() => app.can('importData')), false);
        assert.equal(await page.locator('#triggerImportBtn').evaluate(e => e.classList.contains('is-hidden')), true);
        const results = await page.evaluate(async doctorId => {
            const me = await (await fetch('/api/auth/me')).json();
            if (!me.user?.id || !me.user?.accessContext) throw new Error('Missing authenticated identity/access context');
            const csrf = document.cookie.split('; ').find(c => c.startsWith('clinic_csrf=')).split('=').slice(1).join('=');
            const headers = {'Content-Type': 'application/json', 'X-CSRF-Token': decodeURIComponent(csrf), 'X-Clinic-User': me.user.id, 'X-Clinic-Access': me.user.accessContext};
            const raw = JSON.stringify({schemaVersion: 1, patients: [{name: 'Синтетик Закрытый Browser FIX2', age: 60, priority: 'normal', stage: 'exam', diagnosis: 'C34.1', nosology: 'lung34', head: 'Синтетический', treatingDoctorUserId: doctorId}]});
            const hash = [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw)))].map(v => v.toString(16).padStart(2, '0')).join('');
            const out = [];
            for (const path of ['/api/import/preview', '/api/migrate/localstorage/preview', '/api/import/commit', '/api/migrate/localstorage/commit']) {
                for (const key of path.endsWith('commit') ? [false, true] : [false]) {
                    const body = path.endsWith('preview') ? {raw} : {raw, mode: 'add', unsignedConfirmation: {confirmed: true, fileSha256: hash, mode: 'add'}};
                    const r = await fetch(path, {method: 'POST', headers: {...headers, ...(key ? {'Idempotency-Key': crypto.randomUUID()} : {})}, body: JSON.stringify(body)});
                    out.push({path, key, status: r.status, body: await r.json()});
                }
            }
            return out;
        }, doctor.id);
        assert.equal(results.length, 6);
        for (const r of results) { assert.equal(r.status, 403, JSON.stringify(r)); assert.deepEqual(r.body, {detail: 'Forbidden'}); }
        assert.deepEqual(await api(admin, 'GET', '/api/patients'), before);
        console.log('PASS head with department: import UI enabled; last department revoked: fresh UI disables import');
        console.log('PASS preview/commit import/localStorage, with/without key: 403, generic response, no patient writes');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
