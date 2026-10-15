// Тесты создания пациента (newpatient-core.js). Только синтетические данные.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const ctx = { window: {} }; ctx.globalThis = ctx; vm.createContext(ctx);
vm.runInContext(readFileSync(here('../newpatient-core.js'), 'utf8'), ctx, { filename: 'newpatient-core.js' });
const N = ctx.window.StaffUxNewPatient;
const plain = (x) => JSON.parse(JSON.stringify(x));

const DOCTORS = [{ id: 'd1', name: 'Врач Один Тестович', short: 'Врач О. Т.', dept: 'thor' }, { id: 'd2', name: 'Врач Два Тестович', short: 'Врач Д. Т.', dept: 'abd' }];
const ICD = { lung: ['C34.1', 'C34.9'], colon: ['C18.7'] };
let seq = 0;
const context = (role = 'doctor', extra = {}) => ({
    role, userId: role === 'head' ? 'h1' : 'd1', userName: 'Тест', today: '2026-10-15', now: '2026-10-15T10:00', doctors: DOCTORS, headDepts: role === 'head' ? ['thor'] : [],
    isKnownDiagnosis: (loc, code) => (ICD[loc] || []).includes(code), nosologyFor: (c) => c.startsWith('C34') ? 'lung' : '', nosologyLabel: (k) => k ? 'Рак лёгкого' : '',
    localizationLabel: (k) => k, newId: () => `p-new-${++seq}`, offline: false, ...extra,
});
const form = (extra = {}) => ({ name: '  Тестова   Анна  ', age: '54', phone: '+7 900 000-00-01', policy: '1234 5678-90', localization: 'lung', diagnosis: 'C34.1', stage: 'exam',
    planDate: '2026-10-20', planTime: '', treatmentType: '', factDate: '', factTime: '', priority: 'normal', doctorId: 'd1', head: 'Заведующий Тест', pgiResult: '', pgiResultDate: '', complaint: '', ...extra });
const state = () => ({ inFlight: false, done: {}, created: [], audit: [] });

test('успешное создание: серверный id, нормализация, этап — выбранный, аудит', () => {
    const s = state();
    const { patient: p, repeated } = N.create(s, form({ requestKey: 'k1' }), context());
    assert.equal(repeated, false);
    assert.match(p.id, /^p-new-/);
    assert.equal(p.name, 'Тестова Анна'); assert.equal(p.policy, '1234567890'); assert.equal(p.age, 54);
    assert.equal(p.stage, 'exam'); assert.equal(p.history.length, 1, 'этап не меняется автоматически');
    assert.equal(p.doctorId, 'd1'); assert.equal(p.nosology, 'lung');
    assert.deepEqual(plain(s.audit.map(a => a.event)), ['patient_created']);
});

test('обязательные поля: ФИО, диагноз, этап, заведующий, фактическая дата первичного приёма', () => {
    const v = N.validate(form({ name: ' ', diagnosis: '', stage: '', head: '' }), context());
    assert.deepEqual(plain(v.order), ['name', 'diagnosis', 'stage', 'head']);
    assert.equal(v.errors.name, 'Укажите ФИО пациента.');
    const p = N.validate(form({ stage: 'primary-appointment', factDate: '' }), context());
    assert.equal(p.errors.factDate, 'Для первичного приема укажите фактическую дату — это обязательное поле.');
    const opt = N.validate(form({ age: '', phone: '', policy: '', planDate: '' }), context());
    assert.equal(opt.order.length, 0, 'возраст, телефон, полис и плановая дата необязательны');
});

test('недопустимый МКБ-10: код не из списка или не из выбранной локализации', () => {
    assert.ok(N.validate(form({ diagnosis: 'X99.9' }), context()).errors.diagnosis);
    assert.ok(N.validate(form({ localization: 'colon', diagnosis: 'C34.1' }), context()).errors.diagnosis);
    assert.ok(N.validate(form({ diagnosis: 'рак лёгкого' }), context()).errors.diagnosis, 'свободный текст не принимается');
});

test('полис, ПГИ, время без даты — тексты ошибок как в приложении', () => {
    assert.equal(N.validate(form({ policy: '<b>1</b>' }), context()).errors.policy, 'Номер полиса ОМС не должен содержать HTML или JavaScript');
    assert.equal(N.validate(form({ policy: '1'.repeat(33) }), context()).errors.policy, 'Номер полиса ОМС не должен быть длиннее 32 символов');
    assert.equal(N.validate(form({ pgiResult: 'confirmed' }), context()).errors.pgiResultDate, 'Укажите дату результата ПГИ.');
    assert.ok(N.validate(form({ planDate: '', planTime: '10:00' }), context()).errors.planDate);
    assert.equal(N.validate(form({ planDate: '', planTime: '' }), context()).order.length, 0, 'событие без даты и времени допустимо');
});

test('возможный дубль: телефон, ФИО+возраст, ФИО+диагноз, ФИО+путь; ё=е; полис не учитывается', () => {
    const ps = [{ id: 'a', name: 'Тестова Анна', age: 54, phone: '8 900 000 00 01', icd: 'C34.9', nosology: 'lung', policy: '1234567890' },
        { id: 'b', name: 'Другая Персона', age: 30, phone: '', icd: 'C18.7', nosology: '', policy: '1234567890' }];
    const d = N.findDuplicates(form({ name: 'тестова  анна' }), ps, 'lung');
    assert.deepEqual(plain(d.map(x => [x.patient.id, x.reasons])), [['a', ['совпадает телефон', 'совпадают ФИО и возраст', 'совпадают ФИО и клиентский путь']]]);
    assert.equal(N.findDuplicates(form({ name: 'Тёстова Анна', phone: '', age: '' }), [{ ...ps[0], name: 'Тестова Анна' }], '').length, 0, 'одного ФИО без второго признака мало');
    assert.equal(N.findDuplicates(form({ name: 'Тёстова Анна', phone: '', age: '54' }), [{ ...ps[0], name: 'Тестова Анна' }], '').length, 1, 'ё приравнивается к е');
});

test('повторное нажатие: во время сохранения — отказ, тот же ключ — тот же пациент без второй записи', () => {
    const s = state(); s.inFlight = true;
    assert.throws(() => N.create(s, form({ requestKey: 'k' }), context()), (e) => e.code === 'in_flight');
    s.inFlight = false;
    const a = N.create(s, form({ requestKey: 'k' }), context());
    const b = N.create(s, form({ requestKey: 'k' }), context());
    assert.equal(b.repeated, true); assert.equal(b.patient.id, a.patient.id); assert.equal(s.created.length, 1); assert.equal(s.audit.length, 1);
});

test('потеря соединения (демо): пациент не создан, состояние и форма не изменены', () => {
    const s = state(); const f = form({ requestKey: 'k2' }); const before = plain(f);
    assert.throws(() => N.create(s, f, context('doctor', { offline: true })), (e) => e.code === 'network' && /данные формы сохранены/.test(e.message));
    assert.deepEqual(plain(f), before, 'введённые значения сохраняются');
    assert.equal(s.created.length, 0); assert.deepEqual(plain(s.done), {}, 'ключ не занят — повтор после восстановления связи создаст пациента');
    assert.equal(N.create(s, f, context()).repeated, false);
});

test('ошибка проверки: значения формы не теряются и не меняются', () => {
    const s = state(); const f = form({ name: '', complaint: 'текст' }); const before = plain(f);
    assert.throws(() => N.create(s, f, context()), (e) => e.code === 'validation' && e.order.includes('name'));
    assert.deepEqual(plain(f), before); assert.equal(s.created.length, 0);
});

test('роли: ограничение создания и выбора врача', () => {
    for (const role of ['deputy', 'caop']) assert.equal(N.validate(form(), context(role)).errors.doctorId, 'Ваша роль не позволяет создавать пациентов.');
    assert.equal(N.validate(form({ doctorId: 'd2' }), context('doctor')).errors.doctorId, 'Врач может создать пациента только с закреплением за собой.');
    assert.equal(N.validate(form({ doctorId: '' }), context('head')).errors.doctorId, 'Выберите лечащего врача из вашего отделения.');
    assert.equal(N.validate(form({ doctorId: 'd2' }), context('head')).errors.doctorId, 'Лечащий врач должен быть из отделения, которым вы управляете.');
    assert.equal(N.validate(form({ doctorId: 'd1' }), context('head')).order.length, 0);
    assert.equal(N.validate(form({ doctorId: '' }), context('developer')).doctorId, '', 'разработчик может оставить врача несопоставленным');
    assert.equal(N.validate(form({ doctorId: 'x' }), context('developer')).errors.doctorId, 'Выбранный пользователь не является активным врачом.');
    assert.equal(N.validate(form({ doctorId: '' }), context('chair', { userId: 'c1' })).doctorId, 'c1', 'председатель закрепляет за собой');
});

test('клиентский id запрещён — проверку идентификатора не обойти', () => {
    assert.throws(() => N.create(state(), { ...form(), id: 'p-1' }, context()), (e) => e.code === 'id_forbidden');
});

test('события создаются только из заполненных полей, скрытых событий нет', () => {
    assert.deepEqual(plain(N.plannedEvents(form({ planDate: '' }))), []);
    assert.deepEqual(plain(N.plannedEvents(form({ stage: 'completed', planDate: '2026-10-20' }))), [], 'для завершающих этапов событие не создаётся');
    const prim = N.plannedEvents(form({ stage: 'primary-appointment', factDate: '2026-10-14', factTime: '09:00' }));
    assert.deepEqual(plain(prim), [{ date: '', time: '', actualDate: '2026-10-14', actualTime: '09:00', matchedPlan: null, noPlan: true }]);
    const tr = N.plannedEvents(form({ stage: 'treatment', planDate: '2026-10-21', treatmentType: 'chemo' }));
    assert.equal(tr.length, 1); assert.equal(tr[0].treatmentType, 'chemo'); assert.equal(tr[0].actualDate, '', 'плановая дата не становится фактической');
});

test('создание не меняет данные других пациентов', () => {
    const others = [{ id: 'a', name: 'Тестова Анна', age: 54, history: [{ stage: 'exam', events: [] }] }]; const before = plain(others);
    N.findDuplicates(form(), others, 'lung'); N.create(state(), form(), context());
    assert.deepEqual(plain(others), before);
});

/* ---------- Регрессия ревью v3.1 ---------- */
const sess = (role = 'doctor', userId = 'd1', gen = 0, loggedOut = false) => ({ role, userId, gen, loggedOut });

test('смена роли во время сохранения: создание отменяется, от имени другого пользователя не сохраняется', () => {
    const s = state(); const c = { ...context('doctor'), session: sess('doctor', 'd1', 0) };
    assert.throws(() => N.create(s, form({ requestKey: 'r1' }), c, sess('developer', 'dev', 1)), (e) => e.code === 'context_changed');
    assert.equal(s.created.length, 0); assert.equal(s.audit.length, 0); assert.deepEqual(plain(s.done), {}, 'ключ не занят');
});

test('выход и повторный вход той же ролью во время сохранения тоже отменяют создание', () => {
    const c = { ...context('doctor'), session: sess('doctor', 'd1', 3) };
    assert.throws(() => N.create(state(), form(), c, sess('doctor', 'd1', 3, true)), (e) => e.code === 'context_changed', 'выход');
    assert.throws(() => N.create(state(), form(), c, sess('doctor', 'd1', 4)), (e) => e.code === 'context_changed', 'новый сеанс той же роли');
    assert.throws(() => N.create(state(), form(), c, null), (e) => e.code === 'context_changed', 'контекст потерян (форма сброшена)');
});

test('тот же сеанс: создание проходит один раз; повтор с тем же ключом не создает дубль', () => {
    const s = state(); const c = { ...context('doctor'), session: sess() };
    const a = N.create(s, form({ requestKey: 'r2' }), c, sess());
    const b = N.create(s, form({ requestKey: 'r2' }), c, sess());
    assert.equal(a.patient.createdBy, 'd1'); assert.equal(b.repeated, true); assert.equal(s.created.length, 1);
});

test('поиск диагноза: «е» и «ё» эквивалентны в обе стороны, регистр не важен, данные не меняются', () => {
    const list = [{ code: 'D48.9', label: 'Новообразование неопределённого характера' }, { code: 'C34.1', label: 'Верхняя доля, бронхи или легкое' }];
    const before = plain(list);
    assert.deepEqual(plain(N.searchDiagnoses(list, 'неопределенного').map(d => d.code)), ['D48.9'], 'е → ё');
    assert.deepEqual(plain(N.searchDiagnoses(list, 'ЛЁГКОЕ').map(d => d.code)), ['C34.1'], 'ё → е');
    assert.deepEqual(plain(N.searchDiagnoses(list, 'c34').map(d => d.code)), ['C34.1'], 'по коду');
    assert.deepEqual(plain(list), before);
});

test('нормализация ФИО для дублей (/ё/g) сохранена', () => {
    const p = [{ id: 'x', name: 'Семёнов Пётр', age: 40, phone: '', icd: '', nosology: '' }];
    assert.equal(N.findDuplicates(form({ name: 'семенов петр', phone: '', age: '40' }), p, '').length, 1);
});

/* Реальные справочники прототипа: МКБ-10 (17 групп, 726 кодов — как в приложении) и клиентские пути. */
const R = { window: {} }; R.globalThis = R; vm.createContext(R);
for (const f of ['../../redesign/icd10.js', '../../redesign/nosology.js', '../client-path.js', '../newpatient-core.js']) vm.runInContext(readFileSync(here(f), 'utf8'), R, { filename: f });
const ICD10 = R.window.ICD10, NOS = R.window.NOSOLOGY_REF, CP = R.window.StaffUxClientPath, NR = R.window.StaffUxNewPatient;

test('справочник МКБ-10 полный: 17 групп и 726 кодов, как в static/app.js приложения', () => {
    assert.equal(Object.keys(ICD10).length, 17);
    assert.equal(Object.values(ICD10).reduce((n, g) => n + g.diagnoses.length, 0), 726);
    const hit = NR.searchDiagnoses(ICD10.uncertain_unknown_behavior.diagnoses, 'неопределенного', 1000);
    assert.ok(hit.length > 0 && hit.every(d => /неопредел[её]нного/i.test(d.label)), 'запрос с «е» находит «неопределённого»');
});

test('диагноз из каждой группы выбирается и сохраняется; клиентский путь — по правилу приложения', () => {
    const rctx = () => ({ ...context('doctor'), isKnownDiagnosis: (loc, code) => !!(ICD10[loc] && ICD10[loc].diagnoses.some(d => d.code === code)),
        nosologyFor: (c) => { const k = CP.suggestedNosologyForDiagnosis(c); return NOS[k] ? k : ''; }, nosologyLabel: (k) => (NOS[k] || {}).label || '', localizationLabel: (k) => ICD10[k].label });
    for (const [loc, g] of Object.entries(ICD10)) {
        for (const d of [g.diagnoses[0], g.diagnoses[g.diagnoses.length - 1]]) {
            const s = state();
            const { patient: p } = NR.create(s, form({ localization: loc, diagnosis: d.code }), rctx());
            assert.equal(p.icd, d.code, `${loc} ${d.code}`); assert.equal(p.localization, g.label, 'подпись справочника сохраняется без изменений');
            const k = CP.suggestedNosologyForDiagnosis(d.code);
            assert.equal(p.nosology, NOS[k] ? k : '', `${d.code}: путь как в приложении`);
        }
        assert.ok(NR.validate(form({ localization: loc, diagnosis: 'ZZZ' }), rctx()).errors.diagnosis, `${loc}: код вне справочника отклоняется`);
    }
    assert.equal(CP.suggestedNosologyForDiagnosis('C34.1'), 'lung12');
    assert.equal(CP.suggestedNosologyForDiagnosis('C50.9'), 'breast');
});
