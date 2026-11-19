const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fixture = JSON.parse(fs.readFileSync(process.env.WP_I_QUALITY_FIXTURE, 'utf8'));
const evidenceDir = process.env.WP_I_QUALITY_OUT;
assert(fixture.syntheticOnly && evidenceDir);
assert.match(fixture.baseURL, /^http:\/\/127[.]0[.]0[.]1:\d+$/);
assert.equal(fixture.qualityPath, '/api/_i8a3_lab');

function receipt(name, data) {
    fs.writeFileSync(path.join(evidenceDir, name), JSON.stringify(data, null, 2) + '\n');
}

async function login(page, username) {
    await page.goto(fixture.baseURL);
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(fixture.password);
    await page.locator('#loginSubmit').click();
    await currentSession(page);
}

function currentSession(page) {
    return page.waitForFunction(() => app.appReady && !app.sessionExpired && !app.sessionReconciling);
}

async function setupBounded(page) {
    await page.evaluate(endpoint => app.mountQualityV1(endpoint), fixture.qualityPath);
}

async function qualityView(page) {
    // The sole custom operation attaches a trusted laboratory transport.
    // Workstation navigation still travels through the real sidebar.
    const item = page.locator('[data-view="quality"]');
    const menu = page.locator('#sidebarToggle');
    if (await page.evaluate(() => matchMedia('(max-width: 860px)').matches) &&
        await menu.getAttribute('aria-expanded') !== 'true') await menu.click();
    await item.click();
}

function isQuality(url) {
    const value = new URL(url);
    return value.origin === fixture.baseURL && value.pathname === fixture.qualityPath;
}

function getQuality(page, view = 'page') {
    return page.waitForResponse(response => isQuality(response.url()) &&
        new URL(response.url()).searchParams.get('view') === view);
}

async function api(context, method, pathname, options = {}) {
    const csrf = (await context.cookies()).find(cookie => cookie.name === 'clinic_csrf');
    const headers = { ...options.headers };
    if (csrf && !['GET', 'HEAD'].includes(method)) headers['X-CSRF-Token'] = csrf.value;
    assert(pathname.startsWith('/') && !pathname.startsWith('//'));
    return context.request.fetch(fixture.baseURL + pathname, { ...options, method, headers });
}

function observe(page) {
    const summary = { quality: [], identity: 0, legacyQuality: 0, fullRegistry: 0, writes: 0, objectReads: 0 };
    page.on('request', request => {
        const url = new URL(request.url());
        if (isQuality(url)) {
            summary.quality.push({
                method: request.method(),
                view: url.searchParams.get('view'),
                cursor: url.searchParams.has('cursor'),
                snapshot: url.searchParams.has('snapshot'),
                limit: Number(url.searchParams.get('limit')),
            });
        }
        if (url.pathname === '/api/auth/me') summary.identity++;
        if (url.pathname.startsWith('/api/data-quality/')) summary.legacyQuality++;
        if (url.pathname === '/api/patients' && request.method() === 'GET' &&
            url.searchParams.get('view') !== 'page') summary.fullRegistry++;
        if (url.pathname.startsWith('/api/patients/') && request.method() === 'GET') summary.objectReads++;
        if (url.pathname.startsWith('/api/patients') && !['GET', 'HEAD'].includes(request.method())) summary.writes++;
    });
    return summary;
}

async function successful(response, view) {
    assert.equal(response.status(), 200, 'synthetic Quality endpoint status');
    const data = await response.json();
    assert.equal(data.mode, 'quality-v1');
    assert.equal(data.view, view);
    assert.equal(data.snapshot.timezone, 'Europe/Moscow');
    return data;
}

function arm() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

async function boundedWait(promise, label) {
    let timer;
    try {
        return await Promise.race([
            promise,
            new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), 20000); }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

function syntheticError(route, status, code = null) {
    return route.fulfill({
        status,
        contentType: 'application/json',
        json: { detail: code ? { code, message: 'Синтетический отказ' } : 'Синтетический отказ' },
    });
}

module.exports = {
    api, arm, boundedWait, chromium, currentSession, fixture, getQuality, isQuality,
    login, observe, qualityView, receipt, setupBounded, successful, syntheticError,
};
