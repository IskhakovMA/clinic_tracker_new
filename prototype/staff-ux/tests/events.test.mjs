// Тесты расписания плановых событий (events-core.js). Только синтетические данные.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const ctx = { window: {} }; ctx.globalThis = ctx; vm.createContext(ctx);
for (const f of ['../core.js', '../events-core.js']) vm.runInContext(readFileSync(here(f), 'utf8'), ctx, { filename: f });
const E = ctx.window.StaffUxEvents;
const plain = (x) => JSON.parse(JSON.stringify(x));
const TODAY = '2026-10-15';
const TERMINAL = ['completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
const ev = (date, time = '', extra = {}) => ({ date, time, actualDate: '', actualTime: '', matchedPlan: null, noPlan: false, ...extra });
const pat = (id, name, doctorId, history) => ({ id, name, icd: 'C34.1', doctorId, doctor: 'Врач', stage: history[history.length - 1].stage, history });

const patients = () => [
    pat('p1', 'Иванов Иван', 'd1', [
        { stage: 'primary-appointment', enteredAt: '2026-10-01', events: [ev('2026-10-02', '10:00', { actualDate: '2026-10-02', actualTime: '10:10', matchedPlan: false })] },
        { stage: 'exam', enteredAt: '2026-10-03', events: [ev('2026-10-15', '09:00'), ev('2026-10-15', ''), ev('2026-10-15', '13:30'), ev('2026-10-12', '11:00')] },
    ]),
    pat('p2', 'Константинопольская-Преображенская Александра-Виктория Вячеславовна', 'd2', [
        { stage: 'exam', enteredAt: '2026-10-01', events: [ev(''), ev('2026-10-20', '08:30')] },
    ]),
    pat('p3', 'Петров Пётр', 'd1', [
        { stage: 'completed', enteredAt: '2026-10-01', events: [ev('2026-10-10')] },
    ]),
    pat('p4', 'Сидорова Анна', 'd2', [
        { stage: 'treatment', enteredAt: '2026-10-01', events: [ev('', '', { noPlan: true, actualDate: '2026-10-15', actualTime: '08:00' })] },
    ]),
];

test('несколько событий одной даты, событие без времени — в конце дня, без выдуманного времени', () => {
    const v = E.dayView(E.scheduleItems(patients(), TODAY, TERMINAL), TODAY);
    const rows = v.day.map(x => [x.p.id, x.plannedTime, x.status]);
    assert.deepEqual(plain(rows), [['p4', '', 'done'], ['p1', '09:00', 'today'], ['p1', '13:30', 'today'], ['p1', '', 'today']]);
    assert.equal(v.day.find(x => x.p.id === 'p1' && !x.plannedTime).plannedTime, '', 'время не подставляется');
});

test('просроченное событие не скрывается и считается по тому же правилу, что рабочий день', () => {
    const items = E.scheduleItems(patients(), TODAY, TERMINAL);
    const v = E.dayView(items, '2026-10-20');
    assert.deepEqual(plain(v.overdue.map(x => [x.p.id, x.plannedDate])), [['p1', '2026-10-12']], 'просрочка видна при любом выбранном дне');
    assert.equal(ctx.window.StaffUxCore.overdueEvents(patients()[0], TODAY, TERMINAL).length, 1);
    // Завершенный маршрут не дает открытых и просроченных событий; его неотмеченное событие видно отдельно (v3.1, ревью).
    assert.deepEqual(plain(items.filter(x => x.p.id === 'p3').map(x => x.status)), ['unclosed']);
    assert.equal(v.overdue.some(x => x.p.id === 'p3'), false);
});

test('плановая и фактическая даты не смешиваются', () => {
    const items = E.scheduleItems(patients(), TODAY, TERMINAL);
    const done = items.find(x => x.p.id === 'p1' && x.status === 'done');
    assert.equal(done.day, '2026-10-02', 'выполненное событие стоит на плановой дате');
    assert.equal(done.actualTime, '10:10'); assert.equal(done.matchedPlan, false);
    const noPlan = items.find(x => x.p.id === 'p4');
    assert.equal(noPlan.day, '2026-10-15', 'событие без плана — по фактической дате'); assert.equal(noPlan.noPlan, true);
});

test('событие без плановой даты — отдельный список, не ошибка', () => {
    const v = E.dayView(E.scheduleItems(patients(), TODAY, TERMINAL), TODAY);
    assert.deepEqual(plain(v.nodate.map(x => x.p.id)), ['p2']);
    assert.equal(v.nodate[0].status, 'nodate');
});

test('фильтры: поиск, врач, этап, статус; длинное ФИО ищется', () => {
    const items = E.scheduleItems(patients(), TODAY, TERMINAL);
    assert.equal(E.filterItems(items, { q: 'преображенская' }).length, 2);
    assert.ok(E.filterItems(items, { doctor: 'd2' }).every(x => x.p.doctorId === 'd2'));
    assert.ok(E.filterItems(items, { stage: 'exam' }).every(x => x.stage === 'exam'));
    assert.ok(E.filterItems(items, { status: 'done' }).every(x => x.status === 'done'));
    assert.ok(E.filterItems(items, { status: 'open' }).every(x => x.status !== 'done'));
    assert.equal(E.filterItems(items, { q: 'нет такого' }).length, 0, 'пустое состояние');
});

test('переход в карточку: вкладка «События и даты» и нужный этап', () => {
    const items = E.scheduleItems(patients(), TODAY, TERMINAL);
    const x = items.find(i => i.p.id === 'p1' && i.plannedDate === '2026-10-12');
    assert.equal(E.eventHref(x), '#/patient/p1?tab=events&stage=1');
});

test('расписание только читает данные пациентов', () => {
    const ps = patients(); const before = plain(ps);
    const items = E.scheduleItems(ps, TODAY, TERMINAL);
    E.dayView(E.filterItems(items, { q: 'а' }), TODAY); E.weekCounts(items, ['2026-10-12', '2026-10-15']);
    assert.deepEqual(plain(ps), before);
});

test('счётчики недели: просрочки отдельно, выполненные отдельно', () => {
    const c = E.weekCounts(E.scheduleItems(patients(), TODAY, TERMINAL), ['2026-10-12', '2026-10-15', '2026-10-20']);
    assert.deepEqual(plain(c), [{ date: '2026-10-12', open: 1, overdue: 1, done: 0 }, { date: '2026-10-15', open: 3, overdue: 0, done: 1 }, { date: '2026-10-20', open: 1, overdue: 0, done: 0 }]);
});

/* ---------- Регрессия ревью v3.1 ---------- */
test('matchedPlan: true — по плану, false — с отклонением, null/нет значения — не определено, без плана — отдельно', () => {
    const C = ctx.window.StaffUxCore;
    assert.equal(C.planMatchState({ matchedPlan: true }), 'match');
    assert.equal(C.planMatchState({ matchedPlan: false }), 'mismatch');
    assert.equal(C.planMatchState({ matchedPlan: null }), 'unknown');
    assert.equal(C.planMatchState({}), 'unknown', 'отсутствие значения не считается «по плану»');
    assert.equal(C.planMatchState({ noPlan: true, matchedPlan: true }), 'noplan');
    const ps = [pat('m', 'Тест План', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [
        ev('2026-10-05', '', { actualDate: '2026-10-05', matchedPlan: true }), ev('2026-10-06', '', { actualDate: '2026-10-07', matchedPlan: false }),
        ev('2026-10-08', '', { actualDate: '2026-10-08', matchedPlan: null }), { date: '2026-10-09', time: '', actualDate: '2026-10-09', actualTime: '', noPlan: false }] }])];
    assert.deepEqual(plain(E.scheduleItems(ps, TODAY, TERMINAL).map(x => x.planMatch)), ['match', 'mismatch', 'unknown', 'unknown']);
});

test('неотмеченные события завершенного маршрута и пройденного этапа не теряются и не считаются просрочками', () => {
    const ps = [
        pat('t', 'Завершенный Маршрут', 'd1', [{ stage: 'treatment', enteredAt: '2026-09-01', events: [ev('2026-09-20', '10:00')] }, { stage: 'completed', enteredAt: '2026-10-01', events: [] }]),
        pat('s', 'Пройденный Этап', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-25')] }, { stage: 'consult', enteredAt: '2026-10-10', events: [] }]),
    ];
    const items = E.scheduleItems(ps, TODAY, TERMINAL);
    const v = E.dayView(items, TODAY);
    assert.deepEqual(plain(v.unclosed.map(x => [x.p.id, x.reason, x.plannedDate])), [['t', 'route_closed', '2026-09-20'], ['s', 'stage_passed', '2026-10-25']]);
    assert.equal(v.overdue.length, 0, 'правило «Рабочего дня» не меняется');
    assert.equal(E.dayView(items, '2026-10-25').day.length, 0, 'в списке дня их нет');
    assert.deepEqual(plain(E.weekCounts(items, ['2026-10-25'])), [{ date: '2026-10-25', open: 0, overdue: 0, done: 0 }]);
    assert.equal(E.filterItems(items, { status: 'open' }).length, 2, 'фильтр «Не выполнено» включает «факт не отмечен» (ревью v3.1)');
    assert.equal(E.filterItems(items, {}).length, 2);
});

test('поиск в расписании: «е» и «ё» эквивалентны в обе стороны, данные не меняются', () => {
    const ps = [pat('a', 'Петров Пётр', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-16')] }]),
        pat('b', 'Семенов Олег', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-16')] }])];
    const items = E.scheduleItems(ps, TODAY, TERMINAL);
    assert.deepEqual(plain(E.filterItems(items, { q: 'Петров Петр' }).map(x => x.p.id)), ['a'], 'е → ё');
    assert.deepEqual(plain(E.filterItems(items, { q: 'СЕМЁНОВ' }).map(x => x.p.id)), ['b'], 'ё → е');
    assert.equal(ps[0].name, 'Петров Пётр', 'ФИО не изменено');
});

/* ---------- Заключительное ревью v3.1: единый статус карточки и расписания ---------- */
const C2 = ctx.window.StaffUxCore;
const mixed = () => [
    pat('a', 'Активная Просрочка', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-05')] }, { stage: 'consult', enteredAt: '2026-10-08', events: [ev('2026-10-10'), ev('2026-10-20')] }]),
    pat('r', 'Завершенный Маршрут', 'd1', [{ stage: 'treatment', enteredAt: '2026-09-01', events: [ev('2026-09-20', '10:00')] }, { stage: 'completed', enteredAt: '2026-10-01', events: [] }]),
    pat('s', 'Пройденный Этап', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-25')] }, { stage: 'consult', enteredAt: '2026-10-10', events: [] }]),
    pat('d', 'Выполненное Событие', 'd1', [{ stage: 'exam', enteredAt: '2026-10-01', events: [ev('2026-10-05', '', { actualDate: '2026-10-05', matchedPlan: true })] }]),
];
const st = (p, o, e) => plain(C2.eventState(p, o, e, TODAY, TERMINAL));

test('действующая просрочка текущего и прошлого этапа остается просрочкой', () => {
    const [a] = mixed();
    assert.deepEqual(st(a, 0, 0), { kind: 'overdue', open: true, days: -10 }, 'прошлый этап, дата в прошлом — учитывается «Рабочим днем»');
    assert.deepEqual(st(a, 1, 0), { kind: 'overdue', open: true, days: -5 });
    assert.equal(st(a, 1, 1).kind, 'planned');
    assert.equal(C2.overdueEvents(a, TODAY, TERMINAL).length, 2, 'алгоритм «Рабочего дня» не изменился');
});

test('завершенный маршрут без факта: «факт не отмечен», не просрочка; плановая дата и история не меняются', () => {
    const [, r] = mixed(); const before = plain(r);
    assert.deepEqual(st(r, 0, 0), { kind: 'unclosed', open: false, reason: 'route_closed' });
    assert.equal(C2.UNCLOSED_REASON.route_closed, 'маршрут завершен');
    assert.deepEqual(plain(r), before);
});

test('пройденный этап без факта: «факт не отмечен», причина — этап пройден', () => {
    const [, , s] = mixed();
    assert.deepEqual(st(s, 0, 0), { kind: 'unclosed', open: false, reason: 'stage_passed' });
    assert.equal(C2.UNCLOSED_REASON.stage_passed, 'этап пройден');
});

test('выполненное событие: статус по соответствию плану', () => {
    const [, , , d] = mixed();
    assert.deepEqual(st(d, 0, 0), { kind: 'done', planMatch: 'match' });
});

test('карточка и расписание дают одинаковый статус каждому событию', () => {
    const ps = [...mixed(), ...patients()];
    const items = E.scheduleItems(ps, TODAY, TERMINAL);
    const map = { overdue: 'overdue', today: 'today', planned: 'planned', done: 'done', unclosed: 'unclosed', nodate: 'nodate' };
    items.forEach(x => assert.equal(map[x.status], C2.eventState(x.p, x.occIndex, x.eventIndex, TODAY, TERMINAL).kind, `${x.p.id} ${x.occIndex}:${x.eventIndex}`));
    ps.forEach(p => p.history.forEach((o, oi) => o.events.forEach((e, ei) => {
        const k = C2.eventState(p, oi, ei, TODAY, TERMINAL).kind;
        if (k === 'unclosed') assert.ok(!items.some(x => x.p === p && x.status === 'overdue' && x.occIndex === oi && x.eventIndex === ei));
    })));
    assert.ok(items.some(x => x.status === 'unclosed') && items.some(x => x.status === 'overdue'));
});

test('фильтр «Не выполнено»: просрочки, будущие, без даты и «факт не отмечен»; отдельный блок сохраняется', () => {
    const items = E.scheduleItems([...mixed(), ...patients()], TODAY, TERMINAL);
    const open = E.filterItems(items, { status: 'open' });
    assert.ok(open.every(x => x.status !== 'done'));
    assert.deepEqual(plain([...new Set(open.map(x => x.status))].sort()), ['nodate', 'overdue', 'planned', 'today', 'unclosed']);
    const v = E.dayView(open, TODAY);
    assert.deepEqual(plain(v.unclosed.map(x => x.p.id).sort()), ['p3', 'r', 's'], 'блок «факт не отмечен» остается при фильтре');
    assert.ok(!v.overdue.some(x => x.status === 'unclosed'), 'unclosed не превращается в overdue');
});
