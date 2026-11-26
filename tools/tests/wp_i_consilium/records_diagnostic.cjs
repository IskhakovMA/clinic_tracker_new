const {performance} = require('node:perf_hooks');

const endpointNames = new Map([
    ['/api/auth/me', 'identity'],
    ['/api/auth/activity', 'activity'],
    ['/api/internal-notifications/summary', 'notifications-summary'],
    ['/api/consilium/appointments', 'appointments'],
    ['/api/consilium/schedule', 'schedule'],
    ['/api/consilium/registries', 'registries'],
    ['/api/consilium/meta', 'meta'],
    ['/api/consilium/booked-patients', 'booked-patients'],
    ['/api/consilium/analytics', 'analytics'],
]);

function startNetwork(page, origin, elapsed) {
    const entries = new Map();
    const watched = request => {
        const {origin: host, pathname} = new URL(request.url());
        if (host !== origin) return;
        return endpointNames.get(pathname) ||
            (/^\/api\/consilium\/appointments\/[^/]+\/referral-completion$/.test(pathname) ? 'referral' : null);
    };
    const listeners = {
        request(request) {
            const endpoint = watched(request);
            const method = request.method();
            if (endpoint && ['GET', 'POST'].includes(method) && entries.size < 96) {
                entries.set(request, {endpoint, method, startedMs: elapsed(), endedMs: null, status: null, state: 'pending'});
            }
        },
        response(response) {
            const entry = entries.get(response.request());
            if (entry) entry.status = response.status();
        },
        requestfinished(request) {
            const entry = entries.get(request);
            if (entry) Object.assign(entry, {endedMs: elapsed(), state: 'finished'});
        },
        requestfailed(request) {
            const entry = entries.get(request);
            if (entry) Object.assign(entry, {endedMs: elapsed(), state: 'failed'});
        },
    };
    for (const [event, callback] of Object.entries(listeners)) page.on(event, callback);
    return () => {
        for (const [event, callback] of Object.entries(listeners)) page.off(event, callback);
        return [...entries.values()];
    };
}

async function observeReferral(page, origin) {
    const started = performance.now();
    const elapsed = () => Math.round(performance.now() - started);
    const stopNetwork = startNetwork(page, origin, elapsed);
    await page.evaluate(() => {
        const methods = ['loadConsilium', 'renderConsilium', 'setSurgicalReferralCompleted'];
        const safeError = error => error?.obsolete ? 'obsolete' :
            ['Error', 'TypeError', 'AbortError', 'TimeoutError'].includes(error?.name) ? error.name : 'other';
        const diagnostic = {phases: [], domUpdates: 0, tickets: {}, original: {}};
        const record = (method, state, at, error = null) => {
            if (diagnostic.phases.length < 48) diagnostic.phases.push({
                method, state, atMs: Math.round(at), error,
            });
        };
        const start = performance.now();
        diagnostic.observer = new MutationObserver(records => { diagnostic.domUpdates += records.length; });
        diagnostic.observer.observe(document.getElementById('surgicalRegistryBody'), {childList: true, subtree: true});
        diagnostic.original.beginRead = app.beginRead;
        app.beginRead = function (key) {
            const version = diagnostic.original.beginRead.call(this, key);
            if (key === 'consilium-load' || key === 'consilium-render') {
                diagnostic.tickets[key] = {version, epoch: this.sessionEpoch};
            }
            return version;
        };
        for (const method of methods) {
            const original = diagnostic.original[method] = app[method];
            app[method] = function (...args) {
                record(method, 'started', performance.now() - start);
                const pending = original.apply(this, args);
                pending.then(
                    () => record(method, 'resolved', performance.now() - start),
                    error => record(method, 'rejected', performance.now() - start, safeError(error)),
                );
                return pending;
            };
        }
        window.f111ReferralDiagnostic = diagnostic;
    });
    return async () => {
        const requests = stopNetwork();
        const snapshot = await page.evaluate(() => {
            const diagnostic = window.f111ReferralDiagnostic;
            diagnostic.observer.disconnect();
            for (const [name, original] of Object.entries(diagnostic.original)) app[name] = original;
            delete window.f111ReferralDiagnostic;
            const current = key => diagnostic.tickets[key]
                ? app.sessionEpoch === diagnostic.tickets[key].epoch && app.isCurrentRead(key, diagnostic.tickets[key].version)
                : null;
            return {
                ui: {
                    loading: !!app.consilium.loading, referralPending: !!app.consilium.referralPending,
                    loaded: !!app.consilium.loaded, gridReady: Boolean(app.consiliumGridReady()),
                    readState: ['idle', 'loading', 'ready', 'error'].includes(app.consilium.readState)
                        ? app.consilium.readState : 'other',
                    sessionExpired: !!app.sessionExpired, sessionReconciling: !!app.sessionReconciling,
                    loadTicketCurrent: current('consilium-load'), renderTicketCurrent: current('consilium-render'),
                    domUpdates: diagnostic.domUpdates,
                },
                executions: diagnostic.phases,
            };
        }).catch(() => ({ui: null, executions: []}));
        return {requests, ...snapshot, durationMs: elapsed()};
    };
}

module.exports = {observeReferral};
