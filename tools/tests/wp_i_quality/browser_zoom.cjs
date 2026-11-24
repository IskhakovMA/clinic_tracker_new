// Chromium tab zoom in a disposable local profile, with only a synthetic app origin.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium, fixture, getQuality, login, qualityView, receipt, setupBounded, successful } = require('./browser_helpers.cjs');

async function main() {
    const profileHome = fs.mkdtempSync(path.join(os.tmpdir(), 'clinic-quality-native-zoom-'));
    const extension = path.join(profileHome, 'extension');
    fs.mkdirSync(extension);
    fs.writeFileSync(path.join(extension, 'manifest.json'), JSON.stringify({
        manifest_version: 3, name: 'Synthetic app zoom check', version: '1.0', permissions: ['tabs'],
        background: { service_worker: 'worker.js' },
    }));
    fs.writeFileSync(path.join(extension, 'worker.js'), 'chrome.runtime.onInstalled.addListener(() => {});');
    let context;
    const samples = [];
    try {
        context = await chromium.launchPersistentContext(path.join(profileHome, 'profile'), {
            headless: true, executablePath: process.env.WP_H_FULL_CHROMIUM || chromium.executablePath(),
            viewport: null, ignoreDefaultArgs: ['--disable-extensions'],
            args: ['--window-size=1280,1000', '--disable-extensions-except=' + extension, '--load-extension=' + extension],
        });
        const page = await context.newPage();
        await page.bringToFront();
        await login(page, fixture.accounts.developer);
        await setupBounded(page);
        const response = getQuality(page);
        await qualityView(page);
        const data = await successful(await response, 'page');
        const rows = page.locator('#qualityV1Workspace [data-quality-part="patients"] [data-quality-action="patient"]');
        await rows.first().waitFor({ state: 'visible' });
        const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
        for (const factor of [1, 2, 4]) {
            const zoom = await worker.evaluate(async ({ origin, factor }) => {
                const tab = (await chrome.tabs.query({})).find(item => item.url.startsWith(origin + '/'));
                if (!tab) throw new Error('Synthetic app tab missing');
                await chrome.tabs.setZoom(tab.id, factor);
                return chrome.tabs.getZoom(tab.id);
            }, { origin: fixture.baseURL, factor });
            assert.equal(Math.round(zoom * 100), factor * 100);
            await page.waitForFunction(f => Math.abs(devicePixelRatio - f) < 0.01, factor);
            const geometry = await page.evaluate(() => ({
                width: innerWidth, dpr: devicePixelRatio, scale: visualViewport.scale,
                overflow: document.documentElement.scrollWidth > innerWidth,
            }));
            assert.equal(geometry.overflow, false);
            assert.equal(geometry.scale, 1);
            assert.equal(await rows.count(), data.patients.items.length);
            if (factor === 4) assert.equal(geometry.width, 320);
            samples.push({ factor, ...geometry, visibleRows: await rows.count() });
        }
        receipt('browser-native-zoom.json', { syntheticOnly: true, nativeChromeZoom: true, samples });
        console.log(JSON.stringify({ nativeChromeZoom: true, samples }));
    } finally {
        if (context) await context.close();
        fs.rmSync(profileHome, { recursive: true, force: true });
    }
}

main().catch(error => {
    console.error(error.stack);
    process.exitCode = 1;
});
