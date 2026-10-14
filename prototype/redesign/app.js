/* Прототип редизайна. Без сервера и API: всё на синтетических данных из
   data.js, изменения живут только в памяти вкладки. Видимость разделов и
   кнопок повторяет правила приложения (policy.py, navVisibility в app.js). */
(function () {
    'use strict';
    const D = window.DEMO;

    /* ---------- Безопасная шаблонизация ---------- */
    const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
    const raw = (s) => new Raw(s);
    const esc = (v) => (v instanceof Raw ? v.s : String(v ?? '').replace(/[&<>"']/g, c => ESC[c]));
    const html = (strings, ...values) => raw(strings.reduce((out, s, i) => {
        const v = values[i - 1];
        return out + (Array.isArray(v) ? v.map(esc).join('') : esc(v)) + s;
    }));
    const $ = (sel, root = document) => root.querySelector(sel);

    /* ---------- Роли (как в приложении) ----------
       PERMS — точная копия policy.permissions_payload для шести ролей
       (вычислено из backend/app/policy.py, ветка a9a3a20). Видимость разделов
       выводится из неё так же, как navVisibility в static/app.js:1731.
       Прототип добавляет только свои экраны: «Рабочий день» (врач),
       «Отделение» (заведующий), «Сообщения» (врач). */
    const F = (s) => Object.fromEntries(s.split(' ').filter(Boolean).map(k => [k, true]));
    const PERMS = {
        developer: F('createPatients editPatients deletePatients changeTreatingDoctor manageUsers manageDepartments viewAudit importData exportData manageNotifications runNotificationWorker manageNotificationTemplates accessConsilium bookConsilium manageConsilium importUpdate importReplace viewUnresolved'),
        deputy: F('readOnly viewUnresolved'),
        caop: F('readOnly viewUnresolved'),
        head: F('createPatients editPatients deletePatients importData exportData manageNotifications accessConsilium bookConsilium'),
        doctor: F('createPatients editPatients deletePatients importData exportData manageNotifications accessConsilium bookConsilium'),
        chair: F('createPatients editPatients deletePatients manageNotifications accessConsilium manageConsilium decideConsilium markReferralCompletion useConsiliumFavorites consiliumChair'),
    };
    const navFor = (role, p) => {
        const chair = !!p.consiliumChair;
        const visible = {
            patients: true, consilium: !!p.accessConsilium, calendar: !chair, notifications: !chair, analytics: true,
            checklists: !chair, quality: !chair, data: !chair && !!(p.importData || p.exportData),
            staff: !!p.manageUsers, structure: !!p.manageDepartments, audit: !!p.viewAudit, settings: !chair,
        };
        const extra = { doctor: ['today', 'messages'], head: ['department'] }[role] || [];
        return [...extra, ...Object.keys(visible).filter(k => visible[k])];
    };
    const ROLE_DEFS = {
        developer: { user: D.USERS.developer, home: '#/patients', scopeStrategy: 'global', scope: () => true },
        deputy: { user: D.USERS.deputy, home: '#/analytics', scopeStrategy: 'global_readonly', scope: () => true },
        caop: { user: D.USERS.caop, home: '#/analytics', scopeStrategy: 'global_readonly', scope: () => true },
        head: { user: D.USERS.head, home: '#/department', scopeStrategy: 'by_department', scope: p => p.dept === D.USERS.head.dept },
        doctor: { user: D.USERS.doctor, home: '#/today', scopeStrategy: 'by_treating_doctor', scope: p => p.doctorId === D.USERS.doctor.id },
        // by_treating_doctor, как у врача: пациенты — только свои (в демо их нет), консилиум — все записи.
        chair: { user: D.USERS.chair, home: '#/consilium', scopeStrategy: 'by_treating_doctor', scope: p => p.doctorId === D.USERS.chair.id },
    };
    const ROLES = Object.fromEntries(Object.entries(ROLE_DEFS).map(([k, r]) => {
        const p = PERMS[k];
        return [k, { ...r, perms: p, nav: navFor(k, p),
            can: { write: !!p.editPatients, create: !!p.createPatients, book: !!p.bookConsilium, decide: !!p.decideConsilium,
                consilium: !!p.accessConsilium, manage: !!p.manageConsilium, export: !!p.exportData, readOnly: !!p.readOnly } }];
    }));
    const isReadOnly = () => R().can.readOnly;

    const ICONS = {
        today: '<path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="16" r="3"/>',
        department: '<path d="M3 21h18M5 21V8l7-4 7 4v13"/><path d="M9 21v-5h6v5"/>',
        patients: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.4 3.4-5 6.5-5s5.7 1.6 6.5 5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 15c2 .6 3.2 2.2 3.5 5"/>',
        calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
        consilium: '<path d="M12 3v4M5 9h14l-1.5 11h-11z"/><path d="M9 14h6"/>',
        notifications: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>',
        messages: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9.5h8M8 12.5h5"/>',
        analytics: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
        checklists: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m3 6 1.5 1.5L7 5M3 12l1.5 1.5L7 11M3 18l1.5 1.5L7 17"/>',
        quality: '<path d="M12 3 4 6v6c0 4.5 3.4 8.2 8 9 4.6-.8 8-4.5 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
        data: '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
        staff: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
        structure: '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="3" y="16" width="6" height="5" rx="1"/><rect x="15" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M6 16v-2h12v2"/>',
        audit: '<path d="M8 4h8l4 4v12H4V4h4"/><path d="M8 12h8M8 16h5"/>',
        settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
    };
    const icon = (name) => raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ''}</svg>`);

    const NAV = [
        { group: 'Работа', items: [
            { id: 'today', label: 'Рабочий день' }, { id: 'department', label: 'Отделение' },
            { id: 'patients', label: 'Пациенты' }, { id: 'messages', label: 'Сообщения' }, { id: 'calendar', label: 'Расписание' },
            { id: 'consilium', label: 'Онкологический консилиум' }, { id: 'notifications', label: 'Уведомления', legacy: true },
        ] },
        { group: 'Контроль', items: [
            { id: 'analytics', label: 'Аналитика' }, { id: 'checklists', label: 'Чек-листы', legacy: true },
            { id: 'quality', label: 'Качество данных', legacy: true },
        ] },
        { group: 'Данные и администрирование', items: [
            { id: 'data', label: 'Импорт / Экспорт', legacy: true }, { id: 'staff', label: 'Сотрудники', legacy: true },
            { id: 'structure', label: 'Структура', legacy: true }, { id: 'audit', label: 'Журнал аудита', legacy: true },
            { id: 'settings', label: 'Настройки', legacy: true },
        ] },
    ];
    const NAV_LABEL = Object.fromEntries(NAV.flatMap(g => g.items.map(i => [i.id, i.label])));

    /* ---------- Состояние ---------- */
    const S = { role: 'doctor', demo: 'ok', patients: D.patients, consilium: D.consilium };
    try { const r = localStorage.getItem('proto-role'); if (r && ROLES[r]) S.role = r; else if (r === 'management') S.role = 'deputy'; } catch (e) { /* без хранилища */ }
    const R = () => ROLES[S.role];
    const scoped = () => S.patients.filter(R().scope);
    const patient = (id) => S.patients.find(p => p.id === id);

    /* ---------- Форматирование ---------- */
    const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    const WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const fmtDate = (s) => { const d = parse(s); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
    const fmtNum = (s) => s.split('-').reverse().join('.');
    const relDay = (s) => {
        const diff = Math.round((parse(s) - parse(D.today)) / 86400000);
        if (diff === 0) return 'сегодня';
        if (diff === 1) return 'завтра';
        if (diff === -1) return 'вчера';
        return diff < 0 ? `${-diff} дн. назад` : `через ${diff} дн.`;
    };
    const initials = (name) => name.split(' ').slice(0, 2).map(w => w[0]).join('');
    const plural = (n, a, b, c) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? a : m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c; };
    const stageIdx = (code) => D.STAGE_ORDER.indexOf(code);

    const routeMini = (p) => {
        const idx = p.stage === 'dispensary-observation' ? 7 : stageIdx(p.stage) + 1;
        return html`<div class="route-mini"><div class="route-mini__bar">${D.STAGE_ORDER.map((_, k) => html`<span class="${k < idx ? 'is-on' : ''}"></span>`)}</div><div class="route-mini__label">${D.STAGES[p.stage]}</div></div>`;
    };
    const attentionStatus = (p) => {
        if (p.overdue > 0) return html`<span class="status status--danger">Просрочено: ${p.overdue}</span>`;
        if (p.attention.length) return html`<span class="status status--warn">${D.ATTENTION_REASONS[p.attention[0].code]}</span>`;
        const next = p.events.find(e => !e.done && e.date >= D.today);
        return next ? html`<span class="status status--ok">${next.title} · ${relDay(next.date)}</span>` : html`<span class="status">Нет плановых событий</span>`;
    };
    const CONS_STATUS = {
        // Как STATUS_LABELS в services/consilium.py.
        recorded: ['Записан', 'tag'], in_review: ['На рассмотрении', 'tag tag--accent'], needs_revision: ['На доработке', 'tag tag--warn'],
        revised: ['Возвращен с доработки', 'tag tag--accent'], signed: ['Подписан', 'tag tag--ok'],
    };
    const consTag = (c) => html`<span class="${CONS_STATUS[c.status][1]}">${CONS_STATUS[c.status][0]}</span>`;

    /* ---------- Демонстрационные состояния ---------- */
    const STATE_ICON = {
        empty: '<path d="M4 7h16v13H4z"/><path d="M4 7l2-4h12l2 4M9 12h6"/>',
        error: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6M12 16.5v.5"/>',
    };
    const stateful = (body, opts = {}) => {
        if (S.demo === 'loading') return html`<div class="skeleton" aria-busy="true" aria-label="Загрузка">${Array.from({ length: opts.rows || 5 }, () => html`<span></span>`)}</div>`;
        if (S.demo === 'empty') return html`<div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${raw(STATE_ICON.empty)}</svg><div class="empty__title">${opts.emptyTitle || 'Здесь пока пусто'}</div><div>${opts.emptyText || 'Данные появятся, когда будут добавлены записи.'}</div></div>`;
        if (S.demo === 'error') return html`<div class="error" role="alert"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${raw(STATE_ICON.error)}</svg><div class="error__title">Не удалось загрузить данные</div><div>Сервер не ответил. Изменения не потеряны — повторите попытку.</div><button class="btn btn--sm" data-action="retry">Повторить</button></div>`;
        return body;
    };

    /* ---------- Экраны ---------- */
    const pagehead = (title, sub, actions = '', eyebrow = '') => html`
        <div class="pagehead"><div>${eyebrow ? html`<div class="pagehead__eyebrow">${eyebrow}</div>` : ''}<h1>${title}</h1>${sub ? html`<p class="pagehead__sub">${sub}</p>` : ''}</div>
        <div class="pagehead__actions">${actions}</div></div>`;

    const metrics = (rawItems) => {
        // Сводка разделяет состояние экрана: при загрузке/ошибке — прочерк, а не устаревшие цифры.
        const items = S.demo === 'ok' ? rawItems : rawItems.map(m => ({ ...m, value: S.demo === 'empty' ? 0 : '—', tone: '', note: S.demo === 'loading' ? 'загрузка…' : m.note }));
        return metricsView(items);
    };
    const metricsView = (items) => html`<div class="metrics" style="--cols:${items.length}" ${raw(S.demo === 'loading' ? 'aria-busy="true"' : '')}>${items.map(m => html`
        <${raw(m.href ? 'button' : 'div')} class="metric" ${raw(m.href ? `data-href="${m.href}"` : '')}>
            <div class="metric__label">${m.label}</div><div class="metric__value ${m.tone && Number(m.value) > 0 ? 'is-' + m.tone : ''}">${m.value}</div>${m.note ? html`<div class="metric__note">${m.note}</div>` : ''}
        </${raw(m.href ? 'button' : 'div')}>`)}</div>`;

    const patientRows = (list, cols = ['patient', 'stage', 'status', 'doctor', 'updated']) => html`
        <div class="table-wrap"><table class="table table--cards"><thead><tr>
            ${cols.includes('patient') ? html`<th>Пациент</th>` : ''}${cols.includes('stage') ? html`<th>Этап маршрута</th>` : ''}
            ${cols.includes('status') ? html`<th>Что требует внимания</th>` : ''}${cols.includes('doctor') ? html`<th>Лечащий врач</th>` : ''}
            ${cols.includes('updated') ? html`<th class="num">Изменён</th>` : ''}
        </tr></thead><tbody>${list.map(p => html`
            <tr class="is-link ${p.overdue ? 'row-mark' : ''}" data-href="#/patient/${p.id}" tabindex="0">
                ${cols.includes('patient') ? html`<td><div class="cell-title">${p.name}</div><div class="cell-sub"><span class="mono">${p.icd}</span> · ${p.nosologyLabel} · ${p.age} лет</div></td>` : ''}
                ${cols.includes('stage') ? html`<td data-label="Этап">${routeMini(p)}</td>` : ''}
                ${cols.includes('status') ? html`<td data-label="Внимание">${attentionStatus(p)}</td>` : ''}
                ${cols.includes('doctor') ? html`<td data-label="Врач">${p.doctor || '—'}</td>` : ''}
                ${cols.includes('updated') ? html`<td class="num muted" data-label="Изменён">${fmtDate(p.updated)}</td>` : ''}
            </tr>`)}</tbody></table></div>`;

    /* ---------- Онкологический консилиум: навигация и сетка (этап 3.1) ----------
       По static/app.js (renderConsilium, renderConsiliumSchedule,
       renderConsiliumDoctorSchedule, renderConsiliumStats) и
       services/consilium.py (schedule_slots, generate_schedule,
       delete_schedule_slot, _slot_appointment). Слоты и записи — синтетические,
       живут в памяти вкладки; сервер не вызывается. */
    S.consSlots = (D.consSlots || []).map(x => ({ ...x }));
    S.cons = { chairDate: D.today, doctorDate: D.today, chairGroups: null, groups: null, onlyFree: false, mineOnly: false, search: '' };
    const CONS_GROUP_KEYS = Object.keys(D.GROUPS);
    const consWeekDates = () => {
        const t = parse(D.today); const monday = D.helpers.addDays(t, -((t.getDay() + 6) % 7));
        return Array.from({ length: 7 }, (_, k) => D.helpers.iso(D.helpers.addDays(monday, k)));
    };
    const DEFAULT_TIMES = []; for (let h = 9; h < 17; h++) for (const m of ['00', '15', '30', '45']) DEFAULT_TIMES.push(`${String(h).padStart(2, '0')}:${m}`);
    // schedule_slots: сохранённые слоты + автоматически открытая текущая неделя (isDefaultWeek).
    function consAllSlots() {
        const out = S.consSlots.map(x => ({ ...x, isDefaultWeek: false }));
        const keys = new Set(out.map(x => `${x.date}|${x.time}|${x.group}`));
        for (const date of consWeekDates()) for (const group of CONS_GROUP_KEYS) for (const time of DEFAULT_TIMES) {
            if (!keys.has(`${date}|${time}|${group}`)) out.push({ id: `current-week-${date}-${time}-${group}`, date, time, group, isDefaultWeek: true });
        }
        return out;
    }
    const consAt = (date, time, group) => S.consilium.find(c => c.date === date && c.time === time && c.group === group);
    const consIsChair = () => !!R().perms.consiliumChair;
    const consSelectedGroups = (chair) => { const sel = chair ? S.cons.chairGroups : S.cons.groups; return sel === null ? CONS_GROUP_KEYS : sel; };
    const consEditable = (c) => ['recorded', 'needs_revision', 'revised'].includes(c.status);
    // _slot_appointment: полные сведения — председателю и разработчику (управление
    // консилиумом) и автору записи по пациенту в своей области; заведующему по
    // пациенту отделения — ФИО, врач и статус; остальным — только «Занято».
    function consView(c) {
        const p = patient(c.patientId);
        const inScope = !!p && R().scope(p);
        const mine = c.createdBy === R().user.id;
        if (R().perms.manageConsilium || R().perms.decideConsilium || (mine && inScope)) return { kind: 'full', mine };
        if (S.role === 'head' && inScope) return { kind: 'department', mine: false };
        return null;
    }
    // Полис записи: сохранённый в записи, иначе из карточки (COALESCE(a.policy_number, patients.policy_number)).
    const consPolicy = (c) => c.policy || (patient(c.patientId) || {}).policy || '';
    // Лечащий врач: сервер сохраняет врача пациента (_current_treating_doctor_name), в списках показывает
    // текущего лечащего врача, а после подписи — снимок на момент подписи (signed_treating_doctor_name).
    const consPatientDoctor = (p) => p ? (p.doctorFull || p.doctor || '') : '';
    const consDoctor = (c) => c.status === 'signed' && c.signedDoctor ? c.signedDoctor : consPatientDoctor(patient(c.patientId)) || c.doctor || '';
    // normalize_policy_number (backend/app/models.py): пробелы и дефисы убираются, затем проверки.
    function normalizeConsPolicy(v) {
        const raw = String(v || '').trim();
        if (!raw) return { value: '' };
        if ([...raw].some(ch => ch.charCodeAt(0) < 32 || ch.charCodeAt(0) === 127)) return { error: 'Номер полиса ОМС содержит недопустимые управляющие символы' };
        if (raw.includes('<') || raw.includes('>')) return { error: 'Номер полиса ОМС не должен содержать HTML или JavaScript' };
        const value = raw.replace(/[ -]+/g, '');
        if (value.length > 32) return { error: 'Номер полиса ОМС не должен быть длиннее 32 символов' };
        return { value };
    }
    const consSearchHit = (c) => { const q = S.cons.search.trim().toLowerCase(); return !q || c.patientName.toLowerCase().includes(q) || consPolicy(c).toLowerCase().includes(q); };

    function consGroupPicker(chair) {
        const sel = chair ? S.cons.chairGroups : S.cons.groups;
        const all = sel === null;
        const labels = CONS_GROUP_KEYS.filter(k => (sel || []).includes(k)).map(k => D.GROUPS[k]);
        const summary = all ? 'Все консилиумные группы' : !labels.length ? 'Группы не выбраны' : labels.length === 1 ? labels[0] : `Выбрано групп: ${labels.length}`;
        return html`<details class="group-picker" id="consGroupPicker"><summary class="select group-picker__summary">${summary}</summary>
            <div class="group-picker__menu" role="group" aria-label="Консилиумные группы">
                <label class="check group-picker__all"><input type="checkbox" data-cons-group="*" ${raw(all ? 'checked' : '')}> Все консилиумные группы</label>
                ${CONS_GROUP_KEYS.map(k => html`<label class="check"><input type="checkbox" data-cons-group="${k}" ${raw(all || (sel || []).includes(k) ? 'checked' : '')}> ${D.GROUPS[k]}</label>`)}
            </div></details>`;
    }

    function viewConsilium() {
        const can = R().can;
        if (!can.consilium) return denied('consilium');
        const chair = consIsChair();
        const date = chair ? S.cons.chairDate : S.cons.doctorDate;
        const groups = consSelectedGroups(chair);
        // renderConsiliumStats: у председателя — записи дня по выбранным группам, у остальных — свои записи.
        const rows = (chair ? S.consilium.filter(c => c.date === date && groups.includes(c.group)) : S.consilium.filter(c => c.createdBy === R().user.id && !c.archived)).filter(consSearchHit);
        const stats = [
            { label: 'Всего записей', value: rows.length, note: chair ? 'общая очередь консилиума' : 'созданы вами' },
            { label: 'Предстоящие', value: rows.filter(c => c.date >= D.today).length, note: 'сегодня и позже' },
            { label: 'Группы', value: new Set(rows.map(c => c.group)).size, note: 'задействовано' },
            chair ? { label: 'Подписано', value: rows.filter(c => c.status === 'signed').length, note: 'решение председателя' }
                : { label: 'Нужны специалисты', value: rows.filter(c => c.needsSurgeon || c.needsRadiotherapist).length, note: 'хирург или лучевой терапевт' },
        ];
        return html`
            ${pagehead('Онкологический консилиум', chair ? 'Все пациенты клиники, записанные на консилиум, в хронологическом порядке' : 'Ваши пациенты, записанные на рассмотрение консилиумной группой',
                html`${can.book ? html`<button class="btn btn--ghost" data-cons="archive-list">Архив</button><button class="btn btn--primary" data-action="book">Запись</button>` : ''}`)}
            ${S.role === 'developer' ? html`<div class="demo-note">Расхождение, не согласовано: сервер разрешает разработчику управлять сеткой, реестрами и аналитикой консилиума (manageConsilium), а действующий интерфейс показывает эти функции только председателю. Прототип повторяет действующий интерфейс; это не продуктовое решение.</div>` : ''}
            <div class="callout callout--info callout--flush"><div class="callout__text">${chair ? 'Все дни текущей недели открыты автоматически. Следующие недели становятся доступны врачам после создания слотов председателем.' : 'Текущая неделя доступна автоматически. Следующие недели отображаются только после открытия расписания председателем.'}</div></div>
            <div class="stack">
                <section class="panel cons-nav">
                    <div class="cons-nav__text"><span class="field__label">День и консилиумная группа</span><p class="field__hint">Выберите один день и нужную группу для просмотра сетки свободных и занятых слотов.</p></div>
                    <div class="field cons-nav__search"><label class="field__label" for="consSearch">Поиск по ФИО или полису</label><input class="input" type="search" id="consSearch" placeholder="ФИО или полис…" autocomplete="off" value="${S.cons.search}"></div>
                    <div class="cons-nav__actions">
                        <input class="input" type="date" id="consDate" value="${date}" aria-label="День расписания">
                        ${consGroupPicker(chair)}
                        <button class="btn btn--sm btn--ghost" data-cons="today">Сегодня</button>
                    </div>
                </section>
                ${metrics(stats)}
                <section class="panel">
                    <div class="panel__head panel__head--wrap"><div><h2 class="panel__title">Сетка расписания консилиума</h2><div class="panel__meta">${chair ? 'Текущая неделя открыта автоматически. Здесь можно открыть слоты на любую будущую дату.' : 'Выберите день текущей недели или будущую дату, которую открыл председатель.'}</div></div>
                        <div class="kit-row">
                            <button class="btn btn--sm ${S.cons.onlyFree ? 'btn--primary' : ''}" data-cons="free" aria-pressed="${S.cons.onlyFree}">Только свободные слоты</button>
                            ${chair ? '' : html`<button class="btn btn--sm ${S.cons.mineOnly ? 'btn--primary' : ''}" data-cons="mine" aria-pressed="${S.cons.mineOnly}">Мои записи</button>`}
                        </div></div>
                    ${chair ? consGenerateForm() : ''}
                    ${stateful(chair ? consChairGrid(date, groups) : consDoctorGrid(date, groups), { rows: 8, emptyTitle: 'На выбранный день слотов нет.', emptyText: '' })}
                </section>
                ${consRegistries(date)}
            </div>`;
    }

    // openConsiliumDetails: просмотр записи без действий; у председателя в подвале только «Закрыть».
    function consDetailsModal(c) {
        const f = (label, value, wide) => html`<div class="field ${wide ? 'cons-detail--wide' : ''}"><span class="field__label">${label}</span><div class="cons-detail">${value || 'Не указано'}</div></div>`;
        const specialists = [c.needsSurgeon && 'Хирург', c.needsRadiotherapist && 'Лучевой терапевт', c.additionalParticipant].filter(Boolean).join(', ') || 'Не требуются';
        // revisionComment в приложении очищается при подписи; здесь — последний возврат, пока запись не подписана.
        const lastReturn = c.status !== 'signed' && c.revisions.length && consCanSeeHistory(c) ? c.revisions[c.revisions.length - 1].text : '';
        openModal(modalShell(`Запись: ${c.patientName}`, '', html`
            <div class="form__section">Пациент и запись</div>
            <div class="cons-details">${f('ФИО пациента', c.patientName)}${f('Полис ОМС', consPolicy(c))}${f('Консилиумная группа', D.GROUPS[c.group])}${f('Дата и время', `${fmtNum(c.date)}, ${c.time}`)}${f('Льгота', c.hasBenefit ? 'Да' : 'Нет')}${f('Лечащий врач', consDoctor(c))}</div>
            <div class="form__section">Сведения для консилиума</div>
            <div class="cons-details">${f('Локализация', c.diagnosisLocalization)}${f('Основной диагноз', c.icd)}${f('Дополнительный диагноз', c.additionalDiagnosis)}${f('Участники', specialists)}${f('Отметка УВ/РУВ', c.uvRuv ? 'Да' : 'Нет')}${f('Статус', CONS_STATUS[c.status][0])}${f('Назначенное лечение', c.treatment ? D.TREATMENTS[c.treatment] : '')}${c.status === 'signed' ? f('Этап маршрута на момент решения', c.routeStage ? D.STAGES[c.routeStage] : 'Не установлен') : ''}${f('Отметки ГОК', [c.goc && 'ГОК', c.goc65 && 'ГОК 65+'].filter(Boolean).join(', '))}
                ${f('Схема лечения', c.scheme ? `${c.scheme.code ? c.scheme.code + ' — ' : ''}${c.scheme.name}` : '', true)}${f('Комментарий врача', c.comment, true)}${lastReturn ? f('Комментарий председателя', lastReturn, true) : ''}</div>
            <p class="demo-hint">Только просмотр.</p>`,
            html`${consCanArchive(c) ? html`<button class="btn btn--ghost" data-cons-archive="${c.archived ? 'restore' : 'archive'}" data-id="${c.id}">${c.archived ? 'Восстановить из архива' : 'Перенести в архив'}</button>` : ''}<span class="spacer"></span><button class="btn btn--primary" data-close>Закрыть</button>`));
    }

    function consGenerateForm() {
        const g = S.cons.gen || { date: '', group: CONS_GROUP_KEYS[0], start: '09:00', end: '17:00', interval: '15', error: '' };
        return html`<form class="cons-gen" id="consGenForm" novalidate>
            <div class="field"><label class="field__label" for="genDate">Дата</label><input class="input" type="date" id="genDate" min="${D.today}" value="${g.date}" aria-describedby="genHint"></div>
            <div class="field"><label class="field__label" for="genGroup">Группа</label><select class="select" id="genGroup">${CONS_GROUP_KEYS.map(k => html`<option value="${k}" ${raw(k === g.group ? 'selected' : '')}>${D.GROUPS[k]}</option>`)}</select></div>
            <div class="field"><label class="field__label" for="genStart">Начало</label><input class="input" type="time" id="genStart" value="${g.start}"></div>
            <div class="field"><label class="field__label" for="genEnd">Окончание</label><input class="input" type="time" id="genEnd" value="${g.end}"></div>
            <div class="field"><label class="field__label" for="genInterval">Интервал</label><select class="select" id="genInterval">${['15', '30', '60'].map(v => html`<option value="${v}" ${raw(v === g.interval ? 'selected' : '')}>${v} минут</option>`)}</select></div>
            <div class="cons-gen__submit"><button class="btn btn--primary" data-cons="generate">Создать слоты</button></div>
            <p class="field__hint cons-gen__hint" id="genHint">Прошедшие дни и даты с уже открытым расписанием для выбранной группы недоступны. Демонстрация: слоты создаются только в этой вкладке.</p>
            ${g.error ? html`<div class="callout callout--danger callout--flush cons-gen__error" role="alert"><div class="callout__text">${g.error}</div></div>` : ''}
        </form>`;
    }

    const consNameCell = (c) => html`<button type="button" class="cons-patient" data-cons="details" data-id="${c.id}">${c.patientName}</button>${c.comment ? html`<div class="cell-sub">${c.comment}</div>` : ''}`;
    function consChairActions(c) {
        // renderConsiliumChairActions: кнопка рассмотрения (кроме подписанных и «На доработке») и избранное — у всех записей.
        const review = c.status === 'signed' || c.status === 'needs_revision' ? ''
            : html`<button class="btn btn--sm btn--primary" data-action="decide" data-id="${c.id}">${c.status === 'in_review' ? 'Продолжить рассмотрение' : c.status === 'revised' ? 'Рассмотреть повторно' : 'Рассмотреть'}</button>`;
        const fav = R().perms.useConsiliumFavorites
            ? html`<button class="btn btn--sm btn--ghost ${consIsFav(c) ? 'is-favorite' : ''}" data-cons-fav="${c.id}" aria-pressed="${consIsFav(c)}">${consIsFav(c) ? '★ В избранном' : '☆ В избранное'}</button>` : '';
        return html`${consTag(c)}${review}${fav}`;
    }
    const consTable = (rows) => html`<div class="table-wrap"><table class="table table--cards cons-grid"><thead><tr><th>Время записи</th><th>ФИО пациента</th><th>Код МКБ-10</th><th>ФИО врача</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody>${rows}</tbody></table></div>`;

    function consChairGrid(date, groups) {
        if (!groups.length) return html`<div class="empty"><div class="empty__title">Выберите одну или несколько консилиумных групп.</div></div>`;
        const searching = !!S.cons.search.trim();
        const slots = consAllSlots().filter(x => x.date === date && groups.includes(x.group));
        const slotKeys = new Set(slots.map(x => `${x.date}|${x.time}|${x.group}`));
        // Записи без слота в сетке (как в приложении) показываются как занятые строки.
        let rows = [...slots.map(slot => ({ slot, c: consAt(slot.date, slot.time, slot.group) })),
            ...S.consilium.filter(c => c.date === date && groups.includes(c.group) && !slotKeys.has(`${c.date}|${c.time}|${c.group}`)).map(c => ({ slot: { date: c.date, time: c.time, group: c.group, occupied: true }, c }))]
            .sort((a, b) => `${a.slot.time}|${a.slot.group}`.localeCompare(`${b.slot.time}|${b.slot.group}`));
        if (searching) rows = rows.filter(r => r.c && consSearchHit(r.c));
        if (S.cons.onlyFree) rows = rows.filter(r => !r.c);
        return html`<div class="cons-groups">${groups.map(g => {
            const gr = rows.filter(r => r.slot.group === g);
            return html`<section class="cons-group"><h3 class="cons-group__title">${D.GROUPS[g]}</h3>${gr.length ? consTable(gr.map(({ slot, c }) => html`<tr>
                <td class="mono">${slot.time}</td>
                <td data-label="Пациент">${c ? consNameCell(c) : html`<span class="muted">—</span>`}</td>
                <td class="mono" data-label="МКБ-10">${c ? c.icd : '—'}</td>
                <td data-label="Врач">${c ? consDoctor(c) || '—' : '—'}</td>
                <td class="actions"><div class="cons-actions">${c && c.uvRuv ? html`<span class="tag tag--danger">УВ/РУВ</span>` : ''}${c ? consChairActions(c) : slot.isDefaultWeek ? html`<span class="muted">—</span>` : html`<button class="btn btn--sm btn--ghost" data-cons="delete-slot" data-id="${slot.id}">Удалить</button>`}</div></td></tr>`))
                : html`<div class="empty empty--compact"><div>${searching ? 'По вашему запросу записей не найдено.' : 'На выбранный день слотов нет.'}</div></div>`}</section>`;
        })}</div>`;
    }

    function consDoctorGrid(date, groups) {
        if (!groups.length) return html`<div class="empty"><div class="empty__title">Выберите одну или несколько консилиумных групп.</div></div>`;
        const q = S.cons.search.trim();
        let slots = consAllSlots().filter(x => x.date === date && groups.includes(x.group)).map(slot => {
            const c = consAt(slot.date, slot.time, slot.group);
            return { slot, c, v: c ? consView(c) : null };
        }).sort((a, b) => `${a.slot.time}|${a.slot.group}`.localeCompare(`${b.slot.time}|${b.slot.group}`));
        if (q) slots = slots.filter(r => r.c && r.v && consSearchHit(r.c));
        if (S.cons.onlyFree) slots = slots.filter(r => !r.c);
        if (S.cons.mineOnly) slots = slots.filter(r => r.v && r.v.mine);
        return html`<div class="cons-groups">${groups.map(g => {
            const gr = slots.filter(r => r.slot.group === g);
            return html`<section class="cons-group"><h3 class="cons-group__title">${D.GROUPS[g]}</h3>${gr.length ? consTable(gr.map(({ slot, c, v }) => {
                const visible = c && v;
                const cell = !c ? html`<span class="muted">—</span>` : !v ? html`<span class="muted">Занято</span>` : v.mine && !c.archived ? html`<button type="button" class="cons-patient" data-cons="details" data-id="${c.id}">${c.patientName}</button>` : html`<div class="cell-title">${c.patientName}</div>`;
                const actions = !c ? (R().can.book ? html`<button class="btn btn--sm btn--primary" data-action="book" data-date="${slot.date}" data-group="${slot.group}" data-time="${slot.time}">Запись</button>` : '')
                    : !v ? html`<span class="tag">Занято</span>`
                    : html`${v.kind === 'full' && c.uvRuv ? html`<span class="tag tag--danger">УВ/РУВ</span>` : ''}${v.mine ? html`<span class="tag tag--warn">Моя запись</span>` : ''}${v.kind === 'department' ? html`<span class="tag">${CONS_STATUS[c.status][0]}</span>` : ''}${v.mine && consEditable(c) ? html`<button class="btn btn--sm btn--ghost" data-action="book" data-id="${c.id}">Изменить</button>` : ''}`;
                return html`<tr><td class="mono">${slot.time}</td><td data-label="Пациент">${cell}</td>
                    <td class="mono" data-label="МКБ-10">${visible && v.kind === 'full' ? c.icd : '—'}</td>
                    <td data-label="Врач">${visible ? consDoctor(c) || '—' : '—'}</td>
                    <td class="actions"><div class="cons-actions">${actions}</div></td></tr>`;
            })) : html`<div class="empty empty--compact"><div>${q ? 'По вашему запросу записей не найдено.' : 'На выбранный день слотов нет.'}</div></div>`}</section>`;
        })}</div>`;
    }

    // Управление навигацией, фильтрами и сеткой.
    document.addEventListener('change', (e) => {
        const t = e.target;
        if (t.id === 'consDate') { if (!t.value) return; if (consIsChair()) S.cons.chairDate = t.value; else S.cons.doctorDate = t.value; render(); return; }
        const g = t.closest('[data-cons-group]');
        if (g) {
            const chair = consIsChair(), key = chair ? 'chairGroups' : 'groups';
            if (g.dataset.consGroup === '*') S.cons[key] = t.checked ? null : [];
            else {
                const cur = new Set(S.cons[key] === null ? CONS_GROUP_KEYS : S.cons[key]);
                if (t.checked) cur.add(g.dataset.consGroup); else cur.delete(g.dataset.consGroup);
                S.cons[key] = cur.size === CONS_GROUP_KEYS.length ? null : [...cur];
            }
            render();
            const pk = $('#consGroupPicker'); if (pk) pk.open = true;
            const again = document.querySelector(`[data-cons-group="${g.dataset.consGroup}"]`); if (again) again.focus({ preventScroll: true });
            return;
        }
        if (['genDate', 'genGroup', 'genStart', 'genEnd', 'genInterval'].includes(t.id)) {
            S.cons.gen = { date: $('#genDate').value, group: $('#genGroup').value, start: $('#genStart').value, end: $('#genEnd').value, interval: $('#genInterval').value, error: (S.cons.gen || {}).error || '' };
        }
    });
    document.addEventListener('input', (e) => {
        if (e.target.id !== 'consSearch') return;
        const pos = e.target.selectionStart; S.cons.search = e.target.value; render();
        const n = $('#consSearch'); if (n) { n.focus(); n.setSelectionRange(pos, pos); }
    });
    document.addEventListener('click', (e) => {
        const pk = $('#consGroupPicker');
        if (pk && pk.open && !pk.contains(e.target)) pk.open = false;
        const b = e.target.closest('[data-cons]');
        if (!b) return;
        const act = b.dataset.cons;
        if (act === 'today') { if (consIsChair()) S.cons.chairDate = D.today; else S.cons.doctorDate = D.today; render(); return; }
        if (act === 'free') { S.cons.onlyFree = !S.cons.onlyFree; if (S.cons.onlyFree) S.cons.mineOnly = false; render(); return; }
        if (act === 'mine') { S.cons.mineOnly = !S.cons.mineOnly; if (S.cons.mineOnly) S.cons.onlyFree = false; render(); return; }
        if (act === 'archive-list') { if (R().can.book) consArchiveModal(); return; }
        if (act === 'details') { const c = S.consilium.find(x => x.id === b.dataset.id); if (c && consView(c) && consView(c).kind === 'full') consDetailsModal(c); return; }
        if (!consIsChair()) return;   // генерация и удаление слотов — только у председателя (как в действующем интерфейсе)
        if (act === 'generate') {
            e.preventDefault();
            const g = { date: $('#genDate').value, group: $('#genGroup').value, start: $('#genStart').value, end: $('#genEnd').value, interval: $('#genInterval').value, error: '' };
            const opened = new Set(consAllSlots().filter(x => x.group === g.group).map(x => x.date));
            const toMin = (v) => { const [h, m] = v.split(':').map(Number); return h * 60 + m; };
            if (!g.date || g.date < D.today || opened.has(g.date)) g.error = 'Выберите доступную дату в календаре.';
            else if (!g.start || !g.end || toMin(g.end) <= toMin(g.start)) g.error = 'Не удалось создать сетку: Время окончания должно быть позже времени начала';
            else if (toMin(g.end) - toMin(g.start) > 12 * 60) g.error = 'Не удалось создать сетку: Сетка расписания не может быть длиннее 12 часов';
            if (g.error) { S.cons.gen = g; render(); return; }
            const keys = new Set(S.consSlots.map(x => `${x.date}|${x.time}|${x.group}`));
            let created = 0;
            for (let m = toMin(g.start); m < toMin(g.end); m += Number(g.interval)) {
                const time = `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
                if (keys.has(`${g.date}|${time}|${g.group}`)) continue;   // INSERT OR IGNORE
                S.consSlots.push({ id: `slot-${Date.now()}-${created}`, date: g.date, time, group: g.group, createdBy: R().user.id });
                created++;
            }
            S.cons.gen = { ...g, date: '', error: '' };
            S.cons.chairDate = g.date; if (S.cons.chairGroups !== null && !S.cons.chairGroups.includes(g.group)) S.cons.chairGroups = [...S.cons.chairGroups, g.group];
            render();
            toast(created ? `Создано свободных слотов: ${created}. Демонстрация: только в этой вкладке.` : 'Такие слоты уже существуют.');
            return;
        }
        if (act === 'delete-slot') {
            const slot = S.consSlots.find(x => x.id === b.dataset.id);
            if (!slot) return;
            if (consAt(slot.date, slot.time, slot.group)) { toast('Не удалось удалить слот: Нельзя удалить занятый слот'); return; }
            S.consSlots = S.consSlots.filter(x => x.id !== slot.id);
            render(); toast('Свободный слот удалён (демо).');
        }
    });
    document.addEventListener('submit', (e) => { if (e.target.id === 'consGenForm') { e.preventDefault(); const b = $('[data-cons="generate"]'); if (b) b.click(); } });

    /* ---------- Консилиум: запись и жизненный цикл (этап 3.2) ----------
       По static/app.js (openConsiliumModal, submitConsiliumAppointment,
       deleteConsiliumAppointment, openConsiliumDecision, submitConsiliumDecision,
       submitConsiliumRevision, openConsiliumDetails, openConsiliumArchive) и
       services/consilium.py (create, update, delete_owned, start_review, sign,
       return_for_revision, set_archived). Хранимый на сервере статус
       recorded → in_review → signed здесь хранится сразу публичным:
       «На доработке» = возвращена и не отправлена, «Возвращен с доработки» =
       отправлена повторно. Всё — в памяти вкладки. */
    S.consDeleted = [];   // «мягко» удалённые записи с историей возвратов (delete_owned): в списках не видны
    const CONS_DIAG_RE = /^C(?:0[0-9]|[1-8][0-9]|9[0-7])(?:\.[0-9A-Z]{1,2})?$/;
    const validConsDiagnosis = (value, required = true) => { const v = String(value || '').trim().toUpperCase(); if (!v && !required) return ''; return CONS_DIAG_RE.test(v) ? v : null; };
    // group_for_diagnosis — то же правило, что на сервере и в приложении.
    function consGroupForDiagnosis(value) {
        const m = String(value || '').trim().toUpperCase().match(/^C(\d{2})(?:\.|$)/);
        if (!m) return 'general';
        const code = Number(m[1]);
        if ((code >= 0 && code <= 14) || (code >= 30 && code <= 32)) return 'head_neck';
        if ((code >= 15 && code <= 16) || (code >= 22 && code <= 25)) return 'abdominal';
        if (code === 34) return 'thoracic';
        if (code >= 51 && code <= 57) return 'gynecology';
        if (code >= 60 && code <= 68) return 'urology';
        if (code === 50) return 'breast';
        if (code >= 17 && code <= 20) return 'colorectal';
        return 'general';
    }
    const ICD_ALL = Object.values(window.ICD10 || {}).flatMap(v => v.diagnoses.map(d => ({ ...d, locLabel: v.label })));
    const icdLocLabel = (code) => (ICD_ALL.find(d => d.code === code) || {}).locLabel || '';
    S.consilium.forEach(c => { if (c.diagnosisLocalization === undefined) c.diagnosisLocalization = icdLocLabel(c.icd) || c.localization; });
    const consRevisionHistory = (c) => c.revisions.length ? html`<section class="cons-history" aria-label="История возвратов"><h3 class="cons-history__title">История возвратов</h3>
        <ol class="history">${c.revisions.map(r => html`<li><div class="history__meta">Раунд ${r.round} · ${fmtStamp(r.at.includes('T') ? r.at : `${r.at}T00:00`)} · ${r.author}</div><div>${r.text}</div></li>`)}</ol></section>` : '';
    // Историю возвратов сервер отдаёт только председателю и автору записи.
    const consCanSeeHistory = (c) => R().perms.decideConsilium || c.createdBy === R().user.id;
    const consFreeTimes = (date, group, record) => {
        const times = consAllSlots().filter(x => x.date === date && x.group === group).map(x => x.time)
            .filter(t => !consAt(date, t, group) || (record && record.date === date && record.time === t && record.group === group));
        if (record && record.date === date && record.group === group && !times.includes(record.time)) times.unshift(record.time);
        return [...new Set(times)].sort();
    };
    const consBookablePatients = (group, record) => {
        const booked = new Set(S.consilium.map(c => c.patientId));
        let list = scoped().filter(p => canEdit(p) && (!booked.has(p.id) || (record && record.patientId === p.id)));
        list = list.filter(p => consGroupForDiagnosis(p.icd) === group || (record && record.patientId === p.id));
        return list.sort((a, b) => a.name.localeCompare(b.name, 'ru'));
    };

    /* --- Форма записи: создание и изменение/перенос --- */
    function bookModal(opts) {
        const record = opts.id ? S.consilium.find(c => c.id === opts.id) : null;
        // can_book_consilium: разработчик, заведующий, врач; председатель и руководство не записывают.
        if (!R().can.book) { toast('Запись на консилиум недоступна для вашей роли.'); return; }
        if (opts.id && !record) { toast('Запись не найдена.'); return; }
        if (record && !(record.createdBy === R().user.id && consEditable(record))) { toast('Запись недоступна для изменения.'); return; }
        // Без выбранного слота — первый свободный слот с сегодняшнего дня (как в приложении).
        const firstFree = !record && !opts.time ? consAllSlots().filter(x => x.date >= D.today && !consAt(x.date, x.time, x.group)).sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`))[0] : null;
        const st = record ? { group: record.group, date: record.date, time: record.time, patientId: record.patientId }
            : { group: opts.group || (firstFree && firstFree.group) || CONS_GROUP_KEYS[0], date: opts.date || (firstFree && firstFree.date) || D.today, time: opts.time || (firstFree && firstFree.time) || '', patientId: '' };
        const patients = consBookablePatients(st.group, record);
        if (!record && patients.length) st.patientId = patients[0].id;
        const p0 = patient(st.patientId);
        const v = record ? { ...record, policy: consPolicy(record) } : { policy: p0 ? p0.policy : '', diagnosisLocalization: p0 ? icdLocLabel(p0.icd) : '', icd: p0 && validConsDiagnosis(p0.icd, false) || '', additionalDiagnosis: '', hasBenefit: false, comment: '', additionalParticipant: '', uvRuv: false, needsSurgeon: false, needsRadiotherapist: false };
        S.consForm = { id: record ? record.id : '', ...st };
        openModal(modalShell(record ? 'Изменение и перенос записи на консилиум' : 'Запись на онкологический консилиум', '', consFormBody(record, v, patients),
            html`${record && consEditable(record) ? html`<button class="btn btn--danger" data-cons-form="delete">Удалить</button>` : ''}<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-cons-form="save" ${raw(patients.length ? '' : 'disabled')}>${record ? 'Сохранить изменения' : 'Создать запись'}</button>`));
    }
    function consFormBody(record, v, patients) {
        const f = S.consForm;
        const times = consFreeTimes(f.date, f.group, record);
        const locs = Object.values(window.ICD10 || {}).map(x => x.label);
        const yn = (id, label, val) => html`<div class="field"><label class="field__label" for="${id}">${label} <span class="req">*</span></label><select class="select" id="${id}"><option value="no" ${raw(val ? '' : 'selected')}>Нет</option><option value="yes" ${raw(val ? 'selected' : '')}>Да</option></select></div>`;
        return html`<form class="form" id="consForm" novalidate>
            <div class="callout callout--danger callout--flush" id="cfError" role="alert" hidden><div class="callout__text" id="cfErrorText"></div></div>
            ${record && consCanSeeHistory(record) ? consRevisionHistory(record) : ''}
            <div class="form__section">Пациент и группа</div>
            <div class="field"><label class="field__label" for="cfPatient">ФИО пациента <span class="req">*</span></label>
                <select class="select" id="cfPatient" ${raw(record ? 'disabled' : '')}><option value="">${patients.length ? 'Выберите пациента' : 'Нет пациентов с подходящим кодом МКБ‑10'}</option>${patients.map(p => html`<option value="${p.id}" ${raw(p.id === f.patientId ? 'selected' : '')}>${p.name}${p.icd ? ` · ${p.icd}` : ''}</option>`)}</select>
                <p class="field__hint" id="cfPatientHint">Доступных пациентов по коду МКБ‑10: ${patients.length}. Уже записанные и прошедшие консилиум исключены.</p></div>
            <div class="field"><label class="field__label" for="cfPolicy">Номер полиса ОМС</label><input class="input mono" id="cfPolicy" inputmode="numeric" maxlength="64" autocomplete="off" placeholder="Предзаполняется из карточки пациента" value="${v.policy || ''}"></div>
            <div class="field"><label class="field__label" for="cfGroup">Консилиумная группа <span class="req">*</span></label><select class="select" id="cfGroup" ${raw(record ? 'disabled' : '')}>${CONS_GROUP_KEYS.map(k => html`<option value="${k}" ${raw(k === f.group ? 'selected' : '')}>${D.GROUPS[k]}</option>`)}</select></div>
            <div class="form__section">Свободный слот</div>
            <div class="form__row">
                <div class="field"><label class="field__label" for="cfDate">Дата <span class="req">*</span></label><input class="input" type="date" id="cfDate" min="${D.today}" value="${f.date}" ${raw(record ? '' : 'disabled')} aria-describedby="cfLockedHint"></div>
                <div class="field"><label class="field__label" for="cfTime">Время <span class="req">*</span></label><select class="select" id="cfTime" ${raw(record ? '' : 'disabled')} aria-describedby="cfLockedHint"><option value="">Выберите свободный слот</option>${times.map(t => html`<option value="${t}" ${raw(t === f.time ? 'selected' : '')}>${record && f.date === record.date && t === record.time ? `${t} — текущее время` : t}</option>`)}</select></div>
            </div>
            <p class="field__hint" id="cfSlotHint">${times.length ? 'Показаны свободные слоты из расписания председателя.' : 'На выбранную дату и группу свободных слотов нет.'}</p>
            <p class="field__hint" id="cfLockedHint">При создании слот закреплён. В режиме изменения можно перенести запись на другую дату и только на свободное время в той же консилиумной группе.</p>
            <div class="form__section">Сведения для консилиума</div>
            <div class="form__row">${yn('cfBenefit', 'Наличие льготы', v.hasBenefit)}
                <div class="field"><label class="field__label" for="cfDoctor">ФИО лечащего врача</label><input class="input" id="cfDoctor" readonly value="${consPatientDoctor(patient(f.patientId))}"><p class="field__hint">Лечащий врач пациента — это значение сохраняется в записи.</p></div></div>
            <div class="field"><label class="field__label" for="cfLoc">Локализация <span class="req">*</span></label><input class="input" type="search" id="cfLoc" list="cfLocList" autocomplete="off" placeholder="Начните вводить локализацию" value="${v.diagnosisLocalization || ''}"><datalist id="cfLocList">${locs.map(l => html`<option value="${l}"></option>`)}</datalist><p class="field__hint">После выбора список кодов МКБ‑10 будет отфильтрован по локализации.</p></div>
            <div class="field"><label class="field__label" for="cfPrimary">Основной диагноз МКБ‑10 (C00–C97) <span class="req">*</span></label><input class="input mono" type="search" id="cfPrimary" list="cfDiagList" autocomplete="off" placeholder="Начните вводить код или выберите из списка" value="${v.icd || ''}"></div>
            <div class="field" ${raw(v.additionalDiagnosis ? 'hidden' : '')} id="cfAddWrap"><button type="button" class="btn btn--ghost btn--sm" data-cons-form="add-diag" aria-expanded="false">+ Добавить дополнительный диагноз</button></div>
            <div class="field" id="cfAdditionalField" ${raw(v.additionalDiagnosis ? '' : 'hidden')}><label class="field__label" for="cfAdditional">Дополнительный диагноз МКБ‑10</label>
                <div class="cons-optional-row"><input class="input mono" type="search" id="cfAdditional" list="cfDiagList" autocomplete="off" placeholder="Начните вводить код" value="${v.additionalDiagnosis || ''}"><button type="button" class="btn btn--ghost btn--sm" data-cons-form="remove-diag" aria-label="Удалить дополнительный диагноз">×</button></div></div>
            <datalist id="cfDiagList">${consDiagOptions(v.diagnosisLocalization)}</datalist>
            <div class="field"><label class="field__label" for="cfComment">Комментарий</label><textarea class="textarea" id="cfComment" rows="4" maxlength="4000" placeholder="Любая дополнительная информация для консилиума">${v.comment || ''}</textarea></div>
            <div class="field"><label class="field__label" for="cfParticipant">Дополнительный участник</label><input class="input" id="cfParticipant" maxlength="200" placeholder="ФИО или роль участника" value="${v.additionalParticipant || ''}"></div>
            <label class="check"><input type="checkbox" id="cfUvRuv" ${raw(v.uvRuv ? 'checked' : '')}> УВ/РУВ</label>
            <div class="form__row">${yn('cfSurgeon', 'Нужен хирург', v.needsSurgeon)}${yn('cfRadio', 'Нужен лучевой терапевт', v.needsRadiotherapist)}</div>
            <p class="demo-hint">Демонстрация: запись сохраняется только в этой вкладке; проверки повторяют правила сервера.</p>
        </form>`;
    }
    function consDiagOptions(locLabel) {
        const loc = Object.values(window.ICD10 || {}).find(x => x.label.toLowerCase() === String(locLabel || '').trim().toLowerCase());
        const list = (loc ? loc.diagnoses : ICD_ALL).filter(d => CONS_DIAG_RE.test(d.code));
        const seen = new Set();
        return list.filter(d => !seen.has(d.code) && seen.add(d.code)).map(d => html`<option value="${d.code}">${d.label}</option>`);
    }
    const cfShowError = (text) => { const b = $('#cfError'); $('#cfErrorText').textContent = text; b.hidden = false; b.scrollIntoView({ block: 'nearest' }); };
    function consRefreshForm(changed) {
        // Пересобираем тело формы, сохраняя введённые значения (как обновление полей в приложении).
        const f = S.consForm, record = f.id ? S.consilium.find(c => c.id === f.id) : null;
        const v = { policy: $('#cfPolicy').value, diagnosisLocalization: $('#cfLoc').value, icd: $('#cfPrimary').value, additionalDiagnosis: $('#cfAdditionalField').hidden ? '' : $('#cfAdditional').value,
            hasBenefit: $('#cfBenefit').value === 'yes', comment: $('#cfComment').value, additionalParticipant: $('#cfParticipant').value, uvRuv: $('#cfUvRuv').checked,
            needsSurgeon: $('#cfSurgeon').value === 'yes', needsRadiotherapist: $('#cfRadio').value === 'yes' };
        if (changed === 'patient' || changed === 'group') {
            const p = patient(f.patientId);
            if (changed === 'patient' && p) { v.policy = p.policy; v.icd = validConsDiagnosis(p.icd, false) || ''; v.diagnosisLocalization = icdLocLabel(v.icd); f.group = consGroupForDiagnosis(v.icd); }
        }
        const patients = consBookablePatients(f.group, record);
        if (!record && !patients.some(p => p.id === f.patientId)) { f.patientId = patients[0] ? patients[0].id : ''; const p = patient(f.patientId); if (p) { v.policy = p.policy; v.icd = validConsDiagnosis(p.icd, false) || ''; v.diagnosisLocalization = icdLocLabel(v.icd); } }
        const times = consFreeTimes(f.date, f.group, record);
        if (!times.includes(f.time)) f.time = record && f.date === record.date ? record.time : '';
        $('#modal .modal__body').innerHTML = String(consFormBody(record, v, patients));
        const save = $('[data-cons-form="save"]'); if (save) save.disabled = !patients.length;
    }
    document.addEventListener('change', (e) => {
        const t = e.target;
        if (!t.closest || !t.closest('#consForm')) return;
        const f = S.consForm;
        if (t.id === 'cfPatient') { f.patientId = t.value; consRefreshForm('patient'); $('#cfPatient').focus(); }
        else if (t.id === 'cfGroup') { f.group = t.value; consRefreshForm('group'); $('#cfGroup').focus(); }
        else if (t.id === 'cfDate') { f.date = t.value; consRefreshForm('date'); $('#cfDate').focus(); }
        else if (t.id === 'cfTime') { f.time = t.value; }
        else if (t.id === 'cfLoc') { $('#cfDiagList').innerHTML = String(html`${consDiagOptions(t.value)}`); }
    });
    document.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-cons-form]');
        if (!b) return;
        const act = b.dataset.consForm, f = S.consForm;
        if (act === 'add-diag') { $('#cfAddWrap').hidden = true; $('#cfAdditionalField').hidden = false; $('#cfAdditional').focus(); return; }
        if (act === 'remove-diag') { $('#cfAdditional').value = ''; $('#cfAdditionalField').hidden = true; $('#cfAddWrap').hidden = false; return; }
        const record = f.id ? S.consilium.find(c => c.id === f.id) : null;
        if (act === 'delete') {
            if (!record || record.createdBy !== R().user.id || !consEditable(record)) return;
            const ok = await confirmDialog({ title: 'Удалить запись на консилиум?', text: `${record.patientName}. Слот ${fmtNum(record.date)}, ${record.time} освободится для новой записи.`, confirmLabel: 'Удалить запись', danger: true });
            if (!ok) return;
            // delete_owned: запись с историей возвратов удаляется «мягко» — история сохраняется.
            S.consilium.splice(S.consilium.indexOf(record), 1);   // на месте: S.consilium и DEMO.consilium — один массив
            if (record.revisions.length) S.consDeleted.push({ ...record, deletedAt: nowStamp() });
            render(); updateBell(); toast('Запись удалена, слот снова свободен. Демонстрация: только в этой вкладке.');
            return;
        }
        if (act !== 'save') return;
        const primary = validConsDiagnosis($('#cfPrimary').value);
        const additional = $('#cfAdditionalField').hidden ? '' : validConsDiagnosis($('#cfAdditional').value, false);
        if (!primary) return cfShowError('Укажите основной диагноз кодом МКБ‑10 от C00 до C97.');
        if (additional === null) return cfShowError('Дополнительный диагноз должен быть кодом МКБ‑10 от C00 до C97.');
        const time = $('#cfTime').value, date = $('#cfDate').value, loc = $('#cfLoc').value.trim();
        if (!f.patientId || !date || !time || !f.group || !loc) return cfShowError('Заполните пациента, группу, дату, свободный слот и локализацию.');
        const p = patient(f.patientId);
        // Проверки сервера (create/update): права на пациента, группа по МКБ, пациент без записи, слот есть и свободен.
        if (!p || !canEdit(p) || !R().can.book) return cfShowError('Не удалось сохранить запись: Forbidden');
        if (consGroupForDiagnosis(p.icd) !== f.group) return cfShowError('Не удалось сохранить запись: Консилиумная группа должна соответствовать коду МКБ-10 из карточки пациента');
        if (!record && S.consilium.some(c => c.patientId === p.id)) return cfShowError('Не удалось сохранить запись: Пациент уже записан на онкологический консилиум. Используйте перенос существующей записи.');
        const sameSlot = record && record.date === date && record.time === time;
        if (!sameSlot) {
            if (!consAllSlots().some(x => x.date === date && x.time === time && x.group === f.group)) return cfShowError('Не удалось сохранить запись: Выбранный слот отсутствует в расписании председателя');
            if (consAt(date, time, f.group)) return cfShowError('Не удалось сохранить запись: Выбранный слот уже занят');
        }
        const pol = normalizeConsPolicy($('#cfPolicy').value);
        if (pol.error) return cfShowError(`Не удалось сохранить запись: ${pol.error}`);
        const fields = { date, time, policy: record ? pol.value : pol.value || p.policy, hasBenefit: $('#cfBenefit').value === 'yes', diagnosisLocalization: loc, icd: primary, additionalDiagnosis: additional,
            comment: $('#cfComment').value.trim(), additionalParticipant: $('#cfParticipant').value.trim(), uvRuv: $('#cfUvRuv').checked,
            needsSurgeon: $('#cfSurgeon').value === 'yes', needsRadiotherapist: $('#cfRadio').value === 'yes' };
        if (record) {
            if (record.createdBy !== R().user.id || !consEditable(record)) return cfShowError('Не удалось сохранить запись: Запись на рассмотрении у председателя недоступна для редактирования');
            // update: правка возвращённой записи — повторная отправка председателю.
            Object.assign(record, fields, { doctor: consPatientDoctor(p), status: record.status === 'needs_revision' ? 'revised' : record.status });
            closeModal(); render(); updateBell(); toast('Запись обновлена.');
        } else {
            S.consilium.push({ id: `c-${Date.now()}`, patientId: p.id, patientName: p.name, localization: p.localization, group: f.group, doctor: consPatientDoctor(p), createdBy: R().user.id, status: 'recorded',
                treatment: null, scheme: null, goc: false, goc65: false, routeStage: null, revisions: [], archived: false, ...fields });
            closeModal(); render(); updateBell(); toast('Пациент записан на консилиум.');
        }
    });

    /* --- Решение председателя: рассмотрение, подпись, возврат --- */
    function decisionModal(id, error) {
        const c = S.consilium.find(x => x.id === id);
        if (!c) return;
        if (!R().perms.decideConsilium || !['recorded', 'revised', 'in_review'].includes(c.status)) { if (c.status === 'signed') consDetailsModal(c); return; }
        const inReview = c.status === 'in_review';
        const schemeValue = c.scheme ? c.scheme.name : '';
        openModal(modalShell('Подписание консилиума', '', html`
            <div class="callout callout--info callout--flush"><div class="callout__text">${c.patientName} · ${fmtNum(c.date)}, ${c.time} · ${D.GROUPS[c.group]}</div></div>
            ${inReview ? '' : html`<div class="callout callout--warn callout--flush"><div class="callout__text">Рассмотрение ещё не начато. Подписать решение можно после того, как вы начнёте рассмотрение.</div></div>`}
            <div class="callout callout--danger callout--flush" id="decError" role="alert" ${raw(error ? '' : 'hidden')}><div class="callout__text">${error || ''}</div></div>
            ${consRevisionHistory(c)}
            <form class="form" id="decForm" novalidate>
                <div class="field"><label class="field__label" for="decTreatment">Назначенное лечение <span class="req">*</span></label>
                    <select class="select" id="decTreatment"><option value="">Выберите лечение</option>${Object.entries(D.TREATMENTS).map(([k, t]) => html`<option value="${k}" ${raw(c.treatment === k ? 'selected' : '')}>${t}</option>`)}</select></div>
                <div class="form__row" id="decSchemeRow" ${raw(c.treatment === 'chemotherapy' ? '' : 'hidden')}>
                    <div class="field"><label class="field__label" for="decScheme">Схема лечения <span class="req">*</span></label><input class="input" type="search" id="decScheme" list="decSchemeList" maxlength="1000" autocomplete="off" placeholder="Введите название или код схемы" value="${schemeValue}">
                        <datalist id="decSchemeList">${D.SCHEMES.map(s => html`<option value="${s.code} — ${s.name}">${s.name}</option>`)}</datalist>
                        <p class="field__hint">Можно найти схему по названию или коду sh…, либо ввести собственный вариант.</p></div>
                    <div class="field"><label class="field__label" for="decSchemeCode">Код схемы</label><input class="input mono" id="decSchemeCode" maxlength="32" readonly placeholder="Заполнится автоматически" value="${c.scheme ? c.scheme.code : ''}"></div>
                </div>
                <div class="kit-row"><label class="check"><input type="checkbox" id="decGoc" ${raw(c.goc ? 'checked' : '')}> ГОК</label><label class="check"><input type="checkbox" id="decGoc65" ${raw(c.goc65 ? 'checked' : '')}> ГОК 65+</label></div>
            </form>`,
            html`${inReview ? html`<button class="btn btn--ghost" data-cons-dec="return" data-id="${c.id}">Вернуть на доработку</button>` : ''}<span class="spacer"></span>
                ${inReview ? html`<button class="btn btn--primary" data-cons-dec="sign" data-id="${c.id}">Подписать</button>` : html`<button class="btn btn--primary" data-cons-dec="start" data-id="${c.id}">Начать рассмотрение</button>`}`));
    }
    const consSyncScheme = () => {
        const input = $('#decScheme'); if (!input) return;
        const val = input.value.trim().toLowerCase();
        const s = D.SCHEMES.find(x => x.code.toLowerCase() === val || x.name.toLowerCase() === val || `${x.code} — ${x.name}`.toLowerCase() === val);
        $('#decSchemeCode').value = s ? s.code : '';
        if (s && input.value.includes('—')) input.value = s.name;
    };
    function returnModal(id, error) {
        const c = S.consilium.find(x => x.id === id);
        openModal(modalShell('Вернуть на доработку', c.patientName, html`
            <div class="field"><label class="field__label" for="retComment">Комментарий врачу <span class="req">*</span></label>
                <textarea class="textarea" id="retComment" rows="5" maxlength="4000" placeholder="Укажите, что необходимо уточнить или исправить"></textarea></div>
            <div class="callout callout--danger callout--flush" id="retError" role="alert" ${raw(error ? '' : 'hidden')}><div class="callout__text">${error || ''}</div></div>`,
            html`<span class="spacer"></span><button class="btn btn--ghost" data-close>Отмена</button><button class="btn btn--danger" data-cons-dec="confirm-return" data-id="${c.id}">Вернуть врачу</button>`));
    }
    document.addEventListener('change', (e) => {
        if (e.target.id === 'decTreatment') {
            const chemo = e.target.value === 'chemotherapy';
            $('#decSchemeRow').hidden = !chemo;
            if (!chemo) { $('#decScheme').value = ''; $('#decSchemeCode').value = ''; }
        }
        if (e.target.id === 'decScheme') consSyncScheme();
    });
    document.addEventListener('input', (e) => { if (e.target.id === 'decScheme') consSyncScheme(); });
    document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-cons-dec]');
        if (!b) return;
        const c = S.consilium.find(x => x.id === b.dataset.id);
        if (!c || !R().perms.decideConsilium) return;   // решение — только председатель (can_decide_consilium)
        const act = b.dataset.consDec;
        if (act === 'start') {
            if (!['recorded', 'revised'].includes(c.status)) { decisionModal(c.id, 'Не удалось начать рассмотрение: Рассмотрение можно начать только для записи, ожидающей председателя'); return; }
            c.status = 'in_review'; render(); decisionModal(c.id); return;
        }
        if (act === 'sign') {
            const treatment = $('#decTreatment').value; consSyncScheme();
            const schemeName = $('#decScheme').value.trim(), code = $('#decSchemeCode').value.trim();
            if (!treatment || (treatment === 'chemotherapy' && !schemeName)) { const box = $('#decError'); box.querySelector('.callout__text').textContent = treatment ? 'Для химиотерапевтического лечения укажите схему.' : 'Выберите назначенное лечение.'; box.hidden = false; return; }
            if (c.status !== 'in_review') { decisionModal(c.id, 'Не удалось подписать: Подписать можно только запись, рассмотрение которой начато'); return; }
            const p = patient(c.patientId);
            // sign: схема — только для химиотерапии; этап маршрута фиксируется, но не меняется.
            c.signedDoctor = consDoctor(c);
            Object.assign(c, { status: 'signed', treatment, scheme: treatment === 'chemotherapy' ? { code, name: schemeName } : null, goc: $('#decGoc').checked, goc65: $('#decGoc65').checked, routeStage: p ? p.stage : null });
            closeModal(); render(); updateBell(); toast('Решение консилиума подписано. Демонстрация: только в этой вкладке.');
            return;
        }
        if (act === 'return') { if (c.status === 'in_review') returnModal(c.id); return; }
        if (act === 'confirm-return') {
            const text = $('#retComment').value.trim();
            if (!text) { const box = $('#retError'); box.querySelector('.callout__text').textContent = 'Комментарий для врача обязателен.'; box.hidden = false; $('#retComment').focus(); return; }
            const last = c.revisions[c.revisions.length - 1];
            if (last && last.text === text) { const box = $('#retError'); box.querySelector('.callout__text').textContent = 'Не удалось вернуть запись: Комментарий должен отличаться от предыдущего возврата'; box.hidden = false; return; }
            if (c.status !== 'in_review') { const box = $('#retError'); box.querySelector('.callout__text').textContent = 'Не удалось вернуть запись: Вернуть на доработку можно только запись, рассмотрение которой начато'; box.hidden = false; return; }
            // return_for_revision: новый раунд с автором, временем и текстом сохраняется навсегда.
            c.revisions.push({ round: c.revisions.length + 1, author: R().user.short, at: nowStamp(), text });
            c.status = 'needs_revision';
            closeModal(); render(); updateBell(); toast('Запись возвращена врачу на доработку.');
        }
    });

    /* --- Архив подписанных записей автора (set_archived, openConsiliumArchive) --- */
    const consCanArchive = (c) => c.status === 'signed' && R().can.book && !consIsChair() && c.createdBy === R().user.id;
    S.consArchive = { date: '', q: '' };
    function consArchiveModal() {
        const a = S.consArchive, q = a.q.trim().toLowerCase();
        const rows = S.consilium.filter(c => c.archived && c.createdBy === R().user.id && (!a.date || c.date === a.date) && (!q || c.patientName.toLowerCase().includes(q) || (c.policy || '').toLowerCase().includes(q)));
        openModal(modalShell('Архив консилиумов', 'Подписанные консилиумы, которые вы перенесли в архив. Нажмите на случай, чтобы открыть его или восстановить.', html`
            <div class="form__row"><div class="field"><label class="field__label" for="arDate">Дата консилиума</label><input class="input" type="date" id="arDate" value="${a.date}"></div>
                <div class="field"><label class="field__label" for="arSearch">Поиск по ФИО или полису</label><input class="input" type="search" id="arSearch" placeholder="ФИО или полис…" autocomplete="off" value="${a.q}"></div></div>
            ${rows.length ? html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Дата и время</th><th>Пациент</th><th>Группа</th><th>МКБ-10</th><th>Назначение</th><th>Статус</th></tr></thead><tbody>${rows.map(c => html`
                <tr class="is-link" data-cons-arch="${c.id}" tabindex="0" aria-label="Открыть архивный консилиум: ${c.patientName}"><td class="mono">${fmtNum(c.date)}, ${c.time}</td><td data-label="Пациент"><div class="cell-title">${c.patientName}</div><div class="cell-sub">${consPolicy(c) || 'Полис не указан'}</div></td>
                <td data-label="Группа">${D.GROUPS[c.group]}</td><td class="mono" data-label="МКБ-10">${c.icd}</td><td data-label="Назначение">${c.treatment ? D.TREATMENTS[c.treatment] : '—'}</td><td data-label="Статус"><span class="status status--ok">В архиве</span></td></tr>`)}</tbody></table></div>`
                : html`<div class="empty"><div class="empty__title">Подписанные консилиумы по заданным условиям не найдены.</div></div>`}`,
            html`<span class="spacer"></span><button class="btn btn--primary" data-close>Закрыть</button>`));
    }
    document.addEventListener('input', (e) => { if (e.target.id === 'arSearch') { S.consArchive.q = e.target.value; consArchiveModal(); const n = $('#arSearch'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); } });
    document.addEventListener('change', (e) => { if (e.target.id === 'arDate') { S.consArchive.date = e.target.value; consArchiveModal(); } });
    const openArch = (el) => { const c = S.consilium.find(x => x.id === el.dataset.consArch); if (c) consDetailsModal(c); };
    document.addEventListener('click', (e) => {
        const r = e.target.closest('[data-cons-arch]'); if (r) { openArch(r); return; }
        const b = e.target.closest('[data-cons-archive]');
        if (!b) return;
        const c = S.consilium.find(x => x.id === b.dataset.id);
        if (!c || !consCanArchive(c)) return;   // как сервер: только автор, только подписанная запись
        const archive = b.dataset.consArchive === 'archive';
        c.archived = archive;
        closeModal(); render();
        toast(archive ? 'Запись перенесена в архив. Демонстрация: только в этой вкладке.' : 'Запись восстановлена из архива.');
    });
    document.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches('[data-cons-arch]')) { e.preventDefault(); openArch(e.target); } });

    /* ---------- Консилиум: избранное председателя и реестры (этап 3.3) ----------
       По static/app.js (renderConsiliumChairActions, toggleConsiliumFavorite,
       renderConsiliumRegistries, setSurgicalReferralCompleted,
       export*Registry) и серверу (routes/consilium.py: favorites, registries,
       *.xlsx; services/consilium.py: add_favorite, registry_rows,
       goc_registry_rows, set_referral_completed; services/xlsx_export.py). */

    // Избранное — закладка председателя на пациента; доступ к карточке не даёт.
    S.consFav = {};
    const consFavSet = () => S.consFav[R().user.id] || (S.consFav[R().user.id] = new Set());
    const consIsFav = (c) => consFavSet().has(c.patientId);

    // registry_rows / goc_registry_rows: подписанные (архивные тоже), за выбранный день, по дате, времени и ФИО.
    function consRegistry(kind, date) {
        const signed = S.consilium.filter(c => c.status === 'signed' && (!date || c.date === date));
        const rows = kind === 'surgical' ? signed.filter(c => c.treatment === 'surgery')
            : kind === 'chemotherapy' ? signed.filter(c => c.treatment === 'chemotherapy')
            : kind === 'goc' ? signed.filter(c => c.goc) : signed.filter(c => c.goc65);
        return rows.sort((a, b) => `${a.date} ${a.time}`.localeCompare(`${b.date} ${b.time}`) || a.patientName.localeCompare(b.patientName, 'ru'));
    }
    const consSchemeText = (c) => c.scheme ? `${c.scheme.code ? c.scheme.code + ' — ' : ''}${c.scheme.name}` : '';
    const REGISTRY_EXPORT = {
        chemotherapy: { sheet: 'Химиотерапия', file: 'chemotherapy_registry', ok: 'Excel-реестр сформирован.',
            header: ['Дата', 'Время', 'Пациент', 'Полис ОМС', 'Диагноз', 'Код схемы', 'Схема лечения', 'Лечащий врач', 'Консилиумная группа'],
            row: (c) => [c.date, c.time, c.patientName, consPolicy(c), c.icd, c.scheme ? c.scheme.code : '', c.scheme ? c.scheme.name : '', consDoctor(c), D.GROUPS[c.group]] },
        goc: { sheet: 'ГОК', file: 'goc_registry', ok: 'Excel-реестр ГОК сформирован.',
            header: ['Дата', 'Время', 'Пациент', 'Полис ОМС', 'Диагноз', 'Назначенное лечение', 'Схема лечения', 'Лечащий врач', 'Консилиумная группа'],
            row: (c) => [c.date, c.time, c.patientName, consPolicy(c), c.icd, c.treatment ? D.TREATMENTS[c.treatment] : '', c.scheme ? c.scheme.name : '', consDoctor(c), D.GROUPS[c.group]] },
        goc65: { sheet: 'ГОК 65+', file: 'goc_65_plus_registry', ok: 'Excel-реестр ГОК 65+ сформирован.',
            header: ['Дата', 'Время', 'Пациент', 'Полис ОМС', 'Диагноз', 'Назначенное лечение', 'Схема лечения', 'Лечащий врач', 'Консилиумная группа'],
            row: (c) => [c.date, c.time, c.patientName, consPolicy(c), c.icd, c.treatment ? D.TREATMENTS[c.treatment] : '', c.scheme ? c.scheme.name : '', consDoctor(c), D.GROUPS[c.group]] },
    };

    function consRegistries(date) {
        if (!consIsChair()) return '';
        const who = (c) => html`<div class="cell-title">${c.patientName}</div><div class="cell-sub">${consDoctor(c)}</div>`;
        const table = (kind, cols, cell, empty) => {
            const rows = consRegistry(kind, date);
            return stateful(rows.length ? html`<div class="table-wrap"><table class="table table--cards"><thead><tr>${cols.map(t => html`<th>${t}</th>`)}</tr></thead><tbody>${rows.map(c => html`<tr>
                <td class="mono">${fmtNum(c.date)}, ${c.time}</td><td data-label="Пациент">${who(c)}</td><td class="mono" data-label="Полис">${consPolicy(c) || 'Не указан'}</td><td class="mono" data-label="Диагноз">${c.icd}</td>${cell(c)}</tr>`)}</tbody></table></div>`
                : html`<div class="empty empty--compact"><div>${empty}</div></div>`, { rows: 3, emptyTitle: empty, emptyText: '' });
        };
        const exp = (kind) => html`<button class="btn btn--sm btn--ghost" data-cons-reg="${kind}" ${raw(S.demo === 'ok' ? '' : 'disabled')}>Экспорт в Excel</button>`;
        const treat = (c) => c.scheme ? `${D.TREATMENTS[c.treatment]}: ${consSchemeText(c)}` : (c.treatment ? D.TREATMENTS[c.treatment] : 'Не указано');
        const canMark = !!R().perms.markReferralCompletion;
        return html`<div class="cons-registries">
            <section class="panel"><div class="panel__head"><div><h2 class="panel__title">Реестр хирургического лечения</h2><div class="panel__meta">Формируется автоматически после подписания решения о хирургическом лечении.</div></div></div>
                ${table('surgical', ['Дата и время', 'Пациент', 'Полис', 'Диагноз', 'Направление создано'], (c) => html`<td data-label="Направление"><label class="check"><input type="checkbox" data-cons-referral="${c.id}" ${raw(c.referralCompleted ? 'checked' : '')} ${raw(canMark ? '' : 'disabled')}> ${c.referralCompleted ? 'Отмечено' : 'Поставить отметку'}</label></td>`, 'В выбранном периоде хирургических назначений нет.')}</section>
            <section class="panel"><div class="panel__head panel__head--wrap"><div><h2 class="panel__title">Реестр химиотерапевтического лечения</h2><div class="panel__meta">Содержит подписанные назначения и выбранные схемы лечения.</div></div>${exp('chemotherapy')}</div>
                ${table('chemotherapy', ['Дата и время', 'Пациент', 'Полис', 'Диагноз', 'Схема лечения'], (c) => html`<td data-label="Схема">${consSchemeText(c) || 'Не указана'}</td>`, 'В выбранном периоде химиотерапевтических назначений нет.')}</section>
            <section class="panel"><div class="panel__head panel__head--wrap"><div><h2 class="panel__title">Реестр ГОК</h2><div class="panel__meta">Формируется автоматически из подписанных решений с отметкой «ГОК».</div></div>${exp('goc')}</div>
                ${table('goc', ['Дата и время', 'Пациент', 'Полис', 'Диагноз', 'Лечение'], (c) => html`<td data-label="Лечение">${treat(c)}</td>`, 'В выбранном периоде случаев ГОК нет.')}</section>
            <section class="panel"><div class="panel__head panel__head--wrap"><div><h2 class="panel__title">Реестр ГОК 65+</h2><div class="panel__meta">Формируется автоматически из подписанных решений с отметкой «ГОК 65+».</div></div>${exp('goc65')}</div>
                ${table('goc65', ['Дата и время', 'Пациент', 'Полис', 'Диагноз', 'Лечение'], (c) => html`<td data-label="Лечение">${treat(c)}</td>`, 'В выбранном периоде случаев ГОК 65+ нет.')}</section>
            <p class="demo-hint">Реестры строятся из синтетических записей во вкладке за выбранный день. «Экспорт в Excel» формирует файл в браузере в том же формате, что сервер (все ячейки — текст, формулы не выполняются); в приложении файл формирует сервер и записывает выгрузку в журнал аудита.</p>
        </div>`;
    }

    /* --- XLSX в браузере: тот же состав частей и правила, что services/xlsx_export.py --- */
    // Как _XML_INVALID в services/xlsx_export.py: управляющие (кроме \t \n \r), U+FFFE/U+FFFF и одиночные
    // суррогаты. С флагом u диапазон суррогатов совпадает только с непарными: корректные пары
    // UTF-16 (emoji и другие символы вне BMP) сохраняются — в Python это один символ.
    const XML_INVALID = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]|[\uD800-\uDFFF]/gu;
    const xmlEscape = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    // cell_text: недопустимые в XML символы удаляются, текст экранируется; ячейка — встроенная строка со стилем quotePrefix.
    const xlsxCellText = (v) => xmlEscape(String(v === null || v === undefined ? '' : v).replace(XML_INVALID, ''));
    const xlsxColumn = (i) => { let r = '', v = i + 1; while (v) { const m = (v - 1) % 26; r = String.fromCharCode(65 + m) + r; v = Math.floor((v - 1) / 26); } return r; };
    function xlsxParts(sheetName, rows) {
        const NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main';
        const safeSheet = xmlEscape(Array.from((sheetName || 'Реестр').replace(XML_INVALID, '')).slice(0, 31).join('')).replace(/"/g, '&quot;');   // 31 символ, как [:31] в Python
        const sheetRows = rows.map((row, ri) => `<row r="${ri + 1}">${row.map((v, ci) => `<c r="${xlsxColumn(ci)}${ri + 1}" s="1" t="inlineStr"><is><t xml:space="preserve">${xlsxCellText(v)}</t></is></c>`).join('')}</row>`).join('');
        const head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
        return [
            ['[Content_Types].xml', `${head}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>`],
            ['_rels/.rels', `${head}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`],
            ['xl/workbook.xml', `${head}<workbook xmlns="${NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="${safeSheet}" sheetId="1" r:id="rId1"/></sheets></workbook>`],
            ['xl/_rels/workbook.xml.rels', `${head}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`],
            ['xl/styles.xml', `${head}<styleSheet xmlns="${NS}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf xfId="0"/><xf xfId="0" quotePrefix="1"/></cellXfs></styleSheet>`],
            ['xl/worksheets/sheet1.xml', `${head}<worksheet xmlns="${NS}"><sheetData>${sheetRows}</sheetData></worksheet>`],
        ];
    }
    // ZIP без сжатия (store): CRC-32 и заголовки по спецификации; Excel и LibreOffice такой архив читают.
    const CRC_TABLE = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
    const crc32 = (bytes) => { let c = 0xFFFFFFFF; for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; };
    function zipStore(files) {
        const enc = new TextEncoder(), chunks = [], central = [];
        let offset = 0;
        const u16 = (v) => [v & 0xFF, (v >>> 8) & 0xFF], u32 = (v) => [v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF];
        for (const [name, text] of files) {
            const nameBytes = enc.encode(name), data = enc.encode(text), crc = crc32(data);
            const local = [...u32(0x04034b50), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0)];
            chunks.push(new Uint8Array(local), nameBytes, data);
            central.push(new Uint8Array([...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0x0800), ...u16(0), ...u16(0), ...u16(0x21), ...u32(crc), ...u32(data.length), ...u32(data.length), ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0), ...u32(offset)]), nameBytes);
            offset += local.length + nameBytes.length + data.length;
        }
        const centralSize = central.reduce((n, p) => n + p.length, 0);
        const end = new Uint8Array([...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length), ...u32(centralSize), ...u32(offset), ...u16(0)]);
        return new Blob([...chunks, ...central, end], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    }
    function consRegistryXlsx(kind, date) {
        const spec = REGISTRY_EXPORT[kind];
        const rows = [spec.header, ...consRegistry(kind, date).map(spec.row)];
        return { blob: zipStore(xlsxParts(spec.sheet, rows)), name: `${spec.file}_${date || 'all'}.xlsx`, count: rows.length - 1, rows };
    }

    document.addEventListener('click', (e) => {
        const fav = e.target.closest('[data-cons-fav]');
        if (fav) {
            if (!R().perms.useConsiliumFavorites) return;   // _require_favorites: только председатель
            const c = S.consilium.find(x => x.id === fav.dataset.consFav);
            // Как сервер: пациент должен быть связан с действующей записью консилиума, иначе 404.
            if (!c) { toast('Не удалось изменить избранное: Patient not found'); return; }
            const set = consFavSet();
            if (set.has(c.patientId)) { set.delete(c.patientId); toast('Пациент удалён из избранного.'); }
            else { set.add(c.patientId); toast('Пациент добавлен в избранное консилиума.'); }
            render();
            return;
        }
        const reg = e.target.closest('[data-cons-reg]');
        if (reg) {
            if (!consIsChair()) return;   // интерфейс показывает реестры только председателю (сервер — и разработчику, расхождение 1)
            const spec = REGISTRY_EXPORT[reg.dataset.consReg];
            const out = consRegistryXlsx(reg.dataset.consReg, S.cons.chairDate);
            const link = document.createElement('a');
            link.href = URL.createObjectURL(out.blob); link.download = out.name;
            document.body.append(link); link.click();
            setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 0);
            toast(`${spec.ok} Демонстрация: файл «${out.name}» (${out.count} ${plural(out.count, 'строка', 'строки', 'строк')}) сформирован в браузере из синтетических данных, сервер не вызывался.`);
        }
    });
    document.addEventListener('change', (e) => {
        const box = e.target.closest && e.target.closest('[data-cons-referral]');
        if (!box) return;
        const c = S.consilium.find(x => x.id === box.dataset.consReferral);
        if (!R().perms.markReferralCompletion) { box.checked = !box.checked; return; }   // _require_referral_mark: только председатель
        // set_referral_completed: только подписанное хирургическое направление.
        if (!c || c.status !== 'signed' || c.treatment !== 'surgery') { toast('Не удалось сохранить отметку: Only signed surgical referrals can be completed'); render(); return; }
        c.referralCompleted = box.checked;
        render();
        toast(box.checked ? 'Направление отмечено как созданное.' : 'Отметка направления снята.');
    });

    function viewConsiliumAnalytics() {
        const signed = S.consilium.filter(c => c.status === 'signed');
        const max = (items) => Math.max(1, ...items.map(i => i.value));
        const bars = (items) => html`<div class="bars">${items.map(b => html`<div class="bars__row"><span class="bars__label" title="${b.label}">${b.label}</span><span class="bars__track"><span class="bars__fill" style="width:${(b.value / max(items)) * 100}%"></span></span><span class="bars__value">${b.value}</span></div>`)}</div>`;
        const treat = Object.entries(D.TREATMENTS).map(([k, v]) => ({ label: v, value: signed.filter(c => c.treatment === k).length }));
        const load = Object.entries(D.GROUPS).map(([k, v]) => ({ label: v, value: S.consilium.filter(c => c.group === k).length })).filter(g => g.value);
        const schemes = D.SCHEMES.map(s => ({ label: s.name, value: signed.filter(c => c.scheme && c.scheme.code === s.code).length })).filter(s => s.value);
        return html`
            ${pagehead('Аналитика консилиума', 'Назначения консилиума и фактическая загрузка расписания', html`<button class="btn" data-action="registry">Реестры (XLSX)</button>`)}
            <div class="stack">
                ${metrics([
                    { label: 'Всего записей', value: S.consilium.length, note: 'за выбранный день' },
                    { label: 'Подписано', value: signed.length },
                    { label: 'Ожидают решения', value: S.consilium.filter(c => ['recorded', 'in_review', 'revised'].includes(c.status)).length },
                    { label: 'Возвращено', value: S.consilium.filter(c => c.status === 'needs_revision').length, tone: 'warn', note: 'ожидают доработки врача' },
                ])}
                <div class="grid grid--3">
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Назначенное лечение</h2></div>${stateful(bars(treat), { rows: 5 })}</section>
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Загруженность расписания</h2></div>${stateful(bars(load), { rows: 3 })}</section>
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Назначенные схемы лечения</h2></div>${stateful(schemes.length ? bars(schemes) : html`<div class="empty">Схем ещё не назначено</div>`, { rows: 3 })}</section>
                </div>
            </div>`;
    }

    function viewAnalytics() {
        if (S.role === 'chair') return viewConsiliumAnalytics();
        const list = scoped();
        const period = S.period || '12w';
        const loc = Object.values(list.reduce((acc, p) => { (acc[p.localization] ||= { label: p.localization, value: 0 }).value++; return acc; }, {})).sort((a, b) => b.value - a.value);
        const reasons = Object.entries(D.ATTENTION_REASONS).map(([k, v]) => ({ label: v, value: list.filter(p => p.attention.some(a => a.code === k)).length })).filter(r => r.value);
        const stages = D.byStage.filter(s => s.value);
        const bars = (items) => { const max = Math.max(1, ...items.map(i => i.value)); return html`<div class="bars">${items.map(b => html`<div class="bars__row"><span class="bars__label" title="${b.label}">${b.label}</span><span class="bars__track"><span class="bars__fill" style="width:${(b.value / max) * 100}%"></span></span><span class="bars__value">${b.value}</span></div>`)}</div>`; };
        const weeks = D.weeks.slice(period === '4w' ? -4 : 0);
        const W = 640, H = 200, pad = 28, bw = (W - pad * 2) / weeks.length;
        const maxW = Math.max(...weeks.map(w => Math.max(w.added, w.completed)));
        const y = (v) => H - pad - (v / maxW) * (H - pad * 2);
        const chart = html`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Новые и завершившие лечение пациенты по неделям">
            ${[0, 0.5, 1].map(f => html`<line x1="${pad}" x2="${W - pad / 2}" y1="${y(maxW * f)}" y2="${y(maxW * f)}" stroke="var(--border)" stroke-width="1"/><text x="${pad - 6}" y="${y(maxW * f) + 4}" text-anchor="end">${Math.round(maxW * f)}</text>`)}
            ${weeks.map((w, i) => html`<rect x="${pad + i * bw + bw * 0.18}" y="${y(w.added)}" width="${bw * 0.3}" height="${H - pad - y(w.added)}" rx="2" fill="var(--chart-1)"><title>${w.label}: новых ${w.added}</title></rect>
                <rect x="${pad + i * bw + bw * 0.52}" y="${y(w.completed)}" width="${bw * 0.3}" height="${H - pad - y(w.completed)}" rx="2" fill="var(--chart-2)"><title>${w.label}: завершили ${w.completed}</title></rect>
                <text x="${pad + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${w.label}</text>`)}</svg>`;
        const doctors = D.DOCTORS.map(d => { const ps = list.filter(p => p.doctorId === d.id); return { ...d, n: ps.length, att: ps.filter(p => p.attention.length || p.overdue).length, over: ps.reduce((n, p) => n + p.overdue, 0) }; }).filter(d => d.n);
        return html`
            ${pagehead('Аналитика', isReadOnly() || S.role === 'developer' ? 'Состояние маршрутов пациентов по клинике' : 'Показатели в пределах вашей области доступа',
                html`<div class="segmented" role="group" aria-label="Период"><button aria-pressed="${period === '4w'}" data-action="period" data-v="4w">4 недели</button><button aria-pressed="${period === '12w'}" data-action="period" data-v="12w">12 недель</button></div>${S.role !== 'chair' ? html`<button class="btn" data-action="export">Выгрузить CSV</button>` : ''}`,
                isReadOnly() ? 'Управленческая аналитика' : '')}
            <div class="stack">
                ${metrics([
                    { label: 'Всего пациентов', value: list.length },
                    { label: 'В работе', value: list.filter(p => !['completed', 'dispensary-observation', 'diagnosis-not-confirmed'].includes(p.stage)).length },
                    { label: 'Явно требуют внимания', value: list.filter(p => p.attention.length).length, tone: 'warn' },
                    { label: 'С просроченными событиями', value: list.filter(p => p.overdue).length, tone: 'danger' },
                ])}
                <section class="panel"><div class="panel__head"><div><h2 class="panel__title">Динамика пациентов</h2><div class="panel__meta">Новые и завершившие лечение по неделям</div></div></div>
                    ${stateful(html`<div class="chart">${chart}</div><div class="legend"><span>Новые</span><span class="l2">Завершили лечение</span></div>`, { rows: 6 })}</section>
                <div class="grid grid--3">
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">По этапам маршрута</h2></div>${stateful(bars(stages), { rows: 6 })}</section>
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">По локализациям</h2></div>${stateful(bars(loc), { rows: 6 })}</section>
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Причины внимания</h2></div>${stateful(reasons.length ? bars(reasons) : html`<div class="empty">Открытых отметок нет</div>`, { rows: 4 })}</section>
                </div>
                <section class="panel"><div class="panel__head"><div><h2 class="panel__title">Нагрузка врачей</h2><div class="panel__meta">Пациенты в области доступа</div></div></div>
                    <div class="panel__body--flush">${stateful(html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Врач</th><th>Отделение</th><th class="num">Пациенты</th><th class="num">Требуют внимания</th><th class="num">Просроченные события</th></tr></thead><tbody>
                        ${doctors.map(d => html`<tr><td class="cell-title">${d.name}</td><td data-label="Отделение">${D.DEPARTMENTS.find(x => x.id === d.dept).name}</td><td class="num" data-label="Пациенты">${d.n}</td><td class="num" data-label="Внимание">${d.att || '—'}</td><td class="num" data-label="Просрочки">${d.over ? html`<span class="status status--danger">${d.over}</span>` : '—'}</td></tr>`)}
                    </tbody></table></div>`, { rows: 5 })}</div></section>
            </div>`;
    }

    function denied(id) {
        return html`${pagehead(NAV_LABEL[id] || 'Раздел', '')}<section class="panel"><div class="error"><div class="error__title">Раздел недоступен для вашей роли</div><div>Права проверяет сервер; в прототипе они повторяют правила приложения.</div><button class="btn btn--sm" data-href="${R().home}">На главную</button></div></section>`;
    }

    function viewLegacy(id) {
        return html`${pagehead(NAV_LABEL[id], '')}
            <section class="panel"><div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${raw(ICONS[id] || '')}</svg>
            <div class="empty__title">Раздел сохраняется без изменений функциональности</div>
            <div>В этой демоверсии он не перерисован. После согласования визуального языка он получит те же таблицы, формы и состояния, что и показанные экраны.</div></div></section>`;
    }

    /* ---------- Модальные окна ---------- */
    let lastFocus = null, closeTimer = 0;
    function openModal(content, onReady) {
        const modal = $('#modal'), scrim = $('#scrim');
        clearTimeout(closeTimer);
        if (modal.hidden) lastFocus = document.activeElement;
        modal.innerHTML = String(content);
        modal.hidden = false; scrim.hidden = false;
        modal.classList.remove('is-closing');
        // Перерасчёт стилей фиксирует начальное состояние, затем класс запускает переход.
        void modal.offsetWidth;
        modal.classList.add('is-open'); scrim.classList.add('is-open');
        const first = modal.querySelector('select, input, textarea, button:not([data-close])');
        (first || modal).focus({ preventScroll: true });
        if (onReady) onReady(modal);
    }
    function closeModal() {
        const modal = $('#modal'), scrim = $('#scrim');
        if (modal.hidden) return;
        if (S.confirmResolve) settleConfirm(false);
        modal.classList.remove('modal--confirm');
        modal.classList.add('is-closing');
        modal.classList.remove('is-open'); scrim.classList.remove('is-open');
        clearTimeout(closeTimer);
        closeTimer = setTimeout(() => { modal.hidden = true; scrim.hidden = true; modal.innerHTML = ''; modal.classList.remove('is-closing'); }, 150);
        if (lastFocus) lastFocus.focus({ preventScroll: true });
    }
    const modalShell = (title, sub, body, foot) => html`
        <div class="modal__head"><div><h2 class="modal__title" id="modalTitle">${title}</h2>${sub ? html`<div class="modal__sub">${sub}</div>` : ''}</div>
            <button class="iconbtn" data-close aria-label="Закрыть"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
        <div class="modal__body">${body}</div><div class="modal__foot">${foot}</div>`;

    function patientModal() {
        openModal(modalShell('Новый пациент', 'Карточка появится в списке после сохранения', html`
            <form class="form" id="newForm" novalidate>
                <div class="field"><label class="field__label" for="npName">ФИО <span class="req">*</span></label><input class="input" id="npName" autocomplete="off"><span class="field__error" id="npNameErr" hidden>Укажите ФИО пациента.</span></div>
                <div class="form__row"><div class="field"><label class="field__label" for="npPolicy">Полис ОМС</label><input class="input mono" id="npPolicy" inputmode="numeric" placeholder="16 цифр"><span class="field__error" id="npPolicyErr" hidden>Полис ОМС — 16 цифр.</span></div>
                    <div class="field"><label class="field__label" for="npAge">Возраст</label><input class="input" id="npAge" inputmode="numeric"></div></div>
                <div class="form__row"><div class="field"><label class="field__label" for="npNos">Нозология <span class="req">*</span></label><select class="select" id="npNos"><option value="">Выберите</option>${Object.entries(D.NOSOLOGY).map(([k, v]) => html`<option value="${k}">${v.label}</option>`)}</select><span class="field__error" id="npNosErr" hidden>Выберите нозологию.</span></div>
                    <div class="field"><label class="field__label" for="npStage">Этап</label><select class="select" id="npStage">${['primary-appointment', 'exam'].map(s => html`<option value="${s}">${D.STAGES[s]}</option>`)}</select></div></div>
                <div class="field"><label class="field__label" for="npDoc">Лечащий врач</label><input class="input" id="npDoc" readonly value="${R().scopeStrategy === 'by_treating_doctor' ? R().user.name : R().scopeStrategy === 'by_department' ? 'Выбирается из врачей отделения' : 'Выбирается из активных врачей или не указывается'}"></div>
            </form>`, html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-action="save-patient">Сохранить</button>`));
    }

    /* ---------- Действия ---------- */
    const toast = (text) => {
        const t = document.createElement('div'); t.className = 'toast'; t.textContent = text;
        $('#toasts').append(t);
        setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateY(4px)'; setTimeout(() => t.remove(), 200); }, 2600);
    };
    const invalid = (input, errId, bad) => { input.toggleAttribute('aria-invalid', bad); if (bad) input.setAttribute('aria-invalid', 'true'); $(`#${errId}`).hidden = !bad; return bad; };

    const ACTIONS = {
        retry: () => { $('#stateSelect').value = 'ok'; S.demo = 'ok'; render(); },
        'new-patient': () => patientModal(),
        export: () => toast('В прототипе файл не формируется — экспорт работает в приложении.'),
        import: () => toast('В прототипе файл не загружается — импорт с проверкой и подтверждением работает в приложении.'),
        registry: () => toast('Реестры XLSX выгружаются в приложении; в прототипе — только кнопка.'),
        calview: (el) => { S.calView = el.dataset.v; render(); },
        period: (el) => { S.period = el.dataset.v; render(); },
        decide: (el) => decisionModal(el.dataset.id),
        book: (el) => bookModal({ id: el.dataset.id, time: el.dataset.time, date: el.dataset.date, group: el.dataset.group }),
        'save-patient': () => {
            const name = $('#npName'), pol = $('#npPolicy'), nos = $('#npNos');
            const bad = [invalid(name, 'npNameErr', name.value.trim().split(/\s+/).length < 2), invalid(pol, 'npPolicyErr', !!pol.value && !/^\d{16}$/.test(pol.value)), invalid(nos, 'npNosErr', !nos.value)];
            if (bad.some(Boolean)) { $('#modal').querySelector('[aria-invalid="true"]').focus(); return; }
            toast('В прототипе карточка не сохраняется — форма показывает поведение и проверки.'); closeModal();
        },
    };

    /* ---------- Сообщения пациентам (демонстрация) ----------
       Сценарий взят из Patient Portal v0.5.1: переписка существует только в
       рамках отношения врач–пациент. Раздел и диалоги есть только у роли
       «Врач» и только по своим пациентам. Председатель тоже может быть
       лечащим врачом (созданные им пациенты), но переписка ему не
       открыта — это открытый вопрос, см. README. API переписки нет:
       состояние локальное, доставка, сервер и синхронизация не подключены. */
    S.msg = { list: D.conversations, q: '', drafts: {} };
    const CONTEXT_LABEL = { assigned_doctor: 'Лечащий врач', care_route: 'По маршруту лечения', appointment: 'По записи на прием' };
    const STATUS_LABEL = { sending: 'Отправляется…', demo: 'Только в демо · не доставлено', read: 'Прочитано' };
    const canMessage = () => R().nav.includes('messages');
    const convs = () => (canMessage() ? S.msg.list.filter(c => c.doctorId === R().user.id && R().scope(patient(c.patientId))) : []);
    const convFor = (patientId) => convs().find(c => c.patientId === patientId);
    const unreadTotal = () => convs().reduce((n, c) => n + c.unread, 0);
    const lastMsg = (c) => c.messages[c.messages.length - 1];
    const msgTime = (stamp) => {
        const [d, t] = stamp.split('T');
        const diff = Math.round((parse(D.today) - parse(d)) / 86400000);
        return diff === 0 ? t : diff === 1 ? 'вчера' : fmtNum(d).slice(0, 5);
    };
    const dayLabel = (d) => { const diff = Math.round((parse(D.today) - parse(d)) / 86400000); return diff === 0 ? 'Сегодня' : diff === 1 ? 'Вчера' : `${parse(d).getDate()} ${MONTHS[parse(d).getMonth()]}`; };

    function messagesList(activeId) {
        const q = S.msg.q.toLowerCase();
        const all = convs().slice().sort((a, b) => ((lastMsg(b) || {}).at || '').localeCompare((lastMsg(a) || {}).at || ''));
        const list = q ? all.filter(c => { const p = patient(c.patientId); return p.name.toLowerCase().includes(q) || c.messages.some(m => m.text.toLowerCase().includes(q)); }) : all;
        return html`
            <div class="msg-list__search"><input class="input" type="search" id="msgSearch" placeholder="Поиск по пациенту или тексту" value="${S.msg.q}" aria-label="Поиск по диалогам"></div>
            ${stateful(list.length ? html`<ul class="msg-list__items" role="list">${list.map(c => {
                const p = patient(c.patientId), m = lastMsg(c);
                return html`<li><a class="msg-item ${c.id === activeId ? 'is-active' : ''} ${c.unread ? 'is-unread' : ''}" href="#/messages/${c.id}" ${raw(c.id === activeId ? 'aria-current="true"' : '')}>
                    <span class="avatar" aria-hidden="true">${initials(p.name)}</span>
                    <span class="msg-item__main"><span class="msg-item__top"><span class="msg-item__name">${p.name}</span><span class="msg-item__time">${m ? msgTime(m.at) : ''}</span></span>
                        <span class="msg-item__bottom"><span class="msg-item__preview">${m ? (m.from === 'doctor' ? 'Вы: ' : '') + m.text : 'Сообщений пока нет'}</span>${c.unread ? html`<span class="msg-badge" aria-label="Непрочитанных: ${c.unread}">${c.unread}</span>` : ''}</span>
                        <span class="msg-item__ctx">${CONTEXT_LABEL[c.context]}${c.status === 'read_only' ? ' · только чтение' : ''}</span></span></a></li>`;
            })}</ul>` : html`<div class="empty"><div class="empty__title">${q ? 'Ничего не найдено' : 'Диалогов пока нет'}</div><div>${q ? 'Измените запрос.' : 'Диалог появляется, когда у пациента есть отношение с вами как с лечащим врачом.'}</div></div>`,
            { rows: 6, emptyTitle: 'Диалогов пока нет', emptyText: 'Диалог появляется, когда у пациента есть отношение с вами как с лечащим врачом.' })}`;
    }

    function messagesThread(c, back) {
        if (!c) return html`<div class="msg-thread__placeholder"><div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${raw(ICONS.messages)}</svg><div class="empty__title">Выберите диалог</div><div>Переписка откроется здесь.</div></div></div>`;
        const p = patient(c.patientId);
        let lastDay = '';
        const items = c.messages.map(m => {
            const day = m.at.split('T')[0];
            const sep = day !== lastDay ? html`<li class="msg-day"><span>${dayLabel(day)}</span></li>` : '';
            lastDay = day;
            if (m.from === 'system') return html`${sep}<li class="msg msg--system">${m.text}</li>`;
            return html`${sep}<li class="msg msg--${m.from === 'doctor' ? 'out' : 'in'}"><div class="msg__bubble">${m.text}</div>
                <div class="msg__meta"><span class="mono">${m.at.split('T')[1]}</span>${m.from === 'doctor' && m.status ? html` · <span class="${m.status === 'demo' ? 'msg__demo' : ''}">${STATUS_LABEL[m.status]}</span>` : ''}</div></li>`;
        });
        const backHref = back || '#/messages';
        return html`
            <div class="msg-thread__head">
                <a class="btn btn--sm btn--ghost msg-thread__back" href="${backHref}">← ${back ? 'Карточка пациента' : 'Диалоги'}</a>
                <div class="msg-thread__who"><a class="msg-thread__name" href="#/patient/${p.id}" title="Открыть карточку пациента">${p.name}</a><div class="msg-thread__sub">${CONTEXT_LABEL[c.context]} · ${D.STAGES[p.stage]} · <span class="mono">${p.icd}</span></div></div>
                <a class="btn btn--sm" href="#/patient/${p.id}">Карточка пациента</a>
            </div>
            <div class="msg-thread__notice">Пациент видит в приложении: «Ответ врача может занять время. При экстренной ситуации обратитесь в экстренные службы.»</div>
            <ol class="msg-thread__log" id="msgLog" aria-label="История переписки" aria-live="polite">${items.length ? items : html`<li class="msg-thread__empty"><div class="empty__title">Сообщений пока нет</div><div>Начните переписку — пациент увидит сообщение в Patient Portal (в демо доставка не подключена).</div></li>`}</ol>
            ${c.status === 'read_only'
                ? html`<div class="msg-thread__readonly">Этот диалог завершен. История доступна только для чтения.</div>`
                : html`<form class="msg-composer" id="msgForm" data-conv="${c.id}"><label class="sr-only" for="msgInput">Сообщение пациенту</label>
                    <textarea class="textarea" id="msgInput" rows="2" maxlength="2000" placeholder="Сообщение пациенту">${S.msg.drafts[c.id] || ''}</textarea>
                    <button class="btn btn--primary" type="submit" aria-label="Отправить сообщение">Отправить</button></form>`}`;
    }

    function viewMessages(id, params) {
        if (!canMessage()) return denied('messages');
        const c = id ? convs().find(x => x.id === id) : null;
        if (id && !c) return html`${pagehead('Сообщения', '')}<section class="panel"><div class="error"><div class="error__title">Диалог недоступен</div><div>Переписка видна только её участнику — лечащему врачу пациента.</div><a class="btn btn--sm" href="#/messages">К диалогам</a></div></section>`;
        if (c && c.unread && S.demo === 'ok') c.unread = 0;   // открыли — прочитано
        const back = params.get('back') ? `#/${params.get('back')}` : '';
        return html`<div class="msg-page ${c ? 'has-thread' : ''}">
            ${pagehead('Сообщения', 'Переписка с вашими пациентами из Patient Portal')}
            <div class="demo-note">Демонстрация: сообщения хранятся только в этой вкладке — они не доставляются пациенту, не сохраняются на сервере и не синхронизируются с мобильным приложением Patient Portal. Диалоги синтетические.</div>
            <section class="panel msg-layout ${c ? 'has-thread' : ''}">
                <div class="msg-list">${messagesList(c && c.id)}</div>
                <div class="msg-thread">${S.demo === 'ok' ? messagesThread(c, back) : S.demo === 'loading' ? stateful('', { rows: 6 }) : S.demo === 'empty' ? messagesThread(null) : ''}</div>
            </section></div>`;
    }

    function sendMessage(convId, text) {
        const c = convs().find(x => x.id === convId);
        const body = text.trim();
        if (!c || c.status !== 'active' || !body) return;
        const now = new Date();
        const msg = { id: `m-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`, from: 'doctor', at: `${D.today}T${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`, text: body, status: 'sending' };
        c.messages.push(msg);
        delete S.msg.drafts[convId];
        render();
        setTimeout(() => { msg.status = 'demo'; if (location.hash.startsWith(`#/messages/${convId}`)) { const keep = $('#msgInput') ? $('#msgInput').value : ''; render(); if ($('#msgInput')) { $('#msgInput').value = keep; } } }, 500);
    }

    document.addEventListener('submit', (e) => {
        if (e.target.id !== 'msgForm') return;
        e.preventDefault();
        sendMessage(e.target.dataset.conv, $('#msgInput').value);
        const i = $('#msgInput'); if (i) i.focus({ preventScroll: true });
    });
    document.addEventListener('keydown', (e) => {
        if (e.target.id === 'msgInput' && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.target.form.requestSubmit(); }
    });
    document.addEventListener('input', (e) => {
        if (e.target.id === 'msgInput') S.msg.drafts[e.target.form.dataset.conv] = e.target.value;   // черновик живёт при переходах
        if (e.target.id === 'msgSearch') {
            S.msg.q = e.target.value;
            const pos = e.target.selectionStart; render();
            const n = $('#msgSearch'); if (n) { n.focus(); n.setSelectionRange(pos, pos); }
        }
    });

    /* ---------- ИИ-помощник (демонстрационный сценарий) ----------
       В приложении: POST /api/ai/chat, только чтение, данные — через
       инструменты с проверкой области доступа на сервере. Здесь модели нет:
       ответы заготовлены и считаются по синтетическим данным в той же
       области доступа роли. Сетевых запросов прототип не делает. */
    S.ai = { open: false, messages: [], ctx: null, loading: false, lastFocus: null };

    // Подсказки — дословно как в приложении (roleAISuggestions).
    const AI_SUGGESTIONS = {
        doctor: ['Какие мои пациенты требуют внимания?', 'Что у меня на сегодня?', 'Есть ли у моих пациентов просрочки?'],
        head: ['Кому в отделении требуется решение заведующего?', 'Где больше всего просрочек?', 'Как распределена нагрузка по врачам?'],
        other: ['Сколько пациентов требуют внимания?', 'Какая самая частая причина внимания?', 'Что у меня на сегодня?'],
    };
    const aiAvailable = () => S.demo !== 'error';
    const aiRef = (p, sub) => ({ id: p.id, title: p.name, sub: sub || `${D.STAGES[p.stage]} · ${p.doctor || 'врач не назначен'}` });

    function aiAnswer(text) {
        const q = text.toLowerCase();
        const list = scoped();
        const needs = list.filter(p => p.overdue || p.attention.length);
        const ctx = S.ai.ctx && patient(S.ai.ctx);
        if (/(измени|переведи|перевед|отметь|удали|создай|запиши|назначь|закрой|отправь|исправь)/.test(q)) {
            return { text: 'Режим помощника — только просмотр: он не создаёт, не изменяет и не удаляет данные, не отмечает уведомления и не отправляет сообщения. Это действие выполняется в соответствующем разделе системы.' };
        }
        if (/(диагноз постав|какое лечение|назначить лечение|тяжест|прогноз|дозиров)/.test(q)) {
            return { text: 'Помощник не ставит диагнозы, не назначает лечение и не оценивает медицинскую тяжесть — это решение врача и консилиума. Могу показать сведения о маршруте и событиях в пределах вашей области доступа.' };
        }
        if (/(телефон|полис|снилс|паспорт)/.test(q)) {
            return { text: 'Телефоны, полисы ОМС и другие персональные идентификаторы помощник не запрашивает и не раскрывает. Их можно посмотреть в карточке пациента, если она вам доступна.' };
        }
        if (ctx && !/(мои|отделени|нагрузк|сколько пациентов|причин)/.test(q)) {
            const next = ctx.events.find(e => !e.done && e.date >= D.today);
            const done = ctx.checklist.filter(c => c.done).length;
            const lines = [
                `${ctx.name}: этап «${D.STAGES[ctx.stage]}», лечащий врач — ${ctx.doctor}`,
                ctx.overdue ? `Просрочено событий: ${ctx.overdue}.` : 'Просроченных событий нет.',
                ctx.attention.length ? `Отметка внимания: «${D.ATTENTION_REASONS[ctx.attention[0].code]}» (${relDay(ctx.attention[0].since)}).` : 'Открытых отметок внимания нет.',
                next ? `Ближайшее событие: ${next.title}, ${fmtNum(next.date)} в ${next.time}.` : 'Запланированных событий нет.',
                `Чек-лист этапа: выполнено ${done} из ${ctx.checklist.length}.`,
            ];
            return { text: lines.join('\n'), refs: [aiRef(ctx)] };
        }
        if (/нагрузк|распредел/.test(q)) {
            if (R().scopeStrategy === 'by_treating_doctor') return { text: 'Сводка по отделению недоступна вашей роли. Могу показать ваших пациентов, требующих внимания, и события на сегодня.' };
            const rows = D.DOCTORS.map(d => ({ d, n: list.filter(p => p.doctorId === d.id).length })).filter(x => x.n).sort((a, b) => b.n - a.n);
            return { text: `Пациенты по лечащим врачам в вашей области доступа:\n${rows.map(x => `• ${x.d.short} — ${x.n}`).join('\n')}` };
        }
        if (/решени. заведующ/.test(q)) {
            const ps = list.filter(p => p.attention.some(a => a.code === 'department_head_decision_required'));
            return ps.length ? { text: `Решение заведующего требуется для ${ps.length} ${plural(ps.length, 'пациента', 'пациентов', 'пациентов')}:`, refs: ps.slice(0, 5).map(p => aiRef(p)) }
                : { text: 'Сейчас нет пациентов с отметкой «Требуется решение заведующего».' };
        }
        if (/просроч/.test(q)) {
            const ps = list.filter(p => p.overdue).sort((a, b) => b.overdue - a.overdue);
            if (!ps.length) return { text: 'Просроченных событий в вашей области доступа нет.' };
            const total = ps.reduce((n, p) => n + p.overdue, 0);
            return { text: `Просрочено ${total} ${plural(total, 'событие', 'события', 'событий')} у ${ps.length} ${plural(ps.length, 'пациента', 'пациентов', 'пациентов')}. Больше всего:`, refs: ps.slice(0, 5).map(p => aiRef(p, `Просрочено: ${p.overdue} · ${p.doctor}`)) };
        }
        if (/сегодня/.test(q)) {
            const evs = list.flatMap(p => p.events.filter(e => e.date === D.today).map(e => ({ e, p }))).sort((a, b) => a.e.time.localeCompare(b.e.time));
            if (!evs.length) return { text: 'На сегодня запланированных событий в вашей области доступа нет.' };
            return { text: `На сегодня ${evs.length} ${plural(evs.length, 'событие', 'события', 'событий')}:\n${evs.map(x => `• ${x.e.time} — ${x.e.title}`).join('\n')}`, refs: evs.slice(0, 5).map(x => aiRef(x.p, `${x.e.time} · ${x.e.title}`)) };
        }
        if (/причин/.test(q)) {
            const counts = Object.entries(D.ATTENTION_REASONS).map(([k, v]) => ({ v, n: list.filter(p => p.attention.some(a => a.code === k)).length })).filter(x => x.n).sort((a, b) => b.n - a.n);
            return counts.length ? { text: `Самая частая причина внимания — «${counts[0].v}» (${counts[0].n}).\n${counts.slice(1).map(x => `• ${x.v} — ${x.n}`).join('\n')}`.trim() } : { text: 'Открытых отметок внимания в вашей области доступа нет.' };
        }
        if (/внимани/.test(q)) {
            if (!needs.length) return { text: 'Пациентов, требующих внимания, в вашей области доступа нет.' };
            return { text: `Требуют внимания ${needs.length} ${plural(needs.length, 'пациент', 'пациента', 'пациентов')}${needs.length > 5 ? ', первые пять:' : ':'}`, refs: needs.slice(0, 5).map(p => aiRef(p, p.overdue ? `Просрочено: ${p.overdue} · ${p.doctor}` : `${D.ATTENTION_REASONS[p.attention[0].code]} · ${p.doctor}`)) };
        }
        return { text: 'В демоверсии ответы заготовлены только для вопросов о внимании, просрочках, событиях на сегодня, нагрузке врачей, причинах внимания и о выбранном пациенте. В приложении на любой вопрос по доступным данным отвечает модель.' };
    }

    function renderAI() {
        const body = $('#aiBody');
        if (!body) return;
        const avail = aiAvailable();
        $('#aiSub').textContent = avail ? 'Ответы по доступным данным системы' : 'Основная система продолжает работать';
        const ctx = S.ai.ctx && patient(S.ai.ctx);
        const sugg = AI_SUGGESTIONS[S.role === 'doctor' ? 'doctor' : S.role === 'head' ? 'head' : 'other'];
        const msgs = S.ai.messages.map(m => html`
            <article class="ai-msg ai-msg--${m.role}">
                <div class="ai-msg__role">${m.role === 'user' ? 'Вы' : 'Помощник'}</div>
                <div class="ai-msg__text">${m.text}</div>
                ${m.role === 'assistant' ? html`<span class="ai-demo-tag">Демо-ответ прототипа · не модель</span>` : ''}
                ${m.refs && m.refs.length ? html`<div class="ai-refs">${m.refs.map(r => html`<div class="ai-ref"><div class="ai-ref__body"><div class="ai-ref__title">${r.title}</div><div class="ai-ref__sub">${r.sub}</div></div><button class="btn btn--sm btn--ghost" data-ai-open="${r.id}" aria-label="Открыть пациента: ${r.title}">Открыть</button></div>`)}</div>` : ''}
            </article>`);
        body.innerHTML = String(html`
            <div class="ai-scroll" id="aiScroll">
            <div class="ai-note"><b>Только просмотр.</b> Помощник не изменяет данные и не заменяет медицинское решение. Видит только пациентов в вашей области доступа. История хранится в этой вкладке.
                <br><b>Прототип:</b> модель не подключена — ответы заготовлены по синтетическим данным.</div>
            ${ctx ? html`<div class="ai-context"><span>Пациент: ${ctx.name}</span><button class="btn btn--sm btn--ghost" data-ai-clearctx aria-label="Убрать контекст пациента">Убрать</button></div>` : ''}
            ${avail ? html`
                <div class="ai-suggest">${sugg.map(s => html`<button class="ai-chip" data-ai-suggest="${s}">${s}</button>`)}</div>
                <div class="ai-log" id="aiLog" role="log" aria-live="polite">
                    ${msgs.length ? msgs : html`<div class="ai-empty"><b>Спросите о доступных пациентах, внимании или просрочках</b><span>${ctx ? 'Вопросы о пациенте из контекста — например, «Какой этап и что дальше?».' : 'Подсказки выше подставляются в поле ввода.'}</span></div>`}
                    ${S.ai.loading ? html`<div class="ai-thinking">Помощник готовит ответ…</div>` : ''}
                </div>
                </div>
                <form class="ai-composer" id="aiForm">
                    <label class="sr-only" for="aiInput">Сообщение ИИ-помощнику</label>
                    <textarea class="textarea" id="aiInput" rows="2" maxlength="4000" placeholder="Задайте вопрос по доступным данным" ${raw(S.ai.loading ? 'disabled' : '')}></textarea>
                    <div class="ai-composer__row"><span class="ai-composer__hint">Enter — отправить, Shift+Enter — новая строка</span>
                        <button type="button" class="btn btn--sm btn--ghost" data-ai-clear ${raw(S.ai.messages.length && !S.ai.loading ? '' : 'disabled')}>Очистить</button>
                        <button type="submit" class="btn btn--sm btn--primary" ${raw(S.ai.loading ? 'disabled' : '')}>Отправить</button></div>
                </form>`
            : html`<div class="ai-unavailable" role="status"><b>ИИ-помощник временно недоступен</b><span>Основная система продолжает работать. Локальный AI-сервис недоступен.</span><button class="btn btn--sm" data-ai-retry>Проверить снова</button></div></div>`}`);
        const sc = $('#aiScroll'); if (sc && S.ai.messages.length) sc.scrollTop = sc.scrollHeight;
        $('#aiBtn').classList.toggle('is-muted', !avail);
    }

    function openAI(patientId) {
        if (S.notif && S.notif.open) closeNotif(false);   // панели взаимоисключающие, подложка общая
        if (patientId) S.ai.ctx = patientId;
        S.ai.lastFocus = document.activeElement;
        S.ai.open = true;
        const panel = $('#aiPanel'), scrim = $('#aiScrim');
        renderAI();
        panel.hidden = false; scrim.hidden = false;
        void panel.offsetWidth;
        panel.classList.add('is-open'); scrim.classList.add('is-open');
        panel.setAttribute('aria-hidden', 'false');
        $('#aiBtn').setAttribute('aria-expanded', 'true');
        ($('#aiInput') || $('#aiClose')).focus({ preventScroll: true });
    }
    function closeAI() {
        if (!S.ai.open) return;
        S.ai.open = false;
        const panel = $('#aiPanel'), scrim = $('#aiScrim');
        panel.classList.remove('is-open'); if (!(S.notif && S.notif.open)) scrim.classList.remove('is-open');
        panel.setAttribute('aria-hidden', 'true');
        $('#aiBtn').setAttribute('aria-expanded', 'false');
        setTimeout(() => { if (!S.ai.open) { panel.hidden = true; if (!(S.notif && S.notif.open)) scrim.hidden = true; } }, 260);
        const lf = S.ai.lastFocus;
        const back = lf && lf !== document.body && document.contains(lf) ? lf : $('#aiBtn');
        back.focus({ preventScroll: true });
    }
    function sendAI(text) {
        const msg = text.trim();
        if (!msg || S.ai.loading || !aiAvailable()) return;
        S.ai.messages.push({ role: 'user', text: msg });
        S.ai.loading = true; renderAI();
        setTimeout(() => {
            const a = aiAnswer(msg);
            S.ai.messages.push({ role: 'assistant', text: a.text, refs: a.refs || [] });
            S.ai.loading = false; renderAI();
            const input = $('#aiInput'); if (input && S.ai.open) input.focus({ preventScroll: true });
        }, 650);
    }

    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t.closest('#aiBtn')) { S.ai.open ? closeAI() : openAI(); return; }
        if (t.closest('#aiClose') || t.id === 'aiScrim') { closeAI(); return; }
        const sug = t.closest('[data-ai-suggest]');
        if (sug) { const i = $('#aiInput'); i.value = sug.dataset.aiSuggest; i.focus(); return; }
        if (t.closest('[data-ai-clearctx]')) { S.ai.ctx = null; renderAI(); return; }
        if (t.closest('[data-ai-clear]')) { S.ai.messages = []; renderAI(); $('#aiInput').focus(); return; }
        if (t.closest('[data-ai-retry]')) { toast('В прототипе доступность задаёт переключатель «Состояние».'); return; }
        const open = t.closest('[data-ai-open]');
        // Панель закрывается, чтобы карточка была видна; история и контекст сохраняются.
        if (open) { closeAI(); location.hash = `#/patient/${open.dataset.aiOpen}`; return; }
        const ask = t.closest('[data-ai-ask]');
        if (ask) { openAI(ask.dataset.aiAsk); }
    });
    document.addEventListener('submit', (e) => { if (e.target.id === 'aiForm') { e.preventDefault(); sendAI($('#aiInput').value); } });
    document.addEventListener('keydown', (e) => {
        if (e.target.id === 'aiInput' && e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAI(e.target.value); }
    });

    /* ===================== Общие компоненты (этап 0) =====================
       Без встроенных обработчиков и атрибутов style — только классы и
       делегирование событий, чтобы перенос был совместим с CSP приложения. */

    // --- Вкладки: role="tablist", стрелки/Home/End, автоматическая активация ---
    const TAB_HANDLERS = {};
    const tabsView = (id, items, active, label) => html`
        <div class="tabs" role="tablist" aria-label="${label}" data-tabs="${id}">${items.map(t => html`
            <button type="button" role="tab" class="tabs__tab" id="tab-${id}-${t.value}" data-tab="${t.value}"
                aria-selected="${t.value === active}" tabindex="${t.value === active ? 0 : -1}">${t.label}${t.count ? html`<span class="tabs__count">${t.count}</span>` : ''}</button>`)}
        </div>`;
    function selectTab(group, value) {
        if (!TAB_HANDLERS[group]) return;
        TAB_HANDLERS[group](value);
        const next = document.getElementById(`tab-${group}-${value}`);
        if (next) next.focus({ preventScroll: true });
    }
    document.addEventListener('click', (e) => {
        const t = e.target.closest('[data-tab]');
        if (t) selectTab(t.closest('[data-tabs]').dataset.tabs, t.dataset.tab);
    });
    document.addEventListener('keydown', (e) => {
        const t = e.target.closest && e.target.closest('[role="tab"][data-tab]');
        if (!t || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        const list = [...t.closest('[role="tablist"]').querySelectorAll('[role="tab"]')];
        const i = list.indexOf(t);
        const j = e.key === 'Home' ? 0 : e.key === 'End' ? list.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + list.length) % list.length;
        e.preventDefault();
        selectTab(t.closest('[data-tabs]').dataset.tabs, list[j].dataset.tab);
    });

    // --- Подтверждение опасного действия: Promise<boolean>, фокус на «Отмена» ---
    S.confirmResolve = null;
    function confirmDialog({ title, text, confirmLabel = 'Подтвердить', danger = false, note = '' }) {
        return new Promise((resolve) => {
            S.confirmResolve = resolve;
            openModal(modalShell(title, '', html`<p class="confirm__text">${text}</p>${note ? html`<p class="demo-hint">${note}</p>` : ''}`,
                html`<span class="spacer"></span><button class="btn" data-confirm="no">Отмена</button><button class="btn ${danger ? 'btn--danger-solid' : 'btn--primary'}" data-confirm="yes">${confirmLabel}</button>`));
            $('#modal').classList.add('modal--confirm');
        });
    }
    function settleConfirm(value) {
        const r = S.confirmResolve; S.confirmResolve = null;
        if (r) r(value);
    }
    document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-confirm]');
        if (!b) return;
        settleConfirm(b.dataset.confirm === 'yes');
        closeModal();
    });

    // --- Лист фильтров (снизу на телефоне, справа-снизу на широком экране) ---
    S.sheet = null;
    function openSheet({ title, body, onApply, onReset }) {
        const sheet = $('#sheet'), scrim = $('#sheetScrim');
        S.sheet = { onApply, onReset, lastFocus: document.activeElement };
        sheet.innerHTML = String(html`
            <div class="sheet__head"><h2 class="sheet__title" id="sheetTitle">${title}</h2>
                <button class="iconbtn" data-sheet="close" aria-label="Закрыть"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
            <div class="sheet__body">${body}</div>
            <div class="sheet__foot"><button class="btn" data-sheet="reset">Сбросить</button><button class="btn btn--primary" data-sheet="apply">Показать</button></div>`);
        sheet.hidden = false; scrim.hidden = false;
        void sheet.offsetWidth;
        sheet.classList.add('is-open'); scrim.classList.add('is-open');
        const first = sheet.querySelector('select, input, button:not([data-sheet="close"])');
        (first || sheet).focus({ preventScroll: true });
    }
    function closeSheet() {
        const sheet = $('#sheet'), scrim = $('#sheetScrim');
        if (sheet.hidden) return;
        sheet.classList.remove('is-open'); scrim.classList.remove('is-open');
        const back = S.sheet && S.sheet.lastFocus;
        S.sheet = null;
        setTimeout(() => { if (!sheet.classList.contains('is-open')) { sheet.hidden = true; scrim.hidden = true; sheet.innerHTML = ''; } }, 240);
        if (back && document.contains(back)) back.focus({ preventScroll: true });
    }
    document.addEventListener('click', (e) => {
        if (e.target.id === 'sheetScrim') { closeSheet(); return; }
        const b = e.target.closest('[data-sheet]');
        if (!b || !S.sheet) return;
        const act = b.dataset.sheet;
        if (act === 'apply' && S.sheet.onApply) S.sheet.onApply($('#sheet'));
        if (act === 'reset' && S.sheet.onReset) { S.sheet.onReset($('#sheet')); return; }
        closeSheet();
    });

    // --- Дата и время: нативная дата + время с шагом 15 минут + проверки ---
    const TIMES = []; for (let h = 7; h <= 20; h++) for (const m of ['00', '15', '30', '45']) TIMES.push(`${String(h).padStart(2, '0')}:${m}`);
    const dateTimeField = ({ id, label, required, min, max, value = {}, hint }) => html`
        <fieldset class="dt" id="${id}" data-min="${min || ''}" data-max="${max || ''}" data-required="${required ? '1' : ''}">
            <legend class="field__label">${label}${required ? html` <span class="req">*</span>` : ''}</legend>
            <div class="dt__row">
                <input class="input dt__date" type="date" id="${id}-date" value="${value.date || ''}" min="${min || ''}" max="${max || ''}" aria-label="${label}: дата" aria-describedby="${id}-err">
                <select class="select dt__time" id="${id}-time" aria-label="${label}: время" aria-describedby="${id}-err"><option value="">Время</option>${TIMES.map(t => html`<option ${raw(t === value.time ? 'selected' : '')}>${t}</option>`)}</select>
            </div>
            <div class="dt__quick"><button type="button" class="ai-chip" data-dt-set="${id}" data-days="0">Сегодня</button><button type="button" class="ai-chip" data-dt-set="${id}" data-days="1">Завтра</button></div>
            ${hint ? html`<span class="field__hint">${hint}</span>` : ''}
            <span class="field__error" id="${id}-err" hidden></span>
        </fieldset>`;
    // aria-invalid="" читается как false — ставим явное "true".
    const setInvalid = (el, bad) => { if (bad) el.setAttribute('aria-invalid', 'true'); else el.removeAttribute('aria-invalid'); };
    function readDateTime(id) {
        const box = document.getElementById(id);
        const d = $(`#${id}-date`), t = $(`#${id}-time`), err = $(`#${id}-err`);
        const required = box.dataset.required === '1';
        let error = '';
        if (required && !d.value) error = 'Укажите дату.';
        else if (d.value && box.dataset.min && d.value < box.dataset.min) error = `Дата не может быть раньше ${fmtNum(box.dataset.min)}.`;
        else if (d.value && box.dataset.max && d.value > box.dataset.max) error = `Дата не может быть позже ${fmtNum(box.dataset.max)}.`;
        else if (required && !t.value) error = 'Укажите время.';
        else if (!d.value && t.value) error = 'Укажите дату к выбранному времени.';
        err.textContent = error; err.hidden = !error;
        setInvalid(d, !!error && (!d.value || error.startsWith('Дата')));
        setInvalid(t, !!error && error.startsWith('Укажите время'));
        return { ok: !error, date: d.value, time: t.value, error };
    }
    document.addEventListener('click', (e) => {
        const b = e.target.closest('[data-dt-set]');
        if (!b) return;
        const id = b.dataset.dtSet;
        $(`#${id}-date`).value = D.helpers.iso(D.helpers.addDays(parse(D.today), Number(b.dataset.days)));
        if (!$(`#${id}-time`).value) $(`#${id}-time`).value = '09:00';
        readDateTime(id);
    });

    // --- Поиск по МКБ-10: combobox + listbox, клавиатура, «С» кириллицей ---
    const ICD = Object.entries(window.ICD10 || {}).flatMap(([loc, v]) => v.diagnoses.map(d => ({ ...d, loc, locLabel: v.label })));
    const normIcd = (s) => s.trim().toLowerCase().replace(/^с/, 'c').replace(/,/g, '.');
    function icdSearch(query, loc) {
        const q = normIcd(query);
        const pool = loc ? ICD.filter(d => d.loc === loc) : ICD;
        if (!q) return loc ? pool.slice(0, 40) : [];
        const words = q.split(/\s+/).filter(Boolean);
        return pool.filter(d => d.code.toLowerCase().startsWith(q) || words.every(w => d.label.toLowerCase().includes(w))).slice(0, 40);
    }
    const icdField = ({ id, label, required, locSelectId }) => html`
        <div class="field combo" data-icd="${id}" data-loc-from="${locSelectId || ''}">
            <label class="field__label" for="${id}">${label}${required ? html` <span class="req">*</span>` : ''}</label>
            <input class="input" id="${id}" role="combobox" aria-expanded="false" aria-controls="${id}-list" aria-autocomplete="list" autocomplete="off" placeholder="Код или слова: C34.1, бронх" aria-describedby="${id}-hint">
            <ul class="combo__list" id="${id}-list" role="listbox" aria-label="Диагнозы МКБ-10" hidden></ul>
            <span class="field__hint" id="${id}-hint">Начните вводить код или часть названия.</span>
        </div>`;
    const ICD_STATE = {};
    const ICD_ON_CHOOSE = {};   // id поля → обработчик выбора (вкладка «Данные» карточки)
    function renderIcdList(id) {
        const input = document.getElementById(id), list = document.getElementById(`${id}-list`), box = input.closest('[data-icd]');
        const locSel = box.dataset.locFrom ? document.getElementById(box.dataset.locFrom) : null;
        const st = ICD_STATE[id] || (ICD_STATE[id] = { active: -1, items: [], selected: null });
        st.items = icdSearch(input.value, locSel ? locSel.value : '');
        st.active = st.items.length ? 0 : -1;
        list.innerHTML = String(html`${st.items.map((d, i) => html`<li role="option" id="${id}-opt-${i}" class="combo__opt" data-icd-opt="${i}" aria-selected="${i === st.active}"><span class="mono">${d.code}</span><span class="combo__label">${d.label}</span>${locSel && locSel.value ? '' : html`<span class="combo__loc">${d.locLabel}</span>`}</li>`)}${input.value.trim() && !st.items.length ? html`<li class="combo__empty" role="presentation">Ничего не найдено${locSel && locSel.value ? ' в выбранной локализации' : ''}</li>` : ''}`);
        const open = !!(st.items.length || input.value.trim());
        list.hidden = !open; input.setAttribute('aria-expanded', String(open));
        input.setAttribute('aria-activedescendant', st.active >= 0 ? `${id}-opt-${st.active}` : '');
    }
    function chooseIcd(id, i) {
        const st = ICD_STATE[id], d = st && st.items[i];
        if (!d) return;
        st.selected = d;
        const input = document.getElementById(id);
        input.value = `${d.code} — ${d.label}`;
        document.getElementById(`${id}-list`).hidden = true;
        input.setAttribute('aria-expanded', 'false');
        const hint = document.getElementById(`${id}-hint`);
        hint.textContent = `Выбрано: ${d.code} · ${d.locLabel}`;
        input.removeAttribute('aria-invalid');
        if (ICD_ON_CHOOSE[id]) ICD_ON_CHOOSE[id](d);
    }
    document.addEventListener('input', (e) => {
        const box = e.target.closest && e.target.closest('[data-icd]');
        if (box && e.target.matches('[role="combobox"]')) {
            const st = ICD_STATE[e.target.id]; if (st) st.selected = null;
            renderIcdList(e.target.id);
        }
    });
    document.addEventListener('change', (e) => {
        const box = document.querySelector(`[data-icd][data-loc-from="${e.target.id}"]`);
        if (box && e.target.id) { const input = box.querySelector('[role="combobox"]'); input.value = ''; if (ICD_STATE[input.id]) ICD_STATE[input.id].selected = null; renderIcdList(input.id); $(`#${input.id}-hint`).textContent = 'Начните вводить код или часть названия.'; }
    });
    document.addEventListener('keydown', (e) => {
        if (!(e.target.matches && e.target.matches('[data-icd] [role="combobox"]'))) return;
        const id = e.target.id, st = ICD_STATE[id], list = $(`#${id}-list`);
        if (e.key === 'ArrowDown' && list.hidden) { renderIcdList(id); e.preventDefault(); return; }
        if (!st || list.hidden) return;
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            st.active = (st.active + (e.key === 'ArrowDown' ? 1 : -1) + st.items.length) % st.items.length;
            list.querySelectorAll('[role="option"]').forEach((o, i) => o.setAttribute('aria-selected', String(i === st.active)));
            e.target.setAttribute('aria-activedescendant', `${id}-opt-${st.active}`);
            const o = $(`#${id}-opt-${st.active}`); if (o) o.scrollIntoView({ block: 'nearest' });
        } else if (e.key === 'Enter') { e.preventDefault(); chooseIcd(id, st.active); }
        else if (e.key === 'Escape') { e.stopPropagation(); list.hidden = true; e.target.setAttribute('aria-expanded', 'false'); }
    }, true);
    document.addEventListener('mousedown', (e) => {   // mousedown: до blur поля ввода
        const o = e.target.closest('[data-icd-opt]');
        if (o) { e.preventDefault(); chooseIcd(o.closest('[data-icd]').querySelector('[role="combobox"]').id, Number(o.dataset.icdOpt)); }
    });
    document.addEventListener('focusout', (e) => {
        if (!(e.target.matches && e.target.matches('[data-icd] [role="combobox"]'))) return;
        setTimeout(() => { const l = $(`#${e.target.id}-list`); if (l && !e.target.matches(':focus')) { l.hidden = true; e.target.setAttribute('aria-expanded', 'false'); } }, 0);
    });
    const icdValue = (id) => (ICD_STATE[id] && ICD_STATE[id].selected) || null;

    /* ===================== Вход и смена пароля (демонстрация) =====================
       Ничего не проверяется и не хранится: исход задаёт переключатель «Ответ
       сервера», тексты ошибок — как в приложении (static/app.js:8284–8322). */
    const ROLE_LABELS = { developer: 'Разработчик', deputy: 'Зам. главного врача по онкологии', caop: 'Заведующий ЦАОП', head: 'Заведующий отделением', doctor: 'Врач', chair: 'Председатель консилиума' };
    S.auth = 'in';
    function showAuth(mode) {
        S.auth = mode;
        closeAI(); closeNotif(); closeSheet(); closeModal();
        const auth = $('#auth');
        auth.hidden = mode === 'in';
        $('#app').inert = mode !== 'in';
        $('#loginForm').hidden = mode !== 'login';
        $('#changeForm').hidden = mode !== 'change';
        ['loginError', 'changeError'].forEach(i => { $(`#${i}`).hidden = true; });
        if (mode === 'login') { $('#loginRole').innerHTML = String(html`${Object.keys(ROLES).map(k => html`<option value="${k}" ${raw(k === S.role ? 'selected' : '')}>${ROLE_LABELS[k]}</option>`)}`); $('#loginUser').focus(); }
        if (mode === 'change') $('#changeCurrent').focus();
    }
    const authError = (id, text) => { const el = $(`#${id}`); el.textContent = text; el.hidden = false; };
    $('#loginForm').addEventListener('submit', (e) => {
        e.preventDefault();
        const user = $('#loginUser'), pass = $('#loginPass');
        const empty = !user.value.trim() || !pass.value;
        pass.value = '';                                   // пароль не держим ни секунды дольше
        if (empty) { authError('loginError', 'Введите логин и пароль.'); (user.value.trim() ? pass : user).focus(); return; }
        const scenario = $('#loginScenario').value;
        if (scenario === '401') { authError('loginError', 'Неверный логин или пароль.'); pass.focus(); return; }
        if (scenario === '429') { authError('loginError', 'Слишком много попыток входа. Подождите немного и попробуйте снова.'); return; }
        S.pendingRole = $('#loginRole').value;
        user.value = '';
        if (scenario === 'temp') { showAuth('change'); return; }
        enterAs(S.pendingRole);
    });
    $('#changeForm').addEventListener('submit', (e) => {
        e.preventDefault();
        const cur = $('#changeCurrent'), n1 = $('#changeNew'), n2 = $('#changeNew2');
        const v = { cur: cur.value, n1: n1.value, n2: n2.value };
        [cur, n1, n2].forEach(i => { i.value = ''; });
        if (!v.cur || !v.n1 || !v.n2) { authError('changeError', 'Заполните все поля.'); cur.focus(); return; }
        if (v.n1 !== v.n2) { authError('changeError', 'Новые пароли не совпадают.'); n1.focus(); return; }
        if (v.n1.length < 8) { authError('changeError', 'Пароль должен быть не короче 8 символов.'); n1.focus(); return; }
        if ($('#changeScenario').value === '401') { authError('changeError', 'Временный пароль указан неверно.'); cur.focus(); return; }
        enterAs(S.pendingRole || S.role);
    });
    function enterAs(role) {
        S.role = role;
        try { localStorage.setItem('proto-role', role); } catch (err) { /* ok */ }
        $('#roleSelect').value = role;
        showAuth('in');
        location.hash = R().home; render(); renderAI();
        toast(`Вход выполнен (демо): ${ROLE_LABELS[role]}.`);
    }
    $('#logoutBtn').addEventListener('click', () => {
        // Как в приложении при выходе: история ИИ-помощника и контекст сбрасываются.
        S.ai.messages = []; S.ai.ctx = null; S.ai.loading = false;
        showAuth('login');
    });

    /* ===================== Уведомления (демонстрация) =====================
       Те же типы, заголовки и окно «Скоро» (1–3 дня), что в
       services/internal_notifications.py, но сформированы прототипом по
       синтетическим данным в области доступа роли. Это внутренние напоминания
       сотруднику, а не доставленные сообщения; сервер не опрашивается. */
    S.notif = { open: false, tab: 'all', read: {} };
    const NOTIF_LABEL = { overdue: 'Просрочено', today: 'Сегодня', upcoming: 'Скоро', checklist: 'Чек-листы', data_quality: 'Качество данных', attention: 'Требует внимания', consilium_revision: 'Доработка консилиума' };
    const NOTIF_TONE = { overdue: 'tag--danger', consilium_revision: 'tag--warn', attention: 'tag--warn', today: 'tag--accent', upcoming: '', checklist: '', data_quality: '' };
    function notifItems() {
        const out = [];
        for (const p of scoped()) {
            for (const ev of p.events.filter(x => !x.done)) {
                const delta = Math.round((parse(ev.date) - parse(D.today)) / 86400000);
                if (delta < 0) out.push({ key: `overdue:${ev.id}`, type: 'overdue', title: `Просрочено на ${-delta} дн.`, text: 'Контрольное действие не выполнено.', p, at: ev.date });
                else if (delta === 0) out.push({ key: `today:${ev.id}`, type: 'today', title: 'На сегодня', text: 'Сегодня запланировано контрольное действие.', p, at: ev.date });
                else if (delta <= 3) out.push({ key: `upcoming:${ev.id}`, type: 'upcoming', title: `Скоро срок: ${delta} дн.`, text: 'Запланированное действие скоро наступит.', p, at: ev.date });
            }
            if (p.stage === 'exam' && p.checklist.some(c => !c.done)) out.push({ key: `checklist:${p.id}`, type: 'checklist', title: 'Незавершённый чек-лист', text: 'Обязательные пункты чек-листа не отмечены выполненными.', p, at: D.today });
            p.attention.forEach((a, i) => out.push({ key: `attention:${p.id}:${i}`, type: 'attention', title: 'Требует внимания', text: `Причина: ${D.ATTENTION_REASONS[a.code]}`, p, at: a.since }));
        }
        // Доработка консилиума — только автору записи (как в приложении).
        S.consilium.filter(c => c.createdBy === R().user.id && c.status === 'needs_revision').forEach(c => {
            out.push({ key: `consilium_revision:${c.id}:${c.revisions.length}`, type: 'consilium_revision', title: 'Консилиум возвращён на доработку', text: 'Председатель оставил комментарий. Откройте запись, внесите изменения и отправьте её повторно.', p: patient(c.patientId), at: (c.revisions[c.revisions.length - 1] || {}).at || D.today, consiliumId: c.id });
        });
        const read = S.notif.read[S.role] || (S.notif.read[S.role] = new Set());
        out.forEach(n => { n.unread = !read.has(n.key); });
        // Как в приложении (services/internal_notifications.py:348): непрочитанные, дата события, ФИО.
        return out.sort((a, b) => (a.unread === b.unread ? 0 : a.unread ? -1 : 1) || a.at.localeCompare(b.at) || (a.p ? a.p.name : '').localeCompare(b.p ? b.p.name : ''));
    }
    const notifUnread = () => notifItems().filter(n => n.unread).length;
    function renderNotif() {
        const body = $('#notifBody');
        const all = notifItems(), unread = all.filter(n => n.unread).length;
        $('#notifSub').textContent = unread ? `Новых: ${unread}` : 'Новых уведомлений нет';
        const tabs = [{ value: 'all', label: 'Все' }, { value: 'unread', label: 'Новые', count: unread }, { value: 'overdue', label: 'Просрочено' }, { value: 'today', label: 'Сегодня' }];
        const list = all.filter(n => S.notif.tab === 'all' || (S.notif.tab === 'unread' ? n.unread : n.type === S.notif.tab));
        const items = list.slice(0, 5);
        body.innerHTML = String(html`
            <div class="ai-scroll">
                <div class="ai-note">Демонстрация: уведомления сформированы прототипом по синтетическим данным в вашей области доступа. Сервер не опрашивается; это напоминания сотруднику, а не сообщения пациентам.</div>
                <div class="notif-tabs">${tabsView('notif', tabs, S.notif.tab, 'Фильтр уведомлений')}</div>
                ${S.demo === 'loading' ? html`<div class="skeleton" aria-busy="true">${[1, 2, 3, 4].map(() => html`<span></span>`)}</div>`
                : S.demo === 'error' ? html`<div class="error" role="alert"><div class="error__title">Не удалось загрузить уведомления</div><div>Попробуйте ещё раз.</div></div>`
                : S.demo === 'empty' || !items.length ? html`<div class="empty"><div class="empty__title">${S.demo === 'empty' || !all.length ? 'Новых уведомлений нет' : 'По выбранному фильтру уведомлений нет.'}</div></div>`
                : html`<ul class="notif-list">${items.map(n => html`
                    <li class="notif ${n.unread ? 'is-unread' : ''}">
                        <div class="notif__head"><span class="tag ${NOTIF_TONE[n.type]}">${NOTIF_LABEL[n.type]}</span>${n.unread ? html`<span class="notif__new">Новое</span>` : ''}</div>
                        <div class="notif__title">${n.title}</div>
                        <div class="notif__text">${n.text}</div>
                        <div class="notif__meta">${n.p ? n.p.name : 'Пациент'} · ${fmtNum(n.at)}</div>
                        <div class="notif__actions"><button class="btn btn--sm" data-notif-open="${n.key}">Открыть</button>${n.unread ? html`<button class="btn btn--sm btn--ghost" data-notif-read="${n.key}">Прочитано</button>` : ''}</div>
                    </li>`)}</ul>${list.length > items.length ? html`<p class="notif-more">Показано ${items.length} из ${list.length}.</p>` : ''}`}
            </div>
            <div class="ai-composer notif-foot">
                <button class="btn btn--sm btn--ghost" data-notif-readall ${raw(unread && S.demo === 'ok' ? '' : 'disabled')}>Отметить все прочитанными</button>
                ${R().nav.includes('notifications') ? html`<a class="btn btn--sm" href="#/notifications" data-notif-full>Все уведомления</a>` : ''}
            </div>`);
        updateBell();
    }
    function updateBell() {
        const n = S.auth === 'in' && S.demo === 'ok' ? notifUnread() : 0;
        const b = $('#notifBadge');
        b.hidden = !n; b.textContent = n > 99 ? '99+' : String(n);
        $('#notifBtn').setAttribute('aria-label', n ? `Уведомления, новых: ${n}` : 'Уведомления');
    }
    TAB_HANDLERS.notif = (v) => { S.notif.tab = v; renderNotif(); };
    function openNotif() {
        closeAI();
        S.notif.open = true; S.notif.lastFocus = document.activeElement;
        const panel = $('#notifPanel'), scrim = $('#aiScrim');
        renderNotif();
        panel.hidden = false; scrim.hidden = false;
        void panel.offsetWidth;
        panel.classList.add('is-open'); scrim.classList.add('is-open');
        panel.setAttribute('aria-hidden', 'false');
        $('#notifBtn').setAttribute('aria-expanded', 'true');
        $('#notifClose').focus({ preventScroll: true });
    }
    function closeNotif(returnFocus = true) {
        if (!S.notif.open) return;
        S.notif.open = false;
        const panel = $('#notifPanel'), scrim = $('#aiScrim');
        panel.classList.remove('is-open'); if (!S.ai.open) scrim.classList.remove('is-open');
        panel.setAttribute('aria-hidden', 'true');
        $('#notifBtn').setAttribute('aria-expanded', 'false');
        setTimeout(() => { if (!S.notif.open) { panel.hidden = true; if (!S.ai.open) scrim.hidden = true; } }, 260);
        const lf = S.notif.lastFocus;
        if (returnFocus) (lf && lf !== document.body && document.contains(lf) ? lf : $('#notifBtn')).focus({ preventScroll: true });
    }
    const markRead = (key) => (S.notif.read[S.role] || (S.notif.read[S.role] = new Set())).add(key);
    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t.closest('#notifBtn')) { S.notif.open ? closeNotif() : openNotif(); return; }
        if (t.closest('#notifClose') || (t.id === 'aiScrim' && S.notif.open)) { closeNotif(); return; }
        const r = t.closest('[data-notif-read]');
        if (r) { markRead(r.dataset.notifRead); renderNotif(); renderNav(currentSection()); return; }
        if (t.closest('[data-notif-readall]')) { notifItems().forEach(n => markRead(n.key)); renderNotif(); renderNav(currentSection()); return; }
        if (t.closest('[data-notif-full]')) { closeNotif(false); return; }
        const o = t.closest('[data-notif-open]');
        if (o) {
            const n = notifItems().find(x => x.key === o.dataset.notifOpen);
            if (!n) return;
            markRead(n.key); closeNotif(false);
            if (n.consiliumId) {   // как в приложении: переход к записи консилиума
                location.hash = '#/consilium';
                setTimeout(() => bookModal({ id: n.consiliumId }), 50);
            } else location.hash = `#/patient/${n.p.id}`;
        }
    });

    /* ===================== Набор компонентов прототипа ===================== */
    S.kit = { tab: 'route', filters: { stage: '', attention: false, doctor: '' } };
    TAB_HANDLERS.kit = (v) => { S.kit.tab = v; render(); };
    const kitFilterCount = () => Object.values(S.kit.filters).filter(Boolean).length;
    const kitFilterBody = () => html`<div class="form">
        <div class="field"><label class="field__label" for="kfStage">Этап маршрута</label><select class="select" id="kfStage"><option value="">Все этапы</option>${Object.entries(D.STAGES).map(([k, v]) => html`<option value="${k}" ${raw(S.kit.filters.stage === k ? 'selected' : '')}>${v}</option>`)}</select></div>
        <div class="field"><label class="field__label" for="kfDoctor">Лечащий врач</label><select class="select" id="kfDoctor"><option value="">Все врачи</option>${D.DOCTORS.map(d => html`<option value="${d.id}" ${raw(S.kit.filters.doctor === d.id ? 'selected' : '')}>${d.short}</option>`)}</select></div>
        <label class="check"><input type="checkbox" id="kfAttention" ${raw(S.kit.filters.attention ? 'checked' : '')}> Только требующие внимания</label></div>`;
    function viewKit() {
        const f = S.kit.filters;
        const shown = S.patients.filter(p => (!f.stage || p.stage === f.stage) && (!f.doctor || p.doctorId === f.doctor) && (!f.attention || p.attention.length || p.overdue)).length;
        const panes = {
            route: html`<p>Содержимое вкладки «Маршрут». Вкладки переключаются стрелками, Home и End; фокус следует за выбором.</p>`,
            events: html`<p>Содержимое вкладки «События и даты».</p>`,
            data: html`<p>Содержимое вкладки «Данные».</p>`,
        };
        const tabs = [{ value: 'route', label: 'Маршрут' }, { value: 'events', label: 'События и даты', count: 3 }, { value: 'data', label: 'Данные' }];
        return html`
            ${pagehead('Компоненты прототипа', 'Общие элементы для следующих этапов. Не раздел приложения — служебная страница прототипа.')}
            <div class="grid grid--2">
                <section class="panel"><div class="panel__head"><h2 class="panel__title">Вкладки</h2></div>
                    <div class="panel__body">${tabsView('kit', tabs, S.kit.tab, 'Пример вкладок карточки')}
                        <div class="tabs__panel" role="tabpanel" aria-labelledby="tab-kit-${S.kit.tab}">${panes[S.kit.tab]}</div></div></section>
                <section class="panel"><div class="panel__head"><h2 class="panel__title">Подтверждение опасного действия</h2></div>
                    <div class="panel__body kit-stack"><p class="muted">Фокус по умолчанию — на «Отмена»; Esc и клик по подложке — отказ.</p>
                        <div><button class="btn btn--danger" data-kit="confirm">Удалить пациента (пример)</button></div></div></section>
                <section class="panel"><div class="panel__head"><h2 class="panel__title">Лист фильтров</h2></div>
                    <div class="panel__body kit-stack"><p class="muted">На телефоне фильтры открываются листом снизу; число на кнопке — активные фильтры.</p>
                        <div class="kit-row"><button class="btn" data-kit="filters">Фильтры${kitFilterCount() ? html` <span class="msg-badge">${kitFilterCount()}</span>` : ''}</button><span class="muted">Подходит пациентов: ${shown}</span></div></div></section>
                <section class="panel"><div class="panel__head"><h2 class="panel__title">Дата и время</h2></div>
                    <div class="panel__body kit-stack">${dateTimeField({ id: 'kitFact', label: 'Фактическая дата и время', required: true, max: D.today, hint: 'Факт не может быть в будущем.' })}
                        <div><button class="btn btn--sm" data-kit="check-dt">Проверить</button> <span class="muted" id="kitDtOut"></span></div></div></section>
                <section class="panel kit-wide"><div class="panel__head"><h2 class="panel__title">Поиск по МКБ-10</h2><span class="panel__meta">${ICD.length} диагнозов из справочника приложения</span></div>
                    <div class="panel__body"><div class="form__row">
                        <div class="field"><label class="field__label" for="kitLoc">Локализация</label><select class="select" id="kitLoc"><option value="">Любая</option>${Object.entries(window.ICD10 || {}).map(([k, v]) => html`<option value="${k}">${v.label}</option>`)}</select></div>
                        ${icdField({ id: 'kitIcd', label: 'Диагноз (МКБ-10)', required: true, locSelectId: 'kitLoc' })}
                    </div><div><button class="btn btn--sm" data-kit="check-icd">Проверить выбор</button> <span class="muted" id="kitIcdOut"></span></div></div></section>
            </div>`;
    }
    document.addEventListener('click', async (e) => {
        const k = e.target.closest('[data-kit]');
        if (!k) return;
        const act = k.dataset.kit;
        if (act === 'confirm') {
            const ok = await confirmDialog({ title: 'Удалить пациента?', text: 'Карточка, маршрут и события будут удалены без возможности восстановления. В прототипе ничего не удаляется.', confirmLabel: 'Удалить', danger: true });
            toast(ok ? 'Подтверждено (демо): ничего не удалено.' : 'Отменено.');
        }
        if (act === 'filters') openSheet({ title: 'Фильтры', body: kitFilterBody(),
            onApply: (s) => { S.kit.filters = { stage: s.querySelector('#kfStage').value, doctor: s.querySelector('#kfDoctor').value, attention: s.querySelector('#kfAttention').checked }; render(); },
            onReset: (s) => { s.querySelector('#kfStage').value = ''; s.querySelector('#kfDoctor').value = ''; s.querySelector('#kfAttention').checked = false; } });
        if (act === 'check-dt') { const r = readDateTime('kitFact'); $('#kitDtOut').textContent = r.ok ? `Выбрано: ${fmtNum(r.date)} ${r.time}` : ''; }
        if (act === 'check-icd') {
            const v = icdValue('kitIcd'), input = $('#kitIcd');
            setInvalid(input, !v);
            $('#kitIcdOut').textContent = v ? `Выбран ${v.code}` : 'Выберите диагноз из списка.';
        }
    });

    /* ===================== Карточка пациента (этап 1) =====================
       Состав, тексты и порядок действий — как в static/app.js (renderPatientSide,
       TABS, tabOverview … tabHistory, advanceToStage/changeStage). Изменения
       живут в памяти вкладки: сервер, база и отправка SMS не подключены. */
    const STAGE_NAME = (s) => D.STAGES[s] || s;
    const PT_TABS = [
        { value: 'overview', label: 'Обзор' }, { value: 'notifications', label: 'Уведомления' }, { value: 'data', label: 'Данные' },
        { value: 'complaints', label: 'Комментарии' }, { value: 'events', label: 'События и даты' }, { value: 'checklist', label: 'Чек-лист' },
        { value: 'notes', label: 'Заметки' }, { value: 'history', label: 'История' },
    ];
    const SCHEDULE_LABEL = { exam: 'обследования', appointment: 'приёма', consult: 'онкоконсилиума', 'treatment-prep': 'лечения', treatment: 'лечения' };
    const ADD_EVENT_LABEL = { exam: 'Добавить дату обследования', appointment: 'Добавить приём', consult: 'Добавить дату консилиума', 'treatment-prep': 'Добавить дату лечения', treatment: 'Добавить дату лечения' };
    const SMS_STATUS = { pending: ['Ожидает отправки', 'tag--accent'], processing: ['Отправляется', 'tag--accent'], sent: ['Отправлено', 'tag--ok'], failed: ['Ошибка отправки', 'tag--danger'], cancelled: ['Отменено', ''], skipped: ['Пропущено', 'tag--warn'] };
    const SMS_SOURCE = { manual: 'Ручное напоминание', event: 'Плановое событие' };
    const SMS_REASON = { no_consent: 'Нет согласия', invalid_phone: 'Некорректный телефон', missing_phone: 'Телефон отсутствует', patient_unavailable: 'Пациент недоступен', template_disabled: 'Шаблон отключён', provider_failed: 'Ошибка отправки в тестовом режиме' };
    const CONSENT_LABEL = { not_recorded: 'Согласие не зафиксировано', granted: 'Согласие получено', revoked: 'Согласие отозвано' };
    const READONLY_TEXT = 'Режим просмотра — изменение данных недоступно';
    const OK_NOTE = 'Демонстрация: это отметка решения ОК при переводе этапа, как в карточке приложения. Она не является подписанным решением консилиума и не связана с ним — подпись ставит председатель в разделе «Консилиум». В прототипе ничего не сохраняется.';
    S.pt = { sel: {} };

    const nowStamp = () => { const d = new Date(); return `${D.today}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
    const pad2 = (n) => String(n).padStart(2, '0');
    // formatDateTime приложения: «Сегодня, 10:20», «Вчера, 10:20», иначе дд.мм.гггг.
    function fmtStamp(s) {
        if (!s) return '—';
        const [d, t] = s.split('T');
        const diff = Math.round((parse(d) - parse(D.today)) / 86400000);
        if (diff === 0) return `Сегодня, ${t || '00:00'}`;
        if (diff === -1) return `Вчера, ${t || '00:00'}`;
        return fmtNum(d);
    }
    const fmtStampFull = (s) => s ? `${fmtNum(s.split('T')[0])}, ${s.split('T')[1] || '00:00'}` : '—';
    const canEdit = (p) => R().can.write && R().scope(p);
    const stageLabelOf = (occ) => occ.stage === 'appointment' && occ.events.length && occ.events[occ.events.length - 1].actualDate ? 'Приём врача' : STAGE_NAME(occ.stage);
    const touch = (p) => { p.updatedAt = nowStamp(); D.helpers.derive(p); };
    const daysUntil = (iso) => iso ? Math.floor((parse(iso) - parse(D.today)) / 86400000) : null;

    // buildTimelineNodes приложения: совпадения с шаблоном маршрута, остальное — «(доп.)».
    function timelineNodes(p) {
        const nodes = []; let ti = 0; const last = p.history.length - 1;
        p.history.forEach((occ, i) => {
            const match = ti < D.STAGE_ORDER.length && occ.stage === D.STAGE_ORDER[ti];
            nodes.push({ stage: occ.stage, status: i === last ? 'current' : 'done', extra: !match, occIndex: i });
            if (match) ti++;
        });
        if (p.history.length && D.TERMINAL.includes(p.history[last].stage)) return nodes;
        for (let j = ti; j < D.STAGE_ORDER.length; j++) nodes.push({ stage: D.STAGE_ORDER[j], status: 'future', templateIndex: j, isNext: j === ti });
        return nodes;
    }
    // getStageDeadline: рабочие дни от фактической даты предыдущего этапа × коэффициент приоритета.
    function effectiveDate(occ) {
        if (!occ) return null;
        for (let i = occ.events.length - 1; i >= 0; i--) if (occ.events[i].actualDate) return parse(occ.events[i].actualDate);
        return occ.enteredAt ? parse(occ.enteredAt.split('T')[0]) : null;
    }
    function addWorkingDays(start, days) {
        const d = new Date(start); const total = Math.max(1, Math.round(days)); let n = 0;
        while (n < total) { d.setDate(d.getDate() + 1); if (d.getDay() !== 0 && d.getDay() !== 6) n++; }
        return d;
    }
    function stageDeadline(p, stage) {
        const ref = (window.NOSOLOGY_REF || {})[p.nosology];
        if (!ref) return null;
        const factor = p.priority === 'urgent' ? 0.5 : p.priority === 'high' ? 0.7 : 1;
        const base = stage === 'appointment' ? p.history.find(o => o.stage === 'primary-appointment') : stage === 'consult' ? p.history.find(o => o.stage === 'appointment') : null;
        const start = effectiveDate(base);
        if (!start) return null;
        return addWorkingDays(start, (stage === 'appointment' ? ref.stage1Days : ref.stage2Standard) * factor);
    }
    const fmtD = (d) => `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}`;
    const dateChipText = (days, time) => days === null ? 'дата не установлена' : days < 0 ? `просрочено ${-days} дн.${time ? ' ' + time : ''}` : days === 0 ? `сегодня${time ? ' ' + time : ''}` : `${days} дн.${time ? ' ' + time : ''}`;
    const dateChipTone = (days) => days === null ? '' : days <= 3 ? 'tag--danger' : days <= 7 ? 'tag--warn' : 'tag--ok';
    function eventText(ev, pending) {
        if (ev.noPlan) return { text: `факт ${ev.actualDate || '—'}${ev.actualTime ? ' ' + ev.actualTime : ''}`, status: 'состоялось' };
        const parts = [ev.date ? `план ${ev.date}${ev.time ? ' ' + ev.time : ''}` : 'план не указан'];
        if (ev.actualDate) parts.push(`факт ${ev.actualDate}${ev.actualTime ? ' ' + ev.actualTime : ''}`);
        if (ev.treatmentType) parts.push(D.EVENT_TREATMENT[ev.treatmentType] || ev.treatmentType);
        if (ev.consiliumDecision) parts.push(`Решение: ${D.DECISIONS[ev.consiliumDecision]}`);
        return { text: parts.join(' · '), status: ev.actualDate ? (ev.matchedPlan ? 'состоялось по плану' : 'состоялось с отклонением') : (pending ? 'ожидается' : 'без отметки') };
    }
    const statusChip = (p) => p.stage === 'completed' ? html`<span class="tag tag--ok">Завершил лечение</span>`
        : p.stage === 'dispensary-observation' ? html`<span class="tag tag--ok">Диспансерное наблюдение</span>`
        : p.stage === 'diagnosis-not-confirmed' ? html`<span class="tag">Диагноз не подтверждён</span>`
        : ['treatment', 'treatment-prep'].includes(p.stage) ? html`<span class="tag tag--accent">На лечении</span>`
        : html`<span class="tag tag--accent">В процессе обследования</span>`;
    const progress = (p) => D.TERMINAL.includes(p.stage) ? 100 : Math.round((stageIdx(p.stage) + 1) / D.STAGE_ORDER.length * 100);

    // generateSummary приложения — тот же текст и порядок строк.
    function summaryText(p) {
        const L = [`Пациент: ${p.name}, ${p.age} лет.`, `Номер полиса ОМС: ${p.policy || 'не указан'}.`, `Диагноз (МКБ-10): ${p.icd || 'не указан'}.`];
        const ref = (window.NOSOLOGY_REF || {})[p.nosology];
        if (ref) L.push(`Локализация/клиентский путь: ${ref.label}.`);
        L.push(`Приоритет: ${D.PRIORITY[p.priority]}.`, `Заведующий: ${p.head || 'не указан'}.`, `Лечащий врач: ${p.doctor || 'не указан'}.`, '', `Текущий этап: ${stageLabelOf(p.history[p.history.length - 1])}.`, '', 'Хронология:');
        let prev = null;
        p.history.forEach((occ, i) => {
            const eff = effectiveDate(occ);
            const dur = prev && eff ? ` (по фактическим датам: ${Math.round((eff - prev) / 86400000)} дн. от предыдущего этапа)` : '';
            L.push(`${i + 1}. ${stageLabelOf(occ)} — статус проставлен ${fmtStampFull(occ.enteredAt)}${dur}`);
            if (occ.note) L.push(`   Примечание: ${occ.note}`);
            occ.events.forEach((ev, idx) => {
                const { text, status } = eventText(ev, i === p.history.length - 1 && idx === occ.events.length - 1 && !ev.actualDate);
                L.push(`${occ.events.length > 1 ? `   – событие ${idx + 1}: ` : '   – '}${text} — ${status}`);
                if (ev.consiliumDecision) L.push(`     ОК — Решение: ${D.DECISIONS[ev.consiliumDecision]}`);
            });
            if (eff) prev = eff;
        });
        const left = timelineNodes(p).filter(n => n.status === 'future').length;
        L.push('', left > 0 ? `Осталось этапов: ${left}.` : 'Лечение завершено, дальнейших этапов нет.');
        return L.join('\n');
    }

    // Состояния демонстрации внутри вкладки: загрузка, пусто (тексты приложения), ошибка.
    const tabState = (body, emptyText, rows = 5) => {
        if (S.demo === 'loading') return html`<div class="skeleton" aria-busy="true" aria-label="Загрузка">${Array.from({ length: rows }, () => html`<span></span>`)}</div>`;
        if (S.demo === 'error') return html`<div class="error" role="alert"><div class="error__title">Не удалось загрузить данные</div><div>Сервер не ответил. Повторите попытку.</div><button class="btn btn--sm" data-action="retry">Повторить</button></div>`;
        if (S.demo === 'empty') return html`<div class="empty"><div class="empty__title">${emptyText}</div></div>`;
        return body;
    };

    function viewPatient(id, params) {
        const p = patient(id);
        if (!p || !R().scope(p)) return html`${pagehead('Карточка недоступна', '')}<section class="panel"><div class="error"><div class="error__title">Пациент не найден или вне вашей области доступа</div><div>Сервер не отдаёт карточку пациента вне области доступа роли; удалённая карточка тоже недоступна.</div><button class="btn btn--sm" data-href="#/patients">К списку пациентов</button></div></section>`;
        const tab = PT_TABS.some(t => t.value === params.get('tab')) ? params.get('tab') : 'overview';
        const edit = canEdit(p);
        const conv = convFor(p.id);
        const openIssues = p.attentionIssues.filter(x => x.status === 'open').length;
        const panes = { overview: ptOverview, notifications: ptNotifications, data: ptData, complaints: ptComplaints, events: ptEvents, checklist: ptChecklist, notes: ptNotes, history: ptHistory };
        return html`
            <nav class="crumbs" aria-label="Навигация"><a href="#/patients">Пациенты</a><span aria-hidden="true">/</span><span>${p.name}</span></nav>
            ${edit ? html`<div class="demo-note">Демонстрация: изменения в карточке применяются только в этой вкладке браузера, не отправляются на сервер и пропадают при перезагрузке.</div>` : ''}
            <section class="panel pcard-head">
                <div class="ptitle">
                    <div class="ptitle__main">
                        <span class="avatar avatar--lg avatar--${p.priority}" aria-hidden="true">${initials(p.name)}</span>
                        <div class="ptitle__text">
                            <h1 class="ptitle__name">${p.name}</h1>
                            <div class="facts"><span><b>${p.age}</b> лет · приоритет: <b>${D.PRIORITY[p.priority]}</b></span><span class="mono">${p.phone}</span><span>Полис ОМС: <b class="mono">${p.policy || 'Не указан'}</b></span><span>Диагноз <b class="mono">${p.icd}</b> · ${p.nosologyLabel}</span></div>
                            <div class="ptitle__chips">${statusChip(p)}${openIssues ? html`<span class="tag tag--warn">Требует внимания${openIssues > 1 ? ` · ${openIssues}` : ''}</span>` : ''}${edit ? '' : html`<span class="tag">Режим просмотра</span>`}</div>
                        </div>
                    </div>
                    <div class="pagehead__actions">
                        ${conv ? html`<a class="btn" href="#/messages/${conv.id}?back=patient/${p.id}">${icon('messages')}<span>Переписка с пациентом</span>${conv.unread ? html`<span class="msg-badge" aria-label="Непрочитанных: ${conv.unread}">${conv.unread}</span>` : ''}</a>` : ''}
                        <button class="btn btn--ghost" data-ai-ask="${p.id}" aria-label="Спросить помощника по пациенту: ${p.name}">Спросить помощника</button>
                        <button class="btn btn--ghost" data-pt="copy" data-id="${p.id}">Скопировать сводку</button>
                        ${R().perms.deletePatients ? html`<button class="btn btn--danger" data-pt="delete" data-id="${p.id}">Удалить пациента</button>` : ''}
                    </div>
                </div>
                ${ptTimeline(p, edit)}
            </section>
            <div class="grid grid--main-side pcard">
                <div class="pcard__main panel">
                    ${tabsView('pt', PT_TABS, tab, 'Разделы карточки пациента')}
                    <div class="pcard__panel" role="tabpanel" id="pt-panel" aria-labelledby="tab-pt-${tab}">${panes[tab](p, edit)}</div>
                </div>
                <aside class="stack">${ptSide(p, edit)}</aside>
            </div>`;
    }

    function ptTimeline(p, edit) {
        const sel = S.pt.sel[p.id];
        const nodes = timelineNodes(p);
        // В приложении для ролей просмотра все кнопки панели скрываются (applyReadOnlyToPatientView),
        // вместе с шагами маршрута. Прототип показывает маршрут без действий — см. README.
        return html`<ol class="tl" aria-label="Маршрут лечения">${nodes.map((n, idx) => {
            const isNext = n.status === 'future' && n.isNext;
            const key = n.status === 'future' ? `future:${n.templateIndex}` : `occ:${n.occIndex}`;
            const label = (n.occIndex !== undefined ? stageLabelOf(p.history[n.occIndex]) : STAGE_NAME(n.stage)) + (n.extra ? ' (доп.)' : '');
            const state = n.status === 'done' ? 'Завершён' : n.status === 'current' ? 'Текущий' : isNext ? 'Следующий' : 'Запланирован';
            const dl = ['appointment', 'consult'].includes(n.stage) ? stageDeadline(p, n.stage) : null;
            const inner = html`<span class="tl__mark">${n.status === 'done' ? '✓' : idx + 1}</span><span class="tl__label">${label}</span><span class="tl__state">${state}</span>${dl ? html`<span class="tl__deadline">до ${fmtD(dl)}</span>` : ''}`;
            const cls = `tl__step tl__step--${n.status === 'future' ? (isNext ? 'next' : 'future') : n.status} ${sel === key ? 'is-selected' : ''}`;
            return html`<li>${edit
                ? html`<button type="button" class="${cls}" data-pt="${isNext ? 'next-node' : 'node'}" data-id="${p.id}" data-key="${key}" data-stage="${n.stage}" aria-label="${STAGE_NAME(n.stage)}: ${state}. ${isNext ? 'Перевести пациента на этот этап' : 'Показать даты этапа'}">${inner}</button>`
                : html`<div class="${cls}">${inner}</div>`}</li>`;
        })}</ol>`;
    }

    function ptSide(p, edit) {
        const nodes = timelineNodes(p);
        const next = nodes.find(n => n.status === 'future' && n.isNext);
        const open = p.attentionIssues.filter(x => x.status === 'open').length;
        const cons = S.consilium.filter(c => c.patientId === p.id);
        const act = (stage, label, cls = 'btn', decision = '') => html`<button class="${cls}" data-pt="advance" data-id="${p.id}" data-stage="${stage}" data-decision="${decision}">${label}</button>`;
        return html`
            <section class="panel"><div class="panel__head"><h2 class="panel__title">Этап лечения</h2></div>
                <div class="panel__body pside">
                    <div class="pside__value">${stageLabelOf(p.history[p.history.length - 1])}</div>
                    <div class="progress"><progress max="100" value="${progress(p)}" aria-label="Пройдено этапов маршрута"></progress><span class="mono">${progress(p)}%</span></div>
                    ${edit ? html`<div class="pside__actions">
                        ${next && p.stage !== 'consult' ? act(next.stage, `Перевести: ${STAGE_NAME(next.stage)}`, 'btn btn--primary') : ''}
                        ${p.stage === 'appointment' ? act('exam', 'Дополнительное обследование', 'btn btn--ghost') : ''}
                        ${p.stage === 'consult' ? html`<p class="demo-hint">Решение ОК здесь — отметка при переводе этапа, не подписанное решение консилиума.</p>${act('treatment-prep', 'Определение тактики лечения', 'btn btn--primary', 'treatment_strategy_defined')}${act('treatment-prep', 'Изменение тактики лечения', 'btn', 'treatment_strategy_changed')}${act('exam', 'Направить на дообследование', 'btn')}${act('dispensary-observation', 'Диспансерное наблюдение', 'btn', 'dispensary_observation')}` : ''}
                        ${['exam', 'appointment', 'consult'].includes(p.stage) ? act('diagnosis-not-confirmed', 'Диагноз не подтверждён', 'btn btn--ghost') : ''}
                    </div>` : ''}
                </div></section>
            <section class="panel"><div class="panel__head"><h2 class="panel__title">Контроль внимания</h2></div>
                <div class="panel__body pside">
                    ${open ? html`<span class="tag tag--warn">Требует внимания${open > 1 ? ` · ${open}` : ''}</span>` : html`<div class="muted">Пациент не отмечен как требующий внимания</div>`}
                    ${edit ? html`<div><button class="btn btn--sm" data-pt="attention-add" data-id="${p.id}">Добавить проблему</button></div>` : html`<div class="muted">${READONLY_TEXT}</div>`}
                </div></section>
            <section class="panel"><div class="panel__head"><h2 class="panel__title">Ответственные</h2></div>
                <div class="panel__body pside">
                    <div class="pside__value">${p.doctorFull || p.doctor || 'Врач не указан'}</div>
                    ${p.doctorId ? '' : html`<div class="muted">Учётная запись врача не сопоставлена</div>`}
                    <div class="muted">${p.head ? `Заведующий: ${p.head}` : 'Заведующий не указан'}</div>
                    ${R().perms.changeTreatingDoctor ? html`<div><button class="btn btn--sm btn--ghost" data-pt="doctor" data-id="${p.id}">Сменить лечащего врача</button></div>` : ''}
                </div></section>
            <section class="panel"><div class="panel__head"><h2 class="panel__title">Последняя активность</h2></div>
                <div class="panel__body pside"><div class="pside__value">${fmtStamp(p.updatedAt || p.history[p.history.length - 1].enteredAt)}</div><div class="muted">Добавлен ${fmtStamp(p.createdAt)}</div></div></section>
            <section class="panel"><div class="panel__head"><h2 class="panel__title">Онкологический консилиум</h2><span class="tag">Предложение</span></div>
                <div class="panel__body pside">
                    <div class="muted">В действующей карточке этого блока нет: записи ведутся в разделе «Консилиум», решение ОК фиксируется при переводе с этапа «Направлен на ОК».</div>
                    ${cons.length ? cons.map(c => html`<div class="pside__cons"><div>${fmtNum(c.date)}, <span class="mono">${c.time}</span> · ${D.GROUPS[c.group]}</div><div>${consTag(c)}</div>${c.status === 'signed' ? html`<div class="muted">${D.TREATMENTS[c.treatment]}${c.scheme ? ` · ${c.scheme.name}` : ''}</div>` : ''}</div>`) : html`<div class="muted">Записей на консилиум нет.</div>`}
                    ${R().can.consilium ? html`<div><button class="btn btn--sm btn--ghost" data-href="#/consilium">Открыть раздел «Консилиум»</button></div>` : ''}
                </div></section>`;
    }

    const issueNode = (p, x, writable) => html`<li class="issue ${x.reasonCode === 'emergency_hospitalization' ? 'issue--emergency' : ''}">
        <div class="issue__head"><b>${D.ATTENTION_REASONS[x.reasonCode] || 'Причина не указана'}</b><span class="tag ${x.status === 'open' ? 'tag--warn' : ''}">${x.status === 'open' ? 'Открыта' : 'Закрыта'}</span></div>
        ${x.eventDate ? html`<div class="muted">Дата события: ${x.eventDate}</div>` : ''}
        <div class="muted">Добавил: ${x.createdByName || 'Сотрудник'} · ${fmtStamp(x.createdAt)}</div>
        ${x.comment ? html`<p class="issue__comment">${x.comment}</p>` : ''}
        ${x.status === 'closed' ? html`<div class="muted">Закрыта: ${x.closedByName || 'Сотрудник'} · ${fmtStamp(x.closedAt)}</div>${x.resolutionComment ? html`<p class="issue__comment">${x.resolutionComment}</p>` : ''}` : ''}
        ${x.status === 'open' && writable ? html`<div><button class="btn btn--sm btn--ghost" data-pt="attention-close" data-id="${p.id}" data-issue="${x.id}">Закрыть</button></div>` : ''}
    </li>`;

    function feedEntries(p) {
        const out = [];
        p.history.forEach(occ => {
            out.push({ at: occ.enteredAt, tone: 'info', title: 'Изменение этапа', text: `Пациент переведён на этап: ${stageLabelOf(occ)}` });
            occ.events.forEach(ev => {
                const { text } = eventText(ev, false);
                if (ev.actualDate) out.push({ at: ev.setAt || `${ev.actualDate}T${ev.actualTime || '00:00'}`, tone: ev.matchedPlan === false ? 'warn' : 'ok', title: 'Событие состоялось', text: `${stageLabelOf(occ)}: ${text}` });
                else if (ev.date) out.push({ at: ev.setAt || occ.enteredAt, tone: 'info', title: 'Запланировано событие', text: `${stageLabelOf(occ)}: ${text}` });
            });
            if (occ.note) out.push({ at: occ.enteredAt, tone: 'warn', title: 'Заметка', text: occ.note });
        });
        p.diagnosisHistory.forEach(d => out.push({ at: d.changedAt, tone: 'info', title: 'Изменение диагноза', text: `${d.oldDiagnosis || 'не указан'} → ${d.newDiagnosis || 'не указан'}` }));
        return out.sort((a, b) => b.at.localeCompare(a.at)).slice(0, 12);
    }

    function ptOverview(p, edit) {
        const active = p.attentionIssues.filter(x => x.status === 'open'), closed = p.attentionIssues.filter(x => x.status === 'closed');
        const feed = feedEntries(p);
        const fields = [['ФИО', p.name], ['Телефон', p.phone], ['Номер полиса ОМС', p.policy], ['Диагноз', p.icd], ['Текущий этап', p.stage], ['Лечащий врач', p.doctorId]];
        const missing = fields.filter(f => !f[1]).map(f => f[0]);
        const score = Math.round((fields.length - missing.length) / fields.length * 100);
        // Тексты правил — из services/data_quality.py; смоделированы только эти два.
        const findings = [
            !p.doctorId && ['Не назначен лечащий врач', 'Пациент не сопоставлен с учётной записью лечащего врача; для врачей и заведующих такая запись не входит в scope.'],
            !p.policy && ['Не указан номер полиса ОМС', 'Номер полиса ОМС повышает полноту регистрационной карточки.'],
        ].filter(Boolean);
        const cur = p.history[p.history.length - 1];
        const cl = D.helpers.checklistFor(p.nosology, cur.stage);
        const ref = (window.NOSOLOGY_REF || {})[p.nosology];
        return html`<div class="pt-stack">
            <section class="pt-block ${active.length ? 'pt-block--attention' : ''}">
                <div class="pt-block__head"><h2 class="pt-block__title">Требует внимания</h2>${edit ? html`<button class="btn btn--sm btn--ghost" data-pt="attention-add" data-id="${p.id}">Добавить проблему</button>` : ''}</div>
                ${S.demo === 'error' ? html`<div class="muted" role="alert">Не удалось загрузить отметки внимания: сервер не ответил.</div>`
                : S.demo === 'loading' ? html`<div class="muted">Загрузка…</div>`
                : html`${active.length && S.demo !== 'empty' ? html`<ul class="issues">${active.map(x => issueNode(p, x, edit))}</ul>` : html`<div class="muted">Пациент не отмечен как требующий внимания</div>`}
                    ${closed.length && S.demo !== 'empty' ? html`<details class="pt-details"><summary>История внимания · ${closed.length}</summary><ul class="issues">${closed.map(x => issueNode(p, x, false))}</ul></details>` : ''}`}
            </section>
            <section class="pt-block"><div class="pt-block__head"><h2 class="pt-block__title">Лента событий</h2></div>
                ${tabState(html`<ul class="feed feed--flush">${feed.map(e => html`<li><span class="feed__date">${fmtNum(e.at.split('T')[0])}<br><span class="muted">${e.at.split('T')[1] || ''}</span></span><div><div class="feed__title">${e.title}</div><div class="muted">${e.text}</div></div><span class="status status--${e.tone}"></span></li>`)}</ul>`, 'Событий пока нет — они появятся после первых отметок дат.', 4)}
            </section>
            <div class="grid grid--2">
                <section class="pt-block"><div class="pt-block__head"><h2 class="pt-block__title">Качество данных</h2></div>
                    ${S.demo === 'loading' ? html`<div class="muted">Загрузка…</div>` : S.demo === 'error' ? html`<div class="muted" role="alert">Не удалось загрузить качество данных.</div>` : html`
                    <div class="quality"><span class="quality__value">${score}%</span><span class="muted">Заполненность обязательных полей</span></div>
                    <p class="muted">${missing.length ? `Не заполнено: ${missing.join(', ')}.` : 'Обязательные поля заполнены.'}</p>
                    ${findings.length ? findings.map(f => html`<div class="finding"><span class="tag tag--danger">Проблема</span><div class="finding__title">${f[0]}</div><div class="muted">${f[1]}</div></div>`) : html`<div class="muted">По действующим правилам качества проблем не обнаружено.</div>`}
                    <p class="demo-hint">В прототипе посчитаны только заполненность, лечащий врач и полис; остальные правила качества работают на сервере.</p>`}
                </section>
                <section class="pt-block"><div class="pt-block__head"><h2 class="pt-block__title">Чек-лист этапа</h2>${cl.required.length ? html`<span class="muted">${cl.required.filter(n => cur.checklist[n]).length} из ${cl.required.length}</span>` : ''}</div>
                    ${!ref ? html`<div class="muted">Чек-лист доступен после указания профиля клиентского пути.</div>` : !cl.required.length ? html`<div class="muted">Для текущего этапа чек-лист не предусмотрен.</div>` : html`
                    <ul class="mini-check">${cl.required.slice(0, 5).map(n => html`<li class="${cur.checklist[n] ? 'is-done' : ''}">${cur.checklist[n] ? '✓' : '○'} ${n}</li>`)}</ul>
                    <button class="btn btn--sm btn--ghost" data-pt="tab" data-id="${p.id}" data-tab="checklist">Открыть полный чек-лист</button>`}
                </section>
            </div>
            <section class="pt-block"><div class="pt-block__head"><h2 class="pt-block__title">Сводка для отчётности</h2></div>
                ${tabState(html`<pre class="summary">${summaryText(p)}</pre>`, 'Этапов пока нет.', 6)}</section>
        </div>`;
    }

    function ptNotifications(p, edit) {
        const n = p.notifications;
        const tpl = D.SMS_TEMPLATES;
        const upcoming = n.items.filter(x => x.status === 'pending').sort((a, b) => a.scheduledAt.localeCompare(b.scheduledAt))[0];
        const def = new Date(Date.now() + 3600000);
        const defVal = `${D.today}T${pad2(def.getHours())}:${pad2(def.getMinutes())}`;
        return html`<div class="pt-stack">
            <h2 class="pt-block__title">Уведомления</h2>
            <div class="callout callout--info callout--flush"><div class="callout__title">Тестовый режим: реальные SMS не отправляются</div><div class="callout__text">В прототипе уведомления не создаются на сервере и не отправляются; история ниже — синтетическая.</div></div>
            ${tabState(html`
            <div class="pt-facts"><div>${n.smsEnabled ? 'SMS включены' : 'SMS выключены'}</div><div>${CONSENT_LABEL[n.consent]}</div>
                ${n.consentAt ? html`<div>Дата фиксации: ${fmtStamp(n.consentAt)}</div>` : ''}${n.consentBy ? html`<div>Зафиксировал: ${n.consentBy}</div>` : ''}
                ${upcoming ? html`<div>Ближайшее уведомление: ${fmtStamp(upcoming.scheduledAt)}</div>` : ''}</div>
            ${edit ? html`
                <div class="kit-row"><button class="btn btn--sm btn--ghost" data-pt="consent" data-id="${p.id}" data-v="granted">Зафиксировать согласие</button>
                    <button class="btn btn--sm btn--ghost" data-pt="consent" data-id="${p.id}" data-v="revoked" ${raw(n.consent !== 'granted' ? 'disabled' : '')} title="${n.consent !== 'granted' ? 'Согласие ещё не получено' : 'Ожидающие уведомления будут отменены'}">Отозвать согласие</button></div>
                <div class="form__row">
                    <div class="field"><label class="field__label" for="smsTpl">Шаблон</label><select class="select" id="smsTpl" data-sms-preview>${tpl.map(t => html`<option value="${t.code}">${t.name}</option>`)}</select></div>
                    <div class="field"><label class="field__label" for="smsAt">Дата и время отправки</label><input class="input" type="datetime-local" id="smsAt" value="${defVal}" data-sms-preview></div>
                </div>
                <div class="field"><label class="field__label" for="smsPreview">Предварительный просмотр</label><textarea class="textarea" id="smsPreview" rows="3" readonly>${smsPreview(tpl[0].code, defVal)}</textarea>
                    <p class="field__hint">Свободный текст недоступен: используется только утверждённый шаблон.</p></div>
                <div><button class="btn btn--primary" data-pt="sms-create" data-id="${p.id}">Создать напоминание</button></div>`
            : html`<p class="muted">${READONLY_TEXT}</p>`}
            <h3 class="pt-block__title">История уведомлений</h3>
            ${n.items.length ? html`<ul class="sms-list">${n.items.slice().sort((a, b) => b.scheduledAt.localeCompare(a.scheduledAt)).map(x => html`<li><div><div class="cell-title">${(tpl.find(t => t.code === x.template) || {}).name || x.template}</div><div class="muted">${fmtStamp(x.scheduledAt)} · ${SMS_SOURCE[x.source]}</div><div class="muted">${SMS_REASON[x.reason] || x.reason || '—'}</div></div><span class="tag ${SMS_STATUS[x.status][1]}">${SMS_STATUS[x.status][0]}</span></li>`)}</ul>` : html`<div class="muted">Уведомления ещё не создавались.</div>`}
            `, 'Уведомления ещё не создавались.')}
        </div>`;
    }
    function smsPreview(code, at) {
        const t = D.SMS_TEMPLATES.find(x => x.code === code) || D.SMS_TEMPLATES[0];
        const [d, tm] = (at || '').split('T');
        return t.body.replaceAll('{date}', d ? fmtNum(d) : 'дата').replaceAll('{time}', tm || 'время');
    }

    function ptData(p, edit) {
        const dis = raw(edit ? '' : 'disabled');
        const heads = [...new Set([...Object.values(D.HEADS), p.head].filter(Boolean))];
        return tabState(html`<form class="form pt-form" novalidate data-pt-form="${p.id}">
            <fieldset class="pt-fieldset"><legend class="form__section">Личные данные</legend>
                <div class="field"><label class="field__label" for="pfName">ФИО</label><input class="input" id="pfName" data-pf="name" value="${p.name}" ${dis}></div>
                <div class="form__row"><div class="field"><label class="field__label" for="pfAge">Возраст</label><input class="input" id="pfAge" type="number" min="0" max="150" data-pf="age" value="${p.age}" ${dis}></div>
                    <div class="field"><label class="field__label" for="pfPhone">Телефон</label><input class="input" id="pfPhone" type="tel" data-pf="phone" value="${p.phone}" ${dis}></div></div>
                <div class="field"><label class="field__label" for="pfPolicy">Номер полиса ОМС</label><input class="input mono" id="pfPolicy" data-pf="policy" value="${p.policy}" ${dis}></div>
            </fieldset>
            <fieldset class="pt-fieldset"><legend class="form__section">Медицинская информация</legend>
                ${edit ? html`<div class="form__row"><div class="field"><label class="field__label" for="pfLoc">Локализация</label><select class="select" id="pfLoc"><option value="">Любая</option>${Object.entries(window.ICD10 || {}).map(([k, v]) => html`<option value="${k}">${v.label}</option>`)}</select></div>
                    ${icdField({ id: 'pfIcd', label: `Диагноз — сейчас ${p.icd}`, locSelectId: 'pfLoc' })}</div>
                    <p class="field__hint">Локализация ограничивает список диагнозов; клиентский путь определяется автоматически. В прототипе клиентский путь при смене диагноза не пересчитывается.</p>`
                : html`<div class="field"><span class="field__label">Диагноз</span><input class="input" value="${p.icd} · ${p.nosologyLabel}" disabled aria-label="Диагноз"></div>`}
                <div class="form__row"><div class="field"><label class="field__label" for="pfPgi">Результат ПГИ</label><select class="select" id="pfPgi" data-pgi ${dis}>${Object.entries(D.PGI).map(([k, v]) => html`<option value="${k}" ${raw(p.pgiResult === k ? 'selected' : '')}>${v}</option>`)}</select></div>
                    <div class="field"><label class="field__label" for="pfPgiDate">Дата результата ПГИ</label><input class="input" type="date" id="pfPgiDate" data-pgi value="${p.pgiResultDate}" ${dis} aria-describedby="pfPgiHint"></div></div>
                <p class="field__hint" id="pfPgiHint">Не переводит маршрут автоматически.</p>
            </fieldset>
            <fieldset class="pt-fieldset"><legend class="form__section">Классификация и ответственные</legend>
                <div class="form__row"><div class="field"><label class="field__label" for="pfPriority">Приоритет</label><select class="select" id="pfPriority" data-pf="priority" ${dis}>${Object.entries(D.PRIORITY).map(([k, v]) => html`<option value="${k}" ${raw(p.priority === k ? 'selected' : '')}>${v}</option>`)}</select></div>
                    <div class="field"><label class="field__label" for="pfDoctor">Лечащий врач</label><input class="input" id="pfDoctor" value="${p.doctorFull || p.doctor || ''}" disabled aria-label="Лечащий врач (изменяется Разработчиком)"></div></div>
                <div class="field"><label class="field__label" for="pfHead">Заведующий</label><select class="select" id="pfHead" data-pf="head" ${dis}><option value="" ${raw(p.head ? '' : 'selected')}>Не указан</option>${heads.map(h => html`<option ${raw(p.head === h ? 'selected' : '')}>${h}</option>`)}</select></div>
            </fieldset>
            ${edit ? html`<p class="demo-hint">Поля применяются при выходе из поля — как в приложении, но только в этой вкладке; сервер не вызывается.</p>` : html`<p class="muted">${READONLY_TEXT}</p>`}
        </form>`, 'Этапов пока нет.');
    }

    function ptComplaints(p, edit) {
        return tabState(html`<div class="pt-stack"><h2 class="pt-block__title">Комментарии</h2>
            <textarea class="textarea" rows="10" aria-label="Комментарии" data-pf="complaint" ${raw(edit ? '' : 'disabled')}>${p.complaint}</textarea>
            <p class="field__hint">${edit ? 'В приложении изменения сохраняются на сервере при выходе из поля. В прототипе — только в этой вкладке.' : READONLY_TEXT}</p></div>`, 'Комментариев нет.');
    }

    const noteField = (p, i, note, edit) => html`<div class="field"><label class="field__label" for="note-${i}">Примечание к этапу</label><textarea class="textarea" id="note-${i}" rows="3" placeholder="Свободный комментарий по этому этапу…" data-note="${i}" ${raw(edit ? '' : 'disabled')}>${note}</textarea></div>`;

    function ptEvents(p, edit) {
        if (S.demo !== 'ok') return tabState('', 'Этапов пока нет.');
        const sel = S.pt.sel[p.id];
        if (sel && sel.startsWith('future:')) {
            const stage = D.STAGE_ORDER[Number(sel.split(':')[1])];
            return html`<div class="pt-stack"><h2 class="pt-block__title">${STAGE_NAME(stage)}</h2>
                <p class="field__hint">Этап ещё не наступил. Для перехода отметьте фактические данные текущего события.</p>
                ${edit ? html`<div><button class="btn btn--primary" data-pt="advance" data-id="${p.id}" data-stage="${stage}" data-decision="">Перевести пациента на этот этап</button></div>` : ''}</div>`;
        }
        const i = sel && sel.startsWith('occ:') ? Number(sel.split(':')[1]) : p.history.length - 1;
        const occ = p.history[i];
        if (!occ) return html`<div class="muted">Этапов пока нет.</div>`;
        const dis = raw(edit ? '' : 'disabled');
        const evInput = (j, f, type, val, label) => html`<input class="input" type="${type}" value="${val || ''}" aria-label="${label}" data-ev="${i}:${j}:${f}" ${dis}>`;
        const pickHint = p.history.length > 1 ? html`<p class="muted">Выберите этап на маршруте выше, чтобы открыть его даты.</p>` : '';
        if (occ.stage === 'primary-appointment') {
            const ev = occ.events[0] || { actualDate: '', actualTime: '' };
            return html`<div class="pt-stack">${pickHint}<h2 class="pt-block__title">Первичный приём — фактические дата и время</h2>
                ${!ev.actualDate ? html`<div class="callout callout--danger callout--flush"><div class="callout__text">Дата фактического первичного приёма не заполнена — это обязательное поле.</div></div>` : ''}
                <div class="evcard"><div class="form__row">${evInput(0, 'actualDate', 'date', ev.actualDate, 'Фактическая дата')}${evInput(0, 'actualTime', 'time', ev.actualTime, 'Фактическое время')}</div></div>
                ${noteField(p, i, occ.note, edit)}</div>`;
        }
        if (!SCHEDULE_LABEL[occ.stage]) return html`<div class="pt-stack">${pickHint}<p class="muted">Для завершённого лечения плановые даты не заполняются.</p>${noteField(p, i, occ.note, edit)}</div>`;
        const evs = occ.events, isCur = i === p.history.length - 1, showType = ['treatment', 'treatment-prep'].includes(occ.stage);
        const warning = !evs.length ? `Дата ${SCHEDULE_LABEL[occ.stage]} не заполнена.` : showType && !evs[evs.length - 1].treatmentType ? 'Тип лечения не указан.' : '';
        let deadline = '';
        if (['appointment', 'consult'].includes(occ.stage)) {
            const dl = stageDeadline(p, occ.stage);
            if (dl) {
                const last = evs[evs.length - 1], check = last ? (last.actualDate || last.date) : null;
                const over = check && parse(check) > dl;
                const label = window.NOSOLOGY_REF[p.nosology].label;
                deadline = html`<div class="callout ${over ? 'callout--danger' : 'callout--warn'} callout--flush"><div class="callout__text">${over ? `Регламентный срок нарушен: событие должно было состояться не позднее ${fmtD(dl)} (${label}).` : `Регламентный срок: не позднее ${fmtD(dl)} (${label}${p.priority !== 'normal' ? ', приоритет учтён' : ''}).`}</div></div>`;
            }
        }
        return html`<div class="pt-stack">${pickHint}<h2 class="pt-block__title">Плановые и фактические даты — ${STAGE_NAME(occ.stage)}</h2>
            ${deadline}${warning ? html`<div class="callout callout--danger callout--flush"><div class="callout__text">${warning}</div></div>` : ''}
            ${evs.map((ev, j) => {
                const pending = isCur && j === evs.length - 1 && !ev.actualDate;
                const st = !pending && ev.actualDate ? html`<span class="tag ${ev.matchedPlan === false ? 'tag--warn' : 'tag--ok'}">${ev.matchedPlan === false ? 'Состоялось с отклонением' : 'Состоялось по плану'}</span>`
                    : pending && ev.date ? html`<span class="tag ${dateChipTone(daysUntil(ev.date))}">${dateChipText(daysUntil(ev.date), ev.time)}</span>` : '';
                return html`<div class="evcard">
                    <div class="evcard__head"><span class="evcard__title">${evs.length > 1 ? `Событие ${j + 1}` : 'Дата события'}</span>${st}
                        ${edit ? html`<button type="button" class="iconbtn" data-pt="event-delete" data-id="${p.id}" data-occ="${i}" data-ev="${j}" aria-label="Удалить событие ${j + 1}" title="Удалить событие"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg></button>` : ''}</div>
                    ${ev.consiliumDecision ? html`<div class="evcard__group"><div class="evcard__legend">ОК</div><span class="tag tag--accent">Решение: ${D.DECISIONS[ev.consiliumDecision]}</span><p class="demo-hint">Отметка при переводе этапа (как в приложении), а не подписанное решение консилиума.</p></div>` : ''}
                    <div class="evcard__group"><div class="evcard__legend">План</div><div class="form__row">${evInput(j, 'date', 'date', ev.date, 'Плановая дата')}${evInput(j, 'time', 'time', ev.time, 'Плановое время')}</div></div>
                    <div class="evcard__group"><div class="evcard__legend">${occ.stage === 'appointment' ? 'Факт (заполните после приёма)' : 'Факт'}</div><div class="form__row">${evInput(j, 'actualDate', 'date', ev.actualDate, 'Фактическая дата')}${evInput(j, 'actualTime', 'time', ev.actualTime, 'Фактическое время')}</div></div>
                    ${showType ? html`<div class="evcard__group"><div class="evcard__legend">Тип лечения</div><select class="select" aria-label="Тип лечения" data-ev="${i}:${j}:treatmentType" ${dis}><option value="">Не указан</option>${Object.entries(D.EVENT_TREATMENT).map(([k, v]) => html`<option value="${k}" ${raw(ev.treatmentType === k ? 'selected' : '')}>${v}</option>`)}</select></div>` : ''}
                </div>`;
            })}
            ${edit ? html`<div><button class="btn btn--ghost" data-pt="event-add" data-id="${p.id}" data-occ="${i}">+ ${ADD_EVENT_LABEL[occ.stage]}</button></div>` : ''}
            ${noteField(p, i, occ.note, edit)}</div>`;
    }

    function ptChecklist(p, edit) {
        const i = p.history.length - 1, occ = p.history[i];
        const ref = (window.NOSOLOGY_REF || {})[p.nosology];
        if (!occ || !ref) return html`<div class="muted">Чек-лист доступен после указания профиля клиентского пути.</div>`;
        const cl = D.helpers.checklistFor(p.nosology, occ.stage);
        if (!cl.required.length && !cl.optional.length) return tabState(html`<div class="empty"><div class="empty__title">Для текущего этапа чек-лист не предусмотрен.</div></div>`, 'Для текущего этапа чек-лист не предусмотрен.');
        const item = (name, req, k) => html`<li class="${occ.checklist[name] ? 'is-done' : ''}"><input type="checkbox" id="cl-${k}" data-cl="${name}" ${raw(occ.checklist[name] ? 'checked' : '')} ${raw(edit ? '' : 'disabled')}><label for="cl-${k}"><span>${name}</span>${req ? html`<span class="req"> *</span>` : ''}</label></li>`;
        return tabState(html`<div class="pt-stack">
            <div class="pt-block__head"><h2 class="pt-block__title">Чек-лист: ${ref.label}</h2><span class="muted">${cl.required.filter(n => occ.checklist[n]).length} из ${cl.required.length}</span></div>
            <ul class="checklist checklist--flush">${cl.required.map((n, k) => item(n, true, k))}</ul>
            ${cl.optional.length ? html`<h3 class="form__section">Опционально / по показаниям</h3><ul class="checklist checklist--flush">${cl.optional.map((n, k) => item(n, false, `o${k}`))}</ul>` : ''}
            ${edit ? '' : html`<p class="muted">${READONLY_TEXT}</p>`}</div>`, 'Для текущего этапа чек-лист не предусмотрен.');
    }

    function ptNotes(p, edit) {
        return tabState(html`<div class="pt-stack">${p.history.map((occ, i) => html`<div class="evcard"><div class="evcard__head"><span class="evcard__title">${stageLabelOf(occ)}</span><span class="muted">${fmtStamp(occ.enteredAt)}</span></div>${noteField(p, i, occ.note, edit)}</div>`)}</div>`, 'Этапов пока нет.');
    }

    function ptHistory(p) {
        return tabState(html`<div class="pt-stack">${p.history.map((occ, i) => {
            const cur = i === p.history.length - 1;
            return html`<div class="evcard"><div class="evcard__head"><span class="evcard__title">${i + 1}. ${stageLabelOf(occ)}</span>${cur ? html`<span class="tag tag--accent">Текущий этап</span>` : ''}</div>
                <div class="muted">Статус проставлен: ${fmtStampFull(occ.enteredAt)}</div>
                ${occ.events.length ? occ.events.map((ev, j) => { const { text, status } = eventText(ev, cur && j === occ.events.length - 1 && !ev.actualDate); return html`<div class="plan-row"><span class="mono">${text}</span><span class="tag ${ev.actualDate ? (ev.matchedPlan === false ? 'tag--warn' : 'tag--ok') : ''}">${status}</span></div>`; }) : html`<p class="muted">Плановая дата не указана.</p>`}
                ${occ.note ? html`<div class="callout callout--warn callout--flush"><div class="callout__text">Заметка: ${occ.note}</div></div>` : ''}</div>`;
        })}</div>`, 'История пуста.');
    }

    /* --- Действия карточки (демонстрация) --- */
    TAB_HANDLERS.pt = (v) => {
        const id = location.hash.slice(2).split('?')[0].split('/')[1];
        history.replaceState(null, '', `#/patient/${id}?tab=${v}`);
        render();
    };
    const openPtTab = (id, tab) => TAB_HANDLERS.pt(tab);

    function promptFactDate({ title, text, date, time }) {
        return new Promise((resolve) => {
            S.confirmResolve = resolve;   // Esc и «Отмена» — отказ (null через settleConfirm(false))
            openModal(modalShell(title, '', html`<p class="confirm__text">${text}</p>
                <div class="form__row"><div class="field"><label class="field__label" for="pfdDate">Дата</label><input class="input" type="date" id="pfdDate" value="${date}" max="${D.today}"><span class="field__error" id="pfdErr" hidden>Укажите дату.</span></div>
                <div class="field"><label class="field__label" for="pfdTime">Время</label><input class="input" type="time" id="pfdTime" value="${time}"></div></div>`,
                html`<span class="spacer"></span><button class="btn" data-confirm="no">Отмена</button><button class="btn btn--primary" data-pt="fact-save">Сохранить</button>`));
        });
    }

    async function changeStage(p, newStage, decision) {
        if (p.stage === newStage) return;
        const occ = p.history[p.history.length - 1];
        let update = null;
        const last = occ.events.length ? occ.events[occ.events.length - 1] : null;
        if (!last || !last.actualDate) {
            const lt = last || { date: '', time: '' };
            if (!lt.date) update = { actualDate: D.today, actualTime: '', matchedPlan: null };
            else if (lt.date === D.today) update = { actualDate: D.today, actualTime: lt.time || '', matchedPlan: true };
            else {
                const ans = await promptFactDate({ title: 'Фактическая дата события', text: `Плановая дата этапа «${STAGE_NAME(occ.stage)}»: ${lt.date}${lt.time ? ' ' + lt.time : ''}. Укажите фактическую дату события.`, date: D.today, time: lt.time || '' });
                if (!ans) return;
                update = { actualDate: ans.date, actualTime: ans.time || '', matchedPlan: ans.date === lt.date && (ans.time || '') === (lt.time || '') };
            }
        }
        if (decision) update = update || { actualDate: last.actualDate, actualTime: last.actualTime, matchedPlan: last.matchedPlan };
        if (update) {
            if (decision) update.consiliumDecision = decision;
            if (last) Object.assign(last, update); else occ.events.push({ date: '', time: '', noPlan: false, ...update });
        }
        p.history.push({ stage: newStage, enteredAt: nowStamp(), events: [], note: '', checklist: {} });
        // Как в приложении: переход на завершающий этап отменяет ожидающие напоминания.
        if (D.TERMINAL.includes(newStage)) p.notifications.items.forEach(x => { if (x.status === 'pending') x.status = 'cancelled'; });
        touch(p);
        S.pt.sel[p.id] = `occ:${p.history.length - 1}`;
        render(); updateBell();
        toast(`Пациент переведён: ${STAGE_NAME(newStage)}.`);
    }

    async function advance(p, stage, decision) {
        const ok = await confirmDialog({ title: 'Перевести на следующий этап?', text: `Пациент будет переведён на этап «${STAGE_NAME(stage)}».${decision ? ` Решение ОК: ${D.DECISIONS[decision]}.` : ''}`, confirmLabel: 'Перевести', note: decision ? OK_NOTE : '' });
        if (ok) await changeStage(p, stage, decision);
    }

    function attentionModal(p) {
        openModal(modalShell('Добавить проблему', p.name, html`<form class="form" id="attForm" novalidate>
            <div class="field"><label class="field__label" for="attReason">Причина <span class="req">*</span></label><select class="select" id="attReason" aria-describedby="attReasonErr"><option value="">Выберите причину</option>${Object.entries(D.ATTENTION_REASONS).map(([k, v]) => html`<option value="${k}">${v}</option>`)}</select><span class="field__error" id="attReasonErr" hidden>Выберите причину.</span></div>
            <div class="field"><label class="field__label" for="attDate" id="attDateLabel">Дата события</label><input class="input" type="date" id="attDate" max="${D.today}" aria-describedby="attDateErr"><span class="field__error" id="attDateErr" hidden>Укажите дату события.</span></div>
            <div class="field"><label class="field__label" for="attComment">Комментарий</label><textarea class="textarea" id="attComment" rows="4" maxlength="800"></textarea></div></form>`,
            html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-pt="attention-save" data-id="${p.id}">Сохранить</button>`), (m) => {
            m.querySelector('#attReason').addEventListener('change', (e) => { m.querySelector('#attDateLabel').textContent = e.target.value === 'emergency_hospitalization' ? 'Дата события *' : 'Дата события'; });
        });
    }
    function attentionCloseModal(p, issueId) {
        openModal(modalShell('Закрыть проблему', p.name, html`<div class="field"><label class="field__label" for="attResolution">Комментарий к закрытию</label><textarea class="textarea" id="attResolution" rows="4" maxlength="800"></textarea></div>`,
            html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-pt="attention-close-save" data-id="${p.id}" data-issue="${issueId}">Закрыть</button>`));
    }
    const doctorHint = (id) => {
        const d = D.DOCTORS.find(x => x.id === id);
        if (!d) return 'Пациент попадёт в список «Несопоставленные лечащие врачи».';
        const dep = D.DEPARTMENTS.find(x => x.id === d.dept);
        return dep ? `Отделение врача: ${dep.name}.` : 'Врач не состоит ни в одном отделении — заведующие его пациентов не увидят.';
    };
    function doctorModal(p) {
        openModal(modalShell('Сменить лечащего врача', '', html`
            <p class="confirm__text">Текущий врач: ${p.doctorFull || 'не сопоставлен'}. Пациент: ${p.name}.</p>
            <div class="field"><label class="field__label" for="docSelect">Новый лечащий врач</label><select class="select" id="docSelect"><option value="">— Не сопоставлен —</option>${D.DOCTORS.map(d => html`<option value="${d.id}" ${raw(d.id === p.doctorId ? 'selected' : '')}>${d.name}</option>`)}</select>
                <span class="field__hint" id="docHint">${doctorHint(p.doctorId)}</span></div>
            <div class="callout callout--warn callout--flush"><div class="callout__text">Область доступа изменится немедленно: прежний врач и его заведующий потеряют доступ к пациенту, новый врач и его заведующий — получат.</div></div>`,
            html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-pt="doctor-save" data-id="${p.id}">Подтвердить смену</button>`), (m) => {
            m.querySelector('#docSelect').addEventListener('change', (e) => { m.querySelector('#docHint').textContent = doctorHint(e.target.value); });
        });
    }

    document.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-pt]');
        if (!b) return;
        const act = b.dataset.pt;
        if (act === 'fact-save') {
            const d = $('#pfdDate');
            if (!d.value) { $('#pfdErr').hidden = false; d.setAttribute('aria-invalid', 'true'); d.focus(); return; }
            const r = S.confirmResolve; S.confirmResolve = null;
            const ans = { date: d.value, time: $('#pfdTime').value };
            closeModal(); if (r) r(ans);
            return;
        }
        const p = patient(b.dataset.id);
        if (!p) return;
        if (act === 'tab') { openPtTab(p.id, b.dataset.tab); return; }
        if (act === 'copy') {
            try { await navigator.clipboard.writeText(summaryText(p)); toast('Сводка скопирована в буфер обмена.'); }
            catch (err) { toast('Не удалось скопировать. Текст сводки доступен во вкладке «Обзор».'); }
            return;
        }
        if (!canEdit(p) && !['delete', 'doctor', 'doctor-save'].includes(act)) return;   // как сервер: запись только в своей области
        if (act === 'node') { const k = b.dataset.key; S.pt.sel[p.id] = S.pt.sel[p.id] === k ? undefined : k; openPtTab(p.id, 'events'); return; }
        if (act === 'next-node' || act === 'advance') { await advance(p, b.dataset.stage, b.dataset.decision || ''); return; }
        if (act === 'attention-add') { attentionModal(p); return; }
        if (act === 'attention-save') {
            const reason = $('#attReason'), date = $('#attDate');
            const badReason = !reason.value, badDate = reason.value === 'emergency_hospitalization' && !date.value;
            setInvalid(reason, badReason); $('#attReasonErr').hidden = !badReason;
            setInvalid(date, badDate); $('#attDateErr').hidden = !badDate;
            if (badReason || badDate) { (badReason ? reason : date).focus(); return; }
            p.attentionIssues.unshift({ id: `att-${Date.now()}`, reasonCode: reason.value, status: 'open', eventDate: date.value, comment: $('#attComment').value.trim(), createdByName: R().user.short, createdAt: nowStamp() });
            touch(p); closeModal(); render(); updateBell(); toast('Проблема добавлена.');
            return;
        }
        if (act === 'attention-close') { attentionCloseModal(p, b.dataset.issue); return; }
        if (act === 'attention-close-save') {
            const x = p.attentionIssues.find(i => i.id === b.dataset.issue);
            Object.assign(x, { status: 'closed', closedByName: R().user.short, closedAt: nowStamp(), resolutionComment: $('#attResolution').value.trim() });
            touch(p); closeModal(); render(); updateBell(); toast('Проблема закрыта.');
            return;
        }
        if (act === 'event-add') { p.history[Number(b.dataset.occ)].events.push({ date: '', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false }); touch(p); render(); return; }
        if (act === 'event-delete') { p.history[Number(b.dataset.occ)].events.splice(Number(b.dataset.ev), 1); touch(p); render(); return; }
        if (act === 'consent') {
            const granted = b.dataset.v === 'granted';
            Object.assign(p.notifications, { consent: b.dataset.v, smsEnabled: granted, consentAt: nowStamp(), consentBy: R().user.short });
            if (!granted) p.notifications.items.forEach(x => { if (x.status === 'pending') x.status = 'cancelled'; });
            render(); toast(granted ? 'Согласие зафиксировано.' : 'Согласие отозвано.');
            return;
        }
        if (act === 'sms-create') {
            const code = $('#smsTpl').value, at = $('#smsAt').value;
            if (!code || !at) { toast('Укажите шаблон, дату и время отправки.'); return; }
            const dup = p.notifications.items.some(x => x.status === 'pending' && x.template === code && x.scheduledAt === at);
            if (!dup) p.notifications.items.push({ id: `sms-${Date.now()}`, template: code, scheduledAt: at, source: 'manual', status: 'pending', reason: '' });
            render(); toast(dup ? 'Такое напоминание уже ожидает отправки.' : 'Напоминание создано (демо: SMS не будет отправлено).');
            return;
        }
        if (act === 'delete' && R().perms.deletePatients) {
            const ok = await confirmDialog({ title: 'Удалить пациента?', text: `Запись «${p.name}» и вся её история будут удалены безвозвратно.`, confirmLabel: 'Удалить', danger: true });
            if (!ok) return;
            // Как сервер: при записях консилиума карточку удалить нельзя (409).
            if (S.consilium.some(c => c.patientId === p.id)) { toast('Не удалось удалить: У пациента есть записи онкологического консилиума; карточку удалить нельзя.'); return; }
            S.patients = S.patients.filter(x => x.id !== p.id);
            location.hash = '#/patients'; updateBell();
            toast('Пациент удалён (демо: только в этой вкладке, после перезагрузки вернётся).');
            return;
        }
        if (act === 'doctor' && R().perms.changeTreatingDoctor) { doctorModal(p); return; }
        if (act === 'doctor-save' && R().perms.changeTreatingDoctor) {
            const d = D.DOCTORS.find(x => x.id === $('#docSelect').value);
            Object.assign(p, d ? { doctorId: d.id, doctor: d.short, doctorFull: d.name, dept: d.dept } : { doctorId: null, doctor: '', doctorFull: '', dept: null });
            touch(p); closeModal(); render(); updateBell(); toast('Лечащий врач изменён. Область доступа обновлена.');
        }
    });

    // Поля сохраняются на change, как в приложении (updatePatientField / updateEvent / note / checklist).
    document.addEventListener('change', (e) => {
        const t = e.target;
        const pid = (location.hash.match(/^#\/patient\/([^?/]+)/) || [])[1];
        const p = pid && patient(pid);
        if (!p || !canEdit(p) || !t.closest('#view')) return;
        if (t.dataset.pf) {
            const f = t.dataset.pf; let v = t.value;
            if (f === 'name' && !v.trim()) { toast('Не удалось сохранить: name must not be empty'); t.value = p.name; return; }   // текст ошибки сервера как есть
            if (f === 'age') v = Math.min(150, Math.max(0, parseInt(v, 10) || 0));
            p[f] = v; touch(p); render();
            return;
        }
        if (t.matches('[data-pgi]')) {
            const res = $('#pfPgi').value, date = $('#pfPgiDate').value, hint = $('#pfPgiHint');
            if (res && !date) { hint.textContent = 'Укажите дату результата ПГИ.'; hint.classList.add('field__error'); $('#pfPgiDate').setAttribute('aria-invalid', 'true'); $('#pfPgiDate').focus(); return; }
            p.pgiResult = res; p.pgiResultDate = date; touch(p); render();
            return;
        }
        if (t.dataset.ev) {
            const [i, j, f] = t.dataset.ev.split(':');
            const ev = p.history[Number(i)].events[Number(j)] || (p.history[Number(i)].events[Number(j)] = { date: '', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false });
            ev[f] = t.value;
            if ((f === 'actualDate' || f === 'actualTime') && !ev.noPlan) ev.matchedPlan = ev.actualDate === ev.date && (ev.actualTime || '') === (ev.time || '');
            touch(p); render();
            return;
        }
        if (t.dataset.note !== undefined) { p.history[Number(t.dataset.note)].note = t.value; touch(p); return; }
        if (t.dataset.cl) { p.history[p.history.length - 1].checklist[t.dataset.cl] = t.checked; touch(p); render(); updateBell(); }
    });
    document.addEventListener('input', (e) => {
        if (!e.target.matches('[data-sms-preview]')) return;
        const pv = $('#smsPreview'); if (pv) pv.value = smsPreview($('#smsTpl').value, $('#smsAt').value);
    });
    // Выбор диагноза в комбобоксе вкладки «Данные» — сразу применяется, как change в приложении.
    ICD_ON_CHOOSE.pfIcd = (d) => {
        const pid = (location.hash.match(/^#\/patient\/([^?/]+)/) || [])[1];
        const p = pid && patient(pid);
        if (!p || !canEdit(p) || p.icd === d.code) return;
        p.diagnosisHistory.push({ changedAt: nowStamp(), oldDiagnosis: p.icd, newDiagnosis: d.code });
        p.icd = d.code; touch(p); render();
        toast(`Диагноз изменён: ${d.code}.`);
    };

    /* ===================== Рабочие экраны (этап 2) =====================
       Список пациентов и календарь повторяют приложение (getFiltered,
       renderStats, renderPatients, renderCalendar). «Рабочий день» и
       «Отделение» — предложения редизайна: в приложении таких экранов нет,
       поэтому в них только данные, которые приложение уже считает
       (внутренние уведомления, показатели и нагрузка из аналитики). */
    const proposalNote = (text) => html`<div class="demo-note">Предложение редизайна: ${text}</div>`;
    const pendingEvents = (p) => p.history.flatMap((occ, i) => occ.events.filter(ev => ev.date && !ev.actualDate).map(ev => ({ ...ev, occ, occIndex: i, p })));
    const lastEventDays = (p) => { const occ = p.history[p.history.length - 1]; const ev = occ && occ.events[occ.events.length - 1]; return ev && ev.date ? daysUntil(ev.date) : null; };
    const openIssues = (p) => p.attentionIssues.filter(x => x.status === 'open');
    const firstVisit = (p) => { const ev = p.history.filter(o => o.stage === 'primary-appointment').flatMap(o => o.events).find(e => e.actualDate); return ev ? `${ev.actualDate}T${ev.actualTime || '00:00'}` : ''; };
    const dateChip = (days, time) => html`<span class="tag ${dateChipTone(days)}">${dateChipText(days, time)}</span>`;

    /* --- Список пациентов --- */
    S.list = { stage: '', head: '', deadline: '', attention: '', reason: '', sort: 'updated-newest' };
    const LIST_DEADLINE = [['', 'Любой срок'], ['overdue', 'Просрочено'], ['0-3', 'Ближайшие 3 дня'], ['4-7', '4–7 дней'], ['7+', 'Более 7 дней'], ['no-date', 'Дата не назначена']];
    const LIST_ATTENTION = [['', 'Все пациенты'], ['active', 'Требуют внимания'], ['none', 'Без активных проблем']];
    const LIST_SORT = [['updated-newest', 'Сначала изменённые'], ['attention', 'Сначала требуют внимания'], ['newest', 'Сначала новые'], ['oldest', 'Сначала давние'], ['first-visit-oldest', 'Первый визит: сначала ранние'], ['first-visit-newest', 'Первый визит: сначала поздние'], ['name', 'По алфавиту']];
    const LIST_STAGE_ORDER = ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
    const listFilterCount = () => ['stage', 'head', 'deadline', 'attention', 'reason'].filter(k => S.list[k]).length;

    function filteredPatients(q) {
        const f = S.list;
        let list = scoped();
        if (!f.stage && !q) list = list.filter(p => !D.TERMINAL.includes(p.stage));   // как в приложении: завершённые скрыты без фильтра этапа и поиска
        if (f.stage) list = list.filter(p => p.stage === f.stage);
        if (f.head) list = list.filter(p => p.head === f.head);
        if (f.deadline) list = list.filter(p => {
            const d = lastEventDays(p);
            if (f.deadline === 'no-date') return d === null;
            if (d === null) return false;
            return f.deadline === 'overdue' ? d < 0 : f.deadline === '0-3' ? d >= 0 && d <= 3 : f.deadline === '4-7' ? d >= 4 && d <= 7 : d > 7;
        });
        // Внимание и причина в приложении фильтруются сервером (?attention=&attention_reason=); выбор причины включает «Требуют внимания».
        if (f.attention === 'active' || f.reason) list = list.filter(p => openIssues(p).length && (!f.reason || openIssues(p).some(x => x.reasonCode === f.reason)));
        if (f.attention === 'none') list = list.filter(p => !openIssues(p).length);
        if (q) list = list.filter(p => [p.name, p.policy, p.icd, p.doctor, p.head].some(v => (v || '').toLowerCase().includes(q)));
        const by = (s) => s || '';
        const name = (a, b) => a.name.localeCompare(b.name, 'ru');
        const sorters = {
            'updated-newest': (a, b) => by(b.updatedAt || b.createdAt).localeCompare(by(a.updatedAt || a.createdAt)),
            attention: (a, b) => openIssues(b).length - openIssues(a).length || name(a, b),
            newest: (a, b) => b.createdAt.localeCompare(a.createdAt),
            oldest: (a, b) => a.createdAt.localeCompare(b.createdAt),
            'first-visit-oldest': (a, b) => (firstVisit(a) || '9999').localeCompare(firstVisit(b) || '9999') || name(a, b),
            'first-visit-newest': (a, b) => (firstVisit(b) || '0000').localeCompare(firstVisit(a) || '0000') || name(a, b),
            name,
        };
        return list.slice().sort(sorters[f.sort] || sorters['updated-newest']);
    }

    const listFilterFields = (prefix) => {
        const heads = [...new Set(scoped().map(p => p.head).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ru'));
        const sel = (key, label, opts) => html`<div class="field"><label class="field__label" for="${prefix}-${key}">${label}</label><select class="select" id="${prefix}-${key}" data-list="${key}">${opts.map(([v, t]) => html`<option value="${v}" ${raw(S.list[key] === v ? 'selected' : '')}>${t}</option>`)}</select></div>`;
        return html`
            ${sel('stage', 'Этап', [['', 'Все этапы'], ...LIST_STAGE_ORDER.map(s => [s, D.STAGES[s]])])}
            ${sel('head', 'Заведующий', [['', 'Все заведующие'], ...heads.map(h => [h, h])])}
            ${sel('deadline', 'Срок', LIST_DEADLINE)}
            ${sel('attention', 'Внимание', LIST_ATTENTION)}
            ${sel('reason', 'Причина внимания', [['', 'Любая причина'], ...Object.entries(D.ATTENTION_REASONS)])}
            ${sel('sort', 'Сортировка', LIST_SORT)}`;
    };
    const readListFields = (root) => { root.querySelectorAll('[data-list]').forEach(s => { S.list[s.dataset.list] = s.value; }); if (S.list.reason) S.list.attention = 'active'; };

    function viewPatients(query) {
        const q = (query || '').toLowerCase().trim();
        const all = scoped();
        const list = filteredPatients(q);
        const readOnly = isReadOnly();
        const weekAgo = D.helpers.iso(D.helpers.addDays(parse(D.today), -7));
        const fresh = all.filter(p => p.createdAt.slice(0, 10) >= weekAgo).length;
        const inTreatment = all.filter(p => ['treatment', 'treatment-prep'].includes(p.stage)).length;
        const done = all.filter(p => p.stage === 'completed').length;
        const share = (n) => all.length ? `${(n / all.length * 100).toFixed(1).replace('.', ',')}% от всех` : 'нет данных';
        const unresolved = R().perms.changeTreatingDoctor ? all.filter(p => !p.doctorId).length : 0;
        const n = listFilterCount();
        const empty = !all.length
            ? (R().can.create ? 'Пациентов пока нет. Нажмите «Новый пациент», чтобы добавить первого.' : 'В вашей области видимости пока нет пациентов.')
            : 'По выбранным условиям никого не найдено. Измените фильтры или поиск.';
        return html`
            ${pagehead('Пациенты', ['doctor', 'chair'].includes(S.role) ? 'Пациенты, у которых вы лечащий врач' : S.role === 'head' ? 'Пациенты вашего отделения' : 'Все пациенты клиники',
                readOnly ? html`<span class="tag">Режим просмотра</span>` : html`${R().perms.importData ? html`<button class="btn" data-action="import">Импорт</button>` : ''}${R().can.export ? html`<button class="btn" data-action="export">Экспорт</button>` : ''}${R().can.create ? html`<button class="btn btn--primary" data-action="new-patient">Новый пациент</button>` : ''}`)}
            <div class="stack">
                ${unresolved ? html`<div class="callout callout--warn callout--flush callout--row"><div class="callout__text">Пациентов без сопоставленного лечащего врача: ${unresolved}. Они не видны врачам и заведующим отделений.</div><button class="btn btn--sm" data-href="#/structure">Перейти к сопоставлению</button></div>` : ''}
                ${metrics([
                    { label: 'Всего пациентов', value: all.length, note: all.length ? 'в работе и завершённые' : 'список пуст' },
                    { label: 'Новых за неделю', value: fresh, note: fresh ? 'добавлены за 7 дней' : 'новых нет' },
                    { label: 'На лечении', value: inTreatment, note: share(inTreatment) },
                    { label: 'Завершено', value: done, note: share(done) },
                ])}
                <section class="panel">
                    <div class="toolbar toolbar--list">
                        <input class="input" type="search" id="patientSearch" placeholder="Поиск по ФИО, полису ОМС, диагнозу, врачу…" value="${query || ''}" aria-label="Поиск пациентов">
                        <button class="btn list-filters-btn" data-list-sheet aria-label="Фильтры и сортировка, активных фильтров: ${n}">${n ? `Фильтры · ${n}` : 'Фильтры'}</button>
                    </div>
                    <div class="list-filters">${listFilterFields('lf')}${n ? html`<div class="list-filters__reset"><button class="btn btn--sm btn--ghost" data-list-reset>Сбросить</button></div>` : ''}</div>
                    <div class="panel__meta list-count">${S.demo === 'ok' ? `Показано: ${list.length}${!S.list.stage && !q ? ' · завершённые маршруты скрыты, пока не выбран этап или поиск' : ''}` : ''}</div>
                    ${stateful(list.length ? listTable(list) : html`<div class="empty"><div class="empty__title">${empty}</div></div>`, { rows: 8, emptyTitle: 'Пациентов пока нет', emptyText: empty })}
                </section>
            </div>`;
    }
    function listTable(list) {
        return html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Пациент</th><th>Этап лечения</th><th>Ближайший срок</th><th>Последняя активность</th><th>Заведующий</th></tr></thead><tbody>${list.map(p => {
            const issues = openIssues(p).length;
            const ev = pendingEvents(p).filter(e => e.occIndex === p.history.length - 1).pop();
            return html`<tr class="is-link" data-href="#/patient/${p.id}" tabindex="0" aria-label="Открыть карточку: ${p.name}">
                <td><div class="person"><span class="avatar avatar--${p.priority}" aria-hidden="true">${initials(p.name)}</span><div><div class="cell-title">${p.name}</div><div class="cell-sub">${p.age} лет · <span class="mono">${p.icd || 'диагноз не указан'}</span></div>${issues ? html`<span class="tag tag--warn">Требует внимания${issues > 1 ? ` · ${issues}` : ''}</span>` : ''}</div></div></td>
                <td data-label="Этап"><div class="cell-title cell-title--sm">${stageLabelOf(p.history[p.history.length - 1])}</div><div class="progress progress--sm"><progress max="100" value="${progress(p)}" aria-label="Пройдено этапов маршрута"></progress><span class="mono">${progress(p)}%</span></div></td>
                <td data-label="Срок">${ev ? html`${dateChip(daysUntil(ev.date), ev.time)}` : html`<span class="muted">—</span>`}</td>
                <td class="muted" data-label="Изменён">${fmtStamp(p.updatedAt || p.createdAt)}</td>
                <td data-label="Заведующий">${p.head || '—'}</td>
            </tr>`;
        })}</tbody></table></div>`;
    }

    /* --- Расписание (в приложении — «Календарь»: плановые события без факта) --- */
    S.calWeek = 0;
    function viewCalendar() {
        const evs = scoped().flatMap(pendingEvents).sort((a, b) => (a.date + (a.time || '')).localeCompare(b.date + (b.time || '')));
        const sub = `Ближайшие плановые события ${S.role === 'doctor' ? 'ваших пациентов' : S.role === 'head' ? 'пациентов отделения' : 'по всем пациентам'}`;
        const views = html`<div class="segmented" role="group" aria-label="Вид"><button aria-pressed="${S.calView !== 'week'}" data-action="calview" data-v="list">Список</button><button aria-pressed="${S.calView === 'week'}" data-action="calview" data-v="week">Неделя</button></div>`;
        if (S.calView !== 'week') {
            return html`${pagehead('Расписание', sub, views)}
                <section class="panel"><div class="panel__head"><div><h2 class="panel__title">Плановые события</h2><div class="panel__meta">Все события без отметки о выполнении, включая просроченные · как «Календарь» в приложении</div></div></div>
                ${stateful(evs.length ? html`<ul class="cal-list">${evs.map(e => html`<li>
                    <div class="cal-list__who"><div class="cell-title">${e.p.name}</div><div class="cell-sub">${stageLabelOf(e.occ)} · ${e.p.head || 'заведующий не указан'}</div></div>
                    <div class="cal-list__when">${dateChip(daysUntil(e.date), e.time)}<span class="mono muted">${fmtNum(e.date)}</span></div>
                    <button class="btn btn--sm" data-href="#/patient/${e.p.id}?tab=events" aria-label="Открыть карточку: ${e.p.name}">Открыть</button></li>`)}</ul>`
                    : html`<div class="empty"><div class="empty__title">Плановых событий нет.</div></div>`, { rows: 8, emptyTitle: 'Плановых событий нет.' })}</section>`;
        }
        const base = D.helpers.addDays(parse(D.today), S.calWeek * 7);
        const monday = new Date(base); monday.setDate(base.getDate() - ((base.getDay() + 6) % 7));
        const days = Array.from({ length: 5 }, (_, k) => D.helpers.iso(D.helpers.addDays(monday, k)));
        const hours = [8, 9, 10, 11, 12, 13, 14, 15, 16];
        const inWeek = evs.filter(e => days.includes(e.date));
        const slot = (day, h) => inWeek.filter(e => e.date === day && (h === null ? !e.time : Number(e.time.split(':')[0]) === h));
        const cell = (day, h) => { const items = slot(day, h); return html`<div class="week__cell ${day === D.today ? 'is-today' : ''}">${items.slice(0, 2).map(e => html`<button class="ev ${e.date < D.today ? 'ev--overdue' : ''}" data-href="#/patient/${e.p.id}?tab=events" title="${e.time || 'без времени'} · ${stageLabelOf(e.occ)} · ${e.p.name}"><span class="mono">${e.time || '—'}</span> ${e.p.name.split(' ')[0]} · ${stageLabelOf(e.occ)}</button>`)}${items.length > 2 ? html`<span class="week__more">ещё ${items.length - 2}</span>` : ''}</div>`; };
        const noTime = days.some(d => slot(d, null).length);
        return html`${pagehead('Расписание', sub, views)}
            ${proposalNote('в приложении календарь — только список. Недельная сетка показывает те же плановые события без факта; выполненные события в ней не показываются.')}
            <section class="panel">
                <div class="panel__head"><div><h2 class="panel__title">${fmtDate(days[0])} — ${fmtDate(days[4])}</h2><div class="panel__meta">${inWeek.length} ${plural(inWeek.length, 'событие', 'события', 'событий')} на неделе</div></div>
                    <div class="segmented" role="group" aria-label="Неделя"><button data-cal-week="-1" aria-label="Предыдущая неделя">‹</button><button data-cal-week="0" aria-pressed="${S.calWeek === 0}">Текущая</button><button data-cal-week="1" aria-label="Следующая неделя">›</button></div></div>
                ${stateful(html`<div class="table-wrap"><div class="week"><div class="week__corner"></div>${days.map(d => { const x = parse(d); return html`<div class="week__head ${d === D.today ? 'is-today' : ''}">${WEEKDAYS[x.getDay()]}<b>${x.getDate()} ${MONTHS[x.getMonth()]}</b></div>`; })}
                    ${noTime ? html`<div class="week__hour">без времени</div>${days.map(d => cell(d, null))}` : ''}
                    ${hours.map(h => html`<div class="week__hour">${String(h).padStart(2, '0')}:00</div>${days.map(d => cell(d, h))}`)}</div></div>`, { rows: 8, emptyTitle: 'Плановых событий нет.' })}
            </section>`;
    }

    /* --- Рабочий день (предложение): данные внутренних уведомлений врача --- */
    function viewToday() {
        const mine = scoped();
        const items = notifItems();
        const count = (t) => items.filter(n => n.type === t).length;
        const needs = mine.filter(p => p.overdue || openIssues(p).length).sort((a, b) => b.overdue - a.overdue || openIssues(b).length - openIssues(a).length);
        const todayEvents = mine.flatMap(pendingEvents).filter(e => e.date === D.today).sort((a, b) => (a.time || '').localeCompare(b.time || ''));
        const soon = mine.flatMap(pendingEvents).filter(e => { const d = daysUntil(e.date); return d >= 1 && d <= 3; }).sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time));
        const myCons = S.consilium.filter(c => c.createdBy === R().user.id && !c.archived);
        const d = parse(D.today);
        const evRow = (e) => html`<li class="is-link" data-href="#/patient/${e.p.id}?tab=events" tabindex="0"><span class="queue__time">${e.date === D.today ? (e.time || '—') : fmtDate(e.date)}</span><div><div class="queue__title">${e.p.name}</div><div class="queue__sub">${stageLabelOf(e.occ)}</div></div>${dateChip(daysUntil(e.date), '')}</li>`;
        return html`
            ${pagehead(`Добрый день, ${R().user.name.split(' ')[1]}`, `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]} — ваши пациенты и задачи на сегодня`,
                html`${R().can.book ? html`<button class="btn" data-href="#/consilium">Записать на консилиум</button>` : ''}${R().can.create ? html`<button class="btn btn--primary" data-action="new-patient">Новый пациент</button>` : ''}`, 'Рабочий день')}
            ${proposalNote('в приложении отдельного экрана нет. Здесь собраны те же сведения, что во внутренних уведомлениях (просрочено, на сегодня, скоро, требует внимания) и в записях консилиума, созданных вами.')}
            <div class="stack">
                ${metrics([
                    { label: 'Просрочено', value: count('overdue'), tone: 'danger', note: 'события без отметки о выполнении' },
                    { label: 'На сегодня', value: count('today'), note: 'плановые события' },
                    { label: 'Скоро срок', value: count('upcoming'), note: 'через 1–3 дня' },
                    { label: 'Требует внимания', value: count('attention'), tone: 'warn', note: 'открытые отметки', href: '#/patients' },
                ])}
                <div class="grid grid--main-side">
                    <section class="panel">
                        <div class="panel__head"><div><h2 class="panel__title">Требуют действия</h2><div class="panel__meta">Просроченные события и открытые отметки внимания</div></div><button class="btn btn--sm btn--ghost" data-list-preset="attention">Все требующие внимания</button></div>
                        <div class="panel__body--flush">${stateful(needs.length ? patientRows(needs.slice(0, 8), ['patient', 'stage', 'status']) : html`<div class="empty"><div class="empty__title">Всё под контролем</div><div>Нет пациентов с просрочками и открытыми отметками.</div></div>`, { emptyTitle: 'Всё под контролем', emptyText: 'Нет пациентов с просрочками и открытыми отметками.' })}</div>
                    </section>
                    <div class="stack">
                        <section class="panel"><div class="panel__head"><h2 class="panel__title">На сегодня</h2><button class="btn btn--sm btn--ghost" data-href="#/calendar">Расписание</button></div>
                            ${stateful(html`<ul class="queue">${todayEvents.length ? todayEvents.map(evRow) : html`<li><span></span><div class="queue__sub">На сегодня плановых событий нет</div></li>`}</ul>`, { rows: 3 })}</section>
                        <section class="panel"><div class="panel__head"><h2 class="panel__title">Скоро срок</h2><span class="panel__meta">1–3 дня</span></div>
                            ${stateful(html`<ul class="queue">${soon.length ? soon.slice(0, 6).map(evRow) : html`<li><span></span><div class="queue__sub">Ближайших сроков нет</div></li>`}</ul>`, { rows: 3 })}</section>
                        <section class="panel"><div class="panel__head"><h2 class="panel__title">Мои записи на консилиум</h2></div>
                            ${stateful(html`<ul class="queue">${myCons.length ? myCons.map(c => html`<li class="is-link" data-href="#/consilium" tabindex="0"><span class="queue__time">${c.time}</span><div><div class="queue__title">${c.patientName}</div><div class="queue__sub">${fmtNum(c.date)} · ${D.GROUPS[c.group]}</div></div>${consTag(c)}</li>`) : html`<li><span></span><div class="queue__sub">Записей нет</div></li>`}</ul>`, { rows: 3 })}</section>
                    </div>
                </div>
            </div>`;
    }

    /* --- Отделение (предложение): показатели и нагрузка из аналитики приложения --- */
    function viewDepartment() {
        const list = scoped();
        const dept = D.DEPARTMENTS.find(d => d.id === R().user.dept);
        const active = list.filter(p => !D.TERMINAL.includes(p.stage));
        const incompleteChecklist = (p) => p.stage === 'exam' && p.checklist.some(c => !c.done);
        // analytics/service.py workload: активные пациенты, просрочки, незавершённые обязательные чек-листы, уведомления.
        const groups = [...D.DOCTORS.filter(d => d.dept === dept.id), ...(list.some(p => !p.doctorId) ? [{ id: null, name: 'Не назначен' }] : [])].map(d => {
            const ps = active.filter(p => (p.doctorId || null) === d.id);
            const sms = list.filter(p => (p.doctorId || null) === d.id).flatMap(p => p.notifications.items);
            return { id: d.id, name: d.name, active: ps.length, overdue: ps.filter(p => p.overdue).length, checklists: ps.filter(incompleteChecklist).length, pending: sms.filter(x => x.status === 'pending').length, failed: sms.filter(x => x.status === 'failed').length };
        }).filter(g => g.active || g.id !== null);
        const byStage = LIST_STAGE_ORDER.map(code => ({ code, label: D.STAGES[code], value: list.filter(p => p.stage === code).length }));
        const queue = list.flatMap(p => openIssues(p).map(x => ({ p, x }))).sort((a, b) => a.x.createdAt.localeCompare(b.x.createdAt));
        const headDecision = queue.filter(q => q.x.reasonCode === 'department_head_decision_required').length;
        return html`
            ${pagehead(dept.name, 'Пациенты отделения, нагрузка врачей и открытые отметки внимания', html`<button class="btn" data-href="#/analytics">Аналитика</button>`, 'Рабочее пространство заведующего')}
            ${proposalNote('в приложении отдельного экрана нет. Показатели и нагрузка — те же, что в «Аналитике» (в пределах области доступа), очередь — список «Требуют внимания».')}
            <div class="stack">
                ${metrics([
                    { label: 'Всего доступных пациентов', value: list.length, href: '#/patients' },
                    { label: 'Активные', value: active.length },
                    { label: 'Явно требуют внимания', value: list.filter(p => openIssues(p).length).length, tone: 'warn' },
                    { label: 'Пациенты с просроченными событиями', value: list.filter(p => p.overdue).length, tone: 'danger' },
                ])}
                <div class="grid grid--main-side">
                    <section class="panel">
                        <div class="panel__head"><div><h2 class="panel__title">Нагрузка врачей</h2><div class="panel__meta">Активные пациенты отделения</div></div></div>
                        <div class="panel__body--flush">${stateful(html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Врач</th><th class="num">Активные пациенты</th><th class="num">Просрочки</th><th class="num">Чек-листы</th><th class="num">Ожидают отправки</th><th class="num">Ошибки отправки</th></tr></thead><tbody>
                            ${groups.map(g => html`<tr><td><div class="cell-title">${g.name}</div></td><td class="num" data-label="Активные">${g.active}</td><td class="num" data-label="Просрочки">${g.overdue ? html`<span class="status status--danger">${g.overdue}</span>` : '—'}</td><td class="num" data-label="Чек-листы">${g.checklists || '—'}</td><td class="num" data-label="Ожидают">${g.pending || '—'}</td><td class="num" data-label="Ошибки">${g.failed || '—'}</td></tr>`)}
                        </tbody></table></div>`, { rows: 3, emptyTitle: 'Нет данных по нагрузке в текущей области доступа.' })}</div>
                        <div class="panel__foot">Как в аналитике приложения: просрочки — пациенты с просроченными событиями; чек-листы — незавершённые обязательные; SMS — по пациентам врача, в прототипе синтетические.</div>
                    </section>
                    <section class="panel">
                        <div class="panel__head"><h2 class="panel__title">Пациенты по этапам маршрута</h2></div>
                        ${stateful(byStage.some(b => b.value) ? html`<ul class="stage-bars">${byStage.map(b => html`<li><button class="stage-bars__row" data-list-preset="stage" data-v="${b.code}" aria-label="${b.label}: ${b.value}. Показать в списке"><span class="stage-bars__label">${b.label}</span><progress max="${Math.max(1, list.length)}" value="${b.value}"></progress><span class="mono">${b.value}</span></button></li>`)}</ul>` : html`<div class="empty"><div class="empty__title">Пациентов по этапам нет.</div></div>`, { rows: 6 })}
                    </section>
                </div>
                <section class="panel">
                    <div class="panel__head panel__head--wrap"><div><h2 class="panel__title">Требуют внимания</h2><div class="panel__meta">Открытые отметки по пациентам отделения, сначала давние</div></div>
                        ${headDecision ? html`<button class="btn btn--sm" data-list-preset="reason" data-v="department_head_decision_required">Требуется решение заведующего · ${headDecision}</button>` : ''}</div>
                    <div class="panel__body--flush">${stateful(queue.length ? html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Пациент</th><th>Причина</th><th>Добавлена</th><th>Лечащий врач</th><th>Этап</th></tr></thead><tbody>
                        ${queue.map(({ p, x }) => html`<tr class="is-link" data-href="#/patient/${p.id}" tabindex="0" aria-label="Открыть карточку: ${p.name}"><td><div class="cell-title">${p.name}</div><div class="cell-sub mono">${p.icd}</div></td>
                            <td data-label="Причина"><span class="status status--warn">${D.ATTENTION_REASONS[x.reasonCode]}</span></td>
                            <td data-label="Добавлена" class="muted">${fmtStamp(x.createdAt)} · ${x.createdByName}</td><td data-label="Врач">${p.doctor || '—'}</td><td data-label="Этап">${D.STAGES[p.stage]}</td></tr>`)}
                    </tbody></table></div>` : html`<div class="empty"><div class="empty__title">Открытых отметок нет</div></div>`, { emptyTitle: 'Открытых отметок нет', emptyText: '' })}</div>
                </section>
            </div>`;
    }

    // Фильтры списка: на широком экране — сразу, на телефоне — листом «Фильтры и сортировка».
    document.addEventListener('change', (e) => {
        const s = e.target.closest('.list-filters [data-list]');
        if (!s) return;
        S.list[s.dataset.list] = s.value;
        if (s.dataset.list === 'reason' && s.value) S.list.attention = 'active';
        render();
        const again = document.getElementById(s.id); if (again) again.focus({ preventScroll: true });
    });
    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t.closest('[data-list-reset]')) { Object.assign(S.list, { stage: '', head: '', deadline: '', attention: '', reason: '' }); render(); return; }
        if (t.closest('[data-list-sheet]')) {
            openSheet({ title: 'Фильтры и сортировка', body: html`<div class="form">${listFilterFields('ls')}</div>`,
                onApply: (sh) => { readListFields(sh); render(); },
                onReset: (sh) => { sh.querySelectorAll('[data-list]').forEach(x => { x.value = x.dataset.list === 'sort' ? S.list.sort : ''; }); } });
            return;
        }
        const pr = t.closest('[data-list-preset]');
        if (pr) {
            Object.assign(S.list, { stage: '', head: '', deadline: '', attention: '', reason: '' });
            if (pr.dataset.listPreset === 'attention') S.list.attention = 'active';
            if (pr.dataset.listPreset === 'stage') S.list.stage = pr.dataset.v;
            if (pr.dataset.listPreset === 'reason') { S.list.reason = pr.dataset.v; S.list.attention = 'active'; }
            location.hash = '#/patients';
            return;
        }
        const w = t.closest('[data-cal-week]');
        if (w) { S.calWeek = w.dataset.calWeek === '0' ? 0 : S.calWeek + Number(w.dataset.calWeek); render(); }
    });

    /* ---------- Навигация и отрисовка ---------- */
    const currentSection = () => (location.hash || R().home).slice(2).split('?')[0].split('/')[0];
    function renderNav(active) {
        const allowed = new Set(R().nav);
        $('#nav').innerHTML = String(html`${NAV.map(g => {
            const items = g.items.filter(i => allowed.has(i.id));
            if (!items.length) return '';
            return html`<div class="nav__group"><div class="nav__label">${g.group}</div>${items.map(i => html`<button class="nav__item ${i.legacy ? 'nav__item--legacy' : ''}" data-href="#/${i.id}" ${raw(active === i.id ? 'aria-current="page"' : '')}>${icon(i.id)}<span>${i.label}</span>${i.id === 'today' || i.id === 'department' ? html`<span class="nav__count">${scoped().filter(p => p.overdue || p.attention.length).length}</span>` : ''}${i.id === 'messages' && unreadTotal() ? html`<span class="nav__badge" aria-label="Непрочитанных сообщений: ${unreadTotal()}">${unreadTotal()}</span>` : ''}</button>`)}</div>`;
        })}`);
        const u = R().user;
        $('#userAvatar').textContent = initials(u.name);
        $('#userName').textContent = u.short;
        $('#userRole').textContent = u.roleLabel;
        $('#readOnlyChip').hidden = !isReadOnly();   // как readOnlyChip в приложении
        updateBell();
    }

    function render() {
        const hash = location.hash || R().home;
        const [path, qs] = hash.slice(2).split('?');
        const [section, id] = path.split('/');
        const params = new URLSearchParams(qs || '');
        const allowed = R().nav.includes(section) || section === 'patient' || section === 'kit';
        let out;
        if (!allowed) out = denied(section);
        else if (section === 'today') out = viewToday();
        else if (section === 'department') out = viewDepartment();
        else if (section === 'patients') out = viewPatients(params.get('q'));
        else if (section === 'patient') out = viewPatient(id, params);
        else if (section === 'calendar') out = viewCalendar();
        else if (section === 'consilium') out = viewConsilium();
        else if (section === 'analytics') out = viewAnalytics();
        else if (section === 'messages') out = viewMessages(id, params);
        else if (section === 'kit') out = viewKit();
        else out = viewLegacy(section);
        renderNav(section === 'patient' ? 'patients' : section);
        $('#view').innerHTML = String(out);
        document.title = `${NAV_LABEL[section] || (section === 'kit' ? 'Компоненты прототипа' : 'Карточка пациента')} — Система планирования`;
        if (S.notif && S.notif.open) renderNotif();
        bindView(params);
        const log = $('#msgLog'); if (log) log.scrollTop = log.scrollHeight;
        const tl = $('.tl'), cur = tl && tl.querySelector('.tl__step--current');
        if (tl && cur && tl.scrollWidth > tl.clientWidth) tl.scrollLeft = cur.parentElement.offsetLeft - tl.offsetLeft - 16;
    }

    function bindView() {
        const s = $('#patientSearch');
        if (s) s.addEventListener('input', () => { const pos = s.selectionStart; history.replaceState(null, '', `#/patients${s.value ? `?q=${encodeURIComponent(s.value)}` : ''}`); render(); const n = $('#patientSearch'); n.focus(); n.setSelectionRange(pos, pos); });
    }

    document.addEventListener('click', (e) => {
        const close = e.target.closest('[data-close]');
        if (close || e.target.id === 'scrim') { closeModal(); return; }
        const act = e.target.closest('[data-action]');
        if (act && ACTIONS[act.dataset.action] && act.type !== 'checkbox') { e.preventDefault(); ACTIONS[act.dataset.action](act); return; }
        const link = e.target.closest('[data-href]');
        if (link) {
            if (location.hash === link.dataset.href) render(); else location.hash = link.dataset.href;
            $('#app').classList.remove('is-nav-open'); $('#menuBtn').setAttribute('aria-expanded', 'false');
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (!$('#modal').hidden) { settleConfirm(false); closeModal(); }
            else if (!$('#sheet').hidden) closeSheet();
            else if (S.notif.open) closeNotif();
            else if (S.ai.open) closeAI();
            $('#app').classList.remove('is-nav-open');
        }
        if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('tr[data-href], li[data-href]')) { e.preventDefault(); location.hash = e.target.dataset.href; }
        if (e.key === '/' && !e.target.closest('input, textarea, select')) { e.preventDefault(); $('#globalSearch').focus(); }
    });
    $('#globalSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') { location.hash = `#/patients?q=${encodeURIComponent(e.target.value)}`; e.target.blur(); } });
    $('#roleSelect').value = S.role;
    $('#roleSelect').addEventListener('change', (e) => {
        S.role = e.target.value;
        try { localStorage.setItem('proto-role', S.role); } catch (err) { /* ok */ }
        // Как при выходе в приложении: история и контекст другого пользователя не переносятся.
        S.ai.messages = []; S.ai.ctx = null; S.ai.loading = false;
        closeNotif(false); closeSheet();
        location.hash = R().home; render(); renderAI();
    });
    $('#stateSelect').addEventListener('change', (e) => { S.demo = e.target.value; render(); renderAI(); });
    $('#themeBtn').addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('proto-theme', next); } catch (err) { /* ok */ }
    });
    $('#menuBtn').addEventListener('click', () => { const open = $('#app').classList.toggle('is-nav-open'); $('#menuBtn').setAttribute('aria-expanded', String(open)); });
    window.addEventListener('hashchange', () => { render(); $('#view').focus({ preventScroll: true }); window.scrollTo(0, 0); });

    if (!location.hash) location.hash = R().home; else render();
})();
