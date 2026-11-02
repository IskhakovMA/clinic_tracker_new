/* FIX2: fresh server records and separate unsaved drafts. Isolated FastAPI only. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const origin = process.env.WP_D_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('Set WP_D_SYNTHETIC_ORIGIN to an isolated synthetic server');
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const password = 'synthetic-browser-password', suffix = Date.now().toString(36);
    async function api(context, method, path, data) {
        const csrf = (await context.cookies()).find(c => c.name === 'clinic_csrf')?.value;
        const response = await context.request.fetch(origin + path, {method, data, headers: csrf ? {'X-CSRF-Token': csrf} : {}});
        assert(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
        return response.json();
    }
    async function login(page, username) {
        await page.locator('#username').fill(username); await page.locator('#password').fill(password);
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => app.appReady && !app.sessionExpired && !document.body.classList.contains('session-locked'));
    }
    async function tab(page, name) {
        await page.getByRole('tab', {name, exact: true}).click();
    }
    try {
        const admin = await browser.newContext();
        await api(admin, 'POST', '/api/auth/login', {username: 'synthetic.browser', password});
        const doctor = await api(admin, 'POST', '/api/users', {username: 'fix2.doctor.' + suffix, fullName: 'Синтетический Врач FIX2', roleId: 'doctor', departmentIds: [], password, mustChangePassword: false});
        for (const mode of ['clean', 'unblurred', 'failed401']) {
            const original = await api(admin, 'POST', '/api/patients', {name: `Синтетический FIX2 ${mode} ${suffix}`, diagnosis: 'C34.1', priority: 'normal', stage: 'exam', nosology: 'lung34', head: 'Синтетический Заведующий', treatingDoctorUserId: doctor.id, complaint: 'Серверный комментарий N'});
            const context = await browser.newContext(), page = await context.newPage();
            await page.goto(origin); await login(page, doctor.username);
            await page.evaluate(id => app.openPatient(id), original.id);
            await tab(page, mode === 'clean' ? 'Данные' : 'Комментарии');
            if (mode !== 'clean') await page.getByRole('textbox', {name: 'Комментарии', exact: true}).fill('Несохраненный комментарий FIX2');
            // Независимая сессия администратора изменяет клинические данные и маршрут.
            let fresh = await api(admin, 'PATCH', '/api/patients/' + original.id, {field: 'diagnosis', value: 'C34.9', expectedVersion: original.version});
            fresh = await api(admin, 'PATCH', '/api/patients/' + original.id, {field: 'phone', value: '+70000000009', expectedVersion: fresh.version});
            const occ = fresh.history.at(-1);
            fresh = await api(admin, 'POST', `/api/patients/${original.id}/stage`, {newStage: 'appointment', expectedStage: fresh.stage, expectedHistoryEntryId: occ.id});
            fresh = await api(admin, 'POST', `/api/patients/${original.id}/history/${fresh.history.length - 1}/events`, {expectedEventIds: []});
            const event = fresh.history.at(-1).events.at(-1);
            fresh = await api(admin, 'PATCH', `/api/patients/${original.id}/events/${event.id}`, {field: 'date', value: '2099-01-09', version: event.version});
            await api(admin, 'POST', `/api/users/${doctor.id}/terminate-sessions`, {});
            if (mode === 'failed401') await page.locator('#patientPanels textarea').evaluate(node => node.blur());
            else await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) { if (e.status !== 401) throw e; } });
            await page.waitForFunction(() => app.sessionExpired);
            let writes = 0;
            page.on('request', request => { if (request.method() !== 'GET' && /\/api\/patients(?:\/|$)/.test(new URL(request.url()).pathname)) writes++; });
            await login(page, doctor.username);
            const cached = await page.evaluate(id => app.getPatient(id), original.id);
            console.log(JSON.stringify({mode, originalVersion: original.version, serverVersion: fresh.version, cachedVersion: cached.version, serverDiagnosis: fresh.diagnosis, cachedDiagnosis: cached.diagnosis, serverStage: fresh.stage, cachedStage: cached.stage}));
            assert.deepEqual(cached, fresh, 'cache must contain the authoritative server record');
            if (mode !== 'clean') assert.equal(await page.getByRole('textbox', {name: 'Комментарии', exact: true}).inputValue(), fresh.complaint);
            assert.equal(await page.evaluate(() => app.sessionFieldDrafts.size), mode === 'clean' ? 0 : 1);
            if (mode !== 'clean') {
                const draft = await page.evaluate(id => app.sessionFieldDrafts.get(id + ':complaint'), original.id);
                assert.equal(draft.body.value, 'Несохраненный комментарий FIX2'); assert.equal(draft.body.expectedVersion, original.version);
                assert.match(await page.locator('#sessionFieldDrafts').textContent(), /Несохраненн/);
                assert.match(await page.locator('#sessionFieldDrafts').textContent(), /Версия.*сервера/);
            }
            await tab(page, 'Данные');
            const diagnosis = page.locator('#patientPanels .field').filter({has: page.locator('.field__label', {hasText: /^Диагноз$/})}).locator('select').last();
            assert.equal(await diagnosis.inputValue(), 'C34.9');
            assert.equal(await page.locator('#patientPanels input[type="tel"]').inputValue(), '+70000000009');
            await tab(page, 'События и даты');
            assert((await page.locator('#patientPanels input[type="date"]').evaluateAll(nodes => nodes.map(node => node.value))).includes('2099-01-09'));
            assert.match(await page.locator('#patientPanels').textContent(), /Записан на прием/);
            assert.equal(await page.evaluate(id => app.getPatient(id).history.at(-1).events.at(-1).date, original.id), '2099-01-09');
            assert.equal(writes, 0, 'reauthentication must not write medical data');
            if (mode !== 'clean') {
                const rejected = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith('/api/patients/' + original.id));
                await page.getByRole('button', {name: 'Сохранить правку', exact: true}).click();
                const response = await rejected;
                assert.equal(response.status(), 409); assert.equal((await response.json()).code, 'STALE_PATIENT_VERSION');
                assert.equal(response.request().postDataJSON().expectedVersion, original.version);
                assert.deepEqual(await api(admin, 'GET', '/api/patients/' + original.id), fresh);
                assert.equal(await page.evaluate(() => app.sessionFieldDrafts.size), 1);
            }
            console.log(`PASS ${mode}: fresh diagnosis/version/phone/route/event; separate original-version draft; no automatic write`);
            await context.close();
        }
        {
            // Незатронутая сервером карточка: только ручное сохранение черновика увеличивает версию.
            const p = await api(admin, 'POST', '/api/patients', {name: 'Синтетический FIX2 без конфликта ' + suffix, diagnosis: 'C34.1', priority: 'normal', stage: 'exam', head: 'Синтетический Заведующий', treatingDoctorUserId: doctor.id, complaint: 'Исходный комментарий'});
            const context = await browser.newContext(), page = await context.newPage(); await page.goto(origin); await login(page, doctor.username);
            await page.evaluate(id => app.openPatient(id), p.id); await tab(page, 'Комментарии');
            await page.getByRole('textbox', {name: 'Комментарии', exact: true}).fill('Ручной черновик без конфликта');
            await api(admin, 'POST', `/api/users/${doctor.id}/terminate-sessions`, {});
            await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) {} });
            await page.waitForFunction(() => app.sessionExpired); await login(page, doctor.username);
            assert.deepEqual(await page.evaluate(id => app.getPatient(id), p.id), p);
            assert.equal(await page.getByRole('textbox', {name: 'Комментарии', exact: true}).inputValue(), p.complaint);
            assert.deepEqual(await api(admin, 'GET', '/api/patients/' + p.id), p);
            await page.getByRole('button', {name: 'Сохранить правку', exact: true}).click();
            await page.waitForFunction(() => app.sessionFieldDrafts.size === 0);
            const saved = await api(admin, 'GET', '/api/patients/' + p.id);
            assert.equal(saved.complaint, 'Ручной черновик без конфликта'); assert.equal(saved.version, p.version + 1);
            assert.equal(await page.getByRole('textbox', {name: 'Комментарии', exact: true}).inputValue(), saved.complaint);
            console.log('PASS unchanged record: separate draft saved only by manual action with original version');
            await context.close();
        }
        for (const changedEvent of [false, true]) {
            let p = await api(admin, 'POST', '/api/patients', {name: 'Синтетическая клиническая дата FIX2 ' + suffix, diagnosis: 'C34.1', priority: 'normal', stage: 'exam', head: 'Синтетический Заведующий', treatingDoctorUserId: doctor.id});
            p = await api(admin, 'POST', `/api/patients/${p.id}/history/0/events`, {expectedEventIds: []});
            let event = p.history[0].events[0];
            p = await api(admin, 'PATCH', `/api/patients/${p.id}/events/${event.id}`, {field: 'date', value: '2026-10-01', version: event.version});
            const context = await browser.newContext(), page = await context.newPage(); await page.goto(origin); await login(page, doctor.username);
            await page.evaluate(async id => { await app.openPatient(id); window.clinicalDone = false; window.clinicalChange = app.changeStage(id, 'appointment').then(result => { window.clinicalDone = true; return result; }); }, p.id);
            await page.locator('#promptDate').fill('2026-10-02'); await page.locator('#promptTime').fill('10:25');
            await api(admin, 'POST', `/api/users/${doctor.id}/terminate-sessions`, {});
            await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) {} });
            await page.waitForFunction(() => app.sessionExpired);
            if (changedEvent) {
                event = p.history[0].events[0];
                p = await api(admin, 'PATCH', `/api/patients/${p.id}/events/${event.id}`, {field: 'date', value: '2026-10-03', version: event.version});
            }
            await login(page, doctor.username);
            assert.equal(await page.locator('#promptDate').inputValue(), '2026-10-02');
            assert.equal(await page.locator('#promptTime').inputValue(), '10:25');
            assert.equal(await page.evaluate(() => window.clinicalDone), false);
            assert.deepEqual(await api(admin, 'GET', '/api/patients/' + p.id), p);
            assert.deepEqual(await page.evaluate(id => app.getPatient(id), p.id), p);
            const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/api/patients/${p.id}/stage`));
            await page.locator('#promptOkBtn').click();
            assert.equal((await response).status(), changedEvent ? 409 : 200);
            await page.waitForFunction(() => window.clinicalDone);
            const final = await api(admin, 'GET', '/api/patients/' + p.id);
            if (changedEvent) assert.deepEqual(final, p);
            else { assert.equal(final.stage, 'appointment'); assert.equal(final.history[0].events[0].actualDate, '2026-10-02'); assert.equal(final.history[0].events[0].actualTime, '10:25'); }
            console.log(`PASS clinical date: retained without confirmation; manual action ${changedEvent ? 'gets real 409 with old event version' : 'persists original input'}`);
            await context.close();
        }
        {
            const context = await browser.newContext(), page = await context.newPage(); await page.goto(origin); await login(page, doctor.username);
            await page.evaluate(() => app.openAddModal());
            await page.locator('#fio').fill('Синтетический новый пациент FIX2');
            await api(admin, 'POST', `/api/users/${doctor.id}/terminate-sessions`, {});
            await page.evaluate(async () => { try { await apiGetJson('/api/patients'); } catch (e) {} });
            await page.waitForFunction(() => app.sessionExpired); await login(page, doctor.username);
            assert.equal(await page.locator('#fio').inputValue(), 'Синтетический новый пациент FIX2');
            assert.equal(await page.locator('#addModal').isVisible(), true);
            assert.equal(await page.evaluate(() => sessionStorage.length), 0);
            assert.deepEqual(await page.evaluate(() => Object.keys(localStorage).filter(key => key !== 'theme')), []);
            console.log('PASS permitted creation form retained in memory; no medical data in web storage');
            await context.close();
        }
        await admin.close();
        console.log('All WP-D FIX2 live browser checks passed');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
