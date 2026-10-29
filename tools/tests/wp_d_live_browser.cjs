/* Запуск только против изолированной синтетической базы. Учетные записи создает QA runner. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    const page = await browser.newPage();
    const origin = process.env.WP_D_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('WP_D_SYNTHETIC_ORIGIN must point to an isolated synthetic server');
    try {
        await page.goto(origin);
        await page.locator('#username').fill('synthetic.browser');
        await page.locator('#password').fill('synthetic-browser-password');
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => app.appReady);
        const result = await page.evaluate(async () => {
            const marker = 'СИНТЕТИЧЕСКИЙ_ДИАГНОЗ_ТЕЛЕФОН_ФИО';
            const results = [];
            for (const path of ['/api/patients', '/api/auth/login', '/api/import/commit', '/api/users', '/api/departments']) {
                const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': getCookie('clinic_csrf')}, body: JSON.stringify({[marker]: marker})});
                const text = await response.text();
                results.push({path, status: response.status, leaks: text.includes(marker), body: JSON.parse(text)});
            }
            return results;
        });
        for (const r of result) {
            assert.equal(r.status, 422); assert.equal(r.leaks, false); assert.equal(r.body.code, 'VALIDATION_ERROR');
            assert(r.body.detail.every(e => !('input' in e) && !('ctx' in e)));
        }
        console.log('PASS AUD-24 live browser: five real API routes, no values in validation responses');
        const patient = await page.evaluate(() => apiPost('/api/patients', {name: 'Синтетический Пациент Браузера', priority: 'normal', stage: 'exam', diagnosis: 'C34.1', nosology: 'lung34', head: 'Синтетический Заведующий', complaint: 'Начальное значение'}));
        await page.evaluate(async patient => { await app.loadPatients(); app.openPatient(patient.id); app.currentTab = 'complaints'; app.renderTabs(); app.renderTabPanel(app.getPatient(patient.id)); }, patient);
        const textarea = page.getByRole('textbox', {name: 'Комментарии', exact: true});
        // Завершаем реальную сессию штатным API, не меняя SQLite из браузера.
        await page.evaluate(() => apiPost(`/api/users/${app.user.id}/terminate-sessions`, {}));
        await textarea.fill('Синтетический несохраненный комментарий');
        await textarea.press('Tab');
        await page.waitForFunction(() => app.sessionExpired);
        await page.locator('#password').fill('synthetic-browser-password');
        await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => !app.sessionExpired);
        assert.equal(await textarea.inputValue(), 'Синтетический несохраненный комментарий');
        if (process.env.WP_D_SCREENSHOT) await page.screenshot({path: process.env.WP_D_SCREENSHOT, fullPage: true});
        const before = await page.evaluate(id => apiGetJson(`/api/patients/${id}`), patient.id);
        assert.equal(before.complaint, 'Начальное значение');
        // Конкурирующая правка после повторного входа увеличивает версию сервера.
        await page.evaluate(p => apiPatch(`/api/patients/${p.id}`, {field: 'complaint', value: 'Синтетическая внешняя правка', expectedVersion: p.version}), before);
        await page.getByRole('button', {name: 'Сохранить правку', exact: true}).click();
        await page.waitForFunction(() => document.body.textContent.includes('Карточка уже изменена.'));
        const after = await page.evaluate(id => apiGetJson(`/api/patients/${id}`), patient.id);
        assert.equal(after.complaint, 'Синтетическая внешняя правка');
        assert.equal(await textarea.inputValue(), 'Синтетический несохраненный комментарий');
        assert.equal(await page.getByRole('button', {name: 'Сохранить правку', exact: true}).isVisible(), true);
        console.log('PASS AUD-05 live browser: real session termination, same-owner draft, no auto-save, explicit save gets real WP-C 409 without overwrite');
        await page.getByRole('button', {name: 'Отменить правку', exact: true}).click();
        await page.locator('#confirmOkBtn').click();
        await page.waitForFunction(() => document.getElementById('sessionFieldDrafts').hidden);
        // Новый черновик после новой потери сессии сохраняется только по кнопке.
        const textarea2 = page.getByRole('textbox', {name: 'Комментарии', exact: true});
        await page.evaluate(() => apiPost(`/api/users/${app.user.id}/terminate-sessions`, {}));
        await textarea2.fill('Синтетическая явная правка'); await textarea2.press('Tab');
        await page.waitForFunction(() => app.sessionExpired);
        await page.locator('#password').fill('synthetic-browser-password'); await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => !app.sessionExpired);
        await page.getByRole('button', {name: 'Сохранить правку', exact: true}).click();
        await page.waitForFunction(() => document.getElementById('sessionFieldDrafts').hidden);
        const saved = await page.evaluate(id => apiGetJson(`/api/patients/${id}`), patient.id);
        assert.equal(saved.complaint, 'Синтетическая явная правка');
        const audit = await page.evaluate(() => apiGetJson('/api/audit?limit=100'));
        assert(audit.events.some(e => e.event_type === 'patient_updated' && e.patient_id === patient.id));
        console.log('PASS AUD-05 live browser: explicit save after reauthentication persists draft with audit');
        // Обычная явная новая правка того же поля также закрывает прежний черновик.
        await page.evaluate(() => apiPost(`/api/users/${app.user.id}/terminate-sessions`, {}));
        const textarea3 = page.getByRole('textbox', {name: 'Комментарии', exact: true});
        await textarea3.fill('Синтетический третий черновик'); await textarea3.press('Tab');
        await page.waitForFunction(() => app.sessionExpired);
        await page.locator('#password').fill('synthetic-browser-password'); await page.locator('#loginSubmit').click();
        await page.waitForFunction(() => !app.sessionExpired);
        await textarea3.fill('Синтетическая новая ручная правка'); await textarea3.press('Tab');
        await page.waitForFunction(() => document.getElementById('sessionFieldDrafts').hidden);
        const final = await page.evaluate(id => apiGetJson(`/api/patients/${id}`), patient.id);
        assert.equal(final.complaint, 'Синтетическая новая ручная правка');
        console.log('PASS AUD-05 live browser: a new explicit field edit clears superseded draft');
        console.log('All live synthetic browser checks passed');
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
