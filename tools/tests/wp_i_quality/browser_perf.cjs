// Loopback UI latency samples. Timing includes the Playwright click and render
// observation and is evidence for this laboratory run, not a production SLA.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');

const {
    chromium, fixture, getQuality, login, observe, qualityView, receipt, setupBounded, successful,
} = require('./browser_helpers.cjs');

const patients = '#qualityV1Workspace [data-quality-part="patients"]';

async function measured(page, trigger, expectedOffset) {
    const start = performance.now();
    const [reply] = await Promise.all([getQuality(page), trigger()]);
    const data = await successful(reply, 'page');
    const bytes = await reply.body();
    const first = data.patients.items[0].patient_id;
    await page.locator(`${patients} [data-patient-id="${first}"]`).waitFor({ state: 'visible' });
    const elapsedMs = +(performance.now() - start).toFixed(3);
    const rendered = await page.evaluate(host => ({
        rows: document.querySelectorAll(`${host} [data-quality-action="patient"]`).length,
        elements: document.querySelectorAll('#qualityV1Workspace *').length,
        overflow: document.documentElement.scrollWidth > innerWidth,
    }), patients);
    assert.equal(rendered.rows, data.patients.items.length);
    assert.equal(rendered.rows, 50);
    assert.equal(data.patients.page.offset, expectedOffset);
    assert.equal(rendered.overflow, false);
    assert(bytes.length <= 1024 * 1024);
    return {
        sample: { offset: expectedOffset, elapsedMs, responseBytes: bytes.length,
            bodySha256: crypto.createHash('sha256').update(bytes).digest('hex'), ...rendered },
        token: data.snapshot.token,
    };
}

function stats(data) {
    const values = data.map(row => row.elapsedMs).sort((a, b) => a - b);
    return { samples: values.length, p50: values[Math.ceil(values.length * .5) - 1],
        p95: values[Math.ceil(values.length * .95) - 1] };
}

async function main() {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
    const samples = [];
    try {
        for (const width of [1440, 375, 320]) {
            const context = await browser.newContext({ viewport: { width, height: 960 }, reducedMotion: 'reduce' });
            try {
                const page = await context.newPage();
                await login(page, fixture.accounts.developer);
                await setupBounded(page);
                const log = observe(page);
                const initial = await measured(page, () => qualityView(page), 0);
                const next = await measured(page, () => page.locator(`${patients} [data-quality-action="next"]`).click(), 50);
                assert.equal(next.token, initial.token);
                assert.equal(log.quality.length, 2);
                assert.equal(log.legacyQuality, 0);
                assert.equal(log.fullRegistry, 0);
                assert.equal(log.writes, 0);
                samples.push({ width, first: initial.sample, next: next.sample, identityReads: log.identity });
            } finally {
                await context.close();
            }
        }
        const data = { syntheticOnly: true, realFastApi: true, syntheticPatientCount: fixture.count,
            chromium: browser.version(), firstRenderMs: stats(samples.map(row => row.first)),
            nextRenderMs: stats(samples.map(row => row.next)), samples };
        receipt('browser-perf.json', data);
        console.log(JSON.stringify(data));
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    console.error(error.stack);
    process.exitCode = 1;
});
