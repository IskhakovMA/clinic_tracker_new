/* Синтетические демонстрационные данные. Ни одной реальной записи:
   ФИО, полисы и телефоны вымышлены; этапы, причины внимания, чек-листы,
   группы и схемы лечения повторяют справочники приложения. */
(function () {
    'use strict';

    const STAGES = {
        'primary-appointment': 'Первичный приём',
        'exam': 'На обследовании',
        'appointment': 'Записан на приём',
        'consult': 'Направлен на ОК',
        'treatment-prep': 'Направлен на лечение',
        'treatment': 'Проходит лечение',
        'completed': 'Завершил лечение',
        'dispensary-observation': 'Диспансерное наблюдение',
        'diagnosis-not-confirmed': 'Диагноз не подтверждён',
    };
    const STAGE_ORDER = ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'completed'];

    // Причины внимания и их порядок — как ATTENTION_REASON_LABELS в приложении.
    const ATTENTION_REASONS = {
        emergency_hospitalization: 'Экстренная госпитализация',
        route_change: 'Изменение маршрута пациента',
        critical_overdue: 'Критичная просрочка',
        doctor_decision_required: 'Требуется решение врача',
        department_head_decision_required: 'Требуется решение заведующего',
        organizational_issue: 'Организационная проблема',
        documents_or_data_issue: 'Проблема с документами или данными',
        patient_unavailable: 'Не удалось связаться / пациент временно недоступен',
        other: 'Другая причина',
    };
    // Справочники карточки — тексты из static/app.js.
    const PRIORITY = { urgent: 'Срочно', high: 'Высокий', normal: 'Обычный' };
    const EVENT_TREATMENT = { surgery: 'Хирургическое лечение', radiation: 'Лучевая терапия', chemo: 'Химиотерапия', hormone: 'Гормонотерапия' };
    const DECISIONS = { treatment_strategy_defined: 'Определение тактики лечения', treatment_strategy_changed: 'Изменение тактики лечения', dispensary_observation: 'Диспансерное наблюдение' };
    const PGI = { '': 'Не указано', confirmed: 'Подтверждено', not_confirmed: 'Не подтверждено' };
    // Шаблоны SMS — как NOTIFICATION_TEMPLATE_SEED в backend/app/db.py.
    const SMS_BODY = 'Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.';
    const SMS_TEMPLATES = [
        { code: 'appointment_reminder', name: 'Напоминание о посещении', body: SMS_BODY },
        { code: 'examination_reminder', name: 'Напоминание о посещении', body: SMS_BODY },
        { code: 'custom_reminder', name: 'Ручное напоминание', body: SMS_BODY },
    ];

    const GROUPS = {
        general: 'Общеонкологическая', thoracic: 'Торакальная', abdominal: 'Абдоминальная',
        colorectal: 'Колопроктологическая', breast: 'Маммологическая', urology: 'Онкоурологическая',
        gynecology: 'Онкогинекологическая', head_neck: 'Опухоли головы и шеи',
    };

    const TREATMENTS = {
        radiotherapy: 'Лучевая терапия', chemotherapy: 'Химиотерапевтическое лечение',
        surgery: 'Хирургическое лечение', active_observation: 'Активное наблюдение',
        dispensary_observation: 'Диспансерное наблюдение',
    };

    const SCHEMES = [
        { code: 'sh0162', name: 'Пеметрексед + цисплатин' },
        { code: 'sh0066', name: 'Доцетаксел + карбоплатин + трастузумаб' },
        { code: 'sh0140', name: 'Гемцитабин + оксалиплатин + паклитаксел' },
        { code: 'sh0104', name: 'Капецитабин + трастузумаб' },
        { code: 'sh0161', name: 'Пеметрексед' },
    ];

    const NOSOLOGY = {
        lung34: { label: 'Рак лёгкого (III–IV)', group: 'thoracic', icd: ['C34.1', 'C34.3', 'C34.9'], loc: 'Органы дыхания' },
        lung12: { label: 'Рак лёгкого (I–II)', group: 'thoracic', icd: ['C34.1', 'C34.2'], loc: 'Органы дыхания' },
        breast: { label: 'Рак молочной железы', group: 'breast', icd: ['C50.4', 'C50.8', 'C50.9'], loc: 'Молочная железа' },
        colon: { label: 'Колоректальный рак', group: 'colorectal', icd: ['C18.7', 'C20', 'C19'], loc: 'Толстая кишка' },
        stomach: { label: 'Рак желудка', group: 'abdominal', icd: ['C16.0', 'C16.3'], loc: 'Желудок' },
        prostate: { label: 'Рак предстательной железы', group: 'urology', icd: ['C61'], loc: 'Предстательная железа' },
        uterus: { label: 'Рак тела матки', group: 'gynecology', icd: ['C54.1'], loc: 'Тело матки' },
        melanoma: { label: 'Меланома кожи', group: 'general', icd: ['C43.5', 'C43.6'], loc: 'Кожа' },
    };

    // Чек-листы и сроки этапов — из справочника приложения (nosology.js).
    const REF = window.NOSOLOGY_REF || {};

    const DEPARTMENTS = [
        { id: 'd-thor', name: 'Торакальное отделение' },
        { id: 'd-abd', name: 'Абдоминальное отделение' },
    ];
    // Заведующие — синтетические (в приложении список HEAD_OPTIONS содержит реальных сотрудников).
    const HEADS = { 'd-thor': 'Орлов Дмитрий Николаевич', 'd-abd': 'Лаврова Софья Игоревна' };

    const USERS = {
        doctor: { id: 'u-smirnova', name: 'Смирнова Елена Викторовна', short: 'Смирнова Е. В.', role: 'doctor', roleLabel: 'Врач-онколог', dept: 'd-thor' },
        head: { id: 'u-orlov', name: 'Орлов Дмитрий Николаевич', short: 'Орлов Д. Н.', role: 'department_head', roleLabel: 'Заведующий отделением', dept: 'd-thor' },
        chair: { id: 'u-volkova', name: 'Волкова Ирина Сергеевна', short: 'Волкова И. С.', role: 'consilium_chair', roleLabel: 'Председатель онкологического консилиума' },
        deputy: { id: 'u-gromov', name: 'Громов Павел Андреевич', short: 'Громов П. А.', role: 'deputy_chief_oncology', roleLabel: 'Заместитель главного врача по онкологии' },
        caop: { id: 'u-mironova', name: 'Миронова Анна Петровна', short: 'Миронова А. П.', role: 'caop_head', roleLabel: 'Заведующий ЦАОП' },
        developer: { id: 'u-sergeev', name: 'Сергеев Антон Викторович', short: 'Сергеев А. В.', role: 'developer', roleLabel: 'Разработчик' },
    };

    const DOCTORS = [
        { id: 'u-smirnova', name: 'Смирнова Елена Викторовна', short: 'Смирнова Е. В.', dept: 'd-thor' },
        { id: 'u-belyaev', name: 'Беляев Константин Игоревич', short: 'Беляев К. И.', dept: 'd-thor' },
        { id: 'u-nikitina', name: 'Никитина Ольга Павловна', short: 'Никитина О. П.', dept: 'd-thor' },
        { id: 'u-zaitsev', name: 'Зайцев Артём Валерьевич', short: 'Зайцев А. В.', dept: 'd-abd' },
        { id: 'u-fedorova', name: 'Фёдорова Анна Михайловна', short: 'Фёдорова А. М.', dept: 'd-abd' },
    ];

    // Детерминированный генератор: одинаковые данные при каждом открытии.
    let seed = 20261014;
    const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
    const pick = (list) => list[Math.floor(rnd() * list.length)];

    const NAMES = [
        ['Ивлев', 'Сергей', 'Андреевич', 'm'], ['Кузнецова', 'Мария', 'Олеговна', 'f'], ['Белов', 'Андрей', 'Петрович', 'm'],
        ['Соколова', 'Татьяна', 'Ивановна', 'f'], ['Морозов', 'Виктор', 'Степанович', 'm'], ['Лебедева', 'Наталья', 'Юрьевна', 'f'],
        ['Козлов', 'Геннадий', 'Борисович', 'm'], ['Новикова', 'Людмила', 'Аркадьевна', 'f'], ['Егоров', 'Михаил', 'Фёдорович', 'm'],
        ['Павлова', 'Светлана', 'Николаевна', 'f'], ['Семёнов', 'Олег', 'Васильевич', 'm'], ['Голубева', 'Валентина', 'Петровна', 'f'],
        ['Виноградов', 'Юрий', 'Алексеевич', 'm'], ['Богданова', 'Елена', 'Геннадьевна', 'f'], ['Воробьёв', 'Николай', 'Иванович', 'm'],
        ['Фролова', 'Галина', 'Сергеевна', 'f'], ['Комаров', 'Владимир', 'Олегович', 'm'], ['Киселёва', 'Ирина', 'Викторовна', 'f'],
        ['Макаров', 'Александр', 'Дмитриевич', 'm'], ['Андреева', 'Ольга', 'Борисовна', 'f'], ['Ковалёв', 'Евгений', 'Петрович', 'm'],
        ['Ильина', 'Надежда', 'Андреевна', 'f'], ['Гусев', 'Борис', 'Григорьевич', 'm'], ['Титова', 'Любовь', 'Михайловна', 'f'],
        ['Кудрявцев', 'Анатолий', 'Ильич', 'm'], ['Баранова', 'Вера', 'Алексеевна', 'f'], ['Куликов', 'Игорь', 'Владимирович', 'm'],
        ['Алексеева', 'Тамара', 'Фёдоровна', 'f'], ['Степанов', 'Валерий', 'Николаевич', 'm'], ['Яковлева', 'Зинаида', 'Павловна', 'f'],
        ['Сорокин', 'Леонид', 'Евгеньевич', 'm'], ['Романова', 'Нина', 'Константиновна', 'f'], ['Захаров', 'Пётр', 'Семёнович', 'm'],
        ['Борисова', 'Раиса', 'Ивановна', 'f'], ['Королёв', 'Аркадий', 'Львович', 'm'], ['Герасимова', 'Полина', 'Андреевна', 'f'],
    ];

    const today = new Date(); today.setHours(0, 0, 0, 0);
    const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

    // Карточка устроена как в приложении: история этапов (history), у каждого
    // этапа — события с плановой и фактической датой, примечание и чек-лист.
    // Пути — только переходы, которые предлагает интерфейс приложения.
    const PATHS = {
        'primary-appointment': ['primary-appointment'],
        exam: ['primary-appointment', 'exam'],
        appointment: ['primary-appointment', 'exam', 'appointment'],
        consult: ['primary-appointment', 'exam', 'appointment', 'consult'],
        'treatment-prep': ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep'],
        treatment: ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment'],
        completed: ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'completed'],
        'dispensary-observation': ['primary-appointment', 'exam', 'appointment', 'consult', 'dispensary-observation'],
        'diagnosis-not-confirmed': ['primary-appointment', 'exam', 'diagnosis-not-confirmed'],
    };
    const SCHEDULED = ['exam', 'appointment', 'consult', 'treatment-prep', 'treatment'];
    const TERMINAL = ['completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
    const EVENT_TITLE = { 'primary-appointment': 'Первичный приём', exam: 'Обследование', appointment: 'Приём врача', consult: 'Онкоконсилиум', 'treatment-prep': 'Лечение', treatment: 'Лечение' };
    const NOTES = ['Пациент проинформирован о плане обследования.', 'Результаты КТ получены, ждём гистологию.', 'Перенос по просьбе пациента.', ''];
    const COMPLAINTS = ['Жалобы на кашель, одышку при нагрузке. Направлен из поликлиники по месту жительства.', 'Самостоятельно обнаружила образование, обратилась в ЦАОП.', ''];
    const hm = () => `${String(8 + Math.floor(rnd() * 9)).padStart(2, '0')}:${pick(['00', '15', '30', '45'])}`;
    const stamp = (d, time) => `${iso(d)}T${time}`;
    const checklistFor = (nos, stage) => {
        const ref = REF[nos];
        if (!ref) return { required: [], optional: [] };
        return stage === 'exam'
            ? { required: ref.checklistRequired || [], optional: ref.checklistOptional || [] }
            : { required: ref.checklistRequiredStage2 || [], optional: ref.checklistOptionalStage2 || [] };
    };

    function buildHistory(stage, i, nos) {
        const path = PATHS[stage].slice();
        if (i === 4) path.splice(0, path.length, 'primary-appointment', 'exam', 'appointment', 'exam');   // «Дополнительное обследование»
        let t = addDays(today, -(path.length * 8 + 3 + Math.floor(rnd() * 8)));
        return path.map((s, k) => {
            const current = k === path.length - 1;
            const occ = { stage: s, enteredAt: stamp(t, hm()), events: [], note: rnd() > 0.8 ? pick(NOTES) : '', checklist: {} };
            const cl = checklistFor(nos, s);
            cl.required.forEach((name, n) => { occ.checklist[name] = !current || n === 0 || rnd() > 0.45; });
            if (s === 'primary-appointment') {
                if (!(current && i % 2)) occ.events.push({ date: '', time: '', actualDate: iso(t), actualTime: hm(), matchedPlan: null, noPlan: true });
            } else if (SCHEDULED.includes(s)) {
                const plan = addDays(t, current ? 0 : 3 + Math.floor(rnd() * 4));
                const ev = { date: iso(plan), time: hm(), actualDate: '', actualTime: '', matchedPlan: null, noPlan: false };
                if (s === 'treatment-prep' || s === 'treatment') ev.treatmentType = rnd() > 0.15 ? pick(['surgery', 'radiation', 'chemo', 'hormone']) : '';
                if (current) {
                    // Текущий этап: плановая дата впереди или уже прошла без факта; иногда дата ещё не назначена.
                    if (rnd() > 0.15) { ev.date = iso(addDays(today, Math.floor(rnd() * 16) - 5)); occ.events.push(ev); }
                } else {
                    const late = rnd() > 0.8;
                    ev.actualDate = late ? iso(addDays(plan, 1)) : ev.date;
                    ev.actualTime = ev.time;
                    ev.matchedPlan = !late;
                    occ.events.push(ev);
                    t = parseIso(ev.actualDate);
                }
                if (s === 'consult' && !current) {
                    const next = path[k + 1];
                    ev.consiliumDecision = next === 'dispensary-observation' ? 'dispensary_observation' : next === 'treatment-prep' ? pick(['treatment_strategy_defined', 'treatment_strategy_defined', 'treatment_strategy_changed']) : undefined;
                    if (!ev.consiliumDecision) delete ev.consiliumDecision;
                }
            }
            if (!current && !SCHEDULED.includes(s)) t = addDays(t, 2 + Math.floor(rnd() * 5));
            return occ;
        });
    }
    const parseIso = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

    // Поля, которые читают остальные экраны прототипа (список, «Сегодня»,
    // календарь, аналитика, уведомления, ИИ-помощник), выводятся из карточки.
    function derive(p) {
        const todayIso = iso(today);
        p.stage = p.history.length ? p.history[p.history.length - 1].stage : p.stage;
        p.events = p.history.flatMap((occ, k) => occ.events.map((ev, j) => ({
            id: `${p.id}-${k}-${j}`, title: EVENT_TITLE[occ.stage] || 'Событие', date: ev.actualDate || ev.date, time: ev.actualTime || ev.time || '', done: !!ev.actualDate,
        }))).filter(e => e.date).sort((a, b) => a.date.localeCompare(b.date));
        p.overdue = p.events.filter(e => !e.done && e.date < todayIso).length;
        const cur = p.history[p.history.length - 1];
        const cl = checklistFor(p.nosology, p.stage);
        p.checklist = cur ? cl.required.map(title => ({ title, done: !!cur.checklist[title] })) : [];
        p.attention = p.attentionIssues.filter(x => x.status === 'open').map(x => ({ code: x.reasonCode, since: x.createdAt.slice(0, 10), comment: x.comment }));
        const stamps = [p.createdAt, ...p.history.map(o => o.enteredAt), ...p.history.flatMap(o => o.events.map(e => e.actualDate)), ...p.attentionIssues.map(x => x.closedAt || x.createdAt)].filter(Boolean).map(s => s.slice(0, 10)).filter(s => s <= todayIso).sort();
        p.updated = p.updatedAt ? p.updatedAt.slice(0, 10) : stamps[stamps.length - 1];
        return p;
    }

    const nosKeys = Object.keys(NOSOLOGY);
    // Последний пациент создан председателем консилиума: председатель становится
    // лечащим врачом созданных им пациентов (resolve_create_doctor), отделения у него нет.
    const CHAIR_PATIENT = NAMES.length - 1;
    const patients = NAMES.map(([last, first, middle, sex], i) => {
        const nos = i < 9 ? pick(['lung34', 'lung12']) : pick(nosKeys);
        const n = NOSOLOGY[nos];
        const chairOwn = i === CHAIR_PATIENT;
        const doctor = chairOwn ? { id: USERS.chair.id, short: USERS.chair.short, name: USERS.chair.name, dept: null } : i < 14 ? DOCTORS[0] : pick(DOCTORS);
        const stage = chairOwn ? 'exam' : i === 4 ? 'exam' : pick(['exam', 'exam', 'exam', 'appointment', 'consult', 'consult', 'treatment-prep', 'treatment', 'treatment', 'primary-appointment', 'completed', 'dispensary-observation', 'diagnosis-not-confirmed']);
        const history = buildHistory(stage, i, nos);
        const closed = TERMINAL.includes(stage);
        const createdAt = history[0].enteredAt;
        const pendingLate = history[history.length - 1].events.some(e => !e.actualDate && e.date && e.date < iso(today));
        const attentionIssues = [];
        if (!closed && (i % 6 === 1 || pendingLate && i % 3 === 0)) attentionIssues.push({
            id: `att-${i}-1`, reasonCode: pendingLate ? 'critical_overdue' : pick(['doctor_decision_required', 'documents_or_data_issue', 'patient_unavailable', 'department_head_decision_required']),
            status: 'open', eventDate: '', comment: pendingLate ? 'Плановая дата прошла без отметки о выполнении.' : 'Уточнить у пациента дату повторного обращения.',
            createdByName: doctor.short, createdAt: stamp(addDays(today, -1 - Math.floor(rnd() * 5)), hm()),
        });
        if (i % 5 === 2) attentionIssues.push({
            id: `att-${i}-0`, reasonCode: 'organizational_issue', status: 'closed', eventDate: '', comment: 'Не поступили результаты анализов из лаборатории.',
            createdByName: doctor.short, createdAt: stamp(addDays(today, -14), '10:20'),
            closedByName: doctor.short, closedAt: stamp(addDays(today, -11), '15:05'), resolutionComment: 'Результаты получены.',
        });
        const consent = pick(['granted', 'granted', 'not_recorded', 'revoked']);
        const curEv = history[history.length - 1].events.find(e => !e.actualDate && e.date >= iso(today));
        const sms = [];
        if (consent === 'granted') {
            sms.push({ id: `sms-${i}-1`, template: 'appointment_reminder', scheduledAt: stamp(addDays(today, -6), '18:00'), source: 'event', status: 'sent', reason: '' });
            if (curEv) sms.push({ id: `sms-${i}-2`, template: 'examination_reminder', scheduledAt: stamp(addDays(parseIso(curEv.date), -1), '18:00'), source: 'event', status: 'pending', reason: '' });
        } else if (consent === 'revoked') {
            sms.push({ id: `sms-${i}-1`, template: 'appointment_reminder', scheduledAt: stamp(addDays(today, -3), '18:00'), source: 'event', status: 'cancelled', reason: '' });
        } else if (i % 4 === 0) {
            sms.push({ id: `sms-${i}-1`, template: 'appointment_reminder', scheduledAt: stamp(addDays(today, -2), '18:00'), source: 'event', status: 'skipped', reason: 'no_consent' });
        }
        const icd = pick(n.icd);
        const p = {
            id: `p-${String(i + 1).padStart(3, '0')}`,
            name: `${last} ${first} ${middle}`, sex,
            age: 41 + Math.floor(rnd() * 39),
            policy: i % 11 === 6 ? '' : `77${String(10000000000000 + Math.floor(rnd() * 89999999999999)).slice(0, 14)}`,
            phone: `+7 (9${10 + Math.floor(rnd() * 89)}) ${100 + Math.floor(rnd() * 899)}-${10 + Math.floor(rnd() * 89)}-${10 + Math.floor(rnd() * 89)}`,
            nosology: nos, nosologyLabel: n.label, icd, localization: n.loc,
            doctorId: doctor.id, doctor: doctor.short, doctorFull: doctor.name, dept: doctor.dept,
            head: doctor.dept ? HEADS[doctor.dept] : '',
            createdBy: chairOwn ? USERS.chair.id : doctor.id,
            priority: rnd() > 0.85 ? 'high' : rnd() > 0.93 ? 'urgent' : 'normal',
            createdAt, history, attentionIssues,
            complaint: rnd() > 0.5 ? pick(COMPLAINTS) : '',
            pgiResult: ['treatment-prep', 'treatment', 'completed'].includes(stage) ? 'confirmed' : stage === 'diagnosis-not-confirmed' ? 'not_confirmed' : '',
            pgiResultDate: '',
            diagnosisHistory: i % 7 === 3 ? [{ changedAt: stamp(addDays(parseIso(createdAt.slice(0, 10)), 4), '11:40'), oldDiagnosis: n.icd[0] === icd ? 'C80.9' : n.icd[0], newDiagnosis: icd }] : [],
            notifications: { smsEnabled: consent === 'granted', consent, consentAt: consent === 'not_recorded' ? '' : stamp(parseIso(createdAt.slice(0, 10)), '12:00'), consentBy: consent === 'not_recorded' ? '' : doctor.short, items: sms },
        };
        if (p.pgiResult) p.pgiResultDate = history[Math.min(2, history.length - 1)].enteredAt.slice(0, 10);
        // Последняя активность — последнее изменение карточки: этап, факт события или отметка внимания.
        p.updatedAt = [createdAt, ...history.map(o => o.enteredAt), ...history.flatMap(o => o.events.filter(e => e.actualDate).map(e => `${e.actualDate}T${e.actualTime || '00:00'}`)), ...attentionIssues.map(x => x.closedAt || x.createdAt)]
            .filter(x => x && x.slice(0, 10) <= iso(today)).sort().pop();
        return derive(p);
    });

    // Консилиум: записи текущего дня в торакальной и маммологической группах.
    const slots = [];
    for (let h = 9; h < 13; h++) for (const m of ['00', '15', '30', '45']) slots.push(`${String(h).padStart(2, '0')}:${m}`);
    const consPatients = patients.filter(p => ['thoracic', 'breast'].includes(NOSOLOGY[p.nosology].group)).slice(0, 9);
    // Статусы — как public_status в services/consilium.py: «revised» = возвращена с доработки.
    const statuses = ['signed', 'signed', 'in_review', 'recorded', 'recorded', 'needs_revision', 'recorded', 'revised', 'recorded'];
    const consilium = consPatients.map((p, k) => {
        const status = statuses[k];
        const scheme = status === 'signed' && k === 0 ? SCHEMES[0] : null;
        return {
            id: `c-${k + 1}`, patientId: p.id, patientName: p.name, policy: p.policy, icd: p.icd, localization: p.localization,
            group: NOSOLOGY[p.nosology].group, date: iso(today), time: slots[k * 2],
            doctor: p.doctor, createdBy: p.doctorId, status,
            needsSurgeon: k % 2 === 0, needsRadiotherapist: k % 3 === 0,
            comment: k === 5 ? 'Обсудить тактику после ИГХ.' : 'Обсудить тактику лечения.',
            treatment: status === 'signed' ? (k === 0 ? 'chemotherapy' : 'surgery') : null,
            scheme, goc: status === 'signed' && k === 0, goc65: status === 'signed' && k === 1,
            routeStage: status === 'signed' ? p.stage : null,
            revisions: (status === 'needs_revision' || status === 'revised') ? [{ round: 1, author: 'Волкова И. С.', at: `${iso(addDays(today, -1))}T15:20`, text: 'Приложите заключение КТ с контрастом и результаты ИГХ.' }] : [],
            // Поля формы записи (ConsiliumAppointmentIn): льгота, доп. диагноз, доп. участник, УВ/РУВ.
            hasBenefit: k % 4 === 1, additionalDiagnosis: '', additionalParticipant: k === 3 ? 'Морфолог' : '', uvRuv: k === 4,
            // Одна подписанная запись перенесена автором в архив (set_archived).
            archived: k === 1,
        };
    });

    // Сетка консилиума (как schedule_slots на сервере): текущая неделя открыта
    // автоматически (09:00–16:45, шаг 15 минут, все группы), следующие даты —
    // слоты, созданные председателем. Здесь — один синтетический день на
    // следующей неделе в торакальной группе и одна запись на нём.
    const nextMonday = addDays(today, 7 - ((today.getDay() + 6) % 7));
    const consSlots = [];
    for (let m = 9 * 60; m < 12 * 60; m += 30) consSlots.push({ id: `slot-${consSlots.length + 1}`, date: iso(nextMonday), time: `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`, group: 'thoracic', createdBy: USERS.chair.id });
    const futurePatient = patients.find(p => NOSOLOGY[p.nosology].group === 'thoracic' && p.doctorId === DOCTORS[0].id && !consPatients.includes(p));
    if (futurePatient) consilium.push({
        id: `c-${consilium.length + 1}`, patientId: futurePatient.id, patientName: futurePatient.name, policy: futurePatient.policy, icd: futurePatient.icd, localization: futurePatient.localization,
        group: 'thoracic', date: iso(nextMonday), time: '10:00', doctor: futurePatient.doctor, createdBy: futurePatient.doctorId, status: 'recorded',
        needsSurgeon: true, needsRadiotherapist: false, comment: 'Обсудить тактику лечения.', treatment: null, scheme: null, goc: false, goc65: false, routeStage: null, revisions: [],
        hasBenefit: false, additionalDiagnosis: '', additionalParticipant: '', uvRuv: false, archived: false,
    });

    // Переписка врача с пациентом (по модели Patient Portal v0.5.1): диалог
    // существует только в рамках отношения врач–пациент. Со стороны клиники
    // участник — лечащий врач (аналог ASSIGNED_CARE_TEAM). Тексты синтетические.
    const at = (daysAgo, hhmm) => `${iso(addDays(today, -daysAgo))}T${hhmm}`;
    const THREADS = [
        { context: 'assigned_doctor', unread: 2, messages: [
            ['patient', at(2, '18:40'), 'Добрый день! Подскажите, нужно ли брать на приём диск с КТ или достаточно заключения?'],
            ['doctor', at(1, '09:12'), 'Добрый день. Возьмите, пожалуйста, и диск, и заключение.'],
            ['patient', at(0, '08:05'), 'Спасибо. И ещё: анализы крови сдавать натощак?'],
            ['patient', at(0, '08:06'), 'Запись на 16 октября остаётся в силе?'],
        ] },
        { context: 'care_route', unread: 1, messages: [
            ['doctor', at(3, '14:20'), 'Здравствуйте. Направление на бронхоскопию оформлено, координатор свяжется с вами для записи.'],
            ['patient', at(0, '10:31'), 'Мне ещё не позвонили. Куда можно обратиться?'],
        ] },
        { context: 'appointment', unread: 0, messages: [
            ['patient', at(5, '11:02'), 'Можно перенести приём на следующую неделю?'],
            ['doctor', at(5, '12:40'), 'Да, выберите удобное время через регистратуру, я предупрежу коллег.'],
            ['patient', at(4, '09:15'), 'Перенесли на четверг, спасибо.'],
        ] },
        { context: 'assigned_doctor', unread: 1, messages: [
            ['doctor', at(7, '16:00'), 'Результаты гистологии получены, обсудим их на приёме.'],
            ['patient', at(1, '19:47'), 'Хорошо. Нужно ли что-то подготовить к приёму?'],
        ] },
        { context: 'care_route', unread: 0, messages: [] },   // диалог открыт, сообщений ещё нет
        { context: 'assigned_doctor', unread: 0, messages: [
            ['patient', at(12, '10:10'), 'Спасибо за помощь на всём этапе лечения.'],
            ['doctor', at(12, '11:30'), 'Всего доброго, приходите на контрольный осмотр по плану.'],
        ] },
    ];
    // Диалоги — у части пациентов первого врача; завершённым маршрутам — только чтение.
    const conversations = patients.filter(p => p.doctorId === DOCTORS[0].id).slice(0, THREADS.length).map((p, k) => {
        const t = THREADS[k];
        const closed = ['completed', 'dispensary-observation'].includes(p.stage) || k === THREADS.length - 1;
        const messages = t.messages.map(([from, sentAt, text], j) => ({ id: `m-${k}-${j}`, from, at: sentAt, text, status: from === 'doctor' ? 'read' : null }));
        if (closed) messages.push({ id: `m-${k}-sys`, from: 'system', at: at(10, '09:00'), text: 'Диалог завершен и доступен только для чтения.' });
        return { id: `conv-${p.id}`, patientId: p.id, doctorId: p.doctorId, context: t.context, status: closed ? 'read_only' : 'active', unread: t.unread, messages };
    });

    window.DEMO = {
        today: iso(today), STAGES, STAGE_ORDER, ATTENTION_REASONS, PRIORITY, EVENT_TREATMENT, DECISIONS, PGI, SMS_TEMPLATES, HEADS, PATHS, TERMINAL, SCHEDULED, GROUPS, TREATMENTS, SCHEMES, NOSOLOGY,
        DEPARTMENTS, USERS, DOCTORS, patients, consilium, consSlots, slots, conversations,
        helpers: { iso, addDays, derive, checklistFor },
    };
})();
