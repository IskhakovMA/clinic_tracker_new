// Тесты чистой логики UX-прототипа. Запуск из корня worktree:
//   node --test prototype/staff-ux/tests/
// Только синтетические данные; сеть и сервер не используются.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const ctx = { window: {} };
ctx.globalThis = ctx;
vm.createContext(ctx);
for (const f of ['../core.js', '../../redesign/nosology.js', '../../redesign/data.js']) vm.runInContext(readFileSync(here(f), 'utf8'), ctx, { filename: f });
const C = ctx.window.StaffUxCore;
const D = ctx.window.DEMO;
const TERMINAL = ['completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
const TODAY = '2026-10-15';
const ev = (date, time = '', extra = {}) => ({ date, time, actualDate: '', actualTime: '', matchedPlan: null, noPlan: false, ...extra });
const patient = (history, stage) => ({ stage: stage || history[history.length - 1].stage, history });

test('просроченное событие не скрывается будущим, стоящим раньше в списке', () => {
    const p = patient([{ stage: 'exam', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-20', '10:00'), ev('2026-10-12', '09:00')] }]);
    const n = C.pickNextEvent(p, TODAY, TERMINAL);
    assert.equal(n.kind, 'overdue');
    assert.equal(n.ev.date, '2026-10-12');
    assert.equal(n.d, -3);
    assert.equal(n.overdueTotal, 1);
});

test('из нескольких будущих выбирается ближайшее по дате и времени', () => {
    const p = patient([{ stage: 'exam', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-18', '15:00'), ev('2026-10-17', '16:00'), ev('2026-10-17', '09:30')] }]);
    const n = C.pickNextEvent(p, TODAY, TERMINAL);
    assert.equal(n.ev.date, '2026-10-17');
    assert.equal(n.ev.time, '09:30');
    assert.equal(n.kind, 'soon');
});

test('выполненные события и события без плана не учитываются', () => {
    const p = patient([{ stage: 'exam', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-10', '', { actualDate: '2026-10-10' }), ev('', '', { noPlan: true, actualDate: '2026-10-11' }), ev('2026-10-15', '11:00')] }]);
    const n = C.pickNextEvent(p, TODAY, TERMINAL);
    assert.equal(n.kind, 'today');
});

test('просрочка прошлого этапа видна, предстоящие события прошлого этапа — нет', () => {
    const p = patient([
        { stage: 'exam', enteredAt: '2026-09-01T10:00', events: [ev('2026-09-10', '', { actualDate: '2026-09-10' }), ev('2026-10-01', '10:00'), ev('2026-11-01')] },
        { stage: 'appointment', enteredAt: '2026-10-05T10:00', events: [ev('2026-10-16', '12:00')] },
    ]);
    const n = C.pickNextEvent(p, TODAY, TERMINAL);
    assert.equal(n.kind, 'overdue');
    assert.equal(n.occIndex, 0);
    assert.equal(C.overdueEvents(p, TODAY, TERMINAL).length, 1);
    assert.equal(C.openEvents(p, TODAY, TERMINAL).some(x => x.ev.date === '2026-11-01'), false);
});

test('без дат — «дата не назначена»; завершённый маршрут — нет событий', () => {
    assert.equal(C.pickNextEvent(patient([{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('')] }]), TODAY, TERMINAL).kind, 'nodate');
    assert.equal(C.pickNextEvent(patient([{ stage: 'completed', enteredAt: '2026-10-01', events: [ev('2026-10-01')] }]), TODAY, TERMINAL), null);
});

test('синтетические данные: «Просрочено» в списке совпадает с правилом просрочек аналитики', () => {
    for (const p of D.patients) {
        const n = C.pickNextEvent(p, D.today, D.TERMINAL);
        const overdue = C.overdueEvents(p, D.today, D.TERMINAL).length;
        assert.equal(Boolean(n && n.kind === 'overdue'), overdue > 0, `пациент ${p.id}`);
    }
});

test('период длиннее 90 дней: итоги и CSV — за весь период, график — последние 90 дней', () => {
    const list = [
        { createdAt: '2026-04-01T10:00', history: [{ stage: 'completed', enteredAt: '2026-05-01T10:00' }] },   // за пределами 90 дней
        { createdAt: '2026-09-01T10:00', history: [{ stage: 'exam', enteredAt: '2026-09-01T10:00' }] },
        { createdAt: '2025-12-01T10:00', history: [] },                                                       // вне периода
    ];
    const s = C.periodSummary(list, '2026-03-01', TODAY);
    assert.equal(s.days, 229);
    assert.deepEqual({ ...s.totals }, { added: 2, completed: 1 });
    assert.equal(s.trend.length, C.TREND_MAX_DAYS);
    assert.equal(s.truncated, true);
    assert.equal(s.trendFrom, '2026-07-18');
    assert.equal(s.trend.reduce((n, r) => n + r.added, 0), 1, 'на графике видна только часть периода');
});

test('период до 90 дней: график и итоги совпадают', () => {
    const list = [{ createdAt: '2026-10-01T10:00', history: [{ stage: 'completed', enteredAt: '2026-10-10T10:00' }] }];
    const s = C.periodSummary(list, '2026-09-16', TODAY);
    assert.equal(s.truncated, false);
    assert.equal(s.trend.length, 30);
    assert.equal(s.trend.reduce((n, r) => n + r.added, 0), s.totals.added);
    assert.equal(s.trend.reduce((n, r) => n + r.completed, 0), s.totals.completed);
});

test('одинаковые схемы химиотерапии группируются в одну строку', () => {
    const sc = (code, name) => ({ treatment: 'chemotherapy', status: 'signed', scheme: { code, name } });
    const rows = C.groupSchemes([sc('sh0001', 'FOLFOX'), sc('sh0001', 'FOLFOX'), sc('sh0002', 'CAPOX'), sc('', 'Своя схема'), { treatment: 'surgery', scheme: null }]);
    assert.equal(rows.length, 3);
    assert.deepEqual({ ...rows[0] }, { code: 'sh0001', name: 'FOLFOX', label: 'sh0001 — FOLFOX', count: 2 });
    assert.deepEqual(Object.fromEntries(rows.map(r => [r.label, r.count])), { 'sh0001 — FOLFOX': 2, 'sh0002 — CAPOX': 1, 'Своя схема': 1 });
});

/* ---------- Решение ОК и перевод этапа — самостоятельные операции ---------- */
const consultPatient = () => patient([
    { stage: 'appointment', enteredAt: '2026-09-20T10:00', events: [ev('2026-09-25', '10:00', { actualDate: '2026-09-25', actualTime: '10:00', matchedPlan: true })] },
    { stage: 'consult', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-08', '09:00', { actualDate: '2026-10-08' }), ev('2026-10-14', '11:00'), ev('2026-10-20', '12:00')] },
]);
const snapshot = (p) => JSON.parse(JSON.stringify(p));
const mark = (p, extra = {}) => C.applyManualDecision(p, { role: 'doctor', inScope: true, eventIndex: 1, decision: 'treatment_strategy_defined', at: '2026-10-15T10:00', by: 'Тест', ...extra });

test('сохранение ручного решения не переводит пациента', () => {
    const p = consultPatient();
    const res = mark(p);
    assert.equal(p.stage, 'consult');
    assert.equal(p.history.length, 2);
    assert.equal(res.stage, 'consult');
    assert.equal(p.history[1].events[1].consiliumDecision, 'treatment_strategy_defined');
    assert.equal(p.history[1].events[1].decisionMark.source, 'manual');
});

test('перевод этапа не создаёт и не меняет ручное решение', () => {
    const p = consultPatient();
    mark(p, { eventIndex: 0, decision: 'treatment_strategy_changed' });
    const before = C.manualDecisions(p).map(x => ({ ...x, mark: { ...x.mark } }));
    C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-15', actualTime: '12:00', enteredAt: '2026-10-15T12:00', scheduled: true });
    assert.equal(p.stage, 'treatment-prep');
    assert.deepEqual(C.manualDecisions(p).map(x => ({ ...x, mark: { ...x.mark } })), before);
    const q = consultPatient();
    C.applyStageChange(q, { to: 'dispensary-observation', actualDate: '2026-10-15', enteredAt: '2026-10-15T12:00' });
    assert.equal(C.manualDecisions(q).length, 0, 'переход в «Диспансерное наблюдение» не создаёт отметку');
});

test('при нескольких событиях нужен явный выбор; последнее событие не выбирается само', () => {
    const p = consultPatient();
    const avail = C.decisionAvailability(p, 'doctor', true);
    assert.equal(avail.events.length, 3);
    assert.equal(C.defaultDecisionEvent(avail), null);
    assert.throws(() => mark(p, { eventIndex: undefined }), /event_required/);
    assert.throws(() => mark(p, { eventIndex: null }), /event_required/);
    assert.throws(() => mark(p, { eventIndex: 7 }), /event_not_in_consult_stage/);
    assert.throws(() => mark(p, { decision: '' }), /decision_required/);
    assert.equal(C.manualDecisions(p).length, 0, 'после ошибок ничего не записано');
    mark(p, { eventIndex: 0 });
    assert.deepEqual(C.manualDecisions(p).map(x => x.eventIndex), [0]);
    const single = patient([{ stage: 'consult', enteredAt: '2026-10-01', events: [ev('2026-10-14')] }]);
    assert.equal(C.defaultDecisionEvent(C.decisionAvailability(single, 'doctor', true)), 0, 'единственное событие предлагается, но сохраняется только явно');
});

test('ручная отметка не создаёт и не подменяет подписанное решение председателя', () => {
    const p = { ...consultPatient(), id: 'p-x' };
    const consilium = [{ id: 'c-1', patientId: 'p-x', status: 'in_review', treatment: null, scheme: null, date: '2026-10-15', time: '10:00' }];
    const before = JSON.stringify(consilium);
    mark(p);
    assert.equal(JSON.stringify(consilium), before, 'запись консилиума не изменена');
    assert.equal(C.signedDecisions(consilium, 'p-x').length, 0);
    consilium[0].status = 'signed'; consilium[0].treatment = 'surgery';
    assert.deepEqual(C.signedDecisions(consilium, 'p-x').map(x => x.treatment), ['surgery']);
    assert.deepEqual(C.manualDecisions(p).map(x => x.decision), ['treatment_strategy_defined'], 'подписанное решение не попадает в ручные отметки');
    const signedOnly = { ...consultPatient(), id: 'p-y' };
    assert.equal(C.manualDecisions(signedOnly).length, 0);
});

test('доступность действия по роли и этапу', () => {
    const p = consultPatient();
    for (const r of ['doctor', 'head', 'chair', 'developer']) assert.equal(C.decisionAvailability(p, r, true).available, true, r);
    for (const r of ['deputy', 'caop']) assert.equal(C.decisionAvailability(p, r, true).reason, 'no_edit_right', r);
    assert.equal(C.decisionAvailability(p, 'doctor', false).reason, 'no_edit_right', 'пациент вне области доступа');
    assert.throws(() => C.applyManualDecision(p, { role: 'deputy', inScope: true, eventIndex: 1, decision: 'treatment_strategy_defined' }), /decision_unavailable:no_edit_right/);
    const exam = patient([{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-14')] }]);
    assert.equal(C.decisionAvailability(exam, 'doctor', true).reason, 'not_consult');
    const empty = patient([{ stage: 'consult', enteredAt: '2026-10-01', events: [] }]);
    assert.equal(C.decisionAvailability(empty, 'doctor', true).reason, 'no_events');
});

test('нет скрытых изменений других событий', () => {
    const p = consultPatient();
    const before = snapshot(p);
    mark(p, { eventIndex: 2 });
    const after = snapshot(p);
    after.history[1].events[2] = before.history[1].events[2];   // единственное допустимое изменение
    assert.deepEqual(after, before);
    // перевод этапа меняет только факт последнего события текущего этапа и добавляет новый этап
    const q = consultPatient(); mark(q, { eventIndex: 0 });
    const b2 = snapshot(q);
    C.applyStageChange(q, { to: 'exam', actualDate: '2026-10-15', enteredAt: '2026-10-15T12:00', scheduled: true });
    const a2 = snapshot(q);
    assert.deepEqual(a2.history[0], b2.history[0]);
    assert.deepEqual(a2.history[1].events.slice(0, 2), b2.history[1].events.slice(0, 2));
    const changed = Object.keys(a2.history[1].events[2]).filter(k => JSON.stringify(a2.history[1].events[2][k]) !== JSON.stringify(b2.history[1].events[2][k]));
    assert.deepEqual(changed.sort(), ['actualDate', 'actualTime', 'matchedPlan']);
    assert.throws(() => C.applyStageChange(consultPatient(), { to: 'treatment', actualDate: '2026-10-15' }), /stage_not_allowed/);
});

/* ---------- Регрессия: независимое ревью (факт, повторная отметка, записи консилиума) ---------- */
const withFact = () => patient([
    { stage: 'consult', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-10', '10:00'), ev('2026-10-14', '11:00', { actualDate: '2026-10-14', actualTime: '11:20', matchedPlan: false })] },
]);

test('перевод этапа не перезаписывает существующий факт — блокируется', () => {
    const p = withFact(); const before = snapshot(p);
    const c = C.stageChangeConflict(p, { actualDate: '2026-10-15', actualTime: '' });
    assert.equal(c.eventIndex, 1); assert.equal(c.actualDate, '2026-10-14'); assert.equal(c.actualTime, '11:20');
    assert.throws(() => C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-15', enteredAt: '2026-10-15T12:00' }), /fact_conflict/);
    assert.throws(() => C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-14', actualTime: '12:00', enteredAt: '2026-10-15T12:00' }), /fact_conflict/, 'другое время — тоже конфликт');
    assert.deepEqual(snapshot(p), before, 'после отказа данные не изменены');
});

test('совпадающий факт не меняется, перевод проходит', () => {
    const p = withFact();
    const fact = snapshot(p).history[0].events[1];
    C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-14', actualTime: '11:20', enteredAt: '2026-10-15T12:00' });
    assert.equal(p.stage, 'treatment-prep');
    assert.deepEqual(snapshot(p).history[0].events[1], fact, 'факт, время и matchedPlan сохранены');
    const q = withFact();
    C.applyStageChange(q, { to: 'exam', actualDate: '2026-10-14', actualTime: '', enteredAt: '2026-10-15T12:00' });
    assert.equal(q.history[0].events[1].actualTime, '11:20', 'пустое время не стирает записанное');
    assert.throws(() => C.applyStageChange(withFact(), { to: 'exam', actualDate: '' }), /actual_date_required/);
});

test('повторная отметка в том же событии не перезаписывает прежнюю', () => {
    const p = consultPatient();
    mark(p, { eventIndex: 1, decision: 'treatment_strategy_defined', at: '2026-10-15T10:00', by: 'Первый' });
    const before = snapshot(p);
    assert.throws(() => mark(p, { eventIndex: 1, decision: 'dispensary_observation', at: '2026-10-15T11:00', by: 'Второй' }), /decision_exists/);
    assert.deepEqual(snapshot(p), before, 'решение, автор и время сохранены');
    const avail = C.decisionAvailability(p, 'doctor', true);
    assert.deepEqual(avail.events.map(x => x.marked), [false, true, false]);
    mark(p, { eventIndex: 2, decision: 'dispensary_observation', at: '2026-10-15T11:00', by: 'Второй' });   // другое событие — можно
    mark(p, { eventIndex: 0, decision: 'treatment_strategy_changed' });
    const all = C.decisionAvailability(p, 'doctor', true);
    assert.equal(all.available, false); assert.equal(all.reason, 'all_marked');
    assert.equal(all.events.length, 3, 'существующие отметки возвращаются для показа');
    const single = patient([{ stage: 'consult', enteredAt: '2026-10-01', events: [ev('2026-10-14', '', { consiliumDecision: 'treatment_strategy_defined' })] }]);
    assert.equal(C.defaultDecisionEvent(C.decisionAvailability(single, 'doctor', true)), null);
});

test('неподписанная более поздняя запись не подменяет подписанное решение', () => {
    const consilium = [
        { id: 'c1', patientId: 'p1', status: 'signed', treatment: 'surgery', scheme: null, date: '2026-10-01', time: '10:00' },
        { id: 'c2', patientId: 'p1', status: 'in_review', treatment: null, scheme: null, date: '2026-10-15', time: '09:00' },
        { id: 'c3', patientId: 'p2', status: 'signed', treatment: 'radiotherapy', scheme: null, date: '2026-10-16', time: '' },
    ];
    const before = JSON.stringify(consilium);
    const s = C.consiliumSummary(consilium, 'p1');
    assert.deepEqual(s.signed.map(x => [x.id, x.treatment]), [['c1', 'surgery']]);
    assert.equal(s.current.id, 'c2'); assert.equal(s.current.status, 'in_review');
    assert.equal(s.total, 2);
    assert.equal(JSON.stringify(consilium), before, 'записи консилиума не изменены');
    const none = C.consiliumSummary([{ id: 'c4', patientId: 'p3', status: 'needs_revision', date: '2026-10-15', time: '' }], 'p3');
    assert.equal(none.signed.length, 0, 'без подписанных — пусто, текущая запись отдельно');
    assert.equal(none.current.status, 'needs_revision');
    const p = { ...consultPatient(), id: 'p1' }; const ps = snapshot(p);
    C.consiliumSummary(consilium, 'p1');
    assert.deepEqual(snapshot(p), ps, 'этап и события пациента не меняются');
    assert.equal(C.manualDecisions(p).length, 0, 'подписанные решения не синхронизируются в ручные отметки');
});

test('записанное время пустое, введено 13:30 — конфликт, факт, история и этап не меняются', () => {
    const p = patient([
        { stage: 'consult', enteredAt: '2026-10-01T10:00', events: [ev('2026-10-14', '', { actualDate: '2026-10-14', actualTime: '', matchedPlan: false })] },
    ]);
    const before = snapshot(p);
    const c = C.stageChangeConflict(p, { actualDate: '2026-10-14', actualTime: '13:30' });
    assert.ok(c, 'новое время не игнорируется молча');
    assert.equal(c.actualDate, '2026-10-14'); assert.equal(c.actualTime, '');
    assert.throws(() => C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-14', actualTime: '13:30', enteredAt: '2026-10-15T12:00' }), /fact_conflict/);
    assert.deepEqual(snapshot(p), before, 'история не изменена');
    assert.equal(p.stage, 'consult'); assert.equal(p.history.length, 1);
    // пустое введённое время — изменение не запрошено: перевод проходит, факт прежний
    assert.equal(C.stageChangeConflict(p, { actualDate: '2026-10-14', actualTime: '' }), null);
    C.applyStageChange(p, { to: 'treatment-prep', actualDate: '2026-10-14', actualTime: '', enteredAt: '2026-10-15T12:00' });
    assert.equal(p.stage, 'treatment-prep');
    assert.deepEqual(snapshot(p).history[0], before.history[0], 'факт события не изменён');
});
