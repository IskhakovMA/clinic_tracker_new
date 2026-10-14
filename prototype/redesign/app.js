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

    /* ---------- Роли (как в приложении) ---------- */
    const ROLES = {
        doctor: {
            user: D.USERS.doctor, home: '#/today', scope: p => p.doctorId === D.USERS.doctor.id,
            can: { write: true, book: true, decide: false, consilium: true, data: true, admin: false },
            nav: ['today', 'patients', 'calendar', 'consilium', 'notifications', 'analytics', 'checklists', 'quality', 'data', 'settings'],
        },
        head: {
            user: D.USERS.head, home: '#/department', scope: p => p.dept === D.USERS.head.dept,
            can: { write: true, book: true, decide: false, consilium: true, data: true, admin: false },
            nav: ['department', 'patients', 'calendar', 'consilium', 'notifications', 'analytics', 'checklists', 'quality', 'data', 'settings'],
        },
        chair: {
            // Как в приложении: область пациентов by_treating_doctor, консилиум — все записи.
            user: D.USERS.chair, home: '#/consilium', scope: p => p.doctorId === D.USERS.chair.id,
            can: { write: false, book: false, decide: true, consilium: true, data: false, admin: false },
            nav: ['patients', 'consilium', 'analytics'],
        },
        management: {
            user: D.USERS.management, home: '#/analytics', scope: () => true,
            can: { write: false, book: false, decide: false, consilium: false, data: false, admin: false },
            nav: ['analytics', 'patients', 'calendar', 'notifications', 'checklists', 'quality', 'settings'],
        },
    };

    const ICONS = {
        today: '<path d="M4 6h16M4 12h10M4 18h7"/><circle cx="18" cy="16" r="3"/>',
        department: '<path d="M3 21h18M5 21V8l7-4 7 4v13"/><path d="M9 21v-5h6v5"/>',
        patients: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c.8-3.4 3.4-5 6.5-5s5.7 1.6 6.5 5"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 15c2 .6 3.2 2.2 3.5 5"/>',
        calendar: '<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
        consilium: '<path d="M12 3v4M5 9h14l-1.5 11h-11z"/><path d="M9 14h6"/>',
        notifications: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>',
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
            { id: 'patients', label: 'Пациенты' }, { id: 'calendar', label: 'Расписание' },
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
    try { const r = localStorage.getItem('proto-role'); if (r && ROLES[r]) S.role = r; } catch (e) { /* без хранилища */ }
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
        const readOnly = !R().can.write;
        return html`
            ${pagehead('Пациенты', ['doctor', 'chair'].includes(S.role) ? 'Пациенты, у которых вы лечащий врач' : S.role === 'head' ? 'Пациенты вашего отделения' : 'Все пациенты клиники',
                readOnly ? html`<span class="tag">Только просмотр</span>` : html`<button class="btn" data-action="export">Экспорт</button><button class="btn btn--primary" data-action="new-patient">Новый пациент</button>`)}
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
                        <div class="pagehead__actions">${can.write ? html`<button class="btn" data-action="attention" data-id="${p.id}">Отметить внимание</button><button class="btn btn--primary" data-action="advance" data-id="${p.id}">Перевести на этап</button>` : html`<span class="tag">Только просмотр</span>`}</div>
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
        const mineIds = new Set(scoped().map(p => p.id));
        const action = (c) => {
            if (can.decide) {
                if (c.status === 'signed') return html`<button class="btn btn--sm btn--ghost" data-action="decide" data-id="${c.id}">Открыть</button>`;
                if (c.status === 'needs_revision') return html`<span class="muted" style="font-size:var(--t-sm)">Ожидает врача</span>`;
                return html`<button class="btn btn--sm ${c.status === 'in_review' ? 'btn--primary' : ''}" data-action="decide" data-id="${c.id}">${c.status === 'in_review' ? 'Продолжить' : 'Рассмотреть'}</button>`;
            }
            if (mineIds.has(c.patientId) && ['recorded', 'needs_revision'].includes(c.status)) return html`<button class="btn btn--sm" data-action="book" data-id="${c.id}">Изменить</button>`;
            return mineIds.has(c.patientId) ? html`<span class="muted" style="font-size:var(--t-sm)">Моя запись</span>` : '';
        };
        const d = parse(D.today);
        return html`
            ${pagehead('Онкологический консилиум', can.decide ? 'Записи всех врачей на выбранный день — рассмотрение и подписание решений' : 'Свободные и занятые слоты; записать можно своих пациентов',
                can.decide ? html`<button class="btn" data-action="registry">Реестры (XLSX)</button>` : html`<button class="btn btn--primary" data-action="book">Записать пациента</button>`,
                `${WEEKDAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`)}
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
            ${pagehead('Аналитика', S.role === 'management' ? 'Состояние маршрутов пациентов по клинике' : 'Показатели в пределах вашей области доступа',
                html`<div class="segmented" role="group" aria-label="Период"><button aria-pressed="${period === '4w'}" data-action="period" data-v="4w">4 недели</button><button aria-pressed="${period === '12w'}" data-action="period" data-v="12w">12 недель</button></div>${R().can.data || S.role === 'management' ? html`<button class="btn" data-action="export">Выгрузить CSV</button>` : ''}`,
                S.role === 'management' ? 'Управленческая аналитика' : '')}
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
                <div class="field"><label class="field__label" for="npDoc">Лечащий врач</label><input class="input" id="npDoc" readonly value="${S.role === 'doctor' ? R().user.name : 'Выбирается заведующим'}"></div>
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
                S.consilium.push({ id: `c-${Date.now()}`, patientId: p.id, patientName: p.name, policy: p.policy, icd: p.icd, localization: p.localization, group: S.consGroup || 'thoracic', date: D.today, time, doctor: p.doctor, status: 'recorded', needsSurgeon: $('#bkSurgeon').checked, needsRadiotherapist: $('#bkRadio').checked, comment: $('#bkComment').value || 'Обсудить тактику лечения.', revisions: [] });
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

    /* ---------- Навигация и отрисовка ---------- */
    function renderNav(active) {
        const allowed = new Set(R().nav);
        $('#nav').innerHTML = String(html`${NAV.map(g => {
            const items = g.items.filter(i => allowed.has(i.id));
            if (!items.length) return '';
            return html`<div class="nav__group"><div class="nav__label">${g.group}</div>${items.map(i => html`<button class="nav__item ${i.legacy ? 'nav__item--legacy' : ''}" data-href="#/${i.id}" ${raw(active === i.id ? 'aria-current="page"' : '')}>${icon(i.id)}<span>${i.label}</span>${i.id === 'today' || i.id === 'department' ? html`<span class="nav__count">${scoped().filter(p => p.overdue || p.attention.length).length}</span>` : ''}</button>`)}</div>`;
        })}`);
        const u = R().user;
        $('#userAvatar').textContent = initials(u.name);
        $('#userName').textContent = u.short;
        $('#userRole').textContent = u.roleLabel;
    }

    function render() {
        const hash = location.hash || R().home;
        const [path, qs] = hash.slice(2).split('?');
        const [section, id] = path.split('/');
        const params = new URLSearchParams(qs || '');
        const allowed = R().nav.includes(section) || section === 'patient';
        let out;
        if (!allowed) out = denied(section);
        else if (section === 'today') out = viewToday();
        else if (section === 'department') out = viewDepartment();
        else if (section === 'patients') out = viewPatients(params.get('q'));
        else if (section === 'patient') out = viewPatient(id);
        else if (section === 'calendar') out = viewCalendar();
        else if (section === 'consilium') out = viewConsilium();
        else if (section === 'analytics') out = viewAnalytics();
        else out = viewLegacy(section);
        renderNav(section === 'patient' ? 'patients' : section);
        $('#view').innerHTML = String(out);
        document.title = `${NAV_LABEL[section] || 'Карточка пациента'} — Система планирования`;
        bindView(params);
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
        if (e.key === 'Escape') { closeModal(); $('#app').classList.remove('is-nav-open'); }
        if (e.key === 'Enter' && e.target.matches('tr[data-href]')) location.hash = e.target.dataset.href;
        if (e.key === '/' && !e.target.closest('input, textarea, select')) { e.preventDefault(); $('#globalSearch').focus(); }
    });
    $('#globalSearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') { location.hash = `#/patients?q=${encodeURIComponent(e.target.value)}`; e.target.blur(); } });
    $('#roleSelect').value = S.role;
    $('#roleSelect').addEventListener('change', (e) => { S.role = e.target.value; try { localStorage.setItem('proto-role', S.role); } catch (err) { /* ok */ } location.hash = R().home; render(); });
    $('#stateSelect').addEventListener('change', (e) => { S.demo = e.target.value; render(); });
    $('#themeBtn').addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('proto-theme', next); } catch (err) { /* ok */ }
    });
    $('#notifBtn').addEventListener('click', () => toast('Центр уведомлений в прототипе не перерисован.'));
    $('#menuBtn').addEventListener('click', () => { const open = $('#app').classList.toggle('is-nav-open'); $('#menuBtn').setAttribute('aria-expanded', String(open)); });
    window.addEventListener('hashchange', () => { render(); $('#view').focus({ preventScroll: true }); window.scrollTo(0, 0); });

    if (!location.hash) location.hash = R().home; else render();
})();
