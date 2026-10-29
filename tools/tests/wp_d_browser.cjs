/* WP-D: браузерные регрессии на синтетических ответах, без рабочей базы.
 * PLAYWRIGHT_MODULE=/path/to/playwright BROWSER_EXECUTABLE=/path/to/chromium node tools/tests/wp_d_browser.cjs
 */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
    const root = process.cwd();
    const server = http.createServer((req, res) => {
        const file = path.join(root, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
        if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
        res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'image/png');
        res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'");
        res.end(fs.readFileSync(file));
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const origin = `http://127.0.0.1:${server.address().port}`;
    const user = {id: 'synthetic-a', fullName: 'Синтетический Сотрудник', roleId: 'doctor', permissions: {createPatients: true}};
    async function fresh({authenticated = true, patientsStatus = 200} = {}) {
        const context = await browser.newContext();
        const page = await context.newPage();
        page.on('pageerror', e => { throw e; });
        await page.route('**/api/**', route => {
            const p = new URL(route.request().url()).pathname;
            const data = p === '/api/auth/me' ? {authenticated, user} : p === '/api/auth/login' ? {user}
                : p === '/api/patients' ? [] : p === '/api/ai/status' ? {enabled: false} : {};
            return route.fulfill({status: p === '/api/patients' ? patientsStatus : 200, json: data});
        });
        await page.goto(origin);
        await page.waitForFunction(() => typeof app !== 'undefined');
        if (authenticated && patientsStatus === 200) await page.waitForFunction(() => app.currentView === 'patients');
        return page;
    }
    function log(name) { console.log('PASS', name); }
    try {
        let page = await fresh();
        // Сначала верхний список, затем подтверждение закрытия формы.
        await page.evaluate(() => app.openAddModal());
        await page.locator('#fio').fill('Синтетический Черновик');
        await page.locator('#diagnosisSearch').fill('C34');
        await page.locator('#diagnosisSearch').press('Escape');
        assert.equal(await page.locator('#addModal').evaluate(e => e.hidden), false);
        assert.equal(await page.locator('#diagnosisSearch').getAttribute('aria-expanded'), 'false');
        for (const action of ['escape', 'cross', 'cancel', 'backdrop']) {
            if (action === 'escape') await page.locator('#diagnosisSearch').press('Escape');
            if (action === 'cross') await page.locator('#closeAddIconBtn').click();
            if (action === 'cancel') await page.locator('#closeAddBtn').click();
            if (action === 'backdrop') await page.locator('#addModal').click({position: {x: 2, y: 2}});
            await page.locator('#confirmCancelBtn').click();
            assert.equal(await page.locator('#fio').inputValue(), 'Синтетический Черновик');
            assert.equal(await page.locator('#addModal').evaluate(e => e.hidden), false);
        }
        const leaving = page.waitForEvent('dialog');
        const reload = page.reload().catch(error => error);
        const dialog = await leaving;
        assert.equal(dialog.type(), 'beforeunload');
        await dialog.dismiss();
        const abortedReload = await reload;
        assert(abortedReload instanceof Error);
        assert.equal(await page.locator('#fio').inputValue(), 'Синтетический Черновик');
        await page.locator('#closeAddIconBtn').click();
        await page.locator('#confirmOkBtn').click();
        assert.equal(await page.locator('#addModal').evaluate(e => e.hidden), true);
        await page.evaluate(() => app.openAddModal());
        assert.equal(await page.locator('#fio').inputValue(), '');
        log('AUD-33: Escape, cross, cancel, backdrop and reload preserve draft; explicit discard closes');

        // Все сообщения проходят через настоящий сетевой слой, без сырых detail.
        for (const status of [400, 403, 404, 409, 422, 429, 500, 503]) {
            await page.route('**/api/error-probe', r => r.fulfill({status, json: {detail: 'PRIVATE_SYNTHETIC_TRACE'}}));
            const error = await page.evaluate(async () => { try { await apiFetch('/api/error-probe'); } catch (e) { return {status: e.status, message: e.message}; } });
            assert.equal(error.status, status);
            assert.match(error.message, /[А-Яа-я]/);
            assert(!error.message.includes('PRIVATE_SYNTHETIC_TRACE'));
            await page.unroute('**/api/error-probe');
        }
        for (const code of ['STALE_PATIENT_VERSION', 'STALE_CHECKLIST_ITEM', 'AUDIT_UNAVAILABLE']) {
            await page.route('**/api/code-probe', r => r.fulfill({status: code === 'AUDIT_UNAVAILABLE' ? 503 : 409, json: {code, detail: 'PRIVATE'}}));
            const result = await page.evaluate(async () => { try { await apiFetch('/api/code-probe'); } catch (e) { return e.message; } });
            assert.match(result, code === 'AUDIT_UNAVAILABLE' ? /аудита/ : /изменен/);
            await page.unroute('**/api/code-probe');
        }
        await page.route('**/api/network-probe', r => r.abort('internetdisconnected'));
        assert.match(await page.evaluate(async () => { try { await apiFetch('/api/network-probe'); } catch (e) { return e.message; } }), /Нет связи/);
        log('AUD-38: 400/403/404/409/422/429/500/503, stable codes and network failure');

        // Истекшая сессия: сохраняем DOM и исходную версию, но ничего не переотправляем.
        await page.locator('#fio').fill('Синтетический Черновик Сессии');
        let writes = 0;
        await page.route('**/api/patients/synthetic', r => { writes++; return r.fulfill({status: 401, json: {detail: 'Not authenticated'}}); });
        await page.evaluate(async () => {
            app.patients = [{id: 'synthetic', version: 7}];
            await app.updatePatientField('synthetic', 'complaint', 'Синтетический текст');
        });
        assert.equal(writes, 1);
        await page.waitForFunction(() => app.sessionExpired);
        assert.equal(await page.locator('#addModal').isVisible(), false);
        assert.match(await page.locator('#loginError').textContent(), /Сессия истекла/);
        // Неудачный вход не уничтожает черновик и не открывает закрытый интерфейс.
        await page.route('**/api/auth/login', r => r.fulfill({status: 500, json: {detail: 'PRIVATE'}}));
        await page.locator('#username').fill('synthetic-a'); await page.locator('#password').fill('synthetic'); await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => document.getElementById('loginError').textContent.includes('Внутренняя ошибка'));
        await page.unroute('**/api/auth/login');
        await page.route('**/api/auth/login', r => r.fulfill({json: {user}}));
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => !app.sessionExpired);
        assert.equal(await page.locator('#fio').inputValue(), 'Синтетический Черновик Сессии');
        assert.equal(await page.locator('#addModal').isVisible(), true);
        assert.equal(writes, 1);
        assert.equal(await page.evaluate(() => app.getPatient('synthetic').version), 7);
        assert.equal(await page.evaluate(() => sessionStorage.length), 0);
        assert.equal(await page.evaluate(() => Object.keys(localStorage).filter(k => k !== 'theme').length), 0);
        await page.unroute('**/api/patients/synthetic');
        let submitted;
        await page.route('**/api/patients/synthetic', r => { writes++; submitted = r.request().postDataJSON(); return r.fulfill({status: 409, json: {code: 'STALE_PATIENT_VERSION'}}); });
        await page.route('**/api/patients/synthetic?*', r => r.fulfill({json: {id: 'synthetic', version: 8}}));
        // Явная повторная отправка использует старую версию; серверный 409 видим.
        await page.evaluate(async () => { try { await apiPatch('/api/patients/synthetic', {field: 'complaint', value: 'Синтетический текст', expectedVersion: app.getPatient('synthetic').version}); } catch (e) { ui.toast(e.message, 'warning'); } });
        assert.equal(submitted.expectedVersion, 7); assert.equal(writes, 2);
        log('AUD-05: draft survives 401 and failed login; same owner resumes, no automatic write, WP-C version retained');

        // Новый сотрудник получает новую страницу, без DOM/кэша первого сотрудника.
        await page.evaluate(() => app.expireSession());
        await page.unroute('**/api/auth/login');
        await page.route('**/api/auth/login', r => r.fulfill({json: {user: {...user, id: 'synthetic-b'}}}));
        await page.route('**/api/auth/me', r => r.fulfill({json: {authenticated: true, user: {...user, id: 'synthetic-b'}}}));
        await page.locator('#password').fill('synthetic');
        await Promise.all([page.waitForEvent('load'), page.locator('#loginSubmit').click()]);
        await page.waitForFunction(() => app.user && app.user.id === 'synthetic-b');
        assert.equal(await page.locator('#fio').inputValue(), '');
        assert.equal(await page.locator('#addModal').evaluate(e => e.hidden), true);
        assert.equal(await page.evaluate(() => app.patients.length), 0);
        log('AUD-05: different account reload clears old draft and cache');
        await page.context().close();

        for (const status of [401, 429, 500, 503, 'network']) {
            page = await fresh({authenticated: false});
            await page.route('**/api/auth/login', r => status === 'network' ? r.abort() : r.fulfill({status, json: {detail: 'PRIVATE'}}));
            await page.locator('#username').fill('synthetic'); await page.locator('#password').fill('synthetic'); await page.locator('#loginSubmit').click();
            await page.waitForFunction(() => !document.getElementById('loginError').classList.contains('is-hidden'));
            const text = await page.locator('#loginError').textContent();
            assert.equal(text.includes('Неверный логин'), status === 401);
            assert(!text.includes('PRIVATE'));
            await page.context().close();
        }
        page = await fresh({patientsStatus: 503});
        await page.waitForFunction(() => !document.getElementById('loginError').classList.contains('is-hidden'));
        assert.equal(await page.locator('#appScreen').isVisible(), false);
        assert.match(await page.locator('#loginError').textContent(), /недоступен/);
        log('AUD-05: login failures distinguished; initial-data failure returns usable login');
        await page.context().close();

        // Настоящий AbortController с управляемыми часами браузера: тайм-аут без ожидания 30 секунд.
        page = await fresh();
        await page.clock.install();
        let hangSeen;
        const hanging = new Promise(resolve => { hangSeen = resolve; });
        await page.route('**/api/timeout-probe', () => { hangSeen(); });
        await page.evaluate(() => { window.timedOutRequest = apiFetch('/api/timeout-probe').catch(e => e.message); });
        await hanging;
        await page.clock.fastForward(30001);
        assert.match(await page.evaluate(() => window.timedOutRequest), /Время ожидания/);
        log('AUD-38: timeout uses AbortController and safe Russian message');
        await page.context().close();

        // Ответы поиска выпускаются вручную в обратном порядке, без sleep/retry.
        page = await fresh();
        const held = new Map();
        const waiters = new Map();
        const waitHeld = query => held.has(query) ? Promise.resolve() : new Promise(resolve => waiters.set(query, resolve));
        await page.route('**/api/data-quality/summary', r => r.fulfill({json: {total_issues: 1}}));
        await page.route('**/api/data-quality/issues?*', r => {
            const query = decodeURIComponent(r.request().headers()['x-search'] || '');
            held.set(query, r);
            if (waiters.has(query)) waiters.get(query)();
        });
        await page.evaluate(() => { app.quality.filters.query = 'старый'; window.oldSearch = app.renderDataQuality(); });
        await page.waitForFunction(() => document.getElementById('qualityEmpty').textContent.includes('Загрузка'));
        // waitForRequest registered before triggering to avoid races.
        await waitHeld('старый');
        const newRequested = waitHeld('новый');
        await page.evaluate(() => { app.quality.filters.query = 'новый'; window.newSearch = app.renderDataQuality(); });
        await newRequested;
        // Route handlers complete synchronously on dispatch; request event may precede routing.
        const issueResult = marker => ({issues: [{marker}], patients: [], duplicate_groups: [], rules: {marker}});
        await held.get('новый').fulfill({json: issueResult('new')}); await page.evaluate(() => window.newSearch);
        await held.get('старый').fulfill({json: issueResult('old')}); await page.evaluate(() => window.oldSearch);
        assert.equal(await page.evaluate(() => app.quality.rules.marker), 'new');
        held.clear();
        let requests = 0;
        await page.unroute('**/api/data-quality/issues?*');
        await page.route('**/api/data-quality/issues?*', r => { requests++; return r.fulfill({status: 429, json: {detail: 'Too many requests'}}); });
        await page.evaluate(() => app.showView('quality'));
        await page.waitForFunction(() => document.getElementById('qualityEmpty').textContent.includes('Слишком много запросов'));
        const before = requests;
        const searched = page.waitForResponse(r => r.url().includes('/data-quality/issues'));
        await page.locator('#qualitySearchInput').fill('синтетический поиск');
        await searched;
        assert.equal(requests, before + 1);
        const cleared = page.waitForRequest(r => r.url().includes('/data-quality/issues') && !r.headers()['x-search']);
        await page.locator('#qualitySearchInput').fill(''); await cleared;
        log('AUD-32: reverse responses ignored, debounce, 429 and clear-search');
        await page.context().close();
        console.log('All WP-D browser checks passed');
    } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
