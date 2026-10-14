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

    const ATTENTION_REASONS = {
        critical_overdue: 'Критичная просрочка',
        doctor_decision_required: 'Требуется решение врача',
        department_head_decision_required: 'Требуется решение заведующего',
        documents_or_data_issue: 'Проблема с документами или данными',
        patient_unavailable: 'Не удалось связаться / пациент временно недоступен',
        route_change: 'Изменение маршрута пациента',
    };

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

    const CHECKLISTS = {
        lung34: ['КТ ОГК и ОБП с контрастом', 'ВИЧ, гепатит В и С', 'Общий анализ крови', 'Биохимический анализ', 'Коагулограмма', 'ЭКГ', 'ФВД', 'Направление на бронхоскопию', 'Гистологическое исследование', 'ИГХ и генетические исследования'],
        lung12: ['КТ ОГК и ОБП с контрастом', 'Анализ на ВИЧ', 'Общий анализ крови', 'Биохимический анализ крови', 'ЭКГ', 'ФВД', 'ЭГДС'],
        breast: ['УЗИ молочной железы и лимфатических узлов', 'Биопсия молочной железы и/или л/у', 'Гистологическое исследование', 'Общий анализ крови', 'ЭКГ', 'КТ или рентген ОГК'],
        colon: ['КТ ОГК, ОБП, ОМТ с контрастом', 'Маркеры крови СА19.9, РЭА', 'Общий анализ крови', 'Коагулограмма', 'ЭКГ', 'Гистологическое исследование'],
        stomach: ['КТ ОГК, ОБП, ОМТ с контрастом', 'Гистологическое исследование'],
        prostate: ['Направление на биопсию', 'Выполнение биопсии', 'Гистологическое исследование', 'Оценка результатов гистологии'],
        uterus: ['МРТ ОМТ с контрастом', 'КТ ОГК, ОБП с контрастом', 'Биохимический анализ крови', 'Коагулограмма'],
        melanoma: ['Общий анализ крови', 'Биохимический анализ крови', 'ЭКГ', 'УЗИ регионарных лимфатических узлов', 'Гистологическое исследование'],
    };

    const DEPARTMENTS = [
        { id: 'd-thor', name: 'Торакальное отделение' },
        { id: 'd-abd', name: 'Абдоминальное отделение' },
    ];

    const USERS = {
        doctor: { id: 'u-smirnova', name: 'Смирнова Елена Викторовна', short: 'Смирнова Е. В.', role: 'doctor', roleLabel: 'Врач-онколог', dept: 'd-thor' },
        head: { id: 'u-orlov', name: 'Орлов Дмитрий Николаевич', short: 'Орлов Д. Н.', role: 'department_head', roleLabel: 'Заведующий отделением', dept: 'd-thor' },
        chair: { id: 'u-volkova', name: 'Волкова Ирина Сергеевна', short: 'Волкова И. С.', role: 'consilium_chair', roleLabel: 'Председатель онкологического консилиума' },
        management: { id: 'u-gromov', name: 'Громов Павел Андреевич', short: 'Громов П. А.', role: 'deputy_chief_oncology', roleLabel: 'Заместитель главного врача по онкологии' },
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

    const EVENT_KINDS = ['КТ ОГК и ОБП с контрастом', 'Приём онколога', 'Биопсия', 'Гистологическое исследование', 'МРТ ОМТ с контрастом',
        'Контрольный осмотр', 'Курс химиотерапии', 'Консультация радиолога', 'УЗИ лимфоузлов', 'ЭГДС'];

    const nosKeys = Object.keys(NOSOLOGY);
    const patients = NAMES.map(([last, first, middle, sex], i) => {
        const nos = i < 9 ? pick(['lung34', 'lung12']) : pick(nosKeys);
        const n = NOSOLOGY[nos];
        const doctor = i < 14 ? DOCTORS[0] : pick(DOCTORS);
        const stage = pick(['exam', 'exam', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'treatment', 'primary-appointment', 'completed', 'dispensary-observation']);
        const stageIdx = Math.max(0, STAGE_ORDER.indexOf(stage));
        const enteredDaysAgo = 2 + Math.floor(rnd() * 28);
        const history = STAGE_ORDER.slice(0, stage === 'dispensary-observation' ? STAGE_ORDER.length : stageIdx + 1)
            .map((s, k, arr) => ({ stage: s, at: iso(addDays(today, -enteredDaysAgo - (arr.length - 1 - k) * (6 + Math.floor(rnd() * 9)))) }));
        if (stage === 'dispensary-observation') history.push({ stage, at: iso(addDays(today, -enteredDaysAgo + 3)) });
        const events = [];
        const evCount = 2 + Math.floor(rnd() * 3);
        // Завершённые маршруты: только прошедшие и выполненные события.
        const closed = ['completed', 'dispensary-observation', 'diagnosis-not-confirmed'].includes(stage);
        for (let k = 0; k < evCount; k++) {
            const offset = closed ? -3 - Math.floor(rnd() * 20) : Math.floor(rnd() * 20) - 5;
            const done = offset < 0 && (closed || rnd() > 0.3);
            events.push({ id: `ev-${i}-${k}`, title: pick(EVENT_KINDS), date: iso(addDays(today, offset)), time: `${String(8 + Math.floor(rnd() * 9)).padStart(2, '0')}:${pick(['00', '15', '30', '45'])}`, done });
        }
        events.sort((a, b) => a.date.localeCompare(b.date));
        const overdue = events.filter(e => !e.done && e.date < iso(today)).length;
        const checklist = (CHECKLISTS[nos] || []).map((title, k) => ({ title, done: stage !== 'exam' || rnd() > 0.45 || k === 0 }));
        const attention = (!closed && (i % 6 === 1 || overdue > 1)) ? [{
            code: overdue > 1 ? 'critical_overdue' : pick(['doctor_decision_required', 'documents_or_data_issue', 'patient_unavailable', 'department_head_decision_required']),
            since: iso(addDays(today, -Math.floor(rnd() * 6))),
            comment: overdue > 1 ? 'Две плановые даты прошли без отметки о выполнении.' : 'Уточнить у пациента дату повторного обращения.',
        }] : [];
        return {
            id: `p-${String(i + 1).padStart(3, '0')}`,
            name: `${last} ${first} ${middle}`, sex,
            age: 41 + Math.floor(rnd() * 39),
            policy: `77${String(10000000000000 + Math.floor(rnd() * 89999999999999)).slice(0, 14)}`,
            phone: `+7 (9${10 + Math.floor(rnd() * 89)}) ${100 + Math.floor(rnd() * 899)}-${10 + Math.floor(rnd() * 89)}-${10 + Math.floor(rnd() * 89)}`,
            nosology: nos, nosologyLabel: n.label, icd: pick(n.icd), localization: n.loc,
            doctorId: doctor.id, doctor: doctor.short, doctorFull: doctor.name, dept: doctor.dept,
            stage, history, events, checklist, attention, overdue,
            updated: iso(addDays(today, -Math.floor(rnd() * 5))),
            priority: rnd() > 0.85 ? 'high' : 'normal',
            comments: rnd() > 0.5 ? [{ author: doctor.short, at: iso(addDays(today, -2)), text: 'Пациент проинформирован о плане обследования.' }] : [],
        };
    });

    // Консилиум: записи текущего дня в торакальной и маммологической группах.
    const slots = [];
    for (let h = 9; h < 13; h++) for (const m of ['00', '15', '30', '45']) slots.push(`${String(h).padStart(2, '0')}:${m}`);
    const consPatients = patients.filter(p => ['thoracic', 'breast'].includes(NOSOLOGY[p.nosology].group)).slice(0, 9);
    const statuses = ['signed', 'signed', 'in_review', 'recorded', 'recorded', 'needs_revision', 'recorded', 'resubmitted', 'recorded'];
    const consilium = consPatients.map((p, k) => {
        const status = statuses[k];
        const scheme = status === 'signed' && k === 0 ? SCHEMES[0] : null;
        return {
            id: `c-${k + 1}`, patientId: p.id, patientName: p.name, policy: p.policy, icd: p.icd, localization: p.localization,
            group: NOSOLOGY[p.nosology].group, date: iso(today), time: slots[k * 2],
            doctor: p.doctor, status,
            needsSurgeon: k % 2 === 0, needsRadiotherapist: k % 3 === 0,
            comment: k === 5 ? 'Обсудить тактику после ИГХ.' : 'Обсудить тактику лечения.',
            treatment: status === 'signed' ? (k === 0 ? 'chemotherapy' : 'surgery') : null,
            scheme, goc: status === 'signed' && k === 0, goc65: false,
            routeStage: status === 'signed' ? p.stage : null,
            revisions: (status === 'needs_revision' || status === 'resubmitted') ? [{ round: 1, author: 'Волкова И. С.', at: iso(addDays(today, -1)), text: 'Приложите заключение КТ с контрастом и результаты ИГХ.' }] : [],
        };
    });

    // Аналитика: агрегаты считаются по тем же синтетическим пациентам.
    const byStage = Object.keys(STAGES).map(code => ({ code, label: STAGES[code], value: patients.filter(p => p.stage === code).length }));
    const weeks = [];
    for (let w = 11; w >= 0; w--) {
        const start = addDays(today, -7 * w - 6);
        weeks.push({ label: `${String(start.getDate()).padStart(2, '0')}.${String(start.getMonth() + 1).padStart(2, '0')}`, added: 3 + Math.floor(rnd() * 9), completed: 1 + Math.floor(rnd() * 6) });
    }

    window.DEMO = {
        today: iso(today), STAGES, STAGE_ORDER, ATTENTION_REASONS, GROUPS, TREATMENTS, SCHEMES, NOSOLOGY,
        DEPARTMENTS, USERS, DOCTORS, patients, consilium, slots, byStage, weeks,
        helpers: { iso, addDays },
    };
})();
