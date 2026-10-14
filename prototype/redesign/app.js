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
        recorded: ['Записан', 'tag'], in_review: ['На рассмотрении', 'tag tag--accent'], needs_revision: ['На доработке у врача', 'tag tag--warn'],
        resubmitted: ['Исправлено врачом', 'tag tag--accent'], signed: ['Подписано', 'tag tag--ok'],
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
                ${cols.includes('doctor') ? html`<td data-label="Врач">${p.doctor}</td>` : ''}
                ${cols.includes('updated') ? html`<td class="num muted" data-label="Изменён">${fmtDate(p.updated)}</td>` : ''}
            </tr>`)}</tbody></table></div>`;

    function viewToday() {
        const mine = scoped();
        const needs = mine.filter(p => p.overdue || p.attention.length).sort((a, b) => b.overdue - a.overdue);
        const todayEvents = mine.flatMap(p => p.events.filter(e => e.date === D.today).map(e => ({ ...e, p }))).sort((a, b) => a.time.localeCompare(b.time));
        const myCons = S.consilium.filter(c => mine.some(p => p.id === c.patientId));
        const d = parse(D.today);
        return html`
            ${pagehead(`Добрый день, ${R().user.name.split(' ')[1]}`, `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]} — ваши пациенты и задачи на сегодня`,
                R().can.write ? html`<button class="btn" data-href="#/consilium">Записать на консилиум</button><button class="btn btn--primary" data-action="new-patient">Новый пациент</button>` : '', 'Рабочий день')}
            <div class="stack">
                ${metrics([
                    { label: 'Мои пациенты', value: mine.length, note: `${mine.filter(p => !['completed', 'dispensary-observation'].includes(p.stage)).length} в работе`, href: '#/patients' },
                    { label: 'Требуют внимания', value: needs.length, tone: needs.length ? 'warn' : '', note: 'просрочки и отметки' },
                    { label: 'Просрочено событий', value: mine.reduce((n, p) => n + p.overdue, 0), tone: 'danger', note: 'без отметки о выполнении' },
                    { label: 'Сегодня', value: todayEvents.length, note: `${plural(todayEvents.length, 'событие', 'события', 'событий')} по плану` },
                ])}
                <div class="grid grid--main-side">
                    <section class="panel">
                        <div class="panel__head"><div><h2 class="panel__title">Требуют действия</h2><div class="panel__meta">Сначала — просроченные события</div></div><button class="btn btn--sm btn--ghost" data-href="#/patients">Все пациенты</button></div>
                        <div class="panel__body--flush">${stateful(patientRows(needs.slice(0, 8), ['patient', 'stage', 'status']), { emptyTitle: 'Всё под контролем', emptyText: 'Нет пациентов с просрочками и открытыми отметками.' })}</div>
                    </section>
                    <div class="stack">
                        <section class="panel">
                            <div class="panel__head"><h2 class="panel__title">Сегодня</h2><button class="btn btn--sm btn--ghost" data-href="#/calendar">Расписание</button></div>
                            ${stateful(html`<ul class="queue">${todayEvents.length ? todayEvents.map(e => html`
                                <li class="is-link" data-href="#/patient/${e.p.id}"><span class="queue__time">${e.time}</span><div><div class="queue__title">${e.title}</div><div class="queue__sub">${e.p.name}</div></div>${e.done ? html`<span class="status status--ok">Выполнено</span>` : ''}</li>`) : html`<li><span></span><div class="queue__sub">На сегодня событий нет</div></li>`}</ul>`, { rows: 4 })}
                        </section>
                        <section class="panel">
                            <div class="panel__head"><h2 class="panel__title">Мои записи на консилиум</h2></div>
                            ${stateful(html`<ul class="queue">${myCons.length ? myCons.map(c => html`
                                <li class="is-link" data-href="#/consilium"><span class="queue__time">${c.time}</span><div><div class="queue__title">${c.patientName}</div><div class="queue__sub">${D.GROUPS[c.group]}</div></div>${consTag(c)}</li>`) : html`<li><span></span><div class="queue__sub">Записей нет</div></li>`}</ul>`, { rows: 3 })}
                        </section>
                    </div>
                </div>
            </div>`;
    }

    function viewDepartment() {
        const list = scoped();
        const dept = D.DEPARTMENTS.find(d => d.id === R().user.dept);
        const queue = list.filter(p => p.attention.length || p.overdue).sort((a, b) => b.overdue - a.overdue);
        const doctors = D.DOCTORS.filter(d => d.dept === dept.id).map(d => {
            const ps = list.filter(p => p.doctorId === d.id);
            return { ...d, total: ps.length, active: ps.filter(p => !['completed', 'dispensary-observation'].includes(p.stage)).length,
                attention: ps.filter(p => p.attention.length).length, overdue: ps.reduce((n, p) => n + p.overdue, 0),
                cons: S.consilium.filter(c => ps.some(p => p.id === c.patientId)).length };
        });
        const byStage = D.STAGE_ORDER.map(code => ({ label: D.STAGES[code], value: list.filter(p => p.stage === code).length }));
        const max = Math.max(1, ...byStage.map(b => b.value));
        return html`
            ${pagehead(dept.name, 'Пациенты отделения, нагрузка врачей и вопросы, требующие решения', html`<button class="btn" data-href="#/analytics">Аналитика</button>`, 'Рабочее пространство заведующего')}
            <div class="stack">
                ${metrics([
                    { label: 'Пациентов в отделении', value: list.length, href: '#/patients' },
                    { label: 'Требуется решение заведующего', value: list.filter(p => p.attention.some(a => a.code === 'department_head_decision_required')).length, tone: 'warn' },
                    { label: 'Пациенты с просрочками', value: list.filter(p => p.overdue).length, tone: 'danger' },
                    { label: 'Записей на консилиум сегодня', value: S.consilium.filter(c => list.some(p => p.id === c.patientId)).length, href: '#/consilium' },
                ])}
                <div class="grid grid--main-side">
                    <section class="panel">
                        <div class="panel__head"><div><h2 class="panel__title">Нагрузка врачей</h2><div class="panel__meta">Только пациенты отделения</div></div></div>
                        <div class="panel__body--flush">${stateful(html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Врач</th><th class="num">Пациенты</th><th class="num">В работе</th><th class="num">Внимание</th><th class="num">Просрочки</th><th class="num">Консилиум</th></tr></thead><tbody>
                            ${doctors.map(d => html`<tr><td><div class="cell-title">${d.name}</div></td><td class="num" data-label="Пациенты">${d.total}</td><td class="num" data-label="В работе">${d.active}</td><td class="num" data-label="Внимание">${d.attention || '—'}</td><td class="num" data-label="Просрочки">${d.overdue ? html`<span class="status status--danger">${d.overdue}</span>` : '—'}</td><td class="num" data-label="Консилиум">${d.cons || '—'}</td></tr>`)}
                        </tbody></table></div>`, { rows: 3 })}</div>
                    </section>
                    <section class="panel">
                        <div class="panel__head"><h2 class="panel__title">По этапам маршрута</h2></div>
                        ${stateful(html`<div class="bars">${byStage.map(b => html`<div class="bars__row"><span class="bars__label">${b.label}</span><span class="bars__track"><span class="bars__fill" style="width:${(b.value / max) * 100}%"></span></span><span class="bars__value">${b.value}</span></div>`)}</div>`, { rows: 6 })}
                    </section>
                </div>
                <section class="panel">
                    <div class="panel__head"><div><h2 class="panel__title">Очередь внимания</h2><div class="panel__meta">Отметки врачей и просрочки по отделению</div></div></div>
                    <div class="panel__body--flush">${stateful(html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Пациент</th><th>Причина</th><th>С какого дня</th><th>Лечащий врач</th><th>Этап</th></tr></thead><tbody>
                        ${queue.map(p => html`<tr class="is-link ${p.overdue ? 'row-mark' : ''}" data-href="#/patient/${p.id}" tabindex="0"><td><div class="cell-title">${p.name}</div><div class="cell-sub mono">${p.icd}</div></td>
                            <td data-label="Причина">${p.attention.length ? html`<span class="status status--warn">${D.ATTENTION_REASONS[p.attention[0].code]}</span>` : html`<span class="status status--danger">Просрочено событий: ${p.overdue}</span>`}</td>
                            <td data-label="С" class="muted">${p.attention.length ? relDay(p.attention[0].since) : '—'}</td><td data-label="Врач">${p.doctor}</td><td data-label="Этап">${D.STAGES[p.stage]}</td></tr>`)}
                    </tbody></table></div>`, { emptyTitle: 'Очередь пуста', emptyText: 'Нет открытых отметок внимания и просрочек.' })}</div>
                </section>
            </div>`;
    }

    function viewPatients(query) {
        const q = (query || '').toLowerCase();
        let list = scoped();
        if (q) list = list.filter(p => [p.name, p.policy, p.icd, p.doctor].some(v => v.toLowerCase().includes(q)));
        if (S.stageFilter) list = list.filter(p => p.stage === S.stageFilter);
        if (S.onlyAttention) list = list.filter(p => p.overdue || p.attention.length);
        const readOnly = isReadOnly();
        return html`
            ${pagehead('Пациенты', ['doctor', 'chair'].includes(S.role) ? 'Пациенты, у которых вы лечащий врач' : S.role === 'head' ? 'Пациенты вашего отделения' : 'Все пациенты клиники',
                readOnly ? html`<span class="tag">Режим просмотра</span>` : html`${R().can.export ? html`<button class="btn" data-action="export">Экспорт</button>` : ''}${R().can.create ? html`<button class="btn btn--primary" data-action="new-patient">Новый пациент</button>` : ''}`)}
            <section class="panel">
                <div class="panel__head" style="padding-bottom:var(--s3)"><div class="panel__meta">${S.demo === 'ok' ? `${list.length} ${plural(list.length, 'пациент', 'пациента', 'пациентов')}` : ''}</div></div>
                <div class="toolbar">
                    <input class="input" type="search" id="patientSearch" placeholder="Поиск по ФИО, полису, МКБ-10" value="${query || ''}" aria-label="Поиск пациентов">
                    <select class="select" id="stageFilter" aria-label="Этап маршрута"><option value="">Все этапы</option>${Object.entries(D.STAGES).map(([k, v]) => html`<option value="${k}" ${raw(S.stageFilter === k ? 'selected' : '')}>${v}</option>`)}</select>
                    <label class="check"><input type="checkbox" id="onlyAttention" ${raw(S.onlyAttention ? 'checked' : '')}> Только требующие внимания</label>
                </div>
                ${stateful(list.length ? patientRows(list) : html`<div class="empty"><div class="empty__title">Ничего не найдено</div><div>Измените запрос или сбросьте фильтры.</div></div>`, { rows: 8, emptyTitle: 'Пациентов пока нет', emptyText: readOnly ? 'Пациенты появятся после внесения их врачами.' : 'Добавьте первого пациента кнопкой «Новый пациент».' })}
            </section>`;
    }

    function viewPatient(id) {
        const p = patient(id);
        if (!p || !R().scope(p)) return html`${pagehead('Карточка недоступна', '')}<section class="panel"><div class="error"><div class="error__title">Пациент вне вашей области доступа</div><div>Карточки видны лечащему врачу, заведующему отделением и руководству.</div><button class="btn btn--sm" data-href="#/patients">К списку пациентов</button></div></section>`;
        const curIdx = p.stage === 'dispensary-observation' ? 7 : stageIdx(p.stage);
        const cons = S.consilium.find(c => c.patientId === p.id);
        const can = R().can;
        const done = p.checklist.filter(c => c.done).length;
        return html`
            <div style="margin-bottom:var(--s4)"><a href="#/patients">← Пациенты</a></div>
            <section class="panel" style="margin-bottom:var(--s5)">
                <div class="panel__body" style="padding-top:var(--s5)">
                    <div class="ptitle">
                        <div><h1 style="font-size:var(--t-2xl);font-weight:600;line-height:1.2">${p.name}</h1>
                        <div class="facts"><span><b>${p.age}</b> лет</span><span>Полис ОМС <b class="mono">${p.policy}</b></span><span>Диагноз <b class="mono">${p.icd}</b> · ${p.nosologyLabel}</span><span>Лечащий врач <b>${p.doctor}</b></span></div></div>
                        <div class="pagehead__actions">${convFor(p.id) ? html`<a class="btn" href="#/messages/${convFor(p.id).id}?back=patient/${p.id}">${icon('messages')}<span>Переписка с пациентом</span>${convFor(p.id).unread ? html`<span class="msg-badge" aria-label="Непрочитанных: ${convFor(p.id).unread}">${convFor(p.id).unread}</span>` : ''}</a>` : ''}<button class="btn btn--ghost" data-ai-ask="${p.id}" aria-label="Спросить ИИ-помощника по пациенту: ${p.name}">Спросить помощника</button>${can.write ? html`<button class="btn" data-action="attention" data-id="${p.id}">Отметить внимание</button><button class="btn btn--primary" data-action="advance" data-id="${p.id}">Перевести на этап</button>` : html`<span class="tag">Режим просмотра</span>`}</div>
                    </div>
                </div>
                <div class="route" aria-label="Маршрут лечения">${D.STAGE_ORDER.map((s, k) => {
                    const h = p.history.find(x => x.stage === s);
                    const cls = k < curIdx ? 'is-done' : k === curIdx ? 'is-current' : '';
                    return html`<div class="route__step ${cls}"><div class="route__name">${D.STAGES[s]}</div><div class="route__date">${h ? fmtNum(h.at) : ''}</div></div>`;
                })}</div>
            </section>
            <div class="grid grid--main-side">
                <div class="stack">
                    ${p.attention.length || p.overdue ? html`<section class="panel"><div class="panel__head"><h2 class="panel__title">Требует внимания</h2></div>
                        ${p.overdue ? html`<div class="callout"><div class="callout__title">Просрочено событий: ${p.overdue}</div><div class="callout__text">Плановые даты прошли без отметки о выполнении.</div></div>` : ''}
                        ${p.attention.map(a => html`<div class="callout callout--warn"><div class="callout__title">${D.ATTENTION_REASONS[a.code]}</div><div class="callout__text">${a.comment} · отмечено ${relDay(a.since)}</div></div>`)}
                    </section>` : ''}
                    <section class="panel">
                        <div class="panel__head"><div><h2 class="panel__title">Чек-лист этапа</h2><div class="panel__meta">${D.STAGES[p.stage]} · выполнено ${done} из ${p.checklist.length}</div></div></div>
                        ${stateful(html`<ul class="checklist">${p.checklist.map((c, k) => html`<li class="${c.done ? 'is-done' : ''}"><input type="checkbox" id="ck-${k}" data-action="check" data-id="${p.id}" data-k="${k}" ${raw(c.done ? 'checked' : '')} ${raw(can.write ? '' : 'disabled')}><label for="ck-${k}"><span>${c.title}</span></label></li>`)}</ul>`, { rows: 6 })}
                    </section>
                    <section class="panel">
                        <div class="panel__head"><h2 class="panel__title">Лента событий</h2></div>
                        ${stateful(html`<ul class="feed">${p.events.map(e => html`<li><span class="feed__date">${fmtNum(e.date)}</span><div><div>${e.title}</div><div class="muted" style="font-size:var(--t-sm)">${e.time}</div></div>${e.done ? html`<span class="status status--ok">Выполнено</span>` : e.date < D.today ? html`<span class="status status--danger">Просрочено</span>` : html`<span class="status">${relDay(e.date)}</span>`}</li>`)}</ul>`, { rows: 4 })}
                    </section>
                    <section class="panel">
                        <div class="panel__head"><h2 class="panel__title">Комментарии</h2></div>
                        <div class="panel__body">${p.comments.length ? p.comments.map(c => html`<div style="margin-bottom:var(--s3)"><div class="muted" style="font-size:var(--t-xs)">${c.author} · ${fmtNum(c.at)}</div><div>${c.text}</div></div>`) : html`<div class="muted">Комментариев нет.</div>`}
                        ${can.write ? html`<div class="field" style="margin-top:var(--s3)"><textarea class="textarea" placeholder="Комментарий для коллег" aria-label="Новый комментарий"></textarea><div><button class="btn btn--sm" data-action="comment">Добавить</button></div></div>` : ''}</div>
                    </section>
                </div>
                <div class="stack">
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Сведения</h2></div>
                        <dl class="dl"><dt>Локализация</dt><dd>${p.localization}</dd><dt>Нозология</dt><dd>${p.nosologyLabel}</dd><dt>Телефон</dt><dd class="mono">${p.phone}</dd><dt>Отделение</dt><dd>${D.DEPARTMENTS.find(d => d.id === p.dept).name}</dd><dt>Приоритет</dt><dd>${p.priority === 'high' ? html`<span class="tag tag--warn">Высокий</span>` : 'Обычный'}</dd></dl>
                    </section>
                    <section class="panel"><div class="panel__head"><h2 class="panel__title">Онкологический консилиум</h2></div>
                        <div class="panel__body">${cons ? html`<div style="display:grid;gap:var(--s2)"><div>${fmtNum(cons.date)}, <span class="mono">${cons.time}</span> · ${D.GROUPS[cons.group]}</div><div>${consTag(cons)}</div>
                            ${cons.status === 'signed' ? html`<div class="muted" style="font-size:var(--t-sm)">${D.TREATMENTS[cons.treatment]}${cons.scheme ? ` · ${cons.scheme.name}` : ''}<br>Этап маршрута на момент решения: ${D.STAGES[cons.routeStage]}</div>` : ''}</div>`
                            : html`<div class="muted">Записи нет.</div>${R().can.book ? html`<button class="btn btn--sm" style="margin-top:var(--s3)" data-href="#/consilium">Записать</button>` : ''}`}</div>
                    </section>
                </div>
            </div>`;
    }

    function viewCalendar() {
        const list = scoped();
        const base = parse(D.today);
        const monday = new Date(base); monday.setDate(base.getDate() - ((base.getDay() + 6) % 7));
        const days = Array.from({ length: 5 }, (_, k) => D.helpers.addDays(monday, k));
        const hours = [8, 9, 10, 11, 12, 13, 14, 15, 16];
        const evs = list.flatMap(p => p.events.map(e => ({ ...e, p })));
        const cell = (day, h) => {
            const key = D.helpers.iso(day);
            const items = evs.filter(e => e.date === key && Number(e.time.split(':')[0]) === h);
            return html`<div class="week__cell ${key === D.today ? 'is-today' : ''}">${items.slice(0, 2).map(e => html`<button class="ev ${e.done ? 'ev--done' : e.date < D.today ? 'ev--overdue' : ''}" data-href="#/patient/${e.p.id}" title="${e.time} · ${e.title} · ${e.p.name}"><span class="mono">${e.time}</span> ${e.p.name.split(' ')[0]} · ${e.title}</button>`)}${items.length > 2 ? html`<span class="muted" style="font-size:11px;padding-left:4px">ещё ${items.length - 2}</span>` : ''}</div>`;
        };
        const weekEvents = evs.filter(e => days.some(d => D.helpers.iso(d) === e.date));
        return html`
            ${pagehead('Расписание', `Плановые события ${S.role === 'doctor' ? 'ваших пациентов' : S.role === 'head' ? 'пациентов отделения' : 'всех пациентов'} на неделю`, html`<div class="segmented" role="group" aria-label="Вид"><button aria-pressed="${S.calView !== 'list'}" data-action="calview" data-v="week">Неделя</button><button aria-pressed="${S.calView === 'list'}" data-action="calview" data-v="list">Список</button></div>`)}
            <section class="panel">
                <div class="panel__head"><div><h2 class="panel__title">${fmtDate(D.helpers.iso(days[0]))} — ${fmtDate(D.helpers.iso(days[4]))}</h2><div class="panel__meta">${weekEvents.length} ${plural(weekEvents.length, 'событие', 'события', 'событий')} · <span class="status status--danger" style="font-size:inherit">просрочено</span> · <span class="status status--accent" style="font-size:inherit">запланировано</span></div></div></div>
                ${stateful(S.calView === 'list'
                    ? html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th>Дата</th><th>Время</th><th>Событие</th><th>Пациент</th><th>Статус</th></tr></thead><tbody>${weekEvents.sort((a, b) => (a.date + a.time).localeCompare(b.date + b.time)).map(e => html`<tr class="is-link" data-href="#/patient/${e.p.id}"><td class="mono">${fmtNum(e.date)}</td><td class="mono" data-label="Время">${e.time}</td><td data-label="Событие">${e.title}</td><td data-label="Пациент">${e.p.name}</td><td data-label="Статус">${e.done ? html`<span class="status status--ok">Выполнено</span>` : e.date < D.today ? html`<span class="status status--danger">Просрочено</span>` : html`<span class="status status--accent">Запланировано</span>`}</td></tr>`)}</tbody></table></div>`
                    : html`<div class="table-wrap"><div class="week"><div class="week__corner"></div>${days.map(d => html`<div class="week__head ${D.helpers.iso(d) === D.today ? 'is-today' : ''}">${WEEKDAYS[d.getDay()]}<b>${d.getDate()} ${MONTHS[d.getMonth()]}</b></div>`)}
                        ${hours.map(h => html`<div class="week__hour">${String(h).padStart(2, '0')}:00</div>${days.map(d => cell(d, h))}`)}</div></div>`, { rows: 8, emptyTitle: 'Событий на неделе нет' })}
            </section>`;
    }

    function viewConsilium() {
        const can = R().can;
        if (!can.consilium) return denied('consilium');
        const group = S.consGroup || 'thoracic';
        const rows = D.slots.map(t => ({ t, c: S.consilium.find(c => c.group === group && c.time === t) }));
        const all = S.consilium;
        const counts = {
            total: all.length, review: all.filter(c => c.status === 'in_review').length,
            revision: all.filter(c => c.status === 'needs_revision').length, signed: all.filter(c => c.status === 'signed').length,
        };
        const isMine = (c) => c.createdBy === R().user.id;
        const action = (c) => {
            if (can.decide) {
                if (c.status === 'signed') return html`<button class="btn btn--sm btn--ghost" data-action="decide" data-id="${c.id}">Открыть</button>`;
                if (c.status === 'needs_revision') return html`<span class="muted" style="font-size:var(--t-sm)">Ожидает врача</span>`;
                return html`<button class="btn btn--sm ${c.status === 'in_review' ? 'btn--primary' : ''}" data-action="decide" data-id="${c.id}">${c.status === 'in_review' ? 'Продолжить' : 'Рассмотреть'}</button>`;
            }
            if (isMine(c) && ['recorded', 'needs_revision', 'resubmitted'].includes(c.status)) return html`<button class="btn btn--sm" data-action="book" data-id="${c.id}">Изменить</button>`;
            return isMine(c) ? html`<span class="muted" style="font-size:var(--t-sm)">Моя запись</span>` : '';
        };
        const d = parse(D.today);
        return html`
            ${pagehead('Онкологический консилиум', can.decide ? 'Записи всех врачей на выбранный день — рассмотрение и подписание решений' : 'Свободные и занятые слоты; записать можно своих пациентов',
                can.decide ? html`<button class="btn" data-action="registry">Реестры (XLSX)</button>` : html`<button class="btn btn--primary" data-action="book">Записать пациента</button>`,
                `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`)}
            ${S.role === 'developer' ? html`<div class="demo-note">Расхождение, не согласовано: сервер разрешает разработчику управлять сеткой, реестрами и аналитикой консилиума (manageConsilium), а действующий интерфейс показывает эти функции только председателю. Прототип повторяет действующий интерфейс; это не продуктовое решение.</div>` : ''}
            <div class="stack">
                ${metrics([
                    { label: 'Записано на день', value: counts.total },
                    { label: 'На рассмотрении', value: counts.review },
                    { label: 'Ждут исправлений врача', value: counts.revision, tone: counts.revision ? 'warn' : '' },
                    { label: 'Решения подписаны', value: counts.signed },
                ])}
                <section class="panel slots">
                    <div class="panel__head"><div><h2 class="panel__title">Сетка расписания</h2><div class="panel__meta">${D.GROUPS[group]} группа · слоты по 15 минут</div></div>
                        <select class="select" style="width:auto" id="groupSelect" aria-label="Консилиумная группа">${Object.entries(D.GROUPS).map(([k, v]) => html`<option value="${k}" ${raw(k === group ? 'selected' : '')}>${v}</option>`)}</select></div>
                    <div class="panel__body--flush">${stateful(html`<div class="table-wrap"><table class="table table--cards"><thead><tr><th style="width:72px">Время</th><th>Пациент</th><th>Лечащий врач</th><th>Статус</th><th class="num actions"><span class="sr-only">Действие</span></th></tr></thead><tbody>
                        ${rows.map(({ t, c }) => c ? html`<tr><td class="mono">${t}</td><td data-label="Пациент"><div class="cell-title">${c.patientName}</div><div class="cell-sub"><span class="mono">${c.icd}</span> · ${c.comment}</div></td><td class="nowrap" data-label="Врач">${c.doctor}</td><td data-label="Статус">${consTag(c)}</td><td class="num actions">${action(c)}</td></tr>`
                            : html`<tr><td class="mono slot-free">${t}</td><td class="slot-free" colspan="3">Свободно</td><td class="num actions">${can.book ? html`<button class="btn btn--sm btn--ghost" data-action="book" data-time="${t}">Записать</button>` : ''}</td></tr>`)}
                    </tbody></table></div>`, { rows: 10, emptyTitle: 'На этот день слоты не открыты', emptyText: 'Председатель открывает слоты на будущие недели.' })}</div>
                </section>
            </div>`;
    }

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
                    { label: 'Ожидают решения', value: S.consilium.filter(c => ['recorded', 'in_review', 'resubmitted'].includes(c.status)).length },
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

    function decisionModal(id) {
        const c = S.consilium.find(x => x.id === id);
        const signed = c.status === 'signed';
        const started = c.status === 'in_review';
        const history = c.revisions.length ? html`<div><div class="form__section" style="padding:0 0 6px">История возвратов</div><ul class="history">${c.revisions.map(r => html`<li><div class="history__meta">Раунд ${r.round} · ${r.author} · ${fmtNum(r.at)}</div><div>${r.text}</div></li>`)}</ul></div>` : '';
        const body = signed
            ? html`<div class="callout callout--info" style="margin:0"><div class="callout__title">Решение подписано и не изменяется</div><div class="callout__text">${D.TREATMENTS[c.treatment]}${c.scheme ? ` · ${c.scheme.name}` : ''}. Этап маршрута на момент решения: ${D.STAGES[c.routeStage]}.</div></div>${history}`
            : html`
                ${started ? '' : html`<div class="callout callout--warn" style="margin:0"><div class="callout__title">Рассмотрение ещё не начато</div><div class="callout__text">Подписать решение или вернуть запись можно после начала рассмотрения.</div></div>`}
                ${history}
                <dl class="dl" style="padding:0;grid-template-columns:120px 1fr"><dt>Диагноз</dt><dd><span class="mono">${c.icd}</span> · ${c.localization}</dd><dt>Участники</dt><dd>${[c.needsSurgeon && 'Хирург', c.needsRadiotherapist && 'Радиотерапевт'].filter(Boolean).join(', ') || '—'}</dd><dt>Комментарий врача</dt><dd>${c.comment}</dd></dl>
                <fieldset ${raw(started ? '' : 'disabled')} style="border:0;padding:0;margin:0;display:grid;gap:var(--s4)">
                    <div class="field"><span class="field__label">Назначенное лечение <span class="req">*</span></span>
                        <div class="decision-grid">${Object.entries(D.TREATMENTS).map(([k, v]) => html`<label class="choice"><input type="radio" name="treatment" value="${k}"> ${v}</label>`)}</div>
                        <span class="field__error" id="treatErr" hidden>Выберите назначенное лечение.</span></div>
                    <div class="field" id="schemeField" hidden><label class="field__label" for="scheme">Схема лечения <span class="req">*</span></label>
                        <select class="select" id="scheme"><option value="">Выберите схему</option>${D.SCHEMES.map(s => html`<option value="${s.code}">${s.name} (${s.code})</option>`)}</select>
                        <span class="field__error" id="schemeErr" hidden>Для химиотерапии нужна схема лечения.</span></div>
                    <div style="display:flex;gap:var(--s5)"><label class="check"><input type="checkbox" id="goc"> ГОК</label><label class="check"><input type="checkbox" id="goc65"> ГОК 65+</label></div>
                </fieldset>`;
        const foot = signed ? html`<button class="btn" data-close>Закрыть</button>`
            : started ? html`<button class="btn btn--danger" data-action="return" data-id="${c.id}">Вернуть на доработку</button><span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-action="sign" data-id="${c.id}">Подписать решение</button>`
            : html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-action="start" data-id="${c.id}">Начать рассмотрение</button>`;
        openModal(modalShell(signed ? 'Решение консилиума' : 'Рассмотрение записи', html`${c.patientName} · ${fmtNum(c.date)}, ${c.time} · ${D.GROUPS[c.group]}`, body, foot), (m) => {
            m.querySelectorAll('input[name="treatment"]').forEach(r => r.addEventListener('change', () => { m.querySelector('#schemeField').hidden = r.value !== 'chemotherapy' || !r.checked; m.querySelector('#treatErr').hidden = true; }));
        });
    }

    function returnModal(id) {
        const c = S.consilium.find(x => x.id === id);
        openModal(modalShell('Вернуть на доработку', c.patientName, html`
            <div class="field"><label class="field__label" for="retComment">Комментарий врачу <span class="req">*</span></label>
            <textarea class="textarea" id="retComment" placeholder="Что нужно уточнить или приложить"></textarea>
            <span class="field__hint">Текст сохраняется в истории возвратов и не попадает в журнал аудита.</span><span class="field__error" id="retErr" hidden>Укажите, что нужно исправить.</span></div>`,
            html`<span class="spacer"></span><button class="btn" data-action="decide" data-id="${c.id}">Назад</button><button class="btn btn--danger" data-action="confirm-return" data-id="${c.id}">Вернуть врачу</button>`));
    }

    function bookModal(opts) {
        const existing = opts.id ? S.consilium.find(c => c.id === opts.id) : null;
        const booked = new Set(S.consilium.map(c => c.patientId));
        const options = scoped().filter(p => !booked.has(p.id) || (existing && existing.patientId === p.id));
        const group = S.consGroup || 'thoracic';
        const free = D.slots.filter(t => !S.consilium.some(c => c.group === group && c.time === t) || (existing && existing.time === t));
        openModal(modalShell(existing ? 'Изменение записи на консилиум' : 'Запись на онкологический консилиум', `${D.GROUPS[group]} группа · ${fmtNum(D.today)}`, html`
            ${existing && existing.revisions.length ? html`<ul class="history">${existing.revisions.map(r => html`<li><div class="history__meta">Возврат · раунд ${r.round} · ${r.author}</div><div>${r.text}</div></li>`)}</ul>` : ''}
            <form class="form" id="bookForm" novalidate>
                <div class="form__section">Пациент и слот</div>
                <div class="field"><label class="field__label" for="bkPatient">Пациент <span class="req">*</span></label>
                    <select class="select" id="bkPatient" ${raw(existing ? 'disabled' : '')}><option value="">Выберите пациента</option>${options.map(p => html`<option value="${p.id}" ${raw(existing && existing.patientId === p.id ? 'selected' : '')}>${p.name} · ${p.icd}</option>`)}</select>
                    <span class="field__hint">Показаны ваши пациенты без записи на консилиум.</span><span class="field__error" id="bkPatientErr" hidden>Выберите пациента.</span></div>
                <div class="form__row">
                    <div class="field"><label class="field__label" for="bkTime">Время <span class="req">*</span></label><select class="select" id="bkTime">${free.map(t => html`<option ${raw(t === (existing ? existing.time : opts.time) ? 'selected' : '')}>${t}</option>`)}</select></div>
                    <div class="field"><label class="field__label" for="bkPolicy">Полис ОМС</label><input class="input mono" id="bkPolicy" readonly value="${existing ? existing.policy : ''}" placeholder="Подставляется из карточки"></div>
                </div>
                <div class="form__section">Сведения для консилиума</div>
                <div class="field"><label class="field__label" for="bkComment">Комментарий</label><textarea class="textarea" id="bkComment" placeholder="Что обсудить на консилиуме">${existing ? existing.comment : ''}</textarea></div>
                <div style="display:flex;gap:var(--s5);flex-wrap:wrap"><label class="check"><input type="checkbox" id="bkSurgeon" ${raw(!existing || existing.needsSurgeon ? 'checked' : '')}> Нужен хирург</label><label class="check"><input type="checkbox" id="bkRadio" ${raw(existing && existing.needsRadiotherapist ? 'checked' : '')}> Нужен радиотерапевт</label></div>
            </form>`,
            html`<span class="spacer"></span><button class="btn" data-close>Отмена</button><button class="btn btn--primary" data-action="save-booking" data-id="${existing ? existing.id : ''}">${existing ? 'Сохранить и отправить' : 'Записать'}</button>`), (m) => {
            m.querySelector('#bkPatient').addEventListener('change', (e) => { const p = patient(e.target.value); m.querySelector('#bkPolicy').value = p ? p.policy : ''; m.querySelector('#bkPatientErr').hidden = true; e.target.removeAttribute('aria-invalid'); });
        });
    }

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
        registry: () => toast('Реестры XLSX выгружаются в приложении; в прототипе — только кнопка.'),
        calview: (el) => { S.calView = el.dataset.v; render(); },
        period: (el) => { S.period = el.dataset.v; render(); },
        decide: (el) => decisionModal(el.dataset.id),
        book: (el) => bookModal({ id: el.dataset.id, time: el.dataset.time }),
        start: (el) => { const c = S.consilium.find(x => x.id === el.dataset.id); c.status = 'in_review'; render(); decisionModal(c.id); toast('Рассмотрение начато.'); },
        return: (el) => returnModal(el.dataset.id),
        'confirm-return': (el) => {
            const ta = $('#retComment');
            if (invalid(ta, 'retErr', !ta.value.trim())) { ta.focus(); return; }
            const c = S.consilium.find(x => x.id === el.dataset.id);
            c.revisions.push({ round: c.revisions.length + 1, author: R().user.short, at: D.today, text: ta.value.trim() });
            c.status = 'needs_revision'; closeModal(); render(); toast('Запись возвращена врачу на доработку.');
        },
        sign: (el) => {
            const m = $('#modal'), t = m.querySelector('input[name="treatment"]:checked');
            $('#treatErr').hidden = !!t;
            if (!t) return;
            const scheme = $('#scheme');
            if (t.value === 'chemotherapy' && invalid(scheme, 'schemeErr', !scheme.value)) { scheme.focus(); return; }
            const c = S.consilium.find(x => x.id === el.dataset.id);
            Object.assign(c, { status: 'signed', treatment: t.value, scheme: D.SCHEMES.find(s => s.code === scheme.value) || null, goc: $('#goc').checked, goc65: $('#goc65').checked, routeStage: patient(c.patientId).stage });
            closeModal(); render(); toast('Решение консилиума подписано.');
        },
        'save-booking': (el) => {
            const sel = $('#bkPatient');
            if (!el.dataset.id && invalid(sel, 'bkPatientErr', !sel.value)) { sel.focus(); return; }
            const time = $('#bkTime').value;
            if (el.dataset.id) {
                const c = S.consilium.find(x => x.id === el.dataset.id);
                Object.assign(c, { time, comment: $('#bkComment').value || c.comment, needsSurgeon: $('#bkSurgeon').checked, needsRadiotherapist: $('#bkRadio').checked, status: c.status === 'needs_revision' ? 'resubmitted' : c.status });
                toast('Запись обновлена.');
            } else {
                const p = patient(sel.value);
                S.consilium.push({ id: `c-${Date.now()}`, patientId: p.id, patientName: p.name, policy: p.policy, icd: p.icd, localization: p.localization, group: S.consGroup || 'thoracic', date: D.today, time, doctor: p.doctor, status: 'recorded', createdBy: R().user.id, needsSurgeon: $('#bkSurgeon').checked, needsRadiotherapist: $('#bkRadio').checked, comment: $('#bkComment').value || 'Обсудить тактику лечения.', revisions: [] });
                toast('Пациент записан на консилиум.');
            }
            closeModal(); render();
        },
        'save-patient': () => {
            const name = $('#npName'), pol = $('#npPolicy'), nos = $('#npNos');
            const bad = [invalid(name, 'npNameErr', name.value.trim().split(/\s+/).length < 2), invalid(pol, 'npPolicyErr', !!pol.value && !/^\d{16}$/.test(pol.value)), invalid(nos, 'npNosErr', !nos.value)];
            if (bad.some(Boolean)) { $('#modal').querySelector('[aria-invalid="true"]').focus(); return; }
            toast('В прототипе карточка не сохраняется — форма показывает поведение и проверки.'); closeModal();
        },
        check: (el) => { const p = patient(el.dataset.id); p.checklist[Number(el.dataset.k)].done = el.checked; render(); },
        attention: () => toast('Отметка внимания открывается тем же окном, что и в приложении (в прототипе не реализована).'),
        advance: () => toast('Перевод на этап выполняется в приложении; в прототипе показан маршрут.'),
        comment: () => toast('Комментарий в прототипе не сохраняется.'),
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
    const aiRef = (p, sub) => ({ id: p.id, title: p.name, sub: sub || `${D.STAGES[p.stage]} · ${p.doctor}` });

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
    function confirmDialog({ title, text, confirmLabel = 'Подтвердить', danger = false }) {
        return new Promise((resolve) => {
            S.confirmResolve = resolve;
            openModal(modalShell(title, '', html`<p class="confirm__text">${text}</p>`,
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
        else if (section === 'patient') out = viewPatient(id);
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
    }

    function bindView() {
        const s = $('#patientSearch');
        if (s) s.addEventListener('input', () => { const pos = s.selectionStart; history.replaceState(null, '', `#/patients${s.value ? `?q=${encodeURIComponent(s.value)}` : ''}`); render(); const n = $('#patientSearch'); n.focus(); n.setSelectionRange(pos, pos); });
        const st = $('#stageFilter'); if (st) st.addEventListener('change', () => { S.stageFilter = st.value; render(); });
        const oa = $('#onlyAttention'); if (oa) oa.addEventListener('change', () => { S.onlyAttention = oa.checked; render(); });
        const g = $('#groupSelect'); if (g) g.addEventListener('change', () => { S.consGroup = g.value; render(); });
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
    document.addEventListener('change', (e) => { const act = e.target.closest('[data-action="check"]'); if (act) ACTIONS.check(act); });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (!$('#modal').hidden) { settleConfirm(false); closeModal(); }
            else if (!$('#sheet').hidden) closeSheet();
            else if (S.notif.open) closeNotif();
            else if (S.ai.open) closeAI();
            $('#app').classList.remove('is-nav-open');
        }
        if (e.key === 'Enter' && e.target.matches('tr[data-href]')) location.hash = e.target.dataset.href;
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
