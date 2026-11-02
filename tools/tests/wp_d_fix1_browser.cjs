/* FIX1: настоящий backend и две вкладки одного профиля. Только синтетический стенд. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const origin = process.env.WP_D_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('Set WP_D_SYNTHETIC_ORIGIN to an isolated synthetic server');
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const password = 'synthetic-browser-password';
    const suffix = Date.now().toString(36);
    async function api(context, method, path, data, extra = {}) {
        const csrf = (await context.cookies()).find(c => c.name === 'clinic_csrf')?.value;
        const response = await context.request.fetch(origin + path, {method, data, headers: {...(csrf ? {'X-CSRF-Token': csrf} : {}), ...extra}});
        assert(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
        return response.json();
    }
    async function loginUI(page, username) {
        await page.locator('#username').fill(username); await page.locator('#password').fill(password);
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => app.appReady && !app.sessionExpired && !document.body.classList.contains('session-locked'));
    }
    async function pageFor(context, username, noChannel = false) {
        const page = await context.newPage();
        if (noChannel) await page.addInitScript(() => { window.BroadcastChannel = undefined; });
        await page.goto(origin);
        await page.waitForFunction(() => typeof app !== 'undefined');
        if (!await page.evaluate(() => !!app.user)) await loginUI(page, username);
        else await page.waitForFunction(() => app.appReady);
        return page;
    }
    async function openComments(page, id) {
        await page.evaluate(async id => { await app.openPatient(id); app.currentTab = 'complaints'; app.renderTabs(); app.renderTabPanel(app.getPatient(id)); }, id);
    }
    async function expireWithDraft(page, admin, user, value) {
        const field = page.getByRole('textbox', {name: 'Комментарии', exact: true});
        await field.fill(value);
        await api(admin, 'POST', `/api/users/${user.id}/terminate-sessions`, {});
        await page.locator('#patientPanels textarea[aria-label="Комментарии"]').evaluate(node => node.blur());
        await page.waitForFunction(() => app.sessionExpired);
    }
    try {
        const admin = await browser.newContext();
        await api(admin, 'POST', '/api/auth/login', {username: 'synthetic.browser', password});
        const first = await api(admin, 'POST', '/api/departments', {name: `Синтетическое первое ${suffix}`});
        const second = await api(admin, 'POST', '/api/departments', {name: `Синтетическое второе ${suffix}`});
        const createUser = (name, roleId, departmentIds) => api(admin, 'POST', '/api/users', {username: `fix1.${name}.${suffix}`, fullName: `Синтетический ${name}`, roleId, departmentIds, password, mustChangePassword: false});
        const a = await createUser('a', 'department_head', [first.id]);
        const doc = await createUser('doctor', 'doctor', [first.id]);
        const b = await createUser('b', 'doctor', [second.id]);
        const card = (name, doctor) => api(admin, 'POST', '/api/patients', {name, priority: 'normal', stage: 'exam', diagnosis: 'C34.1', nosology: 'lung34', head: 'Синтетический заведующий', treatingDoctorUserId: doctor.id, complaint: 'Начальный синтетический комментарий'});
        const pA = await card(`Синтетическая закрытая карточка ${suffix}`, doc);
        const pB = await card(`Синтетическая карточка Б ${suffix}`, b);

        // Та же личность и прежние права: черновик и старая версия остаются, записи нет.
        let context = await browser.newContext(); let page = await pageFor(context, a.username);
        await openComments(page, pA.id); await expireWithDraft(page, admin, a, 'Синтетический неизменный доступ');
        await page.route('**/api/patients?*', route => route.fulfill({status: 503, json: {detail: 'Synthetic unavailable'}}), {times: 1});
        await page.locator('#password').fill(password); await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => document.getElementById('loginError').textContent.includes('недоступен'));
        assert.equal(await page.evaluate(() => app.sessionExpired), true);
        assert.equal(await page.locator('#patientPanels textarea').inputValue(), 'Синтетический неизменный доступ');
        await loginUI(page, a.username);
        assert.equal(await page.getByRole('textbox', {name: 'Комментарии', exact: true}).inputValue(), pA.complaint);
        const unchangedDraft = await page.evaluate(id => app.sessionFieldDrafts.get(id + ':complaint'), pA.id);
        assert.equal(unchangedDraft.body.value, 'Синтетический неизменный доступ');
        assert.equal(unchangedDraft.body.expectedVersion, pA.version);
        assert.equal(await page.evaluate(id => app.getPatient(id).version, pA.id), pA.version);
        assert.equal((await api(admin, 'GET', '/api/patients/' + pA.id)).complaint, pA.complaint);
        console.log('PASS same id / unchanged rights: original version and draft, no write');

        await page.evaluate(() => {
            window.promptCompleted = false;
            window.pendingDateDraft = ui.promptDateTime({title: 'Синтетическая дата', text: 'Синтетический черновик даты', date: '2026-10-01', time: '09:15'})
                .then(result => { window.promptCompleted = true; return result; });
        });
        await page.locator('#promptTime').fill('10:25');
        await api(admin, 'POST', `/api/users/${a.id}/terminate-sessions`, {});
        await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) {} });
        await page.waitForFunction(() => app.sessionExpired); await loginUI(page, a.username);
        assert.equal(await page.locator('#promptTime').inputValue(), '10:25');
        assert.equal(await page.evaluate(() => window.promptCompleted), false);
        await page.locator('#promptCancelBtn').click();
        assert.equal(await page.evaluate(() => window.pendingDateDraft), null);
        console.log('PASS permitted clinical date prompt survives reauthentication without automatic confirmation');
        await page.evaluate(name => { app.ai.messages = [{role: 'assistant', content: name}]; }, pA.name);
        await page.evaluate(() => { window.revokedDateDraft = ui.promptDateTime({title: 'Отзываемый черновик', text: 'Синтетическая дата', date: '2026-10-01', time: '11:30'}); });
        // Смена отдела: прежде доступная карточка, DOM и черновик не должны вернуться.
        await api(admin, 'PATCH', '/api/users/' + a.id, {departmentIds: [second.id]});
        await api(admin, 'POST', `/api/users/${a.id}/terminate-sessions`, {});
        await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) {} });
        await page.waitForFunction(() => app.sessionExpired); await loginUI(page, a.username);
        assert.equal(await page.evaluate(id => !!app.getPatient(id), pA.id), false);
        assert(!(await page.locator('body').textContent()).includes(pA.name));
        assert.equal(await page.evaluate(id => [...(app.sessionFieldDrafts || [])].some(([, d]) => d.patientId === id), pA.id), false);
        await page.evaluate(id=>app.openPatient(id),pB.id);
        assert.equal(await page.evaluate(id => !!app.getPatient(id), pB.id), true);
        assert.equal(await page.evaluate(() => app.ai.messages.length), 0);
        assert.equal(await page.locator('#promptModal').evaluate(node => node.hidden), true);
        assert.equal(await page.evaluate(() => window.revokedDateDraft), null);
        console.log('PASS department revoked: no old card, DOM or draft; new scope loaded');
        await context.close();

        // Смена роли с потерей права чтения.
        await api(admin, 'PATCH', '/api/users/' + a.id, {roleId: 'department_head', departmentIds: [first.id]});
        context = await browser.newContext(); page = await pageFor(context, a.username); await openComments(page, pA.id);
        await expireWithDraft(page, admin, a, 'Синтетический отозванный черновик');
        await api(admin, 'PATCH', '/api/users/' + a.id, {roleId: 'doctor', departmentIds: [first.id]});
        await loginUI(page, a.username);
        assert(!(await page.locator('body').textContent()).includes(pA.name));
        assert.equal(await page.evaluate(() => app.user.roleId), 'doctor');
        console.log('PASS role revoked: inaccessible medical cache and draft removed');
        await context.close();

        // Меняется область, но конкретная карточка по-прежнему доступна: черновик сохраняется.
        await api(admin, 'PATCH', '/api/users/' + a.id, {roleId: 'department_head', departmentIds: [first.id]});
        context = await browser.newContext(); page = await pageFor(context, a.username); await openComments(page, pA.id);
        await expireWithDraft(page, admin, a, 'Синтетический сохраненный черновик');
        await api(admin, 'PATCH', '/api/users/' + a.id, {departmentIds: [first.id, second.id]});
        await loginUI(page, a.username);
        const draft = await page.evaluate(id => [...app.sessionFieldDrafts.values()].find(d => d.patientId === id), pA.id);
        assert.equal(draft.body.value, 'Синтетический сохраненный черновик'); assert.equal(draft.body.expectedVersion, pA.version);
        await api(admin, 'PATCH', '/api/patients/' + pA.id, {field: 'complaint', value: 'Внешняя синтетическая правка', expectedVersion: pA.version});
        const rejected = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/patients/' + pA.id));
        await page.getByRole('button', {name: 'Сохранить правку', exact: true}).click(); assert.equal((await rejected).status(), 409);
        assert.equal((await api(admin, 'GET', '/api/patients/' + pA.id)).complaint, 'Внешняя синтетическая правка');
        console.log('PASS changed scope / retained card: draft keeps expectedVersion; explicit write gets 409');
        await context.close();

        context = await browser.newContext(); page = await pageFor(context, a.username); await openComments(page, pA.id);
        await expireWithDraft(page, admin, a, 'Синтетический черновик без права записи');
        await api(admin, 'PATCH', '/api/users/' + a.id, {roleId: 'deputy_chief_oncology', departmentIds: []});
        await loginUI(page, a.username);
        assert.equal(await page.evaluate(() => app.isReadOnly()), true);
        assert.equal(await page.getByRole('button', {name: 'Сохранить правку', exact: true}).isDisabled(), true);
        assert.equal(await page.getByRole('textbox', {name: 'Комментарии', exact: true}).isDisabled(), true);
        assert.equal(await page.evaluate(id => [...app.sessionFieldDrafts.values()].find(d => d.patientId === id).body.value, pA.id), 'Синтетический черновик без права записи');
        console.log('PASS read retained / write revoked: draft readable, old write controls disabled');
        await context.close();

        // Общая cookie двух вкладок: broadcast закрывает старый интерфейс еще при начале входа.
        context = await browser.newContext(); const tabA = await pageFor(context, 'synthetic.browser');
        await openComments(tabA, pA.id);
        const tabB = await context.newPage(); await tabB.goto(origin); await tabB.waitForFunction(() => app.appReady);
        const beforeSwitch = await api(admin, 'GET', '/api/patients/' + pA.id);
        await tabA.evaluate(p => app.keepSessionFieldDraft(p.id, {field: 'complaint', value: 'СИНТЕТИЧЕСКИЙ ЧЕРНОВИК А', expectedVersion: p.version}), beforeSwitch);
        assert.equal(await tabA.evaluate(() => app.sessionFieldDrafts.size), 1);
        await Promise.all([tabB.waitForEvent('load'), tabB.locator('#logoutBtn').click()]);
        await tabA.waitForFunction(() => app.sessionExpired || !app.user);
        await loginUI(tabB, b.username);
        await tabA.waitForFunction(id => typeof app !== 'undefined' && app.user?.id === id && app.appReady, b.id);
        assert(!(await tabA.locator('body').textContent()).includes(pA.name));
        assert.equal(await tabA.evaluate(() => app.sessionFieldDrafts?.size || 0), 0);
        assert(!(await tabA.locator('body').textContent()).includes('СИНТЕТИЧЕСКИЙ ЧЕРНОВИК А'));
        assert.equal((await api(admin, 'GET', '/api/patients/' + pA.id)).complaint, beforeSwitch.complaint);
        console.log('PASS shared tabs: old UI/cache discarded, server identity B shown in a fresh workspace');
        await context.close();

        // Даже без BroadcastChannel серверный барьер запрещает использовать cookie Б как А.
        context = await browser.newContext(); const old = await pageFor(context, 'synthetic.browser', true);
        await openComments(old, pA.id);
        const switcher = await context.newPage(); await switcher.goto(origin); await switcher.waitForFunction(() => app.appReady);
        await Promise.all([switcher.waitForEvent('load'), switcher.locator('#logoutBtn').click()]);
        await loginUI(switcher, b.username);
        const denied = old.waitForResponse(r => (new URL(r.url()).pathname==='/api/patients') && r.status() === 409);
        const action = old.evaluate(async p => { try { await app.loadPatients(); await apiPatch('/api/patients/' + p.id, {field: 'complaint', value: 'Нельзя сохранить под Б', expectedVersion: p.version}); } catch (e) {} }, pB).catch(e => { if (!e.message.includes('Execution context was destroyed')) throw e; });
        await denied; await action;
        await old.waitForFunction(id => typeof app !== 'undefined' && app.user?.id === id && app.appReady, b.id);
        assert.equal((await api(admin, 'GET', '/api/patients/' + pB.id)).complaint, pB.complaint);
        console.log('PASS no broadcast: unexpected identity read rejected; chained medical write never runs');
        await context.close();

        context = await browser.newContext(); const writer = await pageFor(context, 'synthetic.browser', true);
        const other = await context.newPage(); await other.goto(origin); await other.waitForFunction(() => app.appReady);
        await Promise.all([other.waitForEvent('load'), other.locator('#logoutBtn').click()]); await loginUI(other, b.username);
        const deniedWrite = writer.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/patients/' + pB.id));
        const writing = writer.evaluate(async p => {
            try { await apiPatch('/api/patients/' + p.id, {field: 'complaint', value: 'Неожиданная запись под Б', expectedVersion: p.version}); } catch (e) {}
        }, pB).catch(e => { if (!e.message.includes('Execution context was destroyed')) throw e; });
        assert.equal((await deniedWrite).status(), 409); await writing;
        assert.equal((await api(admin, 'GET', '/api/patients/' + pB.id)).complaint, pB.complaint);
        console.log('PASS no broadcast / direct write: server rejects A headers with B cookie before mutation');
        await context.close();

        // Старый ответ, полученный сервером под А, выпускаем только после входа Б.
        context = await browser.newContext(); const heldPage = await pageFor(context, 'synthetic.browser', true);
        let release, seen;
        const received = new Promise(resolve => { seen = resolve; });
        await heldPage.route('**/api/patients?*', async route => {
            const response = await route.fetch(); seen();
            await new Promise(resolve => { release = resolve; });
            await route.fulfill({response});
        }, {times: 1});
        await heldPage.evaluate(() => { window.oldRead = app.loadPatients().catch(() => {}); }); await received;
        const changer = await context.newPage(); await changer.goto(origin); await changer.waitForFunction(() => app.appReady);
        await Promise.all([changer.waitForEvent('load'), changer.locator('#logoutBtn').click()]); await loginUI(changer, b.username);
        release();
        await heldPage.waitForFunction(id => typeof app !== 'undefined' && app.user?.id === id && app.appReady, b.id);
        assert(!(await heldPage.locator('body').textContent()).includes(pA.name));
        console.log('PASS in-flight old read: identity recheck discards its result after switching cookie');
        await context.close(); await admin.close();
        console.log('All WP-D FIX1 live browser checks passed');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
