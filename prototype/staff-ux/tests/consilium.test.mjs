// Тесты логики онкоконсилиума прототипа (consilium-core.js). Только синтетические данные.
//   node --test prototype/staff-ux/tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const ctxVm = { window: {} }; ctxVm.globalThis = ctxVm; vm.createContext(ctxVm);
for (const f of ['../core.js', '../consilium-core.js']) vm.runInContext(readFileSync(here(f), 'utf8'), ctxVm, { filename: f });
const K = ctxVm.window.StaffUxConsilium;
const C = ctxVm.window.StaffUxCore;
const plain = (x) => JSON.parse(JSON.stringify(x));

const TODAY = '2026-10-15';                   // четверг
const TREATMENTS = ['radiotherapy', 'chemotherapy', 'surgery', 'active_observation', 'dispensary_observation'];
const ctx = (extra = {}) => ({ today: TODAY, now: `${TODAY}T10:40`, rules: { wp23: true }, treatments: TREATMENTS, ...extra });
const PERMS = {
    doctor: { accessConsilium: true, bookConsilium: true },
    head: { accessConsilium: true, bookConsilium: true },
    chair: { accessConsilium: true, manageConsilium: true, decideConsilium: true, consiliumChair: true, useConsiliumFavorites: true, markReferralCompletion: true },
    developer: { accessConsilium: true, bookConsilium: true, manageConsilium: true },
    deputy: {}, caop: {},
};
const patients = {
    p1: { id: 'p1', name: 'Иванов Иван Иванович', icd: 'C34.1', stage: 'consult', history: [{ stage: 'consult', events: [{ date: '2026-10-14', actualDate: '' }] }] },
    p2: { id: 'p2', name: 'Константинопольская-Преображенская Александра-Виктория Вячеславовна', icd: 'C34.2', stage: 'consult', history: [] },
    p3: { id: 'p3', name: 'Петрова Анна', icd: 'C50.9', stage: 'consult', history: [] },
};
const actor = (role, userId = `u-${role}`, own = Object.keys(patients)) => ({ userId, name: role, role, perms: PERMS[role], canEditPatient: (p) => own.includes(p.id) && !!PERMS[role].bookConsilium });
const state = () => ({ consilium: [], slots: [], deleted: [], audit: [], favorites: {} });
const fields = { localization: 'Органы дыхания', icd: 'C34.1', comment: 'Синтетический комментарий' };
const bookP1 = (st, extra = {}) => K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-16', time: '10:30', ...fields, ...extra }, ctx());

test('свободный, занятый, прошедший и выходной слоты', () => {
    const st = state();
    const slots = K.allSlots(st, TODAY, ['thoracic']);
    const at = (d, t) => slots.find(x => x.date === d && x.time === t);
    assert.equal(K.slotState(st, at('2026-10-16', '10:30'), ctx()).state, 'free');
    bookP1(st);
    assert.equal(K.slotState(st, at('2026-10-16', '10:30'), ctx()).state, 'occupied');
    assert.equal(K.slotState(st, at(TODAY, '09:00'), ctx()).state, 'past', 'сегодня 09:00 уже прошло (now 10:40)');
    assert.equal(K.slotState(st, at(TODAY, '11:00'), ctx()).state, 'free');
    assert.equal(K.slotState(st, at('2026-10-17', '10:00'), ctx()).state, 'weekend');
    // Без правил WP2.3 — поведение текущего backend: прошлое и выходные не проверяются.
    assert.equal(K.slotState(st, at('2026-10-17', '10:00'), ctx({ rules: { wp23: false } })).state, 'free');
    assert.equal(K.slotState(st, at(TODAY, '09:00'), ctx({ rules: { wp23: false } })).state, 'free');
});

test('пустое расписание следующей недели — записаться нельзя; явно открытый выходной — можно', () => {
    const st = state();
    const s = K.daySummary(st, '2026-10-20', 'thoracic', ctx());
    assert.equal(s.opened, false); assert.equal(s.total, 0);
    const chk = K.bookingCheck(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-20', time: '10:00', ...fields }, ctx());
    assert.equal(chk.code, 'slot_missing');
    K.generateSlots(st, { date: '2026-10-24', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, actor('chair'), ctx());
    const sat = K.allSlots(st, TODAY, ['thoracic']).find(x => x.date === '2026-10-24' && x.time === '10:00');
    assert.equal(K.slotState(st, sat, ctx()).state, 'free', 'суббота, открытая председателем, доступна');
});

test('запись пациента: статус «Записан», этап и события пациента не меняются', () => {
    const st = state(); const before = plain(patients.p1);
    const rec = bookP1(st);
    assert.equal(rec.status, 'recorded'); assert.equal(rec.group, 'thoracic'); assert.equal(rec.time, '10:30');
    assert.deepEqual(plain(patients.p1), before);
    assert.deepEqual(plain(st.audit.map(a => a.event)), ['consilium_appointment_created']);
});

test('повторная запись того же пациента и чужой группы отклоняется', () => {
    const st = state(); bookP1(st);
    assert.throws(() => bookP1(st, { time: '11:00' }), (e) => e.code === 'already_booked');
    assert.throws(() => K.book(st, { actor: actor('doctor'), patient: patients.p3, group: 'thoracic', date: '2026-10-16', time: '11:00', ...fields, icd: 'C50.9' }, ctx()), (e) => e.code === 'group_mismatch');
    assert.equal(st.consilium.length, 1);
});

test('изменение доступности между выбором и подтверждением: слот занят — ошибка, данные не записаны', () => {
    const st = state();
    const chosen = { date: '2026-10-16', time: '10:30' };
    assert.equal(K.bookingCheck(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', ...chosen, ...fields }, ctx()).ok, true);
    K.book(st, { actor: actor('doctor', 'u-other'), patient: patients.p2, group: 'thoracic', ...chosen, ...fields, icd: 'C34.2' }, ctx());   // другой сотрудник
    assert.throws(() => bookP1(st), (e) => e.code === 'slot_occupied');
    assert.equal(st.consilium.filter(c => c.patientId === 'p1').length, 0);
});

test('нельзя записать на прошедшее время и в выходной без явной настройки (WP2.3)', () => {
    const st = state();
    assert.throws(() => bookP1(st, { date: TODAY, time: '09:00' }), (e) => e.code === 'slot_past');
    assert.throws(() => bookP1(st, { date: '2026-10-18', time: '10:00' }), (e) => e.code === 'slot_weekend');
});

test('возврат на доработку, история возвратов, доработка и повторное рассмотрение', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    assert.throws(() => K.returnForRevision(st, rec.id, 'x', chair, ctx()), (e) => e.code === 'not_in_review');
    K.startReview(st, rec.id, chair, ctx());
    assert.throws(() => K.returnForRevision(st, rec.id, '  ', chair, ctx()), (e) => e.code === 'comment_required');
    K.returnForRevision(st, rec.id, 'Приложите КТ', chair, ctx());
    assert.equal(rec.status, 'needs_revision');
    assert.throws(() => K.startReview(st, rec.id, chair, ctx()), (e) => e.code === 'not_reviewable', 'до доработки рассмотрение не начать');
    K.update(st, rec.id, { actor: actor('doctor'), patient: patients.p1, date: rec.date, time: rec.time, ...fields, comment: 'КТ приложено' }, ctx());
    assert.equal(rec.status, 'revised');
    K.startReview(st, rec.id, chair, ctx());
    assert.throws(() => K.returnForRevision(st, rec.id, 'Приложите КТ', chair, ctx()), (e) => e.code === 'comment_repeated');
    K.returnForRevision(st, rec.id, 'Нужна ИГХ', chair, ctx());
    assert.deepEqual(plain(rec.revisions.map(r => [r.round, r.text])), [[1, 'Приложите КТ'], [2, 'Нужна ИГХ']]);
});

test('подписание: только из «На рассмотрении», схема для химиотерапии, неизменяемость, просмотр решения', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    const sctx = ctx({ patientStage: 'consult', doctorName: 'Смирнова Е. В.' });
    assert.throws(() => K.sign(st, rec.id, { treatment: 'surgery' }, chair, sctx), (e) => e.code === 'not_in_review');
    K.startReview(st, rec.id, chair, ctx());
    assert.throws(() => K.sign(st, rec.id, { treatment: '' }, chair, sctx), (e) => e.code === 'treatment_required');
    assert.throws(() => K.sign(st, rec.id, { treatment: 'chemotherapy', schemeName: '' }, chair, sctx), (e) => e.code === 'scheme_required');
    K.sign(st, rec.id, { treatment: 'chemotherapy', schemeName: 'FOLFOX', schemeCode: 'sh0001', goc: true }, chair, sctx);
    assert.equal(rec.status, 'signed'); assert.deepEqual(plain(rec.scheme), { code: 'sh0001', name: 'FOLFOX' }); assert.equal(rec.routeStage, 'consult');
    assert.throws(() => K.update(st, rec.id, { actor: actor('doctor'), patient: patients.p1, date: rec.date, time: rec.time, ...fields }, ctx()), (e) => e.code === 'not_editable');
    assert.throws(() => K.removeOwned(st, rec.id, actor('doctor'), ctx()), (e) => e.code === 'not_editable');
    assert.throws(() => K.startReview(st, rec.id, chair, ctx()), (e) => e.code === 'not_reviewable');
    const s = C.consiliumSummary(st.consilium, 'p1');
    assert.deepEqual(plain(s.signed.map(x => x.treatment)), ['chemotherapy']);
});

test('подписание не переводит этап и не создаёт ручную отметку решения ОК', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    const p = plain(patients.p1); const before = plain(p);
    K.startReview(st, rec.id, chair, ctx());
    K.sign(st, rec.id, { treatment: 'surgery' }, chair, ctx({ patientStage: p.stage }));
    assert.deepEqual(p, before, 'пациент не изменён');
    assert.equal(C.manualDecisions(p).length, 0, 'ручная отметка не появилась');
    assert.equal(st.consilium[0].consiliumDecision, undefined);
});

test('ручная отметка решения ОК не подписывает решение председателя', () => {
    const st = state(); const rec = bookP1(st);
    const p = { id: 'p1', stage: 'consult', history: [{ stage: 'consult', enteredAt: '2026-10-01', events: [{ date: '2026-10-14', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false }] }] };
    C.applyManualDecision(p, { role: 'doctor', inScope: true, eventIndex: 0, decision: 'treatment_strategy_defined', at: `${TODAY}T10:00`, by: 'Врач' });
    assert.equal(rec.status, 'recorded');
    assert.equal(C.consiliumSummary(st.consilium, 'p1').signed.length, 0);
});

test('ограничения ролей', () => {
    const st = state(); const rec = bookP1(st);
    for (const r of ['chair', 'deputy', 'caop']) assert.equal(K.bookingCheck(st, { actor: actor(r), patient: patients.p3, group: 'breast', date: '2026-10-16', time: '11:00', ...fields, icd: 'C50.9' }, ctx()).code, 'forbidden_role', r);
    for (const r of ['doctor', 'head', 'developer']) assert.throws(() => K.startReview(st, rec.id, actor(r), ctx()), (e) => e.code === 'forbidden_role', r);
    assert.throws(() => K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, actor('developer'), ctx()), (e) => e.code === 'forbidden_role', 'разработчику сетка в интерфейсе недоступна');
    assert.throws(() => K.update(st, rec.id, { actor: actor('head', 'u-head'), patient: patients.p1, date: rec.date, time: '11:00', ...fields }, ctx()), (e) => e.code === 'forbidden_author');
    assert.equal(K.bookingCheck(st, { actor: actor('doctor', 'u-doc', []), patient: patients.p3, group: 'breast', date: '2026-10-16', time: '11:00', ...fields, icd: 'C50.9' }, ctx()).code, 'forbidden_patient', 'чужой пациент');
    const viewer = (role, userId, inScope) => ({ userId, role, perms: PERMS[role], inScope: () => inScope });
    assert.equal(K.recordView(rec, viewer('chair', 'u-chair', false)).kind, 'full');
    assert.equal(K.recordView(rec, viewer('doctor', 'u-doctor', true)).kind, 'full');
    assert.equal(K.recordView(rec, viewer('doctor', 'u-other', false)).kind, 'busy');
    assert.equal(K.recordView(rec, viewer('head', 'u-head', true)).kind, 'department');
});

test('архив: только автор и только подписанная запись; председатель не архивирует', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    assert.throws(() => K.setArchived(st, rec.id, true, actor('doctor'), ctx()), (e) => e.code === 'forbidden');
    K.startReview(st, rec.id, chair, ctx()); K.sign(st, rec.id, { treatment: 'surgery' }, chair, ctx());
    assert.throws(() => K.setArchived(st, rec.id, true, chair, ctx()), (e) => e.code === 'forbidden');
    K.setArchived(st, rec.id, true, actor('doctor'), ctx());
    assert.equal(rec.archived, true); assert.equal(rec.status, 'signed', 'архив не меняет решение');
    assert.throws(() => K.setArchived(st, rec.id, true, actor('doctor'), ctx()), (e) => e.code === 'archive_state');
});

test('удаление своей записи: с историей возвратов — мягкое', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    K.startReview(st, rec.id, chair, ctx()); K.returnForRevision(st, rec.id, 'Уточните', chair, ctx());
    const res = K.removeOwned(st, rec.id, actor('doctor'), ctx());
    assert.equal(res.soft, true); assert.equal(st.deleted.length, 1); assert.equal(st.deleted[0].revisions.length, 1);
    assert.equal(K.bookingCheck(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-16', time: '10:30', ...fields }, ctx()).ok, true, 'слот и пациент снова доступны');
});

test('слоты председателя: проверки и удаление только свободного', () => {
    const st = state(); const chair = actor('chair');
    assert.throws(() => K.generateSlots(st, { date: '2026-10-10', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, chair, ctx()), (e) => e.code === 'date_past');
    assert.throws(() => K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '11:00', end: '10:00', interval: '30' }, chair, ctx()), (e) => e.code === 'time_order');
    assert.throws(() => K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '07:00', end: '20:00', interval: '30' }, chair, ctx()), (e) => e.code === 'too_long');
    const created = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, chair, ctx());
    assert.deepEqual(plain(created.map(x => x.time)), ['10:00', '10:30']);
    K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-21', time: '10:00', ...fields }, ctx());
    assert.throws(() => K.deleteSlot(st, created[0].id, chair, ctx()), (e) => e.code === 'slot_occupied');
    K.deleteSlot(st, created[1].id, chair, ctx());
    assert.equal(st.slots.length, 1);
    assert.throws(() => K.deleteSlot(st, 'week-2026-10-16-10:00-thoracic', chair, ctx()), (e) => e.code === 'not_found', 'слот автоматической недели не удаляется');
});

test('заседание без записей и записи без времени', () => {
    assert.deepEqual(plain(K.sessionStats([])), { total: 0, awaiting: 0, inReview: 0, returned: 0, signed: 0 });
    const recs = [
        { id: 'a', date: TODAY, time: '', patientName: 'Б', status: 'recorded' },
        { id: 'b', date: TODAY, time: '09:00', patientName: 'Константинопольская-Преображенская Александра-Виктория Вячеславовна', status: 'signed' },
        { id: 'c', date: TODAY, time: '09:00', patientName: 'А', status: 'needs_revision' },
    ];
    assert.deepEqual(recs.slice().sort(K.sortByTime).map(x => x.id), ['c', 'b', 'a'], 'без времени — в конце, не выдумывается');
    assert.deepEqual(plain(K.sessionStats(recs)), { total: 3, awaiting: 1, inReview: 0, returned: 1, signed: 1 });
});

test('избранное и отметка направления — права председателя и подписанная хирургия', () => {
    const st = state(); const rec = bookP1(st); const chair = actor('chair');
    assert.throws(() => K.toggleFavorite(st, 'p1', actor('doctor'), ctx()), (e) => e.code === 'forbidden_role');
    assert.equal(K.toggleFavorite(st, 'p1', chair, ctx()), true);
    assert.throws(() => K.toggleFavorite(st, 'p3', chair, ctx()), (e) => e.code === 'not_found');
    assert.throws(() => K.setReferralCompleted(st, rec.id, true, chair, ctx()), (e) => e.code === 'not_surgery');
    K.startReview(st, rec.id, chair, ctx()); K.sign(st, rec.id, { treatment: 'surgery' }, chair, ctx());
    K.setReferralCompleted(st, rec.id, true, chair, ctx());
    assert.equal(rec.referralCompleted, true);
});

test('длинное ФИО сохраняется полностью; группа определяется кодом МКБ-10', () => {
    const st = state();
    const rec = K.book(st, { actor: actor('doctor'), patient: patients.p2, group: 'thoracic', date: '2026-10-16', time: '11:00', ...fields, icd: 'C34.2' }, ctx());
    assert.equal(rec.patientName, patients.p2.name);
    assert.equal(K.groupForDiagnosis('C50.9'), 'breast'); assert.equal(K.groupForDiagnosis('C18.7'), 'colorectal'); assert.equal(K.groupForDiagnosis('D12'), 'general');
});

/* ---------- Регрессия аудита v2: открытие слотов ---------- */
const slotKeys = (st, date, group = 'thoracic') => K.allSlots(st, TODAY, [group]).filter(x => x.date === date).map(x => `${x.time}|${x.isDefaultWeek ? 'auto' : 'saved'}`);

test('явное открытие выходного текущей недели: сохранённые слоты замещают автоматические, остальные остаются недоступными', () => {
    const st = state(); const chair = actor('chair');
    // запись, сделанная раньше на автоматический слот субботы (поведение текущего backend без правил WP2.3)
    const early = K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-17', time: '13:00', ...fields }, ctx({ rules: { wp23: false } }));
    const before = plain(early);
    const created = K.generateSlots(st, { date: '2026-10-17', group: 'thoracic', start: '10:00', end: '12:00', interval: '30' }, chair, ctx());
    assert.deepEqual(plain(created.map(x => x.time)), ['10:00', '10:30', '11:00', '11:30']);
    assert.equal(created.replacedAuto, 4);
    const all = K.allSlots(st, TODAY, ['thoracic']).filter(x => x.date === '2026-10-17');
    assert.equal(new Set(all.map(x => x.time)).size, all.length, 'нет дублей по времени');
    const at = (t) => all.find(x => x.time === t);
    assert.equal(at('10:00').isDefaultWeek, false);
    assert.equal(K.slotState(st, at('10:00'), ctx()).state, 'free', 'явно открытый слот выходного доступен');
    assert.equal(K.slotState(st, at('12:00'), ctx()).state, 'weekend', 'остальные автоматические слоты выходного недоступны');
    assert.equal(K.slotState(st, at('13:00'), ctx()).state, 'occupied', 'существующая запись сохранена');
    assert.deepEqual(plain(early), before);
    // врач записывает на явно открытый выходной; двойная запись на тот же слот невозможна
    const rec = K.book(st, { actor: actor('doctor'), patient: patients.p2, group: 'thoracic', date: '2026-10-17', time: '10:30', ...fields, icd: 'C34.2' }, ctx());
    assert.equal(rec.status, 'recorded');
    assert.throws(() => K.book(st, { actor: actor('head', 'u-head', ['p9']), patient: { ...patients.p1, id: 'p9', name: 'Синтетический Пациент' }, group: 'thoracic', date: '2026-10-17', time: '10:30', ...fields }, ctx()), (e) => e.code === 'slot_occupied');
    assert.throws(() => K.book(st, { actor: actor('doctor', 'u-doctor', ['p8']), patient: { ...patients.p1, id: 'p8', name: 'Ещё Пациент' }, group: 'thoracic', date: '2026-10-17', time: '12:00', ...fields }, ctx()), (e) => e.code === 'slot_weekend');
    // повторное открытие тех же слотов не создаёт дублей
    const again = K.generateSlots(st, { date: '2026-10-17', group: 'thoracic', start: '10:00', end: '12:00', interval: '30' }, chair, ctx());
    assert.equal(again.length, 0); assert.equal(again.skipped, 4);
    assert.equal(st.slots.length, 4);
});

test('расширение уже открытого дня: добавляются только новые слоты, записи и слоты не меняются', () => {
    const st = state(); const chair = actor('chair');
    const first = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '10:00', interval: '30' }, chair, ctx());
    assert.deepEqual(plain(first.map(x => x.time)), ['09:00', '09:30']);
    const rec = K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-21', time: '09:00', ...fields }, ctx());
    const before = { slots: plain(st.slots), rec: plain(rec) };
    const added = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '11:00', interval: '30' }, chair, ctx());
    assert.deepEqual(plain(added.map(x => x.time)), ['10:00', '10:30'], 'добавлены только новые');
    assert.equal(added.skipped, 2);
    assert.deepEqual(plain(st.slots.slice(0, 2)), before.slots, 'прежние слоты не изменены');
    assert.deepEqual(plain(rec), before.rec, 'запись не изменена');
    assert.deepEqual(slotKeys(st, '2026-10-21'), ['09:00|saved', '09:30|saved', '10:00|saved', '10:30|saved']);
    assert.equal(new Set(st.slots.map(x => x.id)).size, st.slots.length, 'идентификаторы уникальны');
    assert.equal(st.audit.filter(a => a.event === 'consilium_schedule_generated').map(a => a.detail).join(' | '), 'created=2;interval=30 | created=2;interval=30');
    // другой интервал поверх существующих: пересечения пропускаются
    const mixed = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '10:00', end: '11:00', interval: '15' }, chair, ctx());
    assert.deepEqual(plain(mixed.map(x => x.time)), ['10:15', '10:45']);
    assert.equal(mixed.skipped, 2);
});

test('интервал: допустимы только 15, 30, 60; при отказе расписание и аудит не меняются', () => {
    const st = state(); const chair = actor('chair');
    K.generateSlots(st, { date: '2026-10-22', group: 'thoracic', start: '09:00', end: '10:00', interval: '60' }, chair, ctx());
    const before = { slots: plain(st.slots), audit: plain(st.audit) };
    for (const bad of ['abc', '0', '-30', '45', '', '15.5', ' ', null, undefined, '1e1', 'Infinity', 'NaN']) {
        assert.throws(() => K.generateSlots(st, { date: '2026-10-22', group: 'thoracic', start: '10:00', end: '12:00', interval: bad }, chair, ctx()), (e) => e.code === 'interval_invalid', String(bad));
    }
    assert.deepEqual(plain(st.slots), before.slots);
    assert.deepEqual(plain(st.audit), before.audit);
    for (const ok of [15, '30', '60']) assert.ok(Array.isArray(K.generateSlots(st, { date: '2026-10-23', group: 'thoracic', start: '09:00', end: '10:00', interval: ok }, chair, ctx())));
    assert.deepEqual(plain(st.slots.filter(x => x.date === '2026-10-23').map(x => x.time)), ['09:00', '09:15', '09:30', '09:45']);
    assert.throws(() => K.generateSlots(st, { date: '2026-10-23', group: 'thoracic', start: '9:00', end: '10:00', interval: '30' }, chair, ctx()), (e) => e.code === 'time_order', 'некорректное время');
});

/* ---------- Регрессия: удаление сохранённых слотов (семантика WP2.1 delete_schedule_slot + schedule_slots) ---------- */
const slotAt = (st, date, time, group = 'thoracic') => K.allSlots(st, TODAY, [group]).filter(x => x.date === date && x.time === time);

test('будний день текущей недели: удаление сохранённого слота не закрывает время — возвращается автоматический', () => {
    const st = state(); const chair = actor('chair');
    const created = K.generateSlots(st, { date: '2026-10-16', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, chair, ctx());
    const impact = K.slotDeletionImpact(st, created[0].id, ctx());
    assert.equal(impact.fallback.state, 'free', 'предупреждение до удаления: время останется открытым');
    const res = K.deleteSlot(st, created[0].id, chair, ctx());
    assert.equal(res.fallback.state, 'free');
    const at = slotAt(st, '2026-10-16', '10:00');
    assert.equal(at.length, 1, 'ровно один слот на это время');
    assert.equal(at[0].isDefaultWeek, true, 'доступен автоматический слот');
    assert.equal(K.slotState(st, at[0], ctx()).state, 'free');
    assert.equal(st.audit.filter(a => a.event === 'consilium_schedule_slot_deleted').pop().detail, 'auto_slot_remains=free');
    // врач может записаться — это поведение WP2.1, а не закрытие времени
    assert.equal(K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-16', time: '10:00', ...fields }, ctx()).status, 'recorded');
});

test('выходной текущей недели: после удаления — автоматический слот выходного (недоступен только по демо-правилу WP2.3)', () => {
    const st = state(); const chair = actor('chair');
    const created = K.generateSlots(st, { date: '2026-10-17', group: 'thoracic', start: '10:00', end: '11:00', interval: '30' }, chair, ctx());
    assert.equal(K.slotDeletionImpact(st, created[0].id, ctx({ rules: { wp23: false } })).fallback.state, 'free', 'по текущему backend выходной снова открыт');
    const res = K.deleteSlot(st, created[0].id, chair, ctx());
    assert.equal(res.fallback.state, 'weekend');
    const at = slotAt(st, '2026-10-17', '10:00');
    assert.equal(at.length, 1); assert.equal(at[0].isDefaultWeek, true);
    assert.equal(K.slotState(st, at[0], ctx()).state, 'weekend');
    assert.equal(K.slotState(st, slotAt(st, '2026-10-17', '10:30')[0], ctx()).state, 'free', 'второй явно открытый слот не затронут');
});

test('явно открытый день следующей недели: удаление действительно закрывает время', () => {
    const st = state(); const chair = actor('chair');
    const created = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '10:00', interval: '30' }, chair, ctx());
    assert.equal(K.slotDeletionImpact(st, created[0].id, ctx()).fallback, null);
    const res = K.deleteSlot(st, created[0].id, chair, ctx());
    assert.equal(res.fallback, null);
    assert.equal(slotAt(st, '2026-10-21', '09:00').length, 0);
    assert.equal(K.bookingCheck(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-21', time: '09:00', ...fields }, ctx()).code, 'slot_missing');
    assert.equal(K.daySummary(st, '2026-10-21', 'thoracic', ctx()).total, 1);
});

test('занятый слот не удаляется; данные и журнал не меняются', () => {
    const st = state(); const chair = actor('chair');
    const created = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '10:00', interval: '30' }, chair, ctx());
    K.book(st, { actor: actor('doctor'), patient: patients.p1, group: 'thoracic', date: '2026-10-21', time: '09:00', ...fields }, ctx());
    const before = { slots: plain(st.slots), audit: plain(st.audit), cons: plain(st.consilium) };
    assert.throws(() => K.deleteSlot(st, created[0].id, chair, ctx()), (e) => e.code === 'slot_occupied');
    assert.deepEqual({ slots: plain(st.slots), audit: plain(st.audit), cons: plain(st.consilium) }, before);
    assert.throws(() => K.deleteSlot(st, 'week-2026-10-16-10:00-thoracic', chair, ctx()), (e) => e.code === 'not_found', 'автоматический слот не удаляется');
    assert.throws(() => K.deleteSlot(st, created[1].id, actor('developer'), ctx()), (e) => e.code === 'forbidden_role');
});

test('повторное открытие без новых слотов помечено в журнале как «без изменений»', () => {
    const st = state(); const chair = actor('chair');
    K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '10:00', interval: '30' }, chair, ctx());
    const slots = plain(st.slots);
    const again = K.generateSlots(st, { date: '2026-10-21', group: 'thoracic', start: '09:00', end: '10:00', interval: '30' }, chair, ctx());
    assert.equal(again.length, 0);
    assert.deepEqual(plain(st.slots), slots);
    const gen = st.audit.filter(a => a.event === 'consilium_schedule_generated');
    assert.deepEqual(plain(gen.map(a => [a.detail, a.changed])), [['created=2;interval=30', true], ['created=0;interval=30', false]]);
});
