/* WP-E: реальный FastAPI с фиксированными часами. Только синтетический стенд. */
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
(async () => {
    const origin = process.env.WP_E_SYNTHETIC_ORIGIN;
    if (!origin) throw new Error('Set WP_E_SYNTHETIC_ORIGIN to the isolated WP-E server');
    const browser = await chromium.launch({headless: true, ...(process.env.BROWSER_EXECUTABLE ? {executablePath: process.env.BROWSER_EXECUTABLE} : {})});
    try {
        for (const timezoneId of ['America/Los_Angeles', 'Asia/Tokyo']) {
            const context = await browser.newContext({timezoneId});
            const page = await context.newPage();
            await page.goto(origin);
            await page.locator('#username').fill('synthetic.browser');
            await page.locator('#password').fill('synthetic-browser-password');
            await page.locator('#loginSubmit').click();
            await page.waitForFunction(() => app.appReady);
            const patient = await page.evaluate(async zone => {
                const p = await apiPost('/api/patients', {name: 'Синтетический WP-E ' + zone, phone: '+79990000001', diagnosis: 'C34.1', nosology: 'lung34', priority: 'normal', stage: 'exam', head: 'Синтетический Заведующий'});
                await apiPut(`/api/notifications/patients/${p.id}/preferences`, {smsEnabled: true, consentStatus: 'granted'});
                await apiPut('/api/notifications/templates/custom_reminder', {body: 'Прием {date} в {time}. {{дата}}', enabled: true});
                await app.loadPatients(); app.openPatient(p.id);
                return p;
            }, timezoneId);
            await page.getByRole('tab', {name: 'Уведомления', exact: true}).click();
            const field = page.locator('#patientNotificationSchedule');
            await field.waitFor();
            assert.equal(await field.inputValue(), '2026-10-01T12:00', 'default must use server Moscow time');
            assert.equal(await page.getByLabel('Дата и время отправки (МСК)', {exact: true}).count(), 1);
            await page.locator('#patientNotificationTemplate').selectOption('custom_reminder');
            await field.fill('2026-10-02T00:30');
            assert.equal(await page.locator('#patientNotificationPreview').inputValue(), 'Прием 2026-10-02 в 00:30. {дата}');
            if (process.env.WP_E_SCREENSHOT && timezoneId === 'America/Los_Angeles') await page.screenshot({path: process.env.WP_E_SCREENSHOT, fullPage: true});
            const created = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith('/api/notifications'));
            await page.getByRole('button', {name: 'Создать напоминание', exact: true}).click();
            const response = await created;
            assert.equal(response.status(), 201);
            assert.equal(response.request().postDataJSON().scheduledAt, '2026-10-01T21:30:00.000Z');
            const queue = await page.evaluate(id => apiGetJson(`/api/notifications/patients/${id}`), patient.id);
            assert.equal(queue.upcoming.scheduled_at, '2026-10-01T21:30:00+00:00');
            await page.waitForFunction(() => document.querySelector('#patientNotificationsBlock')?.textContent.includes('02.10.2026, 00:30 МСК'));
            console.log('PASS ' + timezoneId + ': server default, explicit Moscow input/preview, actual UTC queue, Moscow display');

            // Сохранение запрещенного шаблона через настоящий UI/API.
            await page.locator('[data-view="notifications"]').click();
            await page.locator('[data-notifications-tab="outbound"]').click();
            const template = page.locator('#template-body-custom_reminder');
            await template.fill('СИНТЕТИЧЕСКИЙ_МАРКЕР {date.__class__}');
            const rejected = page.waitForResponse(r => r.request().method() === 'PUT' && r.url().endsWith('/api/notifications/templates/custom_reminder'));
            await template.locator('xpath=../..').getByRole('button', {name: 'Сохранить шаблон', exact: true}).click();
            const bad = await rejected;
            assert.equal(bad.status(), 422);
            const error = await bad.text();
            assert(!error.includes('СИНТЕТИЧЕСКИЙ_МАРКЕР') && !error.includes('__class__') && !error.includes('Traceback'));
            const stored = await page.evaluate(() => apiGetJson('/api/notifications/templates'));
            assert.equal(stored.templates.find(t => t.code === 'custom_reminder').body, 'Прием {date} в {time}. {{дата}}');
            console.log('PASS ' + timezoneId + ': forbidden template rejected by real API with safe diagnostics');
            await context.close();
        }
        console.log('All WP-E live browser checks passed');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
