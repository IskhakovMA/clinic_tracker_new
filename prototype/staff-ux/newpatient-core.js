/* Создание пациента — чистая логика без DOM. Правила — по контракту приложения:
   PatientCreateIn (models.py), resolve_create_doctor (policy.py), create_patient (store.py),
   клиентская форма addPatient (static/app.js) и правила возможных дублей data_quality._duplicate_groups.
   Новых медицинских правил нет. Идентификатор генерируется «сервером» прототипа, клиентский id не принимается.
   События создаются только из явно заполненных полей формы и показываются до сохранения. */
(function (root) {
    'use strict';

    const LIMITS = { name: 200, phone: 50, policyInput: 64, policy: 32, head: 200, complaint: 5000 };
    const STAGES = ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
    const PLAN_STAGES = ['exam', 'appointment', 'consult', 'treatment-prep', 'treatment'];      // плановая дата события этапа — необязательна
    const TREATMENT_STAGES = ['treatment-prep', 'treatment'];
    const TREATMENT_TYPES = ['', 'surgery', 'radiation', 'chemo', 'hormone'];
    const PRIORITIES = ['urgent', 'high', 'normal'];
    const PGI = ['', 'confirmed', 'not_confirmed'];
    const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
    const TIME_RE = /^([01]?[0-9]|2[0-3]):[0-5][0-9]$/;
    const validDate = (s) => { if (!DATE_RE.test(s)) return false; const [y, m, d] = s.split('-').map(Number); const x = new Date(y, m - 1, d); return x.getFullYear() === y && x.getMonth() === m - 1 && x.getDate() === d; };
    const collapse = (s) => String(s || '').replace(/\s+/g, ' ').trim();

    /* normalize_policy_number: пробелы и дефисы убираются, затем проверки (тексты — как на сервере). */
    function normalizePolicy(v) {
        const raw = String(v || '').trim();
        if (!raw) return { value: '' };
        if (raw.length > LIMITS.policyInput) return { error: `Номер полиса ОМС не должен быть длиннее ${LIMITS.policy} символов` };
        if ([...raw].some(ch => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return { error: 'Номер полиса ОМС содержит недопустимые управляющие символы' };
        if (raw.includes('<') || raw.includes('>')) return { error: 'Номер полиса ОМС не должен содержать HTML или JavaScript' };
        const value = raw.replace(/[ -]+/g, '');
        if (value.length > LIMITS.policy) return { error: `Номер полиса ОМС не должен быть длиннее ${LIMITS.policy} символов` };
        return { value };
    }

    /* Право создания (can_create_patient) и выбор лечащего врача (resolve_create_doctor). */
    const CAN_CREATE = { doctor: true, head: true, chair: true, developer: true, deputy: false, caop: false };
    function resolveDoctor(role, requestedId, ctx) {
        if (!CAN_CREATE[role]) return { error: 'Ваша роль не позволяет создавать пациентов.' };
        if (role === 'doctor' || role === 'chair') {
            if (requestedId && requestedId !== ctx.userId) return { error: 'Врач может создать пациента только с закреплением за собой.' };
            return { doctorId: ctx.userId };
        }
        if (role === 'head') {
            if (!requestedId) return { error: 'Выберите лечащего врача из вашего отделения.' };
            const d = ctx.doctors.find(x => x.id === requestedId);
            if (!d || !ctx.headDepts.includes(d.dept)) return { error: 'Лечащий врач должен быть из отделения, которым вы управляете.' };
            return { doctorId: d.id };
        }
        if (role === 'developer') {
            if (!requestedId) return { doctorId: '' };                                         // пациент без врача — «назначить позже»
            if (!ctx.doctors.some(x => x.id === requestedId)) return { error: 'Выбранный пользователь не является активным врачом.' };
            return { doctorId: requestedId };
        }
        return { error: 'Ваша роль не позволяет создавать пациентов.' };
    }

    /* Проверка формы. Возвращает { errors: {поле: текст}, order: [...] } — порядок как в форме приложения. */
    function validate(f, ctx) {
        const e = {};
        const name = collapse(f.name);
        if (!name) e.name = 'Укажите ФИО пациента.';
        else if (name.length > LIMITS.name) e.name = `ФИО не длиннее ${LIMITS.name} символов.`;
        if (String(f.age ?? '').trim() !== '') { const a = Number(f.age); if (!Number.isInteger(a) || a < 0 || a > 150) e.age = 'Возраст — целое число от 0 до 150.'; }
        if (String(f.phone || '').length > LIMITS.phone) e.phone = `Телефон не длиннее ${LIMITS.phone} символов.`;
        const pol = normalizePolicy(f.policy); if (pol.error) e.policy = pol.error;
        if (!f.localization || !f.diagnosis || !ctx.isKnownDiagnosis(f.localization, f.diagnosis)) e.diagnosis = 'Выберите локализацию и диагноз (код МКБ-10).';
        if (!STAGES.includes(f.stage)) e.stage = 'Выберите текущий этап лечения.';
        if (!PRIORITIES.includes(f.priority)) e.priority = 'Выберите приоритет.';
        if (!collapse(f.head)) e.head = 'Выберите заведующего.';
        if (!PGI.includes(f.pgiResult || '')) e.pgiResult = 'Выберите результат ПГИ.';
        if (f.pgiResult && !f.pgiResultDate) e.pgiResultDate = 'Укажите дату результата ПГИ.';
        else if (f.pgiResultDate && !validDate(f.pgiResultDate)) e.pgiResultDate = 'Дата результата ПГИ в формате ДД.ММ.ГГГГ.';
        if (String(f.complaint || '').length > LIMITS.complaint) e.complaint = `Комментарий не длиннее ${LIMITS.complaint} символов.`;
        if (f.stage === 'primary-appointment') {
            if (!f.factDate) e.factDate = 'Для первичного приема укажите фактическую дату — это обязательное поле.';
            else if (!validDate(f.factDate)) e.factDate = 'Дата в формате ДД.ММ.ГГГГ.';
            else if (f.factDate > ctx.today) e.factDate = 'Фактическая дата не может быть в будущем.';
            if (f.factTime && !TIME_RE.test(f.factTime)) e.factTime = 'Время в формате ЧЧ:ММ.';
        } else if (PLAN_STAGES.includes(f.stage)) {
            if (f.planDate && !validDate(f.planDate)) e.planDate = 'Дата в формате ДД.ММ.ГГГГ.';
            if (f.planTime && !f.planDate) e.planDate = 'Укажите плановую дату вместе со временем.';
            if (f.planTime && !TIME_RE.test(f.planTime)) e.planTime = 'Время в формате ЧЧ:ММ.';
            if (f.treatmentType && !TREATMENT_TYPES.includes(f.treatmentType)) e.treatmentType = 'Выберите вид лечения.';
        }
        const doc = resolveDoctor(ctx.role, f.doctorId, ctx);
        if (doc.error) e.doctorId = doc.error;
        const order = ['name', 'age', 'phone', 'policy', 'diagnosis', 'stage', 'factDate', 'factTime', 'planDate', 'planTime', 'treatmentType', 'priority', 'doctorId', 'head', 'pgiResult', 'pgiResultDate', 'complaint'].filter(k => e[k]);
        return { errors: e, order, doctorId: doc.doctorId };
    }

    /* События, которые будут созданы (buildEventsFromForm): только из заполненных полей, показываются до сохранения. */
    function plannedEvents(f) {
        if (f.stage === 'primary-appointment') return f.factDate ? [{ date: '', time: '', actualDate: f.factDate, actualTime: f.factTime || '', matchedPlan: null, noPlan: true }] : [];
        if (PLAN_STAGES.includes(f.stage) && (f.planDate || f.planTime)) return [{ date: f.planDate || '', time: f.planTime || '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false, ...(TREATMENT_STAGES.includes(f.stage) ? { treatmentType: f.treatmentType || '' } : {}) }];
        return [];
    }

    /* Возможные дубли — те же признаки, что data_quality._duplicate_groups: телефон (≥10 цифр),
       ФИО + возраст, ФИО + диагноз, ФИО + клиентский путь. Полис не используется. Ищутся только среди
       пациентов, видимых роли. В приложении проверки при создании нет — это предупреждение прототипа. */
    const normName = (s) => collapse(s).toLowerCase().replace(/ё/g, 'е');
    const normPhone = (s) => { let d = String(s || '').replace(/\D/g, ''); if (d.length === 11 && d[0] === '8') d = '7' + d.slice(1); return d; };
    function findDuplicates(f, patients, nosology) {
        const name = normName(f.name), phone = normPhone(f.phone), age = String(f.age ?? '').trim() === '' ? null : Number(f.age);
        const out = [];
        patients.forEach(p => {
            const reasons = [];
            if (phone.length >= 10 && normPhone(p.phone) === phone) reasons.push('совпадает телефон');
            if (name && normName(p.name) === name) {
                if (age !== null && p.age === age) reasons.push('совпадают ФИО и возраст');
                if (f.diagnosis && String(p.icd || '').toLowerCase() === String(f.diagnosis).toLowerCase()) reasons.push('совпадают ФИО и диагноз');
                if (nosology && p.nosology === nosology) reasons.push('совпадают ФИО и клиентский путь');
            }
            if (reasons.length) out.push({ patient: p, reasons });
        });
        return out;
    }

    /* Поиск диагноза: код или название, без различия регистра и «е/ё» (нормализуются обе стороны). */
    const fold = (s) => String(s ?? '').toLowerCase().replace(/ё/g, 'е');
    function searchDiagnoses(list, q, limit = 40) {
        const k = fold(String(q || '').trim());
        return (k ? list.filter(d => fold(d.code).includes(k) || fold(d.label).includes(k)) : list).slice(0, limit);
    }

    /* Контекст отправки фиксируется при нажатии «Создать». Если до ответа сменилась роль, пользователь
       или выполнен выход, создание отменяется: пациент не создается от имени другого пользователя. */
    const sameSession = (a, b) => !!a && !!b && a.role === b.role && a.userId === b.userId && a.gen === b.gen && !a.loggedOut && !b.loggedOut;

    /* Создание: защита от двойного сохранения (ключ запроса), «потеря соединения» в демо, серверный id. */
    function create(state, f, ctx, current) {
        if (ctx.session && !sameSession(ctx.session, current)) { const e = new Error('Создание отменено: сменилась роль или выполнен выход. Пациент не создан.'); e.code = 'context_changed'; throw e; }
        if (state.inFlight) { const e = new Error('Сохранение уже выполняется.'); e.code = 'in_flight'; throw e; }
        if (f.requestKey && state.done[f.requestKey]) return { patient: state.done[f.requestKey], repeated: true };
        if (Object.prototype.hasOwnProperty.call(f, 'id')) { const e = new Error('Идентификатор пациента назначает система.'); e.code = 'id_forbidden'; throw e; }
        const v = validate(f, ctx);
        if (v.order.length) { const e = new Error('Проверьте поля формы.'); e.code = 'validation'; e.errors = v.errors; e.order = v.order; throw e; }
        if (ctx.offline) { const e = new Error('Нет связи с сервером. Пациент не создан — данные формы сохранены, повторите попытку.'); e.code = 'network'; throw e; }
        const doc = ctx.doctors.find(d => d.id === v.doctorId);
        const id = ctx.newId();
        const pol = normalizePolicy(f.policy).value;
        const p = {
            id, name: collapse(f.name), age: String(f.age ?? '').trim() === '' ? 0 : Number(f.age), phone: String(f.phone || '').trim(), policy: pol,
            icd: f.diagnosis, nosology: ctx.nosologyFor(f.diagnosis) || '', nosologyLabel: ctx.nosologyLabel(ctx.nosologyFor(f.diagnosis)) || 'Клиентский путь не определен', localization: ctx.localizationLabel(f.localization),
            doctorId: v.doctorId, doctor: doc ? doc.short : '', doctorFull: doc ? doc.name : '', dept: doc ? doc.dept : '', head: collapse(f.head),
            createdBy: ctx.userId, priority: f.priority, createdAt: ctx.now, updatedAt: ctx.now,
            complaint: String(f.complaint || '').trim(), pgiResult: f.pgiResult || '', pgiResultDate: f.pgiResult ? f.pgiResultDate : '',
            history: [{ stage: f.stage, enteredAt: ctx.now, events: plannedEvents(f), note: '', checklist: {} }],
            attentionIssues: [], diagnosisHistory: [], notifications: { smsEnabled: false, consent: 'not_recorded', items: [] },
        };
        p.stage = f.stage;
        state.created.push(p);
        if (f.requestKey) state.done[f.requestKey] = p;
        if (state.audit) state.audit.push({ event: 'patient_created', patientId: id, by: ctx.userName, at: ctx.now });
        return { patient: p, repeated: false };
    }

    root.StaffUxNewPatient = { searchDiagnoses, sameSession, LIMITS, STAGES, PLAN_STAGES, TREATMENT_STAGES, TREATMENT_TYPES, PRIORITIES, CAN_CREATE, normalizePolicy, resolveDoctor, validate, plannedEvents, findDuplicates, create };
})(typeof window !== 'undefined' ? window : globalThis);
