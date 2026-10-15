/* UX-прототип «Система планирования» для сотрудников.
   Только синтетические данные (../redesign/data.js); все изменения живут в
   памяти вкладки, ничего не отправляется и не сохраняется на сервере. */
(function () {
    'use strict';

    const D = window.DEMO;
    const TODAY = D.today;
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
    const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const ICON = {
        check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
        chev: '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
        x: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
        search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4 4"/></svg>',
        back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
        copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="8" width="11" height="11" rx="2"/><path d="M5 15V6a1 1 0 0 1 1-1h9"/></svg>',
        filter: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4"/></svg>',
        download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v11M7 10l5 5 5-5M5 20h14"/></svg>',
        warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/></svg>',
        lock: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
        plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
        spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/></svg>',
    };
    /* Иконки разделов — те же, что в боковой навигации Stage 3.5 */
    const NAV_ICONS = {
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
    const navIcon = (n) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${NAV_ICONS[n] || ''}</svg>`;
    const NAV = [
        ['Работа', [['today', 'Рабочий день'], ['department', 'Отделение'], ['patients', 'Пациенты'], ['messages', 'Сообщения'], ['calendar', 'Расписание'], ['consilium', 'Онкологический консилиум'], ['notifications', 'Уведомления']]],
        ['Контроль', [['analytics', 'Аналитика'], ['checklists', 'Чек-листы'], ['quality', 'Качество данных']]],
        ['Данные и администрирование', [['data', 'Импорт / Экспорт'], ['staff', 'Сотрудники'], ['structure', 'Структура'], ['audit', 'Журнал аудита'], ['settings', 'Настройки']]],
    ];
    const NAV_LABEL = Object.fromEntries(NAV.flatMap(g => g[1]));
    // Набор разделов по ролям — как navFor() в Stage 3.5 (права из policy.permissions_payload).
    const NAV_BY_ROLE = {
        doctor: 'today messages patients calendar consilium notifications analytics checklists quality data settings',
        head: 'department patients calendar consilium notifications analytics checklists quality data settings',
        chair: 'patients consilium analytics',
        deputy: 'patients calendar notifications analytics checklists quality settings',
        caop: 'patients calendar notifications analytics checklists quality settings',
        developer: 'patients calendar consilium notifications analytics checklists quality data staff structure audit settings',
    };
    const HOME = { doctor: 'today', head: 'department', chair: 'consilium', deputy: 'patients', caop: 'patients', developer: 'patients' };

    /* ---------- Даты ---------- */
    const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
    const parse = (s) => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
    const days = (a, b) => Math.round((parse(b) - parse(a)) / 86400000);
    // Короткая дата для списков: «15 окт»; год добавляется, если он не текущий.
    const fmtShort = (s) => { if (!s) return ''; const d = parse(s); return `${d.getDate()} ${MONTHS[d.getMonth()]}${d.getFullYear() !== Number(TODAY.slice(0, 4)) ? ' ' + d.getFullYear() : ''}`; };
    const whenShort = (d, t) => d ? `${fmtShort(d)}${t ? ', ' + t : ''}` : '';
    const fmtFull = (s) => { if (!s) return ''; const d = parse(s); return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`; };
    const fmtStamp = (s) => s ? `${fmtFull(s)}${s.length > 10 ? ', ' + s.slice(11, 16) : ''}` : '';
    const plural = (n, one, few, many) => { const a = Math.abs(n) % 100, b = a % 10; return a > 10 && a < 20 ? many : b > 1 && b < 5 ? few : b === 1 ? one : many; };

    /* ---------- Роли: область видимости и права, как в приложении ---------- */
    const ROLES = {
        doctor: { label: 'Врач', user: D.USERS.doctor, write: true, scopeText: 'Пациенты, у которых вы лечащий врач' },
        head: { label: 'Заведующий отделением', user: D.USERS.head, write: true, scopeText: 'Пациенты Торакального отделения' },
        chair: { label: 'Председатель консилиума', user: D.USERS.chair, write: true, scopeText: 'Пациенты, которых вы создали' },
        deputy: { label: 'Заместитель главного врача', user: D.USERS.deputy, write: false, scopeText: 'Все пациенты организации (только просмотр)' },
        caop: { label: 'Руководитель ЦАОП', user: D.USERS.caop, write: false, scopeText: 'Все пациенты организации (только просмотр)' },
        developer: { label: 'Разработчик', user: D.USERS.developer, write: true, dev: true, scopeText: 'Все пациенты организации' },
    };
    const S = {
        role: localGet('staffux-role') || 'doctor',
        demo: 'ok',
        list: { view: 'active', q: '', stage: '', doctor: '', deadline: '', reason: '', priority: '', sort: 'urgent' },
        tabs: {},
        editing: null,
        deleted: {},      // patientId → [{occ, ev, deletedAt, by}]
        read: new Set(),
        loggedOut: false,
        convs: D.conversations || [],
    };
    if (!ROLES[S.role]) S.role = 'doctor';
    const R = () => ROLES[S.role];
    function localGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
    function localSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* без хранилища */ } }

    function scope() {
        const u = R().user;
        if (S.role === 'doctor') return D.patients.filter(p => p.doctorId === u.id);
        if (S.role === 'head') return D.patients.filter(p => p.dept === u.dept);
        if (S.role === 'chair') return D.patients.filter(p => p.createdBy === u.id);
        return D.patients;
    }
    const showsDoctor = () => S.role !== 'doctor' && S.role !== 'chair';

    /* ---------- Производные данные пациента ---------- */
    const STAGE = (s) => D.STAGES[s] || s;
    const isTerminal = (p) => D.TERMINAL.includes(p.stage);
    const cur = (p) => p.history[p.history.length - 1];
    // Ближайшее событие — самое раннее открытое событие по дате и времени (core.js); просрочка не скрывается более поздним событием.
    const CORE = window.StaffUxCore;
    function nextEvent(p) {
        const x = CORE.pickNextEvent(p, TODAY, D.TERMINAL);
        if (!x) return null;
        const base = { ev: x.ev, occIndex: x.occIndex, eventIndex: x.eventIndex, title: eventTitleOf(x.stage), overdueTotal: x.overdueTotal, d: x.d };
        if (x.kind === 'nodate') return { ...base, kind: 'nodate', label: 'дата не назначена', tone: 'warn' };
        const d = x.d;
        if (x.kind === 'overdue') return { ...base, kind: 'overdue', label: `просрочено на ${-d} ${plural(-d, 'день', 'дня', 'дней')}`, tone: 'danger' };
        if (x.kind === 'today') return { ...base, kind: 'today', label: 'сегодня', tone: 'info' };
        if (x.kind === 'soon') return { ...base, kind: 'soon', label: `через ${d} ${plural(d, 'день', 'дня', 'дней')}`, tone: 'warn' };
        return { ...base, kind: 'planned', label: `через ${d} ${plural(d, 'день', 'дня', 'дней')}`, tone: 'muted' };
    }
    const openIssues = (p) => p.attentionIssues.filter(x => x.status === 'open');
    function stepInfo(p) {
        const order = D.STAGE_ORDER;
        if (isTerminal(p) && p.stage !== 'completed') return { n: p.history.length, total: p.history.length };
        const i = order.indexOf(p.stage);
        return { n: i + 1, total: order.length };
    }
    function urgency(p) {
        const ne = nextEvent(p);
        let s = 0;
        if (ne && ne.kind === 'overdue') s += 1000 - ne.d;
        if (openIssues(p).length) s += 500;
        if (p.priority === 'urgent') s += 300; else if (p.priority === 'high') s += 150;
        if (ne && ne.kind === 'today') s += 120;
        if (ne && ne.kind === 'soon') s += 60;
        return s;
    }
    const NEXT = { 'primary-appointment': 'exam', exam: 'appointment', appointment: 'consult', 'treatment-prep': 'treatment', treatment: 'completed' };

    /* ---------- Каркас: навигация, пользователь, демо ---------- */
    const allowedNav = () => new Set(NAV_BY_ROLE[S.role].split(' '));
    const initials = (name) => name.split(' ').slice(0, 2).map(w => w[0]).join('');
    const myConvs = () => S.role === 'doctor' ? S.convs.filter(c => c.doctorId === R().user.id && scope().some(p => p.id === c.patientId)) : [];
    const convFor = (p) => myConvs().find(c => c.patientId === p.id);
    function renderShell() {
        document.body.classList.toggle('is-auth', S.loggedOut);
        const list = scope();
        const flagged = list.filter(p => { const n = nextEvent(p); return (n && n.kind === 'overdue') || openIssues(p).length; }).length;
        const unreadMsgs = myConvs().reduce((n, c) => n + (c.unread || 0), 0);
        const route = parseRoute().name;
        const active = route === 'patient' ? 'patients' : route;
        const allowed = allowedNav();
        const extra = (id) => {
            if (id === 'today' || id === 'department') return flagged ? `<span class="nav-link__count nav-link__count--alert" aria-label="Требуют действий: ${flagged}">${flagged}</span>` : '';
            if (id === 'patients') return `<span class="nav-link__count">${list.length}</span>`;
            if (id === 'messages' && unreadMsgs) return `<span class="nav-link__badge" aria-label="Непрочитанных сообщений: ${unreadMsgs}">${unreadMsgs}</span>`;
            return '';
        };
        $('#nav').innerHTML = NAV.map(([title, items]) => {
            const shown = items.filter(([id]) => allowed.has(id));
            if (!shown.length) return '';
            return `<div class="nav-group"><div class="nav-group__title">${title}</div>${shown.map(([id, label]) => `<a class="nav-link" href="#/${id}" ${active === id ? 'aria-current="page"' : ''}>${navIcon(id)}<span>${esc(label)}</span>${extra(id)}</a>`).join('')}</div>`;
        }).join('');
        const u = R().user;
        $('#sideUser').innerHTML = `<div class="user"><span class="avatar" aria-hidden="true">${esc(initials(u.name))}</span><div class="user__text"><b>${esc(u.short)}</b><span>${esc(u.roleLabel)}</span><button class="user__logout" type="button" data-act="logout">Выйти</button></div></div>`;
        $('#demoRole').innerHTML = Object.entries(ROLES).map(([k, r]) => `<option value="${k}" ${k === S.role ? 'selected' : ''}>${esc(r.label)}</option>`).join('');
        $('#demoState').value = S.demo;
        const unread = bellItems().filter(i => !S.read.has(i.id)).length;
        $('#bellCount').textContent = unread ? String(unread) : '';
        $('#bellBtn').setAttribute('aria-label', unread ? `Уведомления сотрудника: ${unread} новых` : 'Уведомления сотрудника');
    }

    /* ---------- Маршрутизация ---------- */
    function parseRoute() {
        const h = location.hash.replace(/^#\/?/, '') || HOME[S.role];
        const [path, query] = h.split('?');
        const parts = path.split('/');
        const params = new URLSearchParams(query || '');
        return { name: parts[0] || HOME[S.role], id: parts[1], params };
    }
    // Точки расширения для модулей прототипа (consilium.js): страницы и хуки.
    const EXT = { pages: {}, hooks: {} };
    function render() {
        closeMenus();
        DD = null;
        renderShell();
        const main = $('#main');
        if (S.loggedOut) { main.innerHTML = pageLogin(); document.title = 'Вход — Система планирования (прототип)'; return; }
        const r = parseRoute();
        const pages = { today: () => pageToday(false), department: () => pageToday(true), patients: pagePatients, patient: () => pagePatient(r.id, r.params), consilium: pageConsilium, messages: pageMessages, analytics: pageAnalytics };
        const title = r.name === 'patient' ? 'Карточка пациента' : NAV_LABEL[r.name] || 'Раздел';
        const ok = r.name === 'patient' || allowedNav().has(r.name);
        main.innerHTML = `<div class="page">${!ok ? pageDenied(title) : (EXT.pages[r.name] || pages[r.name] || (() => pageLater(title, r.name)))(r)}</div>`;
        document.title = `${title} — Система планирования (прототип)`;
        afterRender(r);
    }
    let lastRoute = '';
    function afterRender(r) {
        const key = `${r.name}/${r.id || ''}`;
        if (key !== lastRoute) { lastRoute = key; window.scrollTo(0, 0); }
        const flash = r.params.get('stage');
        if (flash) { const el = document.getElementById(`stage-${flash}`); if (el) { el.scrollIntoView({ block: 'start' }); el.classList.add('is-flash'); } }
    }

    function demoGate(emptyText) {
        if (S.demo === 'loading') return `<div class="table-wrap" aria-busy="true"><div class="skeleton" aria-label="Загрузка данных">${'<div></div>'.repeat(6)}</div></div>`;
        if (S.demo === 'error') return `<div class="table-wrap"><div class="state state--error" role="alert"><h2>Не удалось загрузить данные</h2><p>Сервер не ответил. Данные не изменены — попробуйте ещё раз.</p><button class="btn" data-act="retry">Повторить</button></div></div>`;
        if (S.demo === 'empty') return `<div class="table-wrap"><div class="state"><h2>${emptyText}</h2></div></div>`;
        return null;
    }

    /* ---------- Рабочий день ---------- */
    function pageToday(dept) {
        const u = R().user;
        const list = scope();
        const gate = demoGate(dept ? 'В отделении нет пациентов' : 'На сегодня задач нет');
        const by = (kind) => list.filter(p => { const n = nextEvent(p); return n && n.kind === kind; });
        const attention = list.filter(p => openIssues(p).length);
        // Строка: [пациент · этап] [событие · дата] [статус]. Колонки одинаковой ширины во всех группах,
        // основная строка каждой колонки — на одной линии, второстепенная — строго под ней.
        const item = (p, right) => {
            const ne = nextEvent(p);
            const when = ne ? (ne.ev.date ? whenShort(ne.ev.date, ne.ev.time) : 'Дата не назначена') : 'Нет запланированных событий';
            return `<li><a class="arow" href="#/patient/${p.id}">
                <span class="arow__cell"><span class="arow__main arow__name">${esc(p.name)}</span><span class="arow__sub">${esc(STAGE(p.stage))}${showsDoctor() ? ' · ' + esc(p.doctor) : ''}</span></span>
                <span class="arow__cell"><span class="arow__main">${ne ? esc(ne.title) + (ne.overdueTotal > 1 ? ` <span class="muted">· просрочено событий: ${ne.overdueTotal}</span>` : '') : '<span class="muted">—</span>'}</span><span class="arow__sub">${esc(when)}</span></span>
                <span class="arow__cell arow__status">${right}</span></a></li>`;
        };
        const group = (title, rows, tone, empty, render) => `<section class="agenda__group" aria-labelledby="g-${tone}">
            <h2 id="g-${tone}">${title}<span class="count">${rows.length}</span></h2>
            ${rows.length ? `<ul class="agenda__list arows">${rows.map(render).join('')}</ul>` : `<div class="agenda__list"><p class="agenda__empty">${empty}</p></div>`}</section>`;
        const date = parse(TODAY);
        return `<div class="page-head"><div><h1>${dept ? 'Отделение' : `Добрый день, ${esc(u.name.split(' ')[1])}`}</h1>
                <p class="page-head__sub">${date.toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })} · ${esc(R().scopeText.toLowerCase())}</p></div>
                ${R().write ? `<div class="page-head__actions"><a class="btn btn--primary" href="#/patients" data-act="new-patient">${ICON.plus}Новый пациент</a></div>` : ''}</div>
            ${gate || `<div class="agenda">
                ${group('Просрочено', by('overdue').sort((a, b) => nextEvent(a).d - nextEvent(b).d), 'danger', 'Просроченных событий нет.', p => item(p, `<span class="status status--danger">${esc(nextEvent(p).label)}</span>`))}
                ${group('Сегодня', by('today'), 'today', 'На сегодня плановых событий нет.', p => item(p, `<span class="status status--info">сегодня${nextEvent(p).ev.time ? ' в ' + esc(nextEvent(p).ev.time) : ''}</span>`))}
                ${group('Ближайшие 3 дня', by('soon'), 'soon', 'В ближайшие три дня событий нет.', p => item(p, `<span class="status status--warn">${esc(nextEvent(p).label)}</span>`))}
                ${group('Требуют внимания', attention, 'warn', 'Открытых отметок внимания нет.', p => item(p, `<span class="status status--warn">${esc(D.ATTENTION_REASONS[openIssues(p)[0].reasonCode])}</span>`))}
            </div>`}`;
    }
    const eventTitleOf = (stage) => ({ 'primary-appointment': 'Первичный приём', exam: 'Обследование', appointment: 'Приём врача', consult: 'Онкоконсилиум', 'treatment-prep': 'Лечение', treatment: 'Лечение' }[stage] || 'Событие');
    const eventTitle = (p) => eventTitleOf(p.stage);

    /* ---------- Список пациентов ---------- */
    const VIEWS = [
        { id: 'active', label: 'В работе', test: p => !isTerminal(p) },
        { id: 'attention', label: 'Требуют внимания', tone: 'warn', test: p => openIssues(p).length > 0 },
        { id: 'overdue', label: 'Просрочено', tone: 'danger', test: p => { const n = nextEvent(p); return n && n.kind === 'overdue'; } },
        { id: 'soon', label: 'Ближайшие 3 дня', test: p => { const n = nextEvent(p); return n && (n.kind === 'today' || n.kind === 'soon'); } },
        { id: 'closed', label: 'Завершённые', test: p => isTerminal(p) },
        { id: 'all', label: 'Все', test: () => true },
    ];
    const SORTS = { urgent: 'Сначала срочные', name: 'По ФИО', next: 'По ближайшему событию', updated: 'Сначала изменённые' };
    const DEADLINES = { overdue: 'Просрочено', today: 'Сегодня', soon: 'В ближайшие 3 дня', nodate: 'Дата не назначена' };
    function filtered() {
        const L = S.list;
        const view = VIEWS.find(v => v.id === L.view) || VIEWS[0];
        const q = L.q.trim().toLowerCase();
        let rows = scope().filter(view.test).filter(p => {
            if (q && !(`${p.name} ${p.policy} ${p.icd} ${p.nosologyLabel}`.toLowerCase().includes(q))) return false;
            if (L.stage && p.stage !== L.stage) return false;
            if (L.doctor && p.doctorId !== L.doctor) return false;
            if (L.reason && !openIssues(p).some(x => x.reasonCode === L.reason)) return false;
            if (L.priority && p.priority !== L.priority) return false;
            if (L.deadline) { const n = nextEvent(p); if (!n || n.kind !== L.deadline) return false; }
            return true;
        });
        const cmp = {
            urgent: (a, b) => urgency(b) - urgency(a) || a.name.localeCompare(b.name, 'ru'),
            name: (a, b) => a.name.localeCompare(b.name, 'ru'),
            next: (a, b) => ((nextEvent(a) || {}).ev?.date || '9999').localeCompare(((nextEvent(b) || {}).ev?.date || '9999')),
            updated: (a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''),
        }[L.sort];
        return rows.sort(cmp);
    }
    function activeChips() {
        const L = S.list, chips = [];
        if (L.q) chips.push(['q', `Поиск: «${L.q}»`]);
        if (L.stage) chips.push(['stage', `Этап: ${STAGE(L.stage)}`]);
        if (L.doctor) chips.push(['doctor', `Врач: ${(D.DOCTORS.find(d => d.id === L.doctor) || {}).short || ''}`]);
        if (L.deadline) chips.push(['deadline', `Срок: ${DEADLINES[L.deadline]}`]);
        if (L.reason) chips.push(['reason', `Внимание: ${D.ATTENTION_REASONS[L.reason]}`]);
        if (L.priority) chips.push(['priority', `Приоритет: ${D.PRIORITY[L.priority]}`]);
        return chips;
    }
    /* Выпадающий список фильтра: кнопка-combobox + listbox (шаблон ARIA «select-only combobox»).
       Клавиатура: Enter/Пробел/↓/↑ — открыть; ↑↓ Home End — выбор; Enter — применить; Esc — закрыть; ввод букв — поиск. */
    function ddHtml(id, label, key, options, value, neutral, store) {
        const cur = options.find(o => o[0] === value) || options[0];
        return `<div class="dd"><span class="sr-only" id="${id}-lbl">${esc(label)}</span>
            <button class="dd__btn${value && !neutral ? ' is-set' : ''}" type="button" id="${id}" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-controls="${id}-list" aria-labelledby="${id}-lbl ${id}-val" data-dd="${key}"${store ? ` data-store="${store}"` : ''}>
                <span class="dd__label" aria-hidden="true">${esc(label)}:</span><span class="dd__value" id="${id}-val">${esc(cur[1])}</span>${ICON.chev}</button>
            <ul class="dd__list" id="${id}-list" role="listbox" aria-labelledby="${id}-lbl" hidden>${options.map(([v, t, n], i) => `<li class="dd__opt" role="option" id="${id}-o${i}" data-value="${esc(v)}" aria-selected="${v === cur[0]}">${ICON.check.replace('<svg', '<svg class="tick"')}<span>${esc(t)}</span>${n != null ? `<small>${n}</small>` : ''}</li>`).join('')}</ul></div>`;
    }
    function pagePatients() {
        const L = S.list;
        const all = scope();
        const rows = filtered();
        const extra = ['deadline', 'reason', 'priority'].filter(k => L[k]).length;
        const doctors = D.DOCTORS.filter(d => all.some(p => p.doctorId === d.id));
        const stages = Object.keys(D.STAGES).filter(s => all.some(p => p.stage === s));
        const chips = activeChips();
        const gate = demoGate(S.list.q ? 'Ничего не найдено' : 'Пациентов пока нет');
        return `<div class="page-head">
                <div><h1>Пациенты<span class="count">${all.length}</span></h1><p class="page-head__sub">${esc(R().scopeText)}</p></div>
                <div class="page-head__actions">
                    <div class="menu-wrap">
                        <button class="btn" type="button" data-menu="m-data" aria-haspopup="menu" aria-expanded="false">Импорт и экспорт${ICON.chev}</button>
                        <div class="menu" id="m-data" role="menu" hidden>
                            ${R().write ? '<button class="menu__item" role="menuitem" data-act="import"><span>Загрузить пациентов из файла<small>Предварительная проверка перед импортом</small></span></button>' : ''}
                            <button class="menu__item" role="menuitem" data-act="export"><span>Выгрузить список<small>Подписанный файл, только видимые вам пациенты</small></span></button>
                        </div>
                    </div>
                    ${R().write ? `<button class="btn btn--primary" type="button" data-act="new-patient">${ICON.plus}Новый пациент</button>` : ''}
                </div></div>
            <div class="views" role="group" aria-label="Представления списка">
                ${VIEWS.map(v => { const n = all.filter(v.test).length; return `<button class="view" type="button" data-view="${v.id}" aria-pressed="${L.view === v.id}">${v.label}<span class="view__n${v.tone && n ? ' view__n--' + v.tone : ''}">${n}</span></button>`; }).join('')}
            </div>
            <div class="toolbar">
                <div class="search">${ICON.search}<label class="sr-only" for="listQ">Поиск по списку</label>
                    <input class="input" id="listQ" type="search" placeholder="ФИО, полис ОМС или диагноз" value="${esc(L.q)}" autocomplete="off"></div>
                ${ddHtml('fStage', 'Этап', 'stage', [['', 'Все этапы', all.length], ...stages.map(st => [st, STAGE(st), all.filter(p => p.stage === st).length])], L.stage)}
                ${showsDoctor() ? ddHtml('fDoctor', 'Врач', 'doctor', [['', 'Все врачи', all.length], ...doctors.map(d => [d.id, d.short, all.filter(p => p.doctorId === d.id).length])], L.doctor) : ''}
                <div class="menu-wrap">
                    <button class="btn" type="button" data-menu="m-filters" aria-haspopup="dialog" aria-expanded="false">${ICON.filter}Ещё фильтры${extra ? ` · ${extra}` : ''}</button>
                    <div class="menu menu--left popover" id="m-filters" role="dialog" aria-label="Дополнительные фильтры" hidden>
                        ${ddHtml('fDeadline', 'Ближайший срок', 'deadline', [['', 'Любой'], ...Object.entries(DEADLINES).map(([k, v]) => [k, v, all.filter(p => { const n = nextEvent(p); return n && n.kind === k; }).length])], L.deadline)}
                        ${ddHtml('fReason', 'Причина внимания', 'reason', [['', 'Любая'], ...Object.entries(D.ATTENTION_REASONS).map(([k, v]) => [k, v, all.filter(p => openIssues(p).some(x => x.reasonCode === k)).length])], L.reason)}
                        ${ddHtml('fPriority', 'Приоритет', 'priority', [['', 'Любой'], ...Object.entries(D.PRIORITY).map(([k, v]) => [k, v, all.filter(p => p.priority === k).length])], L.priority)}
                        <div class="popover__foot"><button class="btn btn--text" type="button" data-act="clear-extra">Сбросить эти фильтры</button><button class="btn btn--sm" type="button" data-act="close-menus">Готово</button></div>
                    </div>
                </div>
                <div class="toolbar__sort">${ddHtml('fSort', 'Сортировка', 'sort', Object.entries(SORTS), L.sort, true)}</div>
            </div>
            <div class="chips" aria-live="polite">
                <span class="result-line">${gate ? '' : `Показано ${rows.length} ${plural(rows.length, 'пациент', 'пациента', 'пациентов')} · ${esc((VIEWS.find(v => v.id === L.view) || VIEWS[0]).label.toLowerCase())}`}</span>
                ${chips.map(([k, t]) => `<button class="chip" type="button" data-clear="${k}" aria-label="Убрать фильтр ${esc(t)}">${esc(t)}${ICON.x}</button>`).join('')}
                ${chips.length ? '<button class="btn btn--text btn--sm" type="button" data-act="clear-all">Сбросить всё</button>' : ''}
            </div>
            ${gate || (rows.length ? `<div class="table-wrap"><table class="ptable">
                <caption class="sr-only">Список пациентов. Нажмите на ФИО, чтобы открыть карточку.</caption>
                <thead><tr><th scope="col">Пациент</th><th scope="col">Этап маршрута</th><th scope="col">Ближайшее событие</th><th scope="col">Требует внимания</th>${showsDoctor() ? '<th scope="col">Лечащий врач</th>' : ''}</tr></thead>
                <tbody>${rows.map(rowHtml).join('')}</tbody></table></div>`
            : `<div class="table-wrap"><div class="state"><h2>Ничего не найдено</h2><p>Измените условия поиска или сбросьте фильтры.</p>${chips.length ? '<button class="btn" data-act="clear-all">Сбросить всё</button>' : ''}</div></div>`)}`;
    }
    function rowHtml(p) {
        const ne = nextEvent(p);
        const st = stepInfo(p);
        const issues = openIssues(p);
        const pr = p.priority !== 'normal' ? ` <span class="pill ${p.priority === 'urgent' ? 'pill--danger' : 'pill--warn'}">${D.PRIORITY[p.priority]}</span>` : '';
        return `<tr data-row="${p.id}">
            <td><a class="pname" href="#/patient/${p.id}">${esc(p.name)}</a>${pr}<span class="psub">${p.age} ${plural(p.age, 'год', 'года', 'лет')} · <span class="mono">${esc(p.icd)}</span> · ${esc(p.nosologyLabel)}</span></td>
            <td class="stage-cell" data-label="Этап"><b>${esc(STAGE(p.stage))}</b><span>${isTerminal(p) ? 'маршрут завершён' : `шаг ${st.n} из ${st.total}`}</span></td>
            <td class="ev-cell" data-label="Событие"><div class="cell2">${ne ? `<span class="status status--${ne.tone}">${esc(ne.label)}</span><span class="cell2__sub">${esc(ne.title)}${ne.overdueTotal > 1 ? ` · просрочено: ${ne.overdueTotal}` : ''}${ne.ev.date ? ' · ' + esc(whenShort(ne.ev.date, ne.ev.time)) : ''}</span>` : `<span class="status status--muted">${isTerminal(p) ? 'маршрут завершён' : 'нет запланированных'}</span>`}</div></td>
            <td class="att-cell" data-label="Внимание"><div class="cell2">${issues.length ? `<span class="status status--warn">${esc(D.ATTENTION_REASONS[issues[0].reasonCode])}</span><span class="cell2__sub">${issues.length > 1 ? `и ещё ${issues.length - 1}` : `с ${esc(fmtShort(issues[0].createdAt))}`}</span>` : '<span class="cell2__sub" aria-label="нет отметок">—</span>'}</div></td>
            ${showsDoctor() ? `<td data-label="Врач">${esc(p.doctor)}</td>` : ''}</tr>`;
    }

    /* ---------- Карточка пациента ---------- */
    // Восемь вкладок в порядке и с названиями Stage 3.5.
    const TABS = [
        { id: 'overview', label: 'Обзор' },
        { id: 'sms', label: 'Уведомления' },
        { id: 'data', label: 'Данные' },
        { id: 'comments', label: 'Комментарии' },
        { id: 'events', label: 'События и даты' },
        { id: 'checklist', label: 'Чек-лист' },
        { id: 'notes', label: 'Заметки' },
        { id: 'history', label: 'История' },
    ];
    const TAB_FN = () => ({ overview: tabOverview, sms: tabSms, data: tabData, comments: tabComments, events: tabEvents, checklist: tabChecklist, notes: tabNotes, history: tabHistory });
    const canEdit = (p) => R().write && scope().includes(p);
    function pagePatient(id, params) {
        const p = D.patients.find(x => x.id === id);
        if (!p || !scope().includes(p)) return `<div class="state"><h2>Пациент не найден</h2><p>Карточка недоступна или не существует.</p><a class="btn" href="#/patients">К списку пациентов</a></div>`;
        if (S.demo === 'loading' || S.demo === 'error') return `<nav class="crumbs" aria-label="Навигация"><a href="#/patients">${ICON.back}Пациенты</a></nav>${demoGate('')}`;
        let tab = params.get('tab') || S.tabs[p.id] || 'overview';
        if (!TAB_FN()[tab]) tab = 'overview';
        const conv = convFor(p);
        const edit = canEdit(p);
        const issues = openIssues(p);
        const next = NEXT[p.stage];
        let primary = '';
        if (edit && !isTerminal(p)) {
            primary = p.stage === 'consult'
                ? `<button class="btn btn--primary" type="button" data-act="advance">Перевести на этап</button><button class="btn" type="button" data-act="decision">Отметить решение ОК</button>`
                : next ? `<button class="btn btn--primary" type="button" data-act="advance">Перевести: ${esc(STAGE(next))}</button>` : '';
        }
        const facts = [
            `<span>${p.age} ${plural(p.age, 'год', 'года', 'лет')}</span>`,
            `<span><span class="k">Диагноз</span> <span class="mono">${esc(p.icd)}</span> · ${esc(p.nosologyLabel)}</span>`,
            `<span><span class="k">Телефон</span> ${esc(p.phone)}</span>`,
            `<span><span class="k">Полис ОМС</span> ${p.policy ? `<span class="mono">${esc(p.policy)}</span>` : '<span class="status status--warn">не указан</span>'}</span>`,
        ];
        return `<nav class="crumbs" aria-label="Навигация"><a href="#/patients">${ICON.back}Пациенты</a><span aria-hidden="true">/</span><span>${esc(p.name)}</span></nav>
            <header class="phead">
                <div class="phead__main">
                    <h1>${esc(p.name)}${p.priority !== 'normal' ? ` <span class="pill ${p.priority === 'urgent' ? 'pill--danger' : 'pill--warn'}">Приоритет: ${D.PRIORITY[p.priority]}</span>` : ''}</h1>
                    <div class="phead__facts">${facts.join('')}</div>
                    ${R().write ? '' : `<p class="readonly-note">${ICON.lock}Только просмотр: ваша роль не позволяет изменять карточку.</p>`}
                </div>
                <div class="phead__actions">
                    ${primary}
                    ${conv ? `<button class="btn" type="button" data-act="chat" data-conv="${conv.id}">${navIcon('messages')}Переписка с пациентом${conv.unread ? `<span class="btn__badge" aria-label="непрочитанных: ${conv.unread}">${conv.unread}</span>` : ''}</button>` : ''}
                    <button class="btn" type="button" data-act="assistant">${ICON.spark}Спросить помощника</button>
                    <button class="btn" type="button" data-act="copy" data-id="${p.id}">${ICON.copy}Скопировать сводку</button>
                    ${(edit && !isTerminal(p)) || R().dev || edit ? `<div class="menu-wrap">
                        <button class="btn" type="button" data-menu="m-more" aria-haspopup="menu" aria-expanded="false">Ещё действия${ICON.chev}</button>
                        <div class="menu" id="m-more" role="menu" hidden>
                            ${edit && !isTerminal(p) ? `
                                <button class="menu__item" role="menuitem" data-act="extra-exam"><span>Дополнительное обследование<small>Вернуть на этап обследования</small></span></button>
                                <button class="menu__item" role="menuitem" data-act="not-confirmed"><span>Диагноз не подтверждён<small>Завершить маршрут</small></span></button>` : ''}
                            ${R().dev ? `${edit && !isTerminal(p) ? '<div class="menu__sep" role="separator"></div>' : ''}<button class="menu__item" role="menuitem" data-act="change-doctor"><span>Сменить лечащего врача</span></button>` : ''}
                            ${edit ? `<div class="menu__sep" role="separator"></div><button class="menu__item menu__item--danger" role="menuitem" data-act="delete-patient"><span>Удалить пациента<small>Нельзя, если есть записи консилиума</small></span></button>` : ''}
                        </div>
                    </div>` : ''}
                </div>
            </header>
            ${issues.length ? `<div class="banner banner--warn" role="note"><span class="banner__title">${ICON.warn} Требует внимания</span><span class="banner__body">${issues.map(x => esc(D.ATTENTION_REASONS[x.reasonCode])).join(' · ')}</span>${tab !== 'overview' ? '<span class="banner__actions"><a class="btn btn--sm" href="#" data-tab-go="overview">Подробнее</a></span>' : ''}</div>` : ''}
            ${routeHtml(p)}
            <div class="tabs" role="tablist" aria-label="Разделы карточки">
                ${TABS.map(t => `<button class="tab" role="tab" type="button" id="tab-${t.id}" aria-selected="${t.id === tab}" aria-controls="panel" tabindex="${t.id === tab ? 0 : -1}" data-tab="${t.id}">${t.label}${tabMark(p, t.id)}</button>`).join('')}
            </div>
            <div class="pgrid">
                <div id="panel" role="tabpanel" aria-labelledby="tab-${tab}" tabindex="0">${TAB_FN()[tab](p, edit)}</div>
                ${asideHtml(p)}
            </div>`;
    }

    function tabMark(p, id) {
        if (id === 'sms' && p.notifications.consent !== 'granted') return '<span class="tab__mark" title="Нет согласия на SMS"><span class="sr-only">: нет согласия</span></span>';
        if (id === 'events') { const n = nextEvent(p); if (n && n.kind === 'overdue') return '<span class="tab__mark tab__mark--danger" title="Есть просроченное событие"><span class="sr-only">: есть просрочка</span></span>'; }
        if (id === 'comments' && p.complaint) return '<span class="tab__n" aria-hidden="true">1</span>';
        if (id === 'notes') { const n = p.history.filter(o => o.note).length; if (n) return `<span class="tab__n"><span class="sr-only">: </span>${n}</span>`; }
        return '';
    }
    /* Регламентный срок этапа — как stageDeadline() в Stage 3.5 (рабочие дни, приоритет сокращает срок). */
    function effectiveDate(occ) {
        if (!occ) return null;
        for (let i = occ.events.length - 1; i >= 0; i--) if (occ.events[i].actualDate) return occ.events[i].actualDate;
        return occ.enteredAt ? occ.enteredAt.slice(0, 10) : null;
    }
    function stageDeadline(p, stage) {
        const ref = (window.NOSOLOGY_REF || {})[p.nosology];
        if (!ref || !['appointment', 'consult'].includes(stage)) return null;
        const base = effectiveDate(p.history.find(o => o.stage === (stage === 'appointment' ? 'primary-appointment' : 'appointment')));
        if (!base) return null;
        const factor = p.priority === 'urgent' ? 0.5 : p.priority === 'high' ? 0.7 : 1;
        const d = parse(base); const total = Math.max(1, Math.round((stage === 'appointment' ? ref.stage1Days : ref.stage2Standard) * factor)); let n = 0;
        while (n < total) { d.setDate(d.getDate() + 1); if (d.getDay() !== 0 && d.getDay() !== 6) n++; }
        return { date: D.helpers.iso(d), label: ref.label };
    }
    function routeHtml(p) {
        const steps = p.history.map((occ, i) => {
            const last = i === p.history.length - 1;
            const done = !last || isTerminal(p);
            const repeat = p.history.slice(0, i).some(o => o.stage === occ.stage);
            const pend = last && !isTerminal(p) ? nextEvent(p) : null;
            const date = last && pend && pend.ev.date ? `<span class="step__date${pend.kind === 'overdue' ? ' step__date--late' : ''}">${pend.kind === 'overdue' ? 'срок ' : 'до '}${fmtShort(pend.ev.date)}</span>` : `<span class="step__date">${fmtShort(occ.enteredAt)}</span>`;
            return `<li class="step ${done ? 'step--done' : 'step--current'}"><button class="step__btn" type="button" data-stage="${i}" ${last && !done ? 'aria-current="step"' : ''} aria-label="${esc(STAGE(occ.stage))}${repeat ? ' (повторно)' : ''}: ${done ? 'завершён' : 'текущий этап'}. Показать события этапа">
                <span class="step__dot">${done ? ICON.check : i + 1}</span><span class="step__name">${esc(STAGE(occ.stage))}${repeat ? ' (доп.)' : ''}</span>${date}</button></li>`;
        });
        if (!isTerminal(p)) {
            const after = D.STAGE_ORDER.slice(D.STAGE_ORDER.indexOf(p.stage) + 1);
            after.forEach((s, k) => steps.push(`<li class="step"><button class="step__btn" type="button" disabled aria-label="${esc(STAGE(s))}: впереди"><span class="step__dot">${p.history.length + k + 1}</span><span class="step__name">${esc(STAGE(s))}</span><span class="step__date">впереди</span></button></li>`));
        }
        return `<section class="route" aria-labelledby="routeTitle"><div class="route__title"><h2 id="routeTitle" class="sr-only">Маршрут лечения</h2><span>Маршрут лечения</span>${isTerminal(p) ? `<span class="pill pill--neutral">Маршрут завершён: ${esc(STAGE(p.stage))}</span>` : ''}</div><ol>${steps.join('')}</ol></section>`;
    }

    function asideHtml(p) {
        const c = D.consilium.find(x => x.patientId === p.id && !x.archived);
        const consent = { granted: '<span class="status status--good">получено</span>', revoked: '<span class="status status--danger">отозвано</span>', not_recorded: '<span class="status status--warn">не зафиксировано</span>' }[p.notifications.consent];
        const CS = { recorded: 'записан', in_review: 'на рассмотрении', signed: 'решение подписано', needs_revision: 'возвращён на доработку', revised: 'доработан' };
        return `<aside class="aside" aria-labelledby="asideTitle"><h2 id="asideTitle">Сведения</h2><dl class="dl">
            <div><dt>Лечащий врач</dt><dd>${esc(p.doctorFull)}</dd></div>
            <div><dt>Заведующий</dt><dd>${esc(p.head || '—')}</dd></div>
            <div><dt>Согласие на SMS</dt><dd>${consent}</dd></div>
            <div><dt>Онкоконсилиум</dt><dd>${c ? `${fmtShort(c.date)}${c.time ? ', ' + esc(c.time) : ''} · ${esc(CS[c.status] || c.status)}` : '<span class="muted">записи нет</span>'}</dd></div>
            <div><dt>В системе с</dt><dd>${fmtFull(p.createdAt)}</dd></div>
            <div><dt>Последнее изменение</dt><dd>${fmtStamp(p.updatedAt)}</dd></div>
        </dl></aside>`;
    }

    function tabOverview(p, edit) {
        const c = cur(p);
        const ne = nextEvent(p);
        const issues = openIssues(p);
        const cl = D.helpers.checklistFor(p.nosology, p.stage);
        const done = cl.required.filter(n => c && c.checklist[n]).length;
        const closed = p.attentionIssues.filter(x => x.status !== 'open');
        const q = quality(p);
        const feed = [
            ...p.history.map(o => ({ at: o.enteredAt, text: `Этап: ${STAGE(o.stage)}` })),
            ...p.history.flatMap(o => o.events.filter(e => e.actualDate).map(e => ({ at: `${e.actualDate}T${e.actualTime || '00:00'}`, text: `Состоялось: ${STAGE(o.stage)}${e.matchedPlan === false ? ' (с отклонением от плана)' : ''}` }))),
            ...p.attentionIssues.map(x => ({ at: x.createdAt, text: `Отметка внимания: ${D.ATTENTION_REASONS[x.reasonCode]}` })),
        ].sort((a, b) => b.at.localeCompare(a.at)).slice(0, 6);
        return `<section class="section" aria-labelledby="nowTitle"><div class="now">
                <h2 id="nowTitle" class="sr-only">Сейчас</h2>
                <div class="kv__k">Текущий этап</div>
                <div style="font-size:var(--fs-lg);font-weight:600">${esc(STAGE(p.stage))}<span class="muted" style="font-weight:400;font-size:var(--fs-md)"> · с ${fmtFull(c.enteredAt)}</span></div>
                <div class="now__grid">
                    <div><div class="kv__k">Ближайшее событие</div><div class="kv__v">${ne ? (ne.ev.date ? `${fmtFull(ne.ev.date)}${ne.ev.time ? ', ' + esc(ne.ev.time) : ''}` : 'Дата не назначена') : 'Нет запланированных'}</div>${ne ? `<span class="status status--${ne.tone}">${esc(ne.label)}</span>` : ''}</div>
                    <div><div class="kv__k">Чек-лист этапа</div><div class="kv__v">${cl.required.length ? `${done} из ${cl.required.length} выполнено` : 'Не предусмотрен'}</div>${cl.required.length ? `<div class="progress" aria-hidden="true" style="margin-top:6px"><i style="width:${Math.round(done / cl.required.length * 100)}%"></i></div>` : ''}</div>
                    <div><div class="kv__k">Что дальше</div><div class="kv__v">${isTerminal(p) ? 'Маршрут завершён' : p.stage === 'consult' ? 'Решение консилиума' : esc(STAGE(NEXT[p.stage] || ''))}</div></div>
                </div>
                ${edit && ne ? `<p style="margin-top:var(--sp-3)"><a href="#" data-tab-go="events" data-stage-go="${p.history.length - 1}">Изменить даты или отметить выполнение →</a></p>` : ''}
            </div></section>
            ${consDecisionHtml(p)}
            <section class="section" aria-labelledby="attTitle"><div class="section__head"><h2 id="attTitle">Требует внимания</h2><span class="section__meta">${issues.length ? `${issues.length} ${plural(issues.length, 'открытая отметка', 'открытые отметки', 'открытых отметок')}` : 'открытых отметок нет'}</span>
                ${edit ? '<span class="section__actions"><button class="btn btn--text" type="button" data-act="add-issue">' + ICON.plus + 'Добавить отметку</button></span>' : ''}</div>
                ${issues.length ? issues.map(x => `<div class="issue"><div class="issue__text"><b>${esc(D.ATTENTION_REASONS[x.reasonCode])}</b><p>${esc(x.comment)} · ${esc(x.createdByName)}, ${fmtStamp(x.createdAt)}</p></div>${edit ? `<button class="btn btn--sm" type="button" data-act="close-issue" data-issue="${x.id}">Закрыть отметку</button>` : ''}</div>`).join('') : '<p class="lead">Если с пациентом что-то идёт не по плану, добавьте отметку — она появится у заведующего и в «Рабочем дне».</p>'}
                ${closed.length ? `<details class="deleted"><summary>История внимания<span class="count">${closed.length}</span></summary>${closed.map(x => `<div class="issue"><div class="issue__text"><b>${esc(D.ATTENTION_REASONS[x.reasonCode])}</b><p>${esc(x.comment)} · ${esc(x.createdByName)}, ${fmtStamp(x.createdAt)}</p><p>Закрыта ${fmtStamp(x.closedAt)}${x.closedByName ? ', ' + esc(x.closedByName) : ''}${x.resolutionComment ? ' — ' + esc(x.resolutionComment) : ''}</p></div><span class="pill pill--neutral">закрыта</span></div>`).join('')}</details>` : ''}
            </section>
            <section class="section" aria-labelledby="qTitle"><div class="section__head"><h2 id="qTitle">Качество данных</h2><span class="section__meta">заполненность ${q.score}%</span>${edit && q.missing.length ? '<span class="section__actions"><a class="btn btn--text" href="#" data-tab-go="data">Заполнить данные</a></span>' : ''}</div>
                <div class="progress" aria-hidden="true" style="margin-bottom:var(--sp-2)"><i style="width:${q.score}%"></i></div>
                ${q.findings.length ? q.findings.map(([t, d]) => `<div class="issue"><div class="issue__text"><b>${esc(t)}</b><p>${esc(d)}</p></div><span class="status status--danger">проблема</span></div>`).join('') : '<p class="lead">Обязательные поля заполнены. По действующим правилам проблем не найдено.</p>'}
            </section>
            <section class="section" aria-labelledby="feedTitle"><div class="section__head"><h2 id="feedTitle">Последние изменения</h2><span class="section__actions"><a class="btn btn--text" href="#" data-tab-go="history">Вся история</a></span></div>
                <ul class="feed">${feed.map(f => `<li><time datetime="${esc(f.at)}">${fmtStamp(f.at)}</time><span>${esc(f.text)}</span></li>`).join('')}</ul></section>
            <details class="deleted"><summary>Сводка для отчётности</summary><pre class="summary">${esc(summaryText(p))}</pre><p><button class="btn btn--sm" type="button" data-act="copy" data-id="${p.id}">${ICON.copy}Скопировать сводку</button></p></details>`;
    }
    // Подписанное решение (председатель, раздел консилиума) и ручная отметка в карточке показаны раздельно и ни одно не меняет этап.
    // Подписанные решения председателя, статус текущей записи и ручные отметки показаны раздельно и ничего не меняют.
    function consDecisionHtml(p) {
        const cs = CORE.consiliumSummary(D.consilium, p.id);
        const marks = CORE.manualDecisions(p);
        const extra = EXT.hooks.patientConsilium ? EXT.hooks.patientConsilium(p) : '';
        if (!cs.total && !marks.length && !extra) return '';
        const markLine = (m) => { const o = p.history[m.occIndex]; const e = o.events[m.eventIndex];
            return `${esc(D.DECISIONS[m.decision])}<br><span class="muted">событие ${m.eventIndex + 1} из ${o.events.length} этапа «${esc(STAGE(m.stage))}»${e.date ? ', план ' + esc(fmtFull(e.date)) : ''}${e.actualDate ? ', факт ' + esc(fmtFull(e.actualDate)) : ''}${m.mark ? ` · отмечено ${esc(fmtStamp(m.mark.at))}, ${esc(m.mark.by)}` : ' · отметка из синтетических данных'}</span>`; };
        const signedLine = (c) => `${esc(D.TREATMENTS[c.treatment] || 'лечение не указано')}${c.scheme ? ' · ' + esc(c.scheme.name) : ''}<br><span class="muted">консилиум ${esc(c.time ? whenShort(c.date, c.time) : fmtShort(c.date))}${c.archived ? ' · запись в архиве' : ''}</span>`;
        const cur0 = cs.current; const st = cur0 ? CONS_STATUS[cur0.status] || [cur0.status, 'muted'] : null;
        return `<section class="section" aria-labelledby="okTitle"><div class="section__head"><h2 id="okTitle">Онкологический консилиум</h2><span class="section__meta">решения не синхронизируются и не меняют этап пациента</span></div><dl class="dl">
            <div><dt>Подписанные решения председателя</dt><dd>${cs.signed.length ? cs.signed.map(signedLine).join('<br>') : '<span class="muted">подписанных решений нет</span>'}</dd></div>
            <div><dt>Текущая запись на консилиум</dt><dd>${cur0 ? `<span class="status status--${st[1]}">${st[0]}</span><br><span class="muted">${cur0.time ? esc(whenShort(cur0.date, cur0.time)) : esc(fmtShort(cur0.date)) + ', время не назначено'}${cs.total > 1 ? ` · всего записей: ${cs.total}` : ''}</span>` : '<span class="muted">записи на консилиум нет</span>'}</dd></div>
            <div><dt>Ручная отметка решения ОК</dt><dd>${marks.length ? marks.map(markLine).join('<br>') : '<span class="muted">не ставилась</span>'}</dd></div></dl>${extra}</section>`;
    }
    function quality(p) {
        const fields = [['ФИО', p.name], ['Телефон', p.phone], ['Полис ОМС', p.policy], ['Диагноз', p.icd], ['Текущий этап', p.stage], ['Лечащий врач', p.doctorId]];
        const missing = fields.filter(x => !x[1]).map(x => x[0]);
        const findings = [
            !p.doctorId && ['Не назначен лечащий врач', 'Пациент не сопоставлен с учётной записью лечащего врача.'],
            !p.policy && ['Не указан номер полиса ОМС', 'Полис ОМС нужен для полноты регистрационной карточки.'],
        ].filter(Boolean);
        return { missing, findings, score: Math.round((fields.length - missing.length) / fields.length * 100) };
    }
    function summaryText(p) {
        const L = [`Пациент: ${p.name}, ${p.age} ${plural(p.age, 'год', 'года', 'лет')}.`, `Полис ОМС: ${p.policy || 'не указан'}.`, `Диагноз (МКБ-10): ${p.icd || 'не указан'} — ${p.nosologyLabel}.`, `Приоритет: ${D.PRIORITY[p.priority]}.`, `Лечащий врач: ${p.doctorFull}. Заведующий: ${p.head || 'не указан'}.`, '', `Текущий этап: ${STAGE(p.stage)}.`, 'Хронология:'];
        p.history.forEach((o, i) => { L.push(`${i + 1}. ${STAGE(o.stage)} — с ${fmtStamp(o.enteredAt)}`); if (o.note) L.push(`   Заметка: ${o.note}`); });
        return L.join('\n');
    }

    const evStatus = (ev, isCur) => {
        if (ev.actualDate) return ev.noPlan ? '<span class="status status--good">состоялось</span>' : ev.matchedPlan === false ? '<span class="status status--warn">с отклонением от плана</span>' : '<span class="status status--good">по плану</span>';
        if (!ev.date) return '<span class="status status--warn">дата не назначена</span>';
        const d = days(TODAY, ev.date);
        if (d < 0) return `<span class="status status--danger">просрочено на ${-d} ${plural(-d, 'день', 'дня', 'дней')}</span>`;
        if (d === 0) return '<span class="status status--info">сегодня</span>';
        return `<span class="status status--muted">${isCur ? 'ожидается' : 'запланировано'}</span>`;
    };
    const dt = (d, t) => d ? `${fmtFull(d)}${t ? ', ' + esc(t) : ''}` : '<span class="muted">—</span>';
    function tabEvents(p, edit) {
        const blocks = p.history.map((occ, i) => {
            const isCur = i === p.history.length - 1 && !isTerminal(p);
            const treat = occ.stage === 'treatment-prep' || occ.stage === 'treatment';
            const rows = occ.events.map((ev, j) => {
                const key = `${p.id}:${i}:${j}`;
                if (S.editing === key) return `<tr class="edit-row"><td colspan="4"><form data-form="event" data-key="${key}" novalidate>
                    <div class="edit-grid">
                        <div class="field"><label class="field__label" for="e-pd">Плановая дата</label><input class="input" id="e-pd" type="date" name="date" value="${esc(ev.date)}"></div>
                        <div class="field"><label class="field__label" for="e-pt">Время</label><input class="input" id="e-pt" type="time" name="time" value="${esc(ev.time)}"></div>
                        <div class="field"><label class="field__label" for="e-ad">Фактическая дата</label><input class="input" id="e-ad" type="date" name="actualDate" max="${TODAY}" value="${esc(ev.actualDate)}"></div>
                        <div class="field"><label class="field__label" for="e-at">Время</label><input class="input" id="e-at" type="time" name="actualTime" value="${esc(ev.actualTime)}"></div>
                        ${treat ? `<div class="field"><label class="field__label" for="e-tt">Вид лечения</label><select class="select" id="e-tt" name="treatmentType"><option value="">Не указан</option>${Object.entries(D.EVENT_TREATMENT).map(([k, v]) => `<option value="${k}" ${ev.treatmentType === k ? 'selected' : ''}>${v}</option>`).join('')}</select></div>` : ''}
                    </div>
                    <p class="field__error" id="e-err" role="alert" hidden></p>
                    <p class="field__hint">«По плану» или «с отклонением» система определит сама по совпадению факта с планом.</p>
                    <div class="edit-actions"><button class="btn btn--danger" type="button" data-act="delete-event" data-key="${key}" style="margin-right:auto">Удалить событие</button><button class="btn" type="button" data-act="cancel-edit">Отмена</button><button class="btn btn--primary" type="submit">Сохранить событие</button></div>
                </form></td></tr>`;
                return `<tr><td data-label="План">${ev.noPlan ? '<span class="muted">без плана</span>' : dt(ev.date, ev.time)}</td><td data-label="Факт">${dt(ev.actualDate, ev.actualTime)}</td>
                    <td data-label="Статус">${evStatus(ev, isCur)}${ev.consiliumDecision ? `<br><span class="muted">Ручная отметка решения ОК: ${esc(D.DECISIONS[ev.consiliumDecision])}</span>` : ''}${treat ? `<br><span class="muted">${ev.treatmentType ? esc(D.EVENT_TREATMENT[ev.treatmentType]) : 'вид лечения не указан'}</span>` : ''}</td>
                    <td>${edit ? `<button class="btn btn--text btn--sm" type="button" data-act="edit-event" data-key="${key}" aria-label="Изменить событие этапа ${esc(STAGE(occ.stage))}">Изменить</button>` : ''}</td></tr>`;
            }).join('');
            const dl = stageDeadline(p, occ.stage);
            const lastEv = occ.events[occ.events.length - 1];
            const check = lastEv ? (lastEv.actualDate || lastEv.date) : null;
            const late = dl && check && check > dl.date;
            const dlHtml = dl ? `<div class="banner ${late ? 'banner--danger' : 'banner--info'}" role="note"><span class="banner__body">${late ? `Регламентный срок нарушен: событие должно было состояться не позднее ${fmtFull(dl.date)}` : `Регламентный срок: не позднее ${fmtFull(dl.date)}`} (${esc(dl.label)}${p.priority !== 'normal' ? ', с учётом приоритета' : ''}).</span></div>` : '';
            return `<section class="stage-block" id="stage-${i}" aria-labelledby="sh-${i}" tabindex="-1">
                <div class="stage-block__head"><h3 id="sh-${i}">${i + 1}. ${esc(STAGE(occ.stage))}</h3><span class="muted">с ${fmtStamp(occ.enteredAt)}</span>${isCur ? '<span class="pill pill--info">текущий этап</span>' : ''}
                    ${edit && isCur ? `<span class="section__actions" style="margin-left:auto"><button class="btn btn--text btn--sm" type="button" data-act="add-event" data-occ="${i}">${ICON.plus}Добавить событие</button></span>` : ''}</div>
                ${dlHtml}
                ${occ.events.length ? `<table class="etable"><colgroup><col style="width:26%"><col style="width:26%"><col><col style="width:110px"></colgroup><thead><tr><th>План</th><th>Факт</th><th>Статус</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody>${rows}</tbody></table>` : '<p class="muted">Событий нет.</p>'}
                ${occ.note ? `<p class="stage-note"><b>Заметка:</b> ${esc(occ.note)} <a href="#" data-tab-go="notes">Изменить во вкладке «Заметки»</a></p>` : ''}
            </section>`;
        }).join('');
        const del = S.deleted[p.id] || [];
        return `<p class="lead">Плановые и фактические даты по этапам. Нажмите на этап маршрута вверху, чтобы перейти к нему.</p>${blocks}
            ${edit ? `<details class="deleted" ${del.length ? '' : ''}><summary>Удалённые события<span class="count">${del.length}</span></summary>
                ${del.length ? `<table class="etable"><thead><tr><th>Этап</th><th>План</th><th>Удалено</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody>${del.map((d, k) => `<tr><td data-label="Этап">${esc(STAGE(p.history[d.occ].stage))}</td><td data-label="План">${dt(d.ev.date, d.ev.time)}</td><td data-label="Удалено">${fmtStamp(d.at)}, ${esc(d.by)}</td><td><button class="btn btn--sm" type="button" data-act="restore-event" data-k="${k}">Восстановить</button></td></tr>`).join('')}</tbody></table>` : '<p class="muted" style="padding:var(--sp-2) 0">Удалённых событий нет. Удалённое событие можно вернуть отсюда — данные не стираются.</p>'}
            </details>` : ''}`;
    }

    function tabChecklist(p, edit) {
        const c = cur(p);
        const cl = D.helpers.checklistFor(p.nosology, p.stage);
        if (!cl.required.length && !cl.optional.length) return '<div class="state"><h2>Для текущего этапа чек-лист не предусмотрен</h2></div>';
        const done = cl.required.filter(n => c.checklist[n]).length;
        const item = (n, k) => `<li class="${c.checklist[n] ? 'is-done' : ''}"><label><input type="checkbox" data-check="${esc(n)}" ${c.checklist[n] ? 'checked' : ''} ${edit ? '' : 'disabled'}><span>${esc(n)}</span></label></li>`;
        return `<section class="section"><div class="section__head"><h2>Обязательные пункты</h2><span class="section__meta">${done} из ${cl.required.length} выполнено · этап «${esc(STAGE(p.stage))}»</span></div>
                <div class="progress" aria-hidden="true" style="margin-bottom:var(--sp-3)"><i style="width:${Math.round(done / Math.max(cl.required.length, 1) * 100)}%"></i></div>
                <ul class="checks">${cl.required.map(item).join('')}</ul></section>
            ${cl.optional.length ? `<section class="section"><div class="section__head"><h2>По показаниям</h2><span class="section__meta">не влияют на готовность этапа</span></div><ul class="checks">${cl.optional.map(item).join('')}</ul></section>` : ''}`;
    }

    const SMS_STATUS = { sent: ['Отправлено', 'good'], pending: ['Ожидает отправки', 'info'], cancelled: ['Отменено', 'neutral'], skipped: ['Не отправлено: нет согласия', 'warn'] };
    function tabSms(p, edit) {
        const n = p.notifications;
        const granted = n.consent === 'granted';
        const consent = {
            granted: ['good', 'Согласие получено', `${fmtFull(n.consentAt)} · зафиксировал(а) ${esc(n.consentBy)}`],
            revoked: ['danger', 'Согласие отозвано', `${fmtFull(n.consentAt)} · напоминания не отправляются`],
            not_recorded: ['warn', 'Согласие не зафиксировано', 'Напоминания не будут отправлены'],
        }[n.consent];
        const pending = n.items.filter(i => i.status === 'pending').sort((a, b) => a.scheduledAt.localeCompare(b.scheduledAt))[0];
        const tpl = D.SMS_TEMPLATES[0];
        const ne = nextEvent(p);
        const when = ne && ne.ev.date && ne.ev.date >= TODAY ? ne.ev : null;
        const preview = tpl.body.replace('{date}', when ? fmtFull(when.date) : 'ДД.ММ.ГГГГ').replace('{time}', when ? (when.time || '—') : 'ЧЧ:ММ');
        return `<div class="state-line">
                <div class="state-card state-card--info"><div class="state-card__label">Отправка SMS в организации</div><div class="state-card__value">Тестовый режим</div><div class="state-card__note">Реальные сообщения сейчас не отправляются</div></div>
                <div class="state-card state-card--${consent[0]}"><div class="state-card__label">Согласие пациента</div><div class="state-card__value">${consent[1]}</div><div class="state-card__note">${consent[2]}</div></div>
                <div class="state-card"><div class="state-card__label">Следующее напоминание</div><div class="state-card__value">${pending ? fmtStamp(pending.scheduledAt) : 'Не запланировано'}</div><div class="state-card__note">${pending ? 'будет отправлено автоматически' : granted ? 'создаётся по плановым событиям' : 'нужно согласие пациента'}</div></div>
            </div>
            <section class="section" aria-labelledby="consentTitle"><div class="section__head"><h2 id="consentTitle">Согласие на SMS</h2></div>
                <p class="lead">${granted ? 'Пациент согласился получать напоминания о посещениях.' : n.consent === 'revoked' ? 'Пациент отозвал согласие. Новые напоминания не отправляются, ожидающие отменены.' : 'Без согласия пациента напоминания не создаются и не отправляются.'}</p>
                ${edit ? (granted ? '<button class="btn btn--danger btn--sm" type="button" data-act="revoke-consent">Отозвать согласие</button>' : '<button class="btn btn--primary btn--sm" type="button" data-act="grant-consent">Зафиксировать согласие</button>') : ''}
            </section>
            <section class="section" aria-labelledby="newTitle"><div class="section__head"><h2 id="newTitle">Новое напоминание</h2></div>
                ${!granted ? `<div class="banner banner--warn" role="note"><span class="banner__body">Создать напоминание нельзя: нет действующего согласия пациента.</span></div>` : ''}
                ${edit ? `<form class="composer" data-form="sms" ${granted ? '' : 'aria-disabled="true"'} novalidate>
                    <div class="row">
                        <div class="field"><label class="field__label" for="smsTpl">Шаблон</label><select class="select" id="smsTpl" name="template" ${granted ? '' : 'disabled'}>${D.SMS_TEMPLATES.filter((t, i, a) => a.findIndex(x => x.name === t.name) === i).map(t => `<option value="${t.code}">${esc(t.name)}</option>`).join('')}</select></div>
                        <div class="field"><label class="field__label" for="smsAt">Когда отправить</label><input class="input" id="smsAt" name="at" type="datetime-local" ${granted ? '' : 'disabled'} value="${when ? `${D.helpers.iso(D.helpers.addDays(parse(when.date), -1))}T18:00` : `${TODAY}T18:00`}"></div>
                    </div>
                    <div class="field"><span class="field__label" id="smsTextLabel">Текст сообщения</span>
                        <div class="message" aria-labelledby="smsTextLabel">${esc(preview)}</div>
                        <span class="field__hint">Текст формируется из утверждённого шаблона и не редактируется.${when ? '' : ' Дата и время посещения подставятся из ближайшего планового события — сейчас его нет.'}</span></div>
                    <div><button class="btn btn--primary" type="submit" ${granted ? '' : 'disabled'}>Создать напоминание</button></div>
                </form>` : `<p class="lead">${ICON.lock} Создавать напоминания может лечащий врач или заведующий.</p>`}
            </section>
            <section class="section" aria-labelledby="histTitle"><div class="section__head"><h2 id="histTitle">История уведомлений</h2><span class="section__meta">${n.items.length} ${plural(n.items.length, 'запись', 'записи', 'записей')}</span></div>
                ${n.items.length ? `<table class="htable"><thead><tr><th>Когда</th><th>Шаблон</th><th>Основание</th><th>Статус</th></tr></thead><tbody>${n.items.slice().sort((a, b) => b.scheduledAt.localeCompare(a.scheduledAt)).map(i => { const st = SMS_STATUS[i.status] || [i.status, 'neutral']; return `<tr><td data-label="Когда">${fmtStamp(i.scheduledAt)}</td><td data-label="Шаблон">${esc((D.SMS_TEMPLATES.find(t => t.code === i.template) || {}).name || '')}</td><td data-label="Основание">${i.source === 'manual' ? 'создано вручную' : 'плановое событие'}</td><td data-label="Статус"><span class="pill pill--${st[1]}">${st[0]}</span></td></tr>`; }).join('')}</tbody></table>` : '<p class="muted">Уведомлений пока не было.</p>'}
            </section>`;
    }

    function tabData(p, edit) {
        const ed = (sec) => edit ? `<span class="section__actions"><button class="btn btn--text btn--sm" type="button" data-act="edit-sec" data-sec="${sec}">Изменить</button></span>` : '';
        if (S.editing === `data:${p.id}:personal`) {
            return `<form class="section" data-form="personal" novalidate><div class="section__head"><h2>Личные данные</h2></div>
                <div class="edit-grid" style="max-width:640px">
                    <div class="field" style="grid-column:1/-1"><label class="field__label" for="dName">ФИО</label><input class="input" id="dName" name="name" value="${esc(p.name)}" required></div>
                    <div class="field"><label class="field__label" for="dAge">Возраст</label><input class="input" id="dAge" name="age" type="number" min="0" max="150" value="${p.age}"></div>
                    <div class="field"><label class="field__label" for="dPhone">Телефон</label><input class="input" id="dPhone" name="phone" type="tel" value="${esc(p.phone)}"></div>
                    <div class="field" style="grid-column:1/-1"><label class="field__label" for="dPolicy">Полис ОМС</label><input class="input mono" id="dPolicy" name="policy" inputmode="numeric" value="${esc(p.policy)}"><span class="field__hint">16 цифр, без пробелов</span></div>
                </div>
                <p class="field__error" id="d-err" role="alert" hidden></p>
                <div class="edit-actions" style="justify-content:flex-start"><button class="btn btn--primary" type="submit">Сохранить</button><button class="btn" type="button" data-act="cancel-edit">Отмена</button></div></form>`;
        }
        if (S.editing === `data:${p.id}:medical`) {
            const codes = Object.values(window.ICD10 || {}).flatMap(l => l.diagnoses);
            return `<form class="section" data-form="medical" novalidate><div class="section__head"><h2>Медицинские данные</h2></div>
                <div class="edit-grid" style="max-width:640px">
                    <div class="field"><label class="field__label" for="mIcd">Диагноз, код МКБ-10</label><input class="input mono" id="mIcd" name="icd" list="icdList" value="${esc(p.icd)}" autocomplete="off" required><datalist id="icdList">${codes.map(c => `<option value="${esc(c.code)}">${esc(c.label)}</option>`).join('')}</datalist><span class="field__hint">Начните вводить код или название</span></div>
                    <div class="field"><label class="field__label" for="mNos">Клиентский путь</label><select class="select" id="mNos" name="nosology">${Object.entries(window.NOSOLOGY_REF || {}).map(([k, v]) => `<option value="${k}" ${p.nosology === k ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></div>
                    <div class="field"><label class="field__label" for="mPgi">Результат ПГИ</label><select class="select" id="mPgi" name="pgiResult">${Object.entries(D.PGI).map(([k, v]) => `<option value="${k}" ${(p.pgiResult || '') === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></div>
                    <div class="field"><label class="field__label" for="mPgiD">Дата результата ПГИ</label><input class="input" id="mPgiD" name="pgiResultDate" type="date" max="${TODAY}" value="${esc(p.pgiResultDate || '')}"></div>
                    <div class="field"><label class="field__label" for="mPr">Приоритет</label><select class="select" id="mPr" name="priority">${Object.entries(D.PRIORITY).map(([k, v]) => `<option value="${k}" ${p.priority === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select><span class="field__hint">Срочный и высокий приоритет сокращают регламентные сроки</span></div>
                </div>
                <p class="field__error" id="m-err" role="alert" hidden></p>
                <div class="edit-actions" style="justify-content:flex-start"><button class="btn btn--primary" type="submit">Сохранить</button><button class="btn" type="button" data-act="cancel-edit">Отмена</button></div></form>`;
        }
        return `<section class="section"><div class="section__head"><h2>Личные данные</h2>${ed('personal')}</div><dl class="dl">
                <div><dt>ФИО</dt><dd>${esc(p.name)}</dd></div><div><dt>Возраст</dt><dd>${p.age} ${plural(p.age, 'год', 'года', 'лет')}</dd></div>
                <div><dt>Телефон</dt><dd>${esc(p.phone)}</dd></div><div><dt>Полис ОМС</dt><dd>${p.policy ? `<span class="mono">${esc(p.policy)}</span>` : '<span class="status status--warn">не указан</span>'}</dd></div></dl></section>
            <section class="section"><div class="section__head"><h2>Медицинские данные</h2>${ed('medical')}</div><dl class="dl">
                <div><dt>Диагноз (МКБ-10)</dt><dd><span class="mono">${esc(p.icd)}</span></dd></div>
                <div><dt>Клиентский путь</dt><dd>${esc(p.nosologyLabel)}</dd></div>
                <div><dt>Результат ПГИ</dt><dd>${esc(D.PGI[p.pgiResult || ''])}${p.pgiResultDate ? ' · ' + fmtFull(p.pgiResultDate) : ''}</dd></div>
                <div><dt>Приоритет</dt><dd>${esc(D.PRIORITY[p.priority])}</dd></div>
                ${p.diagnosisHistory.length ? `<div><dt>История диагноза</dt><dd>${p.diagnosisHistory.map(h => `${fmtFull(h.changedAt)}: ${esc(h.oldDiagnosis)} → ${esc(h.newDiagnosis)}`).join('<br>')}</dd></div>` : ''}</dl>
                </section>
            <section class="section"><div class="section__head"><h2>Ответственные</h2>${R().dev ? '<span class="section__actions"><button class="btn btn--text btn--sm" type="button" data-act="change-doctor">Сменить лечащего врача</button></span>' : ''}</div><dl class="dl">
                <div><dt>Лечащий врач</dt><dd>${esc(p.doctorFull)}</dd></div><div><dt>Заведующий</dt><dd>${esc(p.head || '—')}</dd></div></dl></section>`;
    }

    function tabComments(p, edit) {
        if (!edit) return `<section class="section"><div class="section__head"><h2>Жалобы и комментарии</h2></div><p class="lead">${p.complaint ? esc(p.complaint) : '<span class="muted">Комментариев нет.</span>'}</p></section>`;
        return `<form class="section" data-form="complaint" novalidate><div class="section__head"><h2>Жалобы и комментарии</h2><span class="section__meta">видны всем сотрудникам с доступом к карточке</span></div>
            <div class="field"><label class="field__label" for="cText">Комментарий к пациенту</label><textarea class="textarea" id="cText" name="complaint" rows="6" maxlength="2000" placeholder="Жалобы пациента, договорённости, что важно знать коллегам">${esc(p.complaint)}</textarea>
            <span class="field__hint" id="cCount">${(p.complaint || '').length} из 2000 знаков</span></div>
            <div class="edit-actions" style="justify-content:flex-start"><button class="btn btn--primary" type="submit">Сохранить комментарий</button>${p.complaint ? '<button class="btn btn--danger" type="button" data-act="clear-complaint">Очистить</button>' : ''}</div></form>`;
    }
    function tabNotes(p, edit) {
        return `<p class="lead">Заметки привязаны к этапам маршрута: что важно на этом шаге.</p>${p.history.map((o, i) => {
            const isCur = i === p.history.length - 1 && !isTerminal(p);
            const head = `<div class="stage-block__head"><h3 id="nh-${i}">${i + 1}. ${esc(STAGE(o.stage))}</h3><span class="muted">с ${fmtStamp(o.enteredAt)}</span>${isCur ? '<span class="pill pill--info">текущий этап</span>' : ''}</div>`;
            if (!edit) return `<section class="stage-block" aria-labelledby="nh-${i}">${head}<p class="lead">${o.note ? esc(o.note) : '<span class="muted">Заметки нет.</span>'}</p></section>`;
            return `<form class="stage-block" data-form="note" data-key="note:${p.id}:${i}" aria-labelledby="nh-${i}" novalidate>${head}
                <div class="field"><label class="field__label" for="n-${i}">Заметка к этапу «${esc(STAGE(o.stage))}»</label><textarea class="textarea" id="n-${i}" name="note" rows="3" placeholder="Например: пациент просит звонить после 15:00">${esc(o.note)}</textarea></div>
                <div class="edit-actions" style="justify-content:flex-start"><button class="btn btn--sm" type="submit">Сохранить заметку</button></div></form>`;
        }).join('')}`;
    }
    function tabHistory(p) {
        const evLine = (e) => [e.noPlan ? 'без плана' : e.date ? `план ${fmtFull(e.date)}${e.time ? ', ' + esc(e.time) : ''}` : 'план не указан', e.actualDate ? `факт ${fmtFull(e.actualDate)}${e.actualTime ? ', ' + esc(e.actualTime) : ''}` : '', e.treatmentType ? esc(D.EVENT_TREATMENT[e.treatmentType]) : '', e.consiliumDecision ? `ручная отметка решения ОК: ${esc(D.DECISIONS[e.consiliumDecision])}` : ''].filter(Boolean).join(' · ');
        return `<p class="lead">Хронология маршрута — только для чтения. Изменить даты можно во вкладке «События и даты», заметки — в «Заметках».</p>
            <ol class="hist">${p.history.map((o, i) => `<li><div class="hist__head"><b>${i + 1}. ${esc(STAGE(o.stage))}</b><time datetime="${esc(o.enteredAt)}">статус проставлен ${fmtStamp(o.enteredAt)}</time></div>
                ${o.events.length ? `<ul>${o.events.map(e => `<li>${evLine(e)} ${evStatus(e, false)}</li>`).join('')}</ul>` : ''}
                ${o.note ? `<p class="muted">Заметка: ${esc(o.note)}</p>` : ''}</li>`).join('')}</ol>
            <section class="section"><div class="section__head"><h2>История диагноза</h2></div>${p.diagnosisHistory.length ? `<ul class="feed">${p.diagnosisHistory.map(h => `<li><time datetime="${esc(h.changedAt)}">${fmtStamp(h.changedAt)}</time><span><span class="mono">${esc(h.oldDiagnosis)}</span> → <span class="mono">${esc(h.newDiagnosis)}</span></span></li>`).join('')}</ul>` : '<p class="muted">Диагноз не менялся.</p>'}</section>`;
    }

    /* ---------- Аналитика ----------
       Расчёты — те же, что в Stage 3.5 (по backend/app/analytics и services/data_quality.py),
       выполняются в браузере по синтетическим пациентам в области доступа роли; сервер не вызывается.
       Две разные вещи показаны раздельно: «Текущее состояние» (на момент расчёта, период не влияет)
       и «За период» (динамика и события с плановой датой в периоде). */
    const AN_DEFAULT = { period: '30', from: '', to: '', stage: '', nosology: '', doctor: '', department: '', attentionReason: '' };
    S.an = { ...AN_DEFAULT };
    S.anDetail = null;
    S.anCalcAt = '';
    const AN_STAGES = ['primary-appointment', 'exam', 'appointment', 'consult', 'treatment-prep', 'treatment', 'completed', 'dispensary-observation', 'diagnosis-not-confirmed'];
    const AN_QUALITY_LABEL = {
        missing_treating_doctor: 'Не назначен лечащий врач', missing_required_field: 'Не заполнено обязательное поле', missing_policy_number: 'Не указан номер полиса ОМС',
        missing_diagnosis: 'Не указан диагноз', missing_stage: 'Не указан текущий этап', invalid_phone: 'Некорректный телефон',
        actual_date_in_future: 'Фактическая дата в будущем', stage_sequence_violation: 'Нарушение маршрута', overdue_event: 'Просроченное событие',
        incomplete_required_checklist: 'Незавершённый обязательный чек-лист', possible_duplicate: 'Возможный дубль',
    };
    const AN_DEFINITIONS = {
        total_patients: 'Все пациенты, доступные текущей роли с учётом фильтров.',
        active_patients: 'Доступные пациенты в этапах маршрута до завершения.',
        completed_patients: 'Доступные пациенты на этапе «Завершил лечение».',
        patients_need_attention: 'Пациенты, у которых сотрудник поставил и ещё не закрыл ручную отметку «Требует внимания». Просрочки без такой отметки сюда не входят — они в показателе «С просроченными событиями».',
        data_quality_issues: 'Критические проблемы и предупреждения качества данных по доступным пациентам (информационные замечания не входят).',
        unassigned_patients: 'Доступные пациенты без закреплённой учётной записи лечащего врача.',
        patients_with_overdue: 'Пациенты с текущими просроченными плановыми событиями.',
        overdue_events: 'Плановые события с датой в прошлом без фактического выполнения.',
        critical_quality: 'Текущие критические проблемы качества данных.',
        warning_quality: 'Текущие предупреждения качества данных.',
        incomplete_required_checklists: 'Обязательные пункты чек-листа текущего этапа, не отмеченные выполненными.',
        pending_notifications: 'Уведомления пациентам, ожидающие отправки.',
        failed_notifications: 'Уведомления пациентам с ошибкой отправки.',
    };
    const AN_SAME_LABEL = [['lung12', 'lung34'], ['headneckConfirmed', 'headneckSuspected'], ['thyroidConfirmed', 'thyroidSuspected'], ['cervixCytology', 'cervixInvasive']];
    const anCodes = (code) => { const c = (code || '').trim(); if (!c) return []; return AN_SAME_LABEL.find(g => g.includes(c)) || [c]; };
    const anRef = () => window.NOSOLOGY_REF || {};
    const anRequired = (nos, stage) => { const ref = anRef()[nos]; if (!ref) return []; if (stage === 'exam') return ref.checklistRequired || []; return stage === 'consult' && (nos === 'stomach' || nos === 'ovary') ? ref.checklistRequiredStage2 || [] : []; };
    const anDate = (v) => { const x = String(v || '').slice(0, 10); return /^\d{4}-\d{2}-\d{2}$/.test(x) && D.helpers.iso(parse(x)) === x ? x : null; };
    const anPercent = (part, total) => total ? Math.round(part / total * 1000) / 10 : 0;
    const pct = (v) => `${String(v).replace('.', ',')}%`;
    const cpCmp = (u, v) => (u < v ? -1 : u > v ? 1 : 0);
    const anDeptIds = (p) => (p.dept ? [p.dept] : []);
    const anDeptName = (id) => (D.DEPARTMENTS.find(d => d.id === id) || {}).name || id;
    const anOpenIssues = (p) => p.attentionIssues.filter(x => x.status === 'open');
    const anNotif = (p, status) => p.notifications.items.filter(n => n.status === status).length;
    const anScope = () => S.demo === 'empty' ? [] : scope();
    function anRange() {
        const f = S.an;
        if (f.period === 'custom') return { from: f.from, to: f.to };
        return { from: D.helpers.iso(D.helpers.addDays(parse(TODAY), -Number(f.period) + 1)), to: TODAY };
    }
    const anRangeInvalid = () => { const r = anRange(); return S.an.period === 'custom' && (!r.from || !r.to || r.from > r.to); };

    // Правила качества данных (как data_quality.evaluate в Stage 3.5).
    function anQuality(list) {
        const issues = [];
        const add = (p, code, severity, extra = {}) => issues.push({ patientId: p.id, code, severity, ...extra });
        list.forEach(p => {
            if (!p.name) add(p, 'missing_required_field', 'critical');
            if (!p.stage) add(p, 'missing_stage', 'critical');
            if (!p.icd) add(p, 'missing_diagnosis', 'critical');
            if (!p.phone) add(p, 'missing_required_field', 'warning');
            if (!p.policy) add(p, 'missing_policy_number', 'info');
            if (!p.doctorId) add(p, 'missing_treating_doctor', 'warning');
            const phone = (p.phone || '').trim(), digits = phone.replace(/\D/g, '');
            if (phone && (/[A-Za-zА-Яа-я]/.test(phone) || digits.length < 10 || digits.length > 15)) add(p, 'invalid_phone', 'warning');
            const arrival = anDate(p.createdAt);
            const last = p.history.length ? p.history[p.history.length - 1].stage : null;
            if (last !== null && p.stage && last !== p.stage) add(p, 'stage_sequence_violation', 'critical');
            const closed = isTerminal(p);
            let prevIdx = -1, prevEntered = null;
            p.history.forEach(occ => {
                const entered = anDate(occ.enteredAt), idx = AN_STAGES.indexOf(occ.stage);
                if (idx < 0) add(p, 'stage_sequence_violation', 'critical');
                else { if (idx < prevIdx && !(prevIdx === AN_STAGES.indexOf('appointment') && occ.stage === 'exam')) add(p, 'stage_sequence_violation', 'warning'); prevIdx = Math.max(prevIdx, idx); }
                if (entered && prevEntered && entered < prevEntered) add(p, 'stage_sequence_violation', 'critical');
                if (entered) prevEntered = entered;
                occ.events.forEach(ev => {
                    const actual = anDate(ev.actualDate), planned = anDate(ev.date);
                    if (ev.actualDate && !actual) add(p, 'actual_date_in_future', 'critical');
                    else if (actual && actual > TODAY) add(p, 'actual_date_in_future', 'critical');
                    if (actual && arrival && actual < arrival) add(p, 'stage_sequence_violation', 'warning');
                    if (actual && entered && actual < entered) add(p, 'stage_sequence_violation', 'warning');
                    if (p.nosology && planned && planned < TODAY && !actual && !ev.noPlan && !closed) add(p, 'overdue_event', 'warning', { plannedDate: planned, daysOverdue: days(planned, TODAY) });
                });
            });
            const latest = p.history.length && !closed ? p.history[p.history.length - 1] : null;
            if (latest) { const req = anRequired(p.nosology, latest.stage); if (req.length && req.some(n => latest.checklist[n] !== true)) add(p, 'incomplete_required_checklist', 'warning'); }
        });
        const buckets = new Map();
        const put = (key, p) => { if (!buckets.has(key)) buckets.set(key, new Set()); buckets.get(key).add(p.id); };
        list.forEach(p => {
            let phone = (p.phone || '').replace(/\D/g, ''); if (phone.length === 11 && phone[0] === '8') phone = '7' + phone.slice(1);
            const name = (p.name || '').trim().toLowerCase().replace(/ё/g, 'е').split(/\s+/).filter(Boolean).join(' ');
            if (phone.length >= 10) put(`phone|${phone}`, p);
            if (name && p.age) put(`name_age|${name}|${p.age}`, p);
            if (name && p.icd) put(`name_diagnosis|${name}|${p.icd.toLowerCase()}`, p);
        });
        const dup = new Set(); buckets.forEach(ids => { if (ids.size > 1) ids.forEach(id => dup.add(id)); });
        list.filter(p => dup.has(p.id)).forEach(p => add(p, 'possible_duplicate', 'info'));
        return issues;
    }
    function anFilter(list, f) {
        const codes = anCodes(f.nosology);
        return list.filter(p => (!f.department || anDeptIds(p).includes(f.department)) && (!f.doctor || p.doctorId === f.doctor)
            && (!f.stage || p.stage === f.stage) && (!codes.length || codes.includes(p.nosology))
            && (!f.attentionReason || anOpenIssues(p).some(x => x.reasonCode === f.attentionReason)))
            .sort((a, b) => a.name.localeCompare(b.name, 'ru'));
    }
    function anChecklist(list) {
        let incomplete = 0; const patients = [], byPatient = new Map();
        list.forEach(p => {
            const c = cur(p); const req = anRequired(p.nosology, c ? c.stage : p.stage); const left = req.filter(n => !(c && c.checklist[n] === true)).length;
            incomplete += left; byPatient.set(p.id, left); if (left) patients.push(p);
        });
        return { incomplete, patients, byPatient };
    }
    // Заполненность — правило прототипа, а не серверная сводка: 6 обязательных полей карточки × пациенты в выборке.
    const AN_FILL_FIELDS = [['ФИО', 'name'], ['Телефон', 'phone'], ['Полис ОМС', 'policy'], ['Диагноз', 'icd'], ['Текущий этап', 'stage'], ['Лечащий врач', 'doctorId']];
    function anDashboard() {
        const f = S.an; const all = anScope();
        const list = anFilter(all, f), ids = new Set(list.map(p => p.id));
        const issues = anQuality(list);
        const critical = issues.filter(i => i.severity === 'critical'), warnings = issues.filter(i => i.severity === 'warning'), infos = issues.filter(i => i.severity === 'info');
        const overdueIssues = issues.filter(i => i.code === 'overdue_event');
        const overdueIds = new Set(overdueIssues.map(i => i.patientId));
        const problemIds = new Set([...critical, ...warnings].map(i => i.patientId));
        const attentionIds = new Set(list.filter(p => anOpenIssues(p).length).map(p => p.id));
        const cl = anChecklist(list);
        const active = list.filter(p => !isTerminal(p)).length;
        const card = (code, label, value, detail, tone) => ({ code, label, value, detail, tone, definition: AN_DEFINITIONS[code] });
        const cards = [
            card('total_patients', 'Всего пациентов', list.length, 'patients'),
            card('active_patients', 'Активные', active, 'active'),
            card('patients_need_attention', 'С открытой отметкой «Требует внимания»', attentionIds.size, 'attention_patients', 'warn'),
            card('patients_with_overdue', 'С просроченными событиями', overdueIds.size, 'overdue_patients', 'danger'),
            card('data_quality_issues', 'Проблемы качества данных', critical.length + warnings.length, 'quality_all', 'danger'),
            card('completed_patients', 'Завершённые', list.filter(p => p.stage === 'completed').length, 'completed'),
            card('unassigned_patients', 'Без назначенного врача', list.filter(p => !p.doctorId).length, 'unassigned_patients'),
            card('overdue_events', 'Просроченные события', overdueIssues.length, 'overdue_events'),
            card('critical_quality', 'Критические проблемы качества', critical.length, 'quality_critical'),
            card('warning_quality', 'Предупреждения качества', warnings.length, 'quality_warning'),
            card('incomplete_required_checklists', 'Незавершённые пункты обязательных чек-листов', cl.incomplete, 'incomplete_checklists'),
            card('pending_notifications', 'Уведомления, ожидающие отправки', list.reduce((n, p) => n + anNotif(p, 'pending'), 0), ''),
            card('failed_notifications', 'Ошибки отправки уведомлений', list.reduce((n, p) => n + anNotif(p, 'failed'), 0), ''),
        ];
        const stages = AN_STAGES.map(st => { const ps = list.filter(p => p.stage === st); return { key: st, label: STAGE(st), count: ps.length, overdue: ps.filter(p => overdueIds.has(p.id)).length, quality: ps.filter(p => problemIds.has(p.id)).length }; }).filter(r => r.count);
        const prof = new Map(); list.forEach(p => { const key = anCodes(p.nosology)[0] || ''; prof.set(key, (prof.get(key) || 0) + 1); });
        const profiles = [...prof.entries()].sort((a, b) => b[1] - a[1] || cpCmp(a[0], b[0])).map(([key, count]) => ({ key, label: key ? (anRef()[key] || {}).label || key : 'Не указана', count, percent: anPercent(count, list.length) }));
        const stageCount = (st) => list.filter(p => p.stage === st).length;
        const route = [
            { label: 'Активные', count: active, detail: 'active' },
            { label: 'Завершили лечение', count: stageCount('completed'), detail: 'patients', stage: 'completed' },
            { label: 'Диспансерное наблюдение', count: stageCount('dispensary-observation'), detail: 'patients', stage: 'dispensary-observation' },
            { label: 'Диагноз не подтверждён', count: stageCount('diagnosis-not-confirmed'), detail: 'patients', stage: 'diagnosis-not-confirmed' },
        ].map(r => ({ ...r, percent: anPercent(r.count, list.length) }));
        const byCode = new Map(); [...critical, ...warnings].forEach(i => byCode.set(i.code, (byCode.get(i.code) || 0) + 1));
        const qualityRows = [...byCode.entries()].sort((a, b) => b[1] - a[1] || cpCmp(a[0], b[0])).map(([code, count]) => ({ code, label: AN_QUALITY_LABEL[code] || code, count }));
        const fieldsTotal = list.length * AN_FILL_FIELDS.length;
        const filled = list.reduce((n, p) => n + AN_FILL_FIELDS.filter(([, k]) => p[k]).length, 0);
        const missingByField = AN_FILL_FIELDS.map(([label, k]) => ({ label, count: list.filter(p => !p[k]).length })).filter(r => r.count);
        const wl = new Map();
        list.forEach(p => {
            const key = p.doctorId || '—';
            if (!wl.has(key)) wl.set(key, { id: p.doctorId, name: p.doctorFull || 'Не назначен', count: 0, overdue: 0, checklists: 0, failed: 0 });
            const w = wl.get(key);
            if (!isTerminal(p)) w.count++;
            if (overdueIds.has(p.id)) w.overdue++;
            w.checklists += cl.byPatient.get(p.id) || 0; w.failed += anNotif(p, 'failed');
        });
        const workload = [...wl.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, 'ru'));
        const reasons = new Map(); list.forEach(p => anOpenIssues(p).forEach(x => reasons.set(x.reasonCode, (reasons.get(x.reasonCode) || 0) + 1)));
        const reasonTotal = [...reasons.values()].reduce((a, b) => a + b, 0);
        const attention = [...reasons.entries()].map(([code, count]) => ({ code, label: D.ATTENTION_REASONS[code] || code, count, percent: anPercent(count, reasonTotal) })).sort((a, b) => b.count - a.count);
        // За период
        const r = anRange();
        // Итоги — за весь период; график — последние 90 дней периода (core.js periodSummary).
        const period = CORE.periodSummary(list, r.from, r.to);
        const trend = period.trend;
        const overdueInPeriod = overdueIssues.filter(i => i.plannedDate >= r.from && i.plannedDate <= r.to);
        // Списки вариантов фильтров — из всей области доступа роли
        const docs = []; all.forEach(p => { if (p.doctorId && !docs.some(d => d.id === p.doctorId)) docs.push({ id: p.doctorId, name: p.doctor }); });
        const depts = []; all.forEach(p => anDeptIds(p).forEach(id => { if (!depts.includes(id)) depts.push(id); }));
        return { list, ids, cards, stages, profiles, route, qualityRows, infos: infos.length, fill: { filled, total: fieldsTotal, percent: anPercent(filled, fieldsTotal), missingByField }, workload, attention, trend, period, overdueInPeriod, overdueIssues, issues, cl, docs: docs.sort((a, b) => a.name.localeCompare(b.name, 'ru')), depts };
    }
    function anDetailItems(d, data) {
        const byId = new Map(data.list.map(p => [p.id, p]));
        const row = (p, extra) => ({ p, extra: extra || '' });
        const t = d.type, x = d.extra || {};
        if (t === 'patients') return data.list.filter(p => (!x.stage || p.stage === x.stage) && (!x.nosology || anCodes(x.nosology).includes(p.nosology)) && (!x.doctor || (p.doctorId || '—') === x.doctor)).map(p => row(p));
        if (t === 'active') return data.list.filter(p => !isTerminal(p) && (!x.doctor || (p.doctorId || '—') === x.doctor)).map(p => row(p));
        if (t === 'completed') return data.list.filter(p => p.stage === 'completed').map(p => row(p));
        if (t === 'unassigned_patients') return data.list.filter(p => !p.doctorId).map(p => row(p));
        if (t === 'attention_patients') return data.list.filter(p => anOpenIssues(p).some(i => !x.reason || i.reasonCode === x.reason)).map(p => row(p, D.ATTENTION_REASONS[anOpenIssues(p)[0].reasonCode]));
        if (t === 'overdue_patients') return data.list.filter(p => data.overdueIssues.some(i => i.patientId === p.id)).map(p => { const n = data.overdueIssues.filter(i => i.patientId === p.id).length; return row(p, `просроченных событий: ${n}`); });
        if (t === 'overdue_events' || t === 'overdue_period') {
            const src = t === 'overdue_period' ? data.overdueInPeriod : data.overdueIssues;
            return src.slice().sort((a, b) => b.daysOverdue - a.daysOverdue).map(i => row(byId.get(i.patientId), `плановая дата ${fmtFull(i.plannedDate)}, просрочено на ${i.daysOverdue} ${plural(i.daysOverdue, 'день', 'дня', 'дней')}`));
        }
        if (t.startsWith('quality')) {
            const sev = t === 'quality_critical' ? ['critical'] : t === 'quality_warning' ? ['warning'] : ['critical', 'warning'];
            return data.issues.filter(i => sev.includes(i.severity) && (!x.code || i.code === x.code)).map(i => row(byId.get(i.patientId), `${AN_QUALITY_LABEL[i.code] || i.code} · ${i.severity === 'critical' ? 'критическая' : 'предупреждение'}`));
        }
        // Показатель считает пункты, поэтому и список — по одному пункту на строку.
        if (t === 'incomplete_checklists') return data.cl.patients.flatMap(p => { const c = cur(p); return anRequired(p.nosology, c ? c.stage : p.stage).filter(n => !(c && c.checklist[n] === true)).map(n => row(p, `не выполнено: ${n}`)); });
        return [];
    }
    function anCsv(data) {
        const cell = (v) => { let x = String(v); if (typeof v === 'string' && /^[=+\-@\t\r\n]/.test(v.trimStart())) x = `'${x}`; return /[",\r\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
        const r = anRange();
        const lines = [['section', 'metric', 'label', 'value', 'definition'].join(',')];
        data.cards.forEach(c => lines.push(['current', c.code, c.label, c.value, c.definition].map(cell).join(',')));
        lines.push(['period', 'patients_added', `Новые пациенты ${r.from} — ${r.to}`, data.period.totals.added, 'Карточки, созданные в выбранном периоде.'].map(cell).join(','));
        lines.push(['period', 'patients_completed', `Завершения ${r.from} — ${r.to}`, data.period.totals.completed, 'Переходы на этап «Завершил лечение» в выбранном периоде.'].map(cell).join(','));
        lines.push(['period', 'overdue_in_period', `Просрочены с плановой датой ${r.from} — ${r.to}`, data.overdueInPeriod.length, 'Просроченные события, плановая дата которых входит в период.'].map(cell).join(','));
        const two = (n) => String(n).padStart(2, '0'); const d = new Date();
        const name = `analytics_summary_${TODAY.replace(/-/g, '')}_${two(d.getHours())}${two(d.getMinutes())}.csv`;
        return { blob: new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' }), name, count: lines.length - 1 };
    }
    // Список с полосами: подпись и число на одной строке, полоса и пояснение — под ними.
    function anBars(rows, o = {}) {
        if (!rows.length) return `<p class="an-empty">${o.empty || 'Данных по выбранным условиям нет.'}</p>`;
        const max = Math.max(1, ...rows.map(r => r.count));
        return `<ul class="bars">${rows.map(r => {
            const attrs = o.attrs ? o.attrs(r) : '';
            const tag = attrs ? 'button' : 'div';
            return `<li><${tag} class="bar${attrs ? ' bar--btn' : ''}${o.tone ? ' bar--' + o.tone(r) : ''}"${attrs ? ` type="button" ${attrs}` : ''}>
                <span class="bar__label">${esc(r.label)}</span><span class="bar__value">${r.count}</span>
                <span class="bar__track" aria-hidden="true"><span class="bar__fill" style="width:${Math.round(r.count / max * 100)}%"></span></span>
                ${o.meta ? `<span class="bar__meta">${o.meta(r)}</span>` : ''}</${tag}></li>`;
        }).join('')}</ul>`;
    }
    const anAttr = (type, title, extra = {}) => `data-an-detail="${type}" data-title="${esc(title)}" data-extra="${esc(JSON.stringify(extra))}"`;
    function anTrend(period) {
        const rows = period.trend;
        if (!rows.length) return '<p class="an-empty">Данных по динамике за период нет.</p>';
        const max = Math.max(3, ...rows.map(r => Math.max(r.added, r.completed)));
        const W = 640, H = 200, L = 34, Rt = W - 16, T = 14, B = H - 36;
        const pt = (r, i, k) => [Math.round((L + (rows.length === 1 ? 0 : i * ((Rt - L) / (rows.length - 1)))) * 10) / 10, Math.round((B - (r[k] / max) * (B - T)) * 10) / 10];
        const path = (k) => rows.map((r, i) => `${i ? 'L' : 'M'} ${pt(r, i, k).join(' ')}`).join(' ');
        const grid = [...new Set([0, Math.ceil(max / 2), max])];
        const ticks = [...new Set([0, Math.floor(rows.length / 2), rows.length - 1])];
        const sumA = period.totals.added, sumC = period.totals.completed;   // итоги всего периода, а не только видимой части графика
        return `<div class="trend-legend"><span class="lg lg--added">Новые пациенты за период · <b>${sumA}</b></span><span class="lg lg--completed">Завершения за период · <b>${sumC}</b></span></div>
            ${period.truncated ? `<p class="an-rule" style="margin:0 0 var(--sp-2)">Период — ${period.days} ${plural(period.days, 'день', 'дня', 'дней')}. График показывает последние ${CORE.TREND_MAX_DAYS} дней (с ${fmtFull(period.trendFrom)}); итоги выше и в CSV — за весь период.</p>` : ''}
            <svg class="trend" viewBox="0 0 ${W} ${H}" role="img" aria-label="Динамика: новых пациентов за период ${sumA}, завершений ${sumC}${period.truncated ? `; на графике последние ${CORE.TREND_MAX_DAYS} дней` : ''}. Подробно — в таблице ниже.">
            ${grid.map(v => { const y = Math.round((B - (v / max) * (B - T)) * 10) / 10; return `<line class="trend__grid" x1="${L}" x2="${Rt}" y1="${y}" y2="${y}"/><text x="${L - 8}" y="${y + 4}" text-anchor="end">${v}</text>`; }).join('')}
            ${['added', 'completed'].map(k => `<path class="trend__line trend__line--${k}" d="${path(k)}" fill="none"/>${rows.map((r, i) => r[k] ? `<circle class="trend__pt trend__pt--${k}" cx="${pt(r, i, k)[0]}" cy="${pt(r, i, k)[1]}" r="3.5"><title>${r.label}: ${r[k]}</title></circle>` : '').join('')}`).join('')}
            <line class="trend__axis" x1="${L}" x2="${Rt}" y1="${B}" y2="${B}"/>
            ${ticks.map(i => `<text x="${pt(rows[i], i, 'added')[0]}" y="${H - 10}" text-anchor="middle">${rows[i].label}</text>`).join('')}
            </svg>
            <details class="an-table"><summary>Показать данные таблицей</summary><div class="table-scroll"><table class="htable"><thead><tr><th>Дата</th><th>Новые пациенты</th><th>Завершения</th></tr></thead><tbody>${rows.filter(r => r.added || r.completed).map(r => `<tr><td data-label="Дата">${fmtFull(r.day)}</td><td data-label="Новые">${r.added}</td><td data-label="Завершения">${r.completed}</td></tr>`).join('') || '<tr><td colspan="3">В периоде изменений нет</td></tr>'}</tbody></table></div></details>`;
    }
    function pageAnalytics() {
        if (!S.anCalcAt) S.anCalcAt = new Date().toTimeString().slice(0, 5);
        if (S.role === 'chair') return pageChairAnalytics();
        const f = S.an;
        const head = `<div class="page-head"><div><h1>Аналитика</h1><p class="page-head__sub">Маршруты, просрочки и качество данных · ${esc(R().scopeText.toLowerCase())}</p></div>
            <div class="page-head__actions"><button class="btn" type="button" data-an-act="csv">${ICON.download}Скачать сводку CSV</button></div></div>`;
        const invalid = anRangeInvalid();
        const failed = S.demo === 'error';
        const data = failed || S.demo === 'loading' || invalid ? null : anDashboard();
        const opts = data || anDashboard();
        const r = anRange();
        const locs = []; Object.entries(anRef()).forEach(([code, x]) => { if (!locs.some(l => l[1] === (x.label || code))) locs.push([code, x.label || code]); });
        const periods = [['7', 'Последние 7 дней'], ['30', 'Последние 30 дней'], ['90', 'Последние 90 дней'], ['custom', 'Свой период']];
        const extra = ['nosology', 'department', 'attentionReason'].filter(k => f[k]).length;
        const chips = [];
        if (f.stage) chips.push(['stage', `Этап: ${STAGE(f.stage)}`]);
        if (f.doctor) chips.push(['doctor', `Врач: ${(opts.docs.find(d => d.id === f.doctor) || {}).name || ''}`]);
        if (f.nosology) chips.push(['nosology', `Локализация: ${(anRef()[f.nosology] || {}).label || f.nosology}`]);
        if (f.department) chips.push(['department', `Отделение: ${anDeptName(f.department)}`]);
        if (f.attentionReason) chips.push(['attentionReason', `Причина внимания: ${D.ATTENTION_REASONS[f.attentionReason]}`]);
        const toolbar = `<div class="toolbar an-toolbar" role="group" aria-label="Период и фильтры аналитики">
                ${ddHtml('anPeriod', 'Период', 'period', periods, f.period, true, 'an')}
                ${f.period === 'custom' ? `<div class="an-dates"><label class="sr-only" for="anFrom">Дата начала периода</label><input class="input" type="date" id="anFrom" value="${esc(f.from)}" max="${TODAY}" aria-describedby="anRangeErr"><span aria-hidden="true">—</span><label class="sr-only" for="anTo">Дата окончания периода</label><input class="input" type="date" id="anTo" value="${esc(f.to)}" max="${TODAY}" aria-describedby="anRangeErr"></div>` : ''}
                ${ddHtml('anStage', 'Этап', 'stage', [['', 'Все этапы'], ...AN_STAGES.map(st => [st, STAGE(st)])], f.stage, false, 'an')}
                ${opts.docs.length > 1 ? ddHtml('anDoctor', 'Врач', 'doctor', [['', 'Все врачи'], ...opts.docs.map(d => [d.id, d.name])], f.doctor, false, 'an') : ''}
                <div class="menu-wrap">
                    <button class="btn" type="button" data-menu="m-anfilters" aria-haspopup="dialog" aria-expanded="false">${ICON.filter}Ещё фильтры${extra ? ` · ${extra}` : ''}</button>
                    <div class="menu menu--left popover" id="m-anfilters" role="dialog" aria-label="Дополнительные фильтры аналитики" hidden>
                        ${ddHtml('anNos', 'Локализация', 'nosology', [['', 'Любая'], ...locs], f.nosology, false, 'an')}
                        ${opts.depts.length > 1 ? ddHtml('anDept', 'Отделение', 'department', [['', 'Любое'], ...opts.depts.map(id => [id, anDeptName(id)])], f.department, false, 'an') : ''}
                        ${ddHtml('anReason', 'Причина внимания', 'attentionReason', [['', 'Любая'], ...Object.entries(D.ATTENTION_REASONS)], f.attentionReason, false, 'an')}
                        <div class="popover__foot"><button class="btn btn--sm" type="button" data-act="close-menus">Готово</button></div>
                    </div>
                </div>
            </div>
            ${invalid ? `<p class="field__error" id="anRangeErr" role="alert">${!r.from || !r.to ? 'Укажите обе даты периода.' : 'Дата начала периода позже даты окончания.'} Показатели не пересчитаны.</p>` : ''}
            <div class="chips" aria-live="polite"><span class="result-line">Период: ${r.from && r.to && !invalid ? `${esc(fmtFull(r.from))} — ${esc(fmtFull(r.to))}` : 'не задан'}</span>
                ${chips.map(([k, t]) => `<button class="chip" type="button" data-an-clear="${k}" aria-label="Убрать фильтр ${esc(t)}">${esc(t)}${ICON.x}</button>`).join('')}
                ${chips.length || f.period !== '30' ? '<button class="btn btn--text btn--sm" type="button" data-an-act="reset">Сбросить всё</button>' : ''}</div>`;
        if (failed) return `${head}${toolbar}<div class="table-wrap"><div class="state state--error" role="alert"><h2>Не удалось загрузить аналитику</h2><p>Сервер не ответил. Попробуйте обновить данные.</p><button class="btn" type="button" data-act="retry">Повторить</button></div></div>`;
        if (S.demo === 'loading') return `${head}${toolbar}<div class="table-wrap" aria-busy="true"><div class="skeleton" aria-label="Загрузка аналитики">${'<div></div>'.repeat(6)}</div></div>`;
        if (!data) return `${head}${toolbar}`;
        const kpi = data.cards.slice(0, 5);
        const detail = S.anDetail;
        let detailHtml = '';
        if (detail) {
            const items = anDetailItems(detail, data);
            detailHtml = `<section class="an-detail" id="anDetail" tabindex="-1" aria-labelledby="anDetailTitle"><div class="section__head"><h2 id="anDetailTitle">${esc(detail.title)}</h2><span class="section__meta">${items.length} ${plural(items.length, 'запись', 'записи', 'записей')}${items.length > 50 ? ', показаны первые 50' : ''}</span><span class="section__actions"><button class="btn btn--text btn--sm" type="button" data-an-act="close-detail">Закрыть детализацию</button></span></div>
                ${items.length ? `<ul class="agenda__list arows an-detail__list">${items.slice(0, 50).map(({ p, extra: ex }) => `<li><a class="arow arow--2" href="#/patient/${p.id}"><span class="arow__cell"><span class="arow__main arow__name">${esc(p.name)}</span><span class="arow__sub">${esc(STAGE(p.stage))} · ${esc(p.doctor || 'врач не назначен')}</span></span><span class="arow__cell"><span class="arow__main">${esc(ex || '—')}</span></span></a></li>`).join('')}</ul>` : '<p class="an-empty">По выбранным условиям записей нет.</p>'}</section>`;
        }
        const sec = (id, title, body, meta) => `<section class="an-sec" aria-labelledby="${id}"><div class="section__head"><h3 id="${id}">${title}</h3>${meta ? `<span class="section__meta">${meta}</span>` : ''}</div>${body}</section>`;
        return `${head}${toolbar}${detailHtml}
            <section class="an-block" aria-labelledby="anNow">
                <div class="an-block__head"><h2 id="anNow">Текущее состояние</h2><span class="section__meta">на ${esc(fmtFull(TODAY))}, ${esc(S.anCalcAt)} · период на эти показатели не влияет</span><button class="btn btn--text btn--sm" type="button" data-an-act="refresh">Обновить</button></div>
                <ul class="kpis">${kpi.map(c => `<li><button class="kpi" type="button" ${anAttr(c.detail, c.label)}><span class="kpi__label">${esc(c.label)}</span><span class="kpi__value${c.tone && c.value ? ' kpi__value--' + c.tone : ''}">${c.value}</span><span class="kpi__hint">Показать список</span></button></li>`).join('')}</ul>
                <details class="deleted an-all"><summary>Все показатели и определения<span class="count">${data.cards.length}</span></summary>
                    <div class="table-scroll"><table class="htable an-metrics"><thead><tr><th>Показатель</th><th>Значение</th><th>Как считается</th></tr></thead><tbody>${data.cards.map(c => `<tr><td data-label="Показатель">${c.detail ? `<button class="btn btn--text btn--sm an-link" type="button" ${anAttr(c.detail, c.label)}>${esc(c.label)}</button>` : esc(c.label)}</td><td data-label="Значение" class="mono">${c.value}</td><td data-label="Как считается">${esc(c.definition)}</td></tr>`).join('')}</tbody></table></div></details>
                <div class="an-grid">
                    ${sec('anStages', 'Пациенты по этапам маршрута', anBars(data.stages, { empty: 'Пациентов по выбранным условиям нет.', attrs: x => anAttr('patients', `Этап: ${x.label}`, { stage: x.key }), meta: x => `просрочки: ${x.overdue} · проблемы данных: ${x.quality}` }))}
                    ${sec('anRoute', 'Состояние маршрутов', anBars(data.route, { attrs: x => anAttr(x.detail, x.label, x.stage ? { stage: x.stage } : {}), meta: x => `${pct(x.percent)} от выборки` }))}
                    ${sec('anLoc', 'Локализации', anBars(data.profiles, { empty: 'Пациентов с профилем клиентского пути нет.', attrs: x => x.key ? anAttr('patients', `Локализация: ${x.label}`, { nosology: x.key }) : '', meta: x => pct(x.percent) }))}
                    ${sec('anReasons', 'Причины внимания', anBars(data.attention, { empty: 'Открытых отметок внимания нет.', tone: x => x.code === 'critical_overdue' || x.code === 'emergency_hospitalization' ? 'danger' : 'warn', attrs: x => anAttr('attention_patients', `Причина: ${x.label}`, { reason: x.code }), meta: x => `${pct(x.percent)} отметок` }))}
                    ${sec('anLoad', 'Нагрузка врачей', anBars(data.workload.map(w => ({ ...w, label: w.name })), { empty: 'Нет данных по нагрузке.', attrs: x => anAttr('active', `Активные пациенты: ${x.name}`, { doctor: x.id || '—' }), meta: x => `активных пациентов; с просрочками: ${x.overdue} · пунктов чек-листов: ${x.checklists} · ошибок отправки: ${x.failed}` }), 'число — активные пациенты')}
                    ${sec('anQuality', 'Качество данных', `
                        <dl class="dl an-quality">
                            <div><dt>Проблемы (критические и предупреждения)</dt><dd><button class="btn btn--text btn--sm an-link" type="button" ${anAttr('quality_all', 'Проблемы качества данных')}>${data.cards[4].value}</button></dd></div>
                            <div><dt>Заполненность обязательных полей</dt><dd>${data.fill.total ? `${pct(data.fill.percent)} <span class="muted">(${data.fill.filled} из ${data.fill.total} полей)</span>` : '<span class="muted">нет пациентов в выборке</span>'}</dd></div>
                            <div><dt>Информационные замечания</dt><dd>${data.infos} <span class="muted">не входят в число проблем</span></dd></div>
                        </dl>
                        ${anBars(data.qualityRows, { empty: 'Проблем качества данных по выбранным условиям нет.', tone: () => 'danger', attrs: x => anAttr('quality_all', x.label, { code: x.code }) })}
                        <p class="an-rule"><b>Как считается в прототипе.</b> Заполненность — доля заполненных полей «${AN_FILL_FIELDS.map(x => x[0]).join('», «')}» у пациентов выборки${data.fill.missingByField.length ? ` (не заполнено: ${data.fill.missingByField.map(x => `${x.label.toLowerCase()} — ${x.count}`).join(', ')})` : ''}. Проблемы — по правилам качества данных Stage 3.5 на синтетических данных. В приложении сводка заполненности в аналитику не передаётся, поэтому прежний показатель «100%» сюда не перенесён.</p>`)}
                </div>
            </section>
            <section class="an-block" aria-labelledby="anPer">
                <div class="an-block__head"><h2 id="anPer">За период</h2><span class="section__meta">${esc(fmtFull(r.from))} — ${esc(fmtFull(r.to))}${data.period.truncated ? ` · ${data.period.days} ${plural(data.period.days, 'день', 'дня', 'дней')}; график — последние ${CORE.TREND_MAX_DAYS}` : ''}</span></div>
                <div class="an-grid an-grid--period">
                    ${sec('anTrend', 'Динамика пациентов', anTrend(data.period))}
                    ${sec('anOverP', 'Просрочки с плановой датой в периоде', `<p class="an-big"><button class="btn btn--text an-link an-link--big" type="button" ${anAttr('overdue_period', 'Просрочки с плановой датой в периоде')}>${data.overdueInPeriod.length}</button> <span class="muted">${plural(data.overdueInPeriod.length, 'событие', 'события', 'событий')} у ${new Set(data.overdueInPeriod.map(i => i.patientId)).size} ${plural(new Set(data.overdueInPeriod.map(i => i.patientId)).size, 'пациента', 'пациентов', 'пациентов')}</span></p><p class="an-rule">Сейчас всего просрочено ${data.overdueIssues.length} — это текущее состояние, без учёта периода.</p>`)}
                </div>
            </section>
            <p class="an-rule">Показатели считаются в браузере по синтетическим пациентам в области доступа вашей роли; сервер не вызывается.</p>`;
    }
    // Аналитика председателя: записи консилиума (как viewConsiliumAnalytics в Stage 3.5)
    function pageChairAnalytics() {
        const f = S.an; const r = anRange(); const invalid = anRangeInvalid();
        const periods = [['7', 'Последние 7 дней'], ['30', 'Последние 30 дней'], ['90', 'Последние 90 дней'], ['custom', 'Свой период']];
        const head = `<div class="page-head"><div><h1>Аналитика</h1><p class="page-head__sub">Записи онкологического консилиума и загрузка расписания</p></div></div>
            <div class="toolbar an-toolbar">${ddHtml('anPeriod', 'Период', 'period', periods, f.period, true, 'an')}
            ${f.period === 'custom' ? `<div class="an-dates"><label class="sr-only" for="anFrom">Дата начала периода</label><input class="input" type="date" id="anFrom" value="${esc(f.from)}"><span aria-hidden="true">—</span><label class="sr-only" for="anTo">Дата окончания периода</label><input class="input" type="date" id="anTo" value="${esc(f.to)}"></div>` : ''}</div>
            ${invalid ? '<p class="field__error" role="alert">Проверьте даты периода. Показатели не пересчитаны.</p>' : ''}`;
        if (S.demo === 'error') return `${head}<div class="table-wrap"><div class="state state--error" role="alert"><h2>Не удалось загрузить аналитику консилиума</h2><button class="btn" type="button" data-act="retry">Повторить</button></div></div>`;
        if (S.demo === 'loading') return `${head}<div class="table-wrap" aria-busy="true"><div class="skeleton">${'<div></div>'.repeat(4)}</div></div>`;
        if (invalid) return head;
        const rows = S.demo === 'empty' ? [] : D.consilium.filter(c => c.date >= r.from && c.date <= r.to);
        const signed = rows.filter(c => c.status === 'signed');
        const treatments = Object.entries(D.TREATMENTS).map(([code, label]) => ({ code, label, count: signed.filter(c => c.treatment === code).length })).filter(x => x.count);
        const schemes = CORE.groupSchemes(signed);
        const activeDays = new Set(rows.map(c => c.date)).size; const capacity = activeDays * Object.keys(D.GROUPS).length * 16;
        const occupied = new Set(rows.filter(c => c.time).map(c => `${c.date}|${c.time}|${c.group}`)).size;
        const k = [['Всего записей', rows.length, ''], ['Решение подписано', signed.length, 'good'], ['Ожидают решения', rows.length - signed.length, ''], ['Возвращено на доработку', rows.filter(c => c.status === 'needs_revision').length, 'warn']];
        return `${head}<section class="an-block" aria-labelledby="chP"><div class="an-block__head"><h2 id="chP">За период</h2><span class="section__meta">${esc(fmtFull(r.from))} — ${esc(fmtFull(r.to))}</span></div>
            <ul class="kpis kpis--static">${k.map(([l, v, t]) => `<li><div class="kpi"><span class="kpi__label">${l}</span><span class="kpi__value${t && v ? ' kpi__value--' + t : ''}">${v}</span></div></li>`).join('')}</ul>
            <div class="an-grid">
                <section class="an-sec"><div class="section__head"><h3>Назначенное лечение</h3><span class="section__meta">подписанные решения</span></div>${anBars(treatments, { empty: 'Подписанных решений за период нет.' })}</section>
                <section class="an-sec"><div class="section__head"><h3>Загрузка расписания</h3><span class="pill pill--warn">демонстрационная оценка</span></div><p class="an-big">${capacity ? pct(Math.round(occupied / capacity * 1000) / 10) : '—'} <span class="muted">условно</span></p><p class="an-rule"><b>Не фактическая загрузка.</b> Вместимость в прототипе условная — 16 слотов на каждую группу в день с записями, как в Stage 3.5; реальная вместимость задаётся сеткой консилиума и здесь не рассчитывается. Занято ${occupied} из условных ${capacity} слотов, дней с записями: ${activeDays}. Записи без времени (${rows.filter(c => !c.time).length}) слот не занимают. Специалисты нужны в ${rows.filter(c => c.needsSurgeon || c.needsRadiotherapist).length} записях.</p></section>
                <section class="an-sec"><div class="section__head"><h3>Схемы химиотерапии</h3></div>${anBars(schemes, { empty: 'Подписанных схем за период нет.' })}</section>
            </div></section>
            <p class="an-rule">Считается по синтетическим записям консилиума во вкладке; сервер не вызывается.</p>`;
    }

    /* ---------- Сообщения, вход, недоступные разделы ---------- */
    function pageMessages() {
        const list = myConvs();
        const gate = demoGate('Сообщений нет');
        return `<div class="page-head"><div><h1>Сообщения</h1><p class="page-head__sub">Переписка с вашими пациентами</p></div></div>
            ${gate || (list.length ? `<ul class="agenda__list arows">${list.map(c => { const p = D.patients.find(x => x.id === c.patientId); const last = c.messages.filter(m => m.from !== 'system').slice(-1)[0]; return `<li><button class="arow agenda__item--btn" type="button" data-act="chat" data-conv="${c.id}"><span class="arow__cell"><span class="arow__main arow__name">${esc(p.name)}</span><span class="arow__sub">${esc(STAGE(p.stage))}</span></span><span class="arow__cell"><span class="arow__main arow__clip">${esc(last ? last.text : '')}</span><span class="arow__sub">${last ? esc(fmtStampShort(last.at)) : ''}</span></span><span class="arow__cell arow__status">${c.unread ? `<span class="status status--info">${c.unread} ${plural(c.unread, 'новое', 'новых', 'новых')}</span>` : c.status === 'read_only' ? '<span class="status status--muted">только чтение</span>' : '<span class="status status--muted">прочитано</span>'}</span></button></li>`; }).join('')}</ul>` : '<div class="table-wrap"><div class="state"><h2>Сообщений нет</h2></div></div>')}`;
    }
    const fmtStampShort = (s) => s ? whenShort(s.slice(0, 10), s.length > 10 ? s.slice(11, 16) : '') : '';
    function pageLogin() {
        return `<div class="login"><div class="login__card"><span class="brand__mark" aria-hidden="true">СП</span><h1>Вход в систему</h1>
            <p class="muted">Прототип: пароль не нужен, выберите роль для входа. Используются только синтетические данные.</p>
            <form data-form="login" novalidate><fieldset class="field" style="border:0;padding:0;margin:0"><legend class="field__label">Роль</legend>
            ${Object.entries(ROLES).map(([k, r]) => `<label class="radio"><input type="radio" name="role" value="${k}" ${k === S.role ? 'checked' : ''}> ${esc(r.label)}</label>`).join('')}</fieldset>
            <button class="btn btn--primary" type="submit" style="width:100%;margin-top:var(--sp-4)">Войти</button></form></div></div>`;
    }
    function pageDenied(title) {
        return `<div class="state"><h2>Раздел «${esc(title)}» недоступен</h2><p>Для вашей роли этот раздел не входит в меню.</p><a class="btn" href="#/${HOME[S.role]}">На главную</a></div>`;
    }

    /* ---------- Онкоконсилиум (сокращённо: показан общий подход) ---------- */
    const CONS_STATUS = { recorded: ['Записан', 'muted'], in_review: ['На рассмотрении', 'info'], signed: ['Решение подписано', 'good'], needs_revision: ['Возвращён на доработку', 'warn'], revised: ['Доработан', 'info'] };
    const consSpecialists = (c) => [c.needsSurgeon && 'Хирург', c.needsRadiotherapist && 'Лучевой терапевт'].filter(Boolean);
    function consRow(c, withDate) {
        const p = D.patients.find(x => x.id === c.patientId);
        const open = p && scope().includes(p);
        const spec = consSpecialists(c);
        const st = CONS_STATUS[c.status] || [c.status, 'muted'];
        const tag = open ? 'a' : 'div';
        return `<li><${tag} class="crow"${open ? ` href="#/patient/${c.patientId}"` : ''}>
            <span class="arow__cell crow__time">${c.time ? `<span class="arow__main mono">${esc(c.time)}</span>` : '<span class="arow__main crow__notime">Время не назначено</span>'}<span class="arow__sub">${withDate ? esc(fmtShort(c.date)) + ' · ' : ''}${esc(D.GROUPS[c.group] || '')}</span></span>
            <span class="arow__cell"><span class="arow__main arow__name">${esc(c.patientName)}</span><span class="arow__sub">${esc(c.localization || '')} · <span class="mono">${esc(c.icd)}</span> · врач ${esc(c.doctor)}</span></span>
            <span class="arow__cell"><span class="arow__main">${spec.length ? esc(spec.join(', ')) : '<span class="muted">Не требуются</span>'}</span>${spec.length ? `<span class="arow__sub">${spec.length === 2 ? 'нужны два специалиста' : 'нужен один специалист'}</span>` : ''}</span>
            <span class="arow__cell arow__status"><span class="status status--${st[1]}">${st[0]}</span></span></${tag}></li>`;
    }
    function pageConsilium() {
        const mine = S.role === 'chair' || R().dev || S.role === 'deputy' || S.role === 'caop' ? D.consilium : D.consilium.filter(c => scope().some(p => p.id === c.patientId));
        const byTime = (a, b) => (a.date + (a.time || '99')).localeCompare(b.date + (b.time || '99'));
        const today = mine.filter(c => c.date === TODAY && !c.archived).sort(byTime);
        const later = mine.filter(c => c.date > TODAY && !c.archived).sort(byTime);
        const gate = demoGate('Записей на консилиум нет');
        const head = '<li class="crow crow--head" aria-hidden="true"><span>Время</span><span>Пациент</span><span>Специалисты</span><span>Статус решения</span></li>';
        const list = (rows, withDate) => rows.length ? `<ul class="agenda__list crows">${head}${rows.map(c => consRow(c, withDate)).join('')}</ul>` : '<div class="agenda__list"><p class="agenda__empty">Записей нет.</p></div>';
        return `<div class="page-head"><div><h1>Онкологический консилиум</h1><p class="page-head__sub">${S.role === 'chair' ? 'Очередь консилиума' : 'Записи ваших пациентов'}</p></div>
                <div class="page-head__actions">${S.role === 'chair' ? '<a class="btn" href="../redesign/#/consilium">Рассмотрение и подписание (Stage 3.5)</a>' : ''}${R().write && S.role !== 'chair' ? '<button class="btn btn--primary" type="button" data-act="book">Записать на консилиум</button>' : ''}</div></div>
            <div class="banner banner--info" role="note"><span class="banner__body">Здесь перепроектирован только список записей. Полная сетка расписания, запись, рассмотрение, подписание решения и архив работают в <a href="../redesign/#/consilium">прототипе Stage 3.5</a> и сюда не перенесены.</span></div>
            ${gate || `<div class="agenda"><section class="agenda__group" aria-labelledby="cToday"><h2 id="cToday">Сегодня, ${fmtShort(TODAY)}<span class="count">${today.length}</span></h2>${list(today, false)}</section>
                <section class="agenda__group" aria-labelledby="cLater"><h2 id="cLater">Позже<span class="count">${later.length}</span></h2>${list(later, true)}</section></div>`}`;
    }
    function pageLater(title, id) {
        return `<div class="page-head"><div><h1>${navIcon(id)}${esc(title)}</h1></div></div><div class="later"><b>Раздел работает как в Stage 3.5</b><p>В этом прототипе перепроектированы рабочий день, список и карточка пациента, уведомления. Раздел «${esc(title)}» остаётся на своём месте в меню и работает без изменений.</p><p><a class="btn" href="../redesign/#/${esc(id)}">Открыть раздел «${esc(title)}» в Stage 3.5</a></p></div>`;
    }

    /* ---------- ИИ-помощник: боковая панель (по образцу Stage 3.5) ----------
       В приложении: POST /api/ai/chat, только чтение, с проверкой области доступа на сервере.
       Здесь модели нет: ответы заготовлены и считаются по синтетическим данным в области
       доступа текущей роли. Сетевых запросов прототип не делает. */
    const AI = { open: false, messages: [], ctx: null, loading: false, lastFocus: null, timer: 0, gen: 0 };
    // Отмена ожидающего демонстрационного ответа: при смене роли, выходе и очистке диалога.
    const cancelAIPending = () => { clearTimeout(AI.timer); AI.timer = 0; AI.gen++; AI.loading = false; };
    const AI_SUGGESTIONS = {
        doctor: ['Какие мои пациенты требуют внимания?', 'Что у меня на сегодня?', 'Есть ли у моих пациентов просрочки?'],
        head: ['Кому в отделении требуется решение заведующего?', 'Где больше всего просрочек?', 'Как распределена нагрузка по врачам?'],
        other: ['Сколько пациентов требуют внимания?', 'Какая самая частая причина внимания?', 'Что у меня на сегодня?'],
        patient: ['Какой этап и что дальше?', 'Есть ли просрочки у пациента?', 'Что не выполнено в чек-листе?'],
    };
    const aiAvailable = () => S.demo !== 'error';
    const overdueCount = (p) => CORE.overdueEvents(p, TODAY, D.TERMINAL).length;
    const aiRef = (p, sub) => ({ id: p.id, title: p.name, sub: sub || `${STAGE(p.stage)} · ${p.doctor || 'врач не назначен'}` });
    function aiAnswer(text) {
        const q = text.toLowerCase();
        const list = scope();
        const needs = list.filter(p => overdueCount(p) || openIssues(p).length);
        const ctx = AI.ctx && D.patients.find(p => p.id === AI.ctx);
        if (/(измени|переведи|перевед|отметь|удали|создай|запиши|назначь|закрой|отправь|исправь)/.test(q)) return { text: 'Помощник работает только в режиме просмотра: он не создаёт, не изменяет и не удаляет данные, не переводит этапы и не отправляет сообщения. Это действие выполняется в соответствующем разделе системы.' };
        if (/(диагноз постав|какое лечение|назначить лечение|тяжест|прогноз|дозиров)/.test(q)) return { text: 'Помощник не ставит диагнозы, не назначает лечение и не оценивает медицинскую тяжесть — это решение врача и консилиума. Могу показать сведения о маршруте и событиях в пределах вашей области доступа.' };
        if (/(телефон|полис|снилс|паспорт)/.test(q)) return { text: 'Телефоны, полисы ОМС и другие персональные идентификаторы помощник не запрашивает и не раскрывает. Их можно посмотреть в карточке пациента, если она вам доступна.' };
        if (ctx && !/(мои|отделени|нагрузк|сколько пациентов|причин)/.test(q)) {
            const ne = nextEvent(ctx); const c = cur(ctx);
            const cl = D.helpers.checklistFor(ctx.nosology, ctx.stage);
            const done = cl.required.filter(n => c && c.checklist[n]).length;
            const missing = cl.required.filter(n => !(c && c.checklist[n]));
            const od = overdueCount(ctx); const iss = openIssues(ctx);
            const lines = [
                `${ctx.name}: этап «${STAGE(ctx.stage)}» с ${fmtFull(c.enteredAt)}, лечащий врач — ${ctx.doctor.replace(/\.$/, '')}.`,
                od ? `Просроченных событий: ${od}.` : 'Просроченных событий нет.',
                iss.length ? `Отметка внимания: «${D.ATTENTION_REASONS[iss[0].reasonCode]}» с ${fmtFull(iss[0].createdAt)}.` : 'Открытых отметок внимания нет.',
                ne ? `Ближайшее событие: ${ne.title}, ${ne.ev.date ? fmtFull(ne.ev.date) + (ne.ev.time ? ', ' + ne.ev.time : '') + ` (${ne.label})` : 'дата не назначена'}.` : 'Запланированных событий нет.',
                cl.required.length ? `Чек-лист этапа: выполнено ${done} из ${cl.required.length}.${missing.length ? '\nНе выполнено:\n' + missing.map(n => `• ${n}`).join('\n') : ''}` : 'Чек-лист для текущего этапа не предусмотрен.',
                isTerminal(ctx) ? 'Маршрут завершён.' : `Следующий этап по маршруту: ${ctx.stage === 'consult' ? 'по решению консилиума' : STAGE(NEXT[ctx.stage] || '')}.`,
            ];
            return { text: lines.join('\n'), refs: [aiRef(ctx)] };
        }
        if (/нагрузк|распредел/.test(q)) {
            if (S.role === 'doctor' || S.role === 'chair') return { text: 'Сводка по отделению недоступна вашей роли. Могу показать ваших пациентов, требующих внимания, и события на сегодня.' };
            const rows = D.DOCTORS.map(d => ({ d, n: list.filter(p => p.doctorId === d.id).length })).filter(x => x.n).sort((a, b) => b.n - a.n);
            return { text: rows.length ? `Пациенты по лечащим врачам в вашей области доступа:\n${rows.map(x => `• ${x.d.short} — ${x.n}`).join('\n')}` : 'В вашей области доступа пациентов нет.' };
        }
        if (/решени. заведующ/.test(q)) {
            const ps = list.filter(p => openIssues(p).some(a => a.reasonCode === 'department_head_decision_required'));
            return ps.length ? { text: `Решение заведующего требуется для ${ps.length} ${plural(ps.length, 'пациента', 'пациентов', 'пациентов')}:`, refs: ps.slice(0, 5).map(p => aiRef(p)) } : { text: 'Сейчас нет пациентов с отметкой «Требуется решение заведующего».' };
        }
        if (/просроч/.test(q)) {
            const ps = list.filter(p => overdueCount(p)).sort((a, b) => overdueCount(b) - overdueCount(a));
            if (!ps.length) return { text: 'Просроченных событий в вашей области доступа нет.' };
            const total = ps.reduce((n, p) => n + overdueCount(p), 0);
            return { text: `Просрочено ${total} ${plural(total, 'событие', 'события', 'событий')} у ${ps.length} ${plural(ps.length, 'пациента', 'пациентов', 'пациентов')}${ps.length > 5 ? '. Больше всего:' : ':'}`, refs: ps.slice(0, 5).map(p => aiRef(p, `${nextEvent(p) ? nextEvent(p).label : 'просрочено'} · ${p.doctor}`)) };
        }
        if (/сегодня/.test(q)) {
            const evs = list.flatMap(p => isTerminal(p) ? [] : cur(p).events.filter(e => e.date === TODAY && !e.actualDate).map(e => ({ e, p }))).sort((a, b) => (a.e.time || '99').localeCompare(b.e.time || '99'));
            if (!evs.length) return { text: 'На сегодня запланированных событий в вашей области доступа нет.' };
            return { text: `На сегодня ${evs.length} ${plural(evs.length, 'событие', 'события', 'событий')}:\n${evs.map(x => `• ${x.e.time || 'время не указано'} — ${eventTitle(x.p)}`).join('\n')}`, refs: evs.slice(0, 5).map(x => aiRef(x.p, `${x.e.time || 'время не указано'} · ${eventTitle(x.p)}`)) };
        }
        if (/причин/.test(q)) {
            const counts = Object.entries(D.ATTENTION_REASONS).map(([k, v]) => ({ v, n: list.filter(p => openIssues(p).some(a => a.reasonCode === k)).length })).filter(x => x.n).sort((a, b) => b.n - a.n);
            return counts.length ? { text: `Самая частая причина внимания — «${counts[0].v}» (${counts[0].n}).${counts.length > 1 ? '\n' + counts.slice(1).map(x => `• ${x.v} — ${x.n}`).join('\n') : ''}` } : { text: 'Открытых отметок внимания в вашей области доступа нет.' };
        }
        if (/внимани/.test(q)) {
            if (!needs.length) return { text: 'Пациентов, требующих внимания, в вашей области доступа нет.' };
            return { text: `Требуют внимания ${needs.length} ${plural(needs.length, 'пациент', 'пациента', 'пациентов')}${needs.length > 5 ? ', первые пять:' : ':'}`, refs: needs.slice(0, 5).map(p => aiRef(p, overdueCount(p) ? `${nextEvent(p) ? nextEvent(p).label : 'есть просрочка'} · ${p.doctor}` : `${D.ATTENTION_REASONS[openIssues(p)[0].reasonCode]} · ${p.doctor}`)) };
        }
        return { text: 'В прототипе ответы заготовлены только для вопросов о внимании, просрочках, событиях на сегодня, нагрузке врачей, причинах внимания и о выбранном пациенте. В приложении на вопросы по доступным данным отвечает модель.' };
    }
    function renderAI() {
        const body = $('#aiBody'); if (!body) return;
        const avail = aiAvailable();
        const ctx = AI.ctx && D.patients.find(p => p.id === AI.ctx);
        $('#aiSub').textContent = avail ? (ctx ? `Контекст: пациент ${ctx.name}` : `Контекст: ${R().scopeText.toLowerCase()}`) : 'Помощник недоступен, основная система работает';
        const sugg = AI_SUGGESTIONS[ctx ? 'patient' : S.role === 'doctor' ? 'doctor' : S.role === 'head' ? 'head' : 'other'];
        const msgs = AI.messages.map(m => `<article class="ai-msg ai-msg--${m.role}" aria-label="${m.role === 'user' ? 'Ваш вопрос' : 'Ответ помощника'}">
            <div class="ai-msg__role">${m.role === 'user' ? 'Вы' : 'Помощник'}</div>
            <div class="ai-msg__text">${esc(m.text)}</div>
            ${m.role === 'assistant' ? '<span class="ai-demo-tag">Заготовленный ответ прототипа · не модель</span>' : ''}
            ${m.refs && m.refs.length ? `<ul class="ai-refs">${m.refs.map(r => `<li class="ai-ref"><span class="ai-ref__body"><span class="ai-ref__title">${esc(r.title)}</span><span class="ai-ref__sub">${esc(r.sub)}</span></span><button class="btn btn--sm" type="button" data-ai-open="${r.id}" aria-label="Открыть карточку: ${esc(r.title)}">Открыть</button></li>`).join('')}</ul>` : ''}
        </article>`).join('');
        body.innerHTML = `<div class="ai-scroll" id="aiScroll">
            <p class="ai-note"><b>Только просмотр.</b> Помощник не изменяет данные и не заменяет решение врача. Видит только пациентов в вашей области доступа. История хранится в этой вкладке.<br><b>Прототип:</b> модель не подключена, ответы заготовлены по синтетическим данным.</p>
            ${ctx ? `<div class="ai-context"><span>Пациент: ${esc(ctx.name)}</span><button class="btn btn--sm btn--text" type="button" data-ai-clearctx aria-label="Убрать контекст пациента">Убрать</button></div>` : ''}
            ${avail ? `<div class="ai-suggest" role="group" aria-label="Примеры вопросов">${sugg.map(q => `<button class="ai-chip" type="button" data-ai-suggest="${esc(q)}">${esc(q)}</button>`).join('')}</div>
                <div class="ai-log" id="aiLog" role="log" aria-live="polite" aria-relevant="additions">
                    ${msgs || `<div class="ai-empty"><b>${ctx ? 'Спросите о пациенте' : 'Спросите о доступных пациентах, внимании или просрочках'}</b><span>Пример вопроса подставится в поле ввода — его можно изменить перед отправкой.</span></div>`}
                    ${AI.loading ? '<div class="ai-thinking" role="status">Помощник готовит ответ…</div>' : ''}
                </div></div>
                <form class="ai-composer" id="aiForm" novalidate>
                    <label class="sr-only" for="aiInput">Сообщение помощнику</label>
                    <textarea class="textarea" id="aiInput" rows="2" maxlength="4000" placeholder="Задайте вопрос по доступным данным" ${AI.loading ? 'disabled' : ''}></textarea>
                    <div class="ai-composer__row"><span class="ai-composer__hint">Enter — отправить, Shift+Enter — новая строка</span>
                        <button type="button" class="btn btn--sm btn--text" data-ai-clear ${AI.messages.length ? '' : 'disabled'}>${AI.loading ? 'Отменить и очистить' : 'Очистить диалог'}</button>
                        <button type="submit" class="btn btn--sm btn--primary" ${AI.loading ? 'disabled' : ''}>Отправить</button></div>
                </form>`
            : `<div class="ai-unavailable" role="status"><b>ИИ-помощник временно недоступен</b><span>Основная система продолжает работать. В прототипе доступность задаёт переключатель «Состояние экрана».</span><button class="btn btn--sm" type="button" data-ai-retry>Проверить снова</button></div></div>`}`;
        const sc = $('#aiScroll'); if (sc && AI.messages.length) sc.scrollTop = sc.scrollHeight;
    }
    function openAI(ctxId) {
        closeMenus(); toggleBell(false);
        AI.ctx = ctxId || null;
        if (!AI.open) AI.lastFocus = document.activeElement;
        AI.open = true;
        const panel = $('#aiPanel'), scrim = $('#aiScrim');
        renderAI();
        panel.hidden = false; scrim.hidden = false;
        void panel.offsetWidth;
        panel.classList.add('is-open'); scrim.classList.add('is-open');
        $('#aiBtn').setAttribute('aria-expanded', 'true');
        ($('#aiInput') || $('#aiClose')).focus({ preventScroll: true });
    }
    function closeAI() {
        if (!AI.open) return;
        AI.open = false;
        const panel = $('#aiPanel'), scrim = $('#aiScrim');
        panel.classList.remove('is-open'); scrim.classList.remove('is-open');
        $('#aiBtn').setAttribute('aria-expanded', 'false');
        setTimeout(() => { if (!AI.open) { panel.hidden = true; scrim.hidden = true; } }, 240);
        const lf = AI.lastFocus;
        const back = lf && lf !== document.body && document.contains(lf) ? lf : (lf && lf.dataset && lf.dataset.act === 'assistant' && $('.phead [data-act=assistant]')) || $('#aiBtn');
        back.focus({ preventScroll: true });
    }
    function sendAI(text) {
        const msg = text.trim();
        if (!msg || AI.loading || !aiAvailable()) return;
        AI.messages.push({ role: 'user', text: msg });
        AI.loading = true; renderAI();
        const gen = AI.gen;
        const a = aiAnswer(msg);   // ответ считается по роли и контексту на момент вопроса
        AI.timer = setTimeout(() => {
            if (gen !== AI.gen) return;   // ответ отменён
            AI.timer = 0;
            AI.messages.push({ role: 'assistant', text: a.text, refs: a.refs || [] });
            AI.loading = false; renderAI();
            const input = $('#aiInput'); if (input && AI.open) input.focus({ preventScroll: true });
        }, 650);
    }
    const resetAI = () => { cancelAIPending(); AI.messages = []; AI.ctx = null; if (AI.open) renderAI(); };

    /* ---------- Уведомления сотрудника (колокольчик) ---------- */
    function bellItems() {
        const items = [];
        scope().forEach(p => {
            const ne = nextEvent(p);
            if (ne && ne.kind === 'overdue') items.push({ id: `o-${p.id}`, p, title: `Просрочено: ${ne && ne.title}`, note: ne.label, tone: 'danger' });
            if (ne && ne.kind === 'today') items.push({ id: `t-${p.id}`, p, title: `Сегодня: ${ne && ne.title}`, note: ne.ev.time || 'время не указано', tone: 'info' });
            openIssues(p).forEach(x => items.push({ id: `a-${x.id}`, p, title: D.ATTENTION_REASONS[x.reasonCode], note: 'требует внимания', tone: 'warn' }));
        });
        if (EXT.hooks.bellItems) items.push(...EXT.hooks.bellItems());
        return items;
    }
    function toggleBell(force) {
        const panel = $('#bellPanel');
        const open = force != null ? force : panel.hidden;
        if (!open) { panel.hidden = true; $('#bellBtn').setAttribute('aria-expanded', 'false'); return; }
        const items = bellItems();
        panel.innerHTML = `<div class="bell-panel__head"><h2 id="bellTitle">Уведомления сотрудника</h2>${items.length ? '<button class="btn btn--text btn--sm" type="button" data-act="read-all">Прочитать все</button>' : ''}</div>
            <p class="muted" style="padding:0 var(--sp-4) var(--sp-2);font-size:var(--fs-sm)">Напоминания вам, а не сообщения пациентам.</p>
            ${items.length ? `<ul class="bell-list">${items.map(i => `<li><a href="${i.href || `#/patient/${i.p.id}`}" data-bell="${i.id}"><span class="status status--${i.tone}">${esc(i.title)}</span><span>${esc(i.p.name)}</span><small>${esc(i.note)}${S.read.has(i.id) ? ' · прочитано' : ''}</small></a></li>`).join('')}</ul>` : '<p class="agenda__empty">Новых уведомлений нет.</p>'}`;
        panel.hidden = false;
        $('#bellBtn').setAttribute('aria-expanded', 'true');
        const first = panel.querySelector('a, button'); if (first) first.focus();
    }

    /* ---------- Меню ---------- */
    function closeMenus(except) {
        $$('.menu').forEach(m => { if (m !== except) m.hidden = true; });
        $$('[data-menu]').forEach(b => { if (!except || b.dataset.menu !== except.id) b.setAttribute('aria-expanded', 'false'); });
    }
    function toggleMenu(btn) {
        const menu = document.getElementById(btn.dataset.menu);
        const open = menu.hidden;
        closeMenus(open ? menu : null);
        menu.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        if (open) { const f = menu.querySelector('[role=menuitem], select, button'); if (f) f.focus(); }
    }

    /* ---------- Диалоги и тосты ---------- */
    let lastFocus = null;
    function dialog({ title, body, actions, danger, icon }) {
        lastFocus = document.activeElement;
        // Пункт меню скрывается вместе с меню — фокус вернётся на кнопку меню.
        const fromMenu = lastFocus && lastFocus.closest('.menu');
        if (fromMenu) lastFocus = document.querySelector(`[data-menu="${fromMenu.id}"]`);
        const layer = $('#layer');
        layer.innerHTML = `<div class="scrim" data-scrim><div class="dialog${danger ? ' dialog--danger' : ''}" role="${danger ? 'alertdialog' : 'dialog'}" aria-modal="true" aria-labelledby="dlgTitle" aria-describedby="dlgBody">
            <div class="dialog__head">${icon ? `<span class="dialog__icon">${icon}</span>` : ''}<h2 id="dlgTitle">${title}</h2></div>
            <div class="dialog__body" id="dlgBody">${body}</div>
            <div class="dialog__foot">${actions.map((a, i) => `<button class="btn${a.kind ? ' btn--' + (a.kind === 'danger' ? 'danger-solid' : a.kind) : ''}" type="button" data-dlg="${i}" ${a.autofocus ? 'data-autofocus' : ''}>${a.label}</button>`).join('')}</div></div></div>`;
        const dlg = $('.dialog', layer);
        dlg.addEventListener('click', (e) => {
            const b = e.target.closest('[data-dlg]');
            if (!b) return;
            const a = actions[Number(b.dataset.dlg)];
            if (a.run && a.run(dlg) === false) return;
            closeDialog();
        });
        (dlg.querySelector('[data-autofocus]') || dlg.querySelector('input, select, textarea, button')).focus();
    }
    function closeDialog() {
        $('#layer').innerHTML = '';
        if (lastFocus === document.body) lastFocus = null;
        if (lastFocus && document.contains(lastFocus)) { lastFocus.focus(); return; }
        // Экран перерисован: вернуть фокус на ту же по смыслу кнопку или на заголовок.
        const sel = lastFocus && (lastFocus.dataset.menu ? `[data-menu="${lastFocus.dataset.menu}"]` : lastFocus.dataset.act ? `[data-act="${lastFocus.dataset.act}"]` : null);
        const again = sel && document.querySelector(sel);
        const target = again || document.querySelector('#main h1');
        if (target) { if (target.tagName === 'H1') target.setAttribute('tabindex', '-1'); target.focus(); }
    }
    function toast(text, tone) {
        const el = document.createElement('div');
        el.className = `toast${tone ? ' toast--' + tone : ''}`;
        el.textContent = text;
        $('#toasts').appendChild(el);
        setTimeout(() => el.remove(), 4200);
    }
    const now = () => { const d = new Date(); return `${TODAY}T${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
    const currentPatient = () => { const r = parseRoute(); return r.name === 'patient' ? D.patients.find(p => p.id === r.id) : null; };
    const touch = (p) => { p.updatedAt = now(); D.helpers.derive(p); };

    /* ---------- Действия ---------- */
    const ACTIONS = {
        retry() { S.demo = 'ok'; render(); toast('Данные загружены'); },
        'close-menus'() { closeMenus(); },
        'clear-all'() { Object.assign(S.list, { q: '', stage: '', doctor: '', deadline: '', reason: '', priority: '' }); render(); $('#listQ') && $('#listQ').focus(); },
        'clear-extra'() { Object.assign(S.list, { deadline: '', reason: '', priority: '' }); render(); },
        'new-patient'(btn, e) {
            if (e) e.preventDefault();
            dialog({ title: 'Новый пациент', body: '<p>Форма создания пациента в этом прототипе не перепроектировалась: она будет показана на следующем этапе вместе с поиском по МКБ-10.</p><p class="muted">В Stage 3.5 форма доступна полностью.</p>', actions: [{ label: 'Понятно', kind: 'primary', autofocus: true }] });
        },
        import() { dialog({ title: 'Загрузить пациентов из файла', body: '<p>Сначала система проверит файл и покажет, что изменится: сколько пациентов будет добавлено, есть ли конфликты и подтверждено ли происхождение файла. Ничего не меняется до вашего подтверждения.</p><p class="muted">В прототипе файлы не загружаются.</p>', actions: [{ label: 'Закрыть', autofocus: true }] }); },
        export() { toast(`Выгрузка ${scope().length} ${plural(scope().length, 'пациента', 'пациентов', 'пациентов')} подготовлена (в прототипе файл не создаётся)`); },
        copy(btn) {
            const p = D.patients.find(x => x.id === btn.dataset.id);
            const ne = nextEvent(p);
            const text = `${p.name}, ${p.age} ${plural(p.age, 'год', 'года', 'лет')}\nДиагноз: ${p.icd} (${p.nosologyLabel})\nЭтап: ${STAGE(p.stage)}\nБлижайшее событие: ${ne && ne.ev.date ? fmtFull(ne.ev.date) + ' (' + ne.label + ')' : 'нет'}\nЛечащий врач: ${p.doctorFull}`;
            const done = () => toast('Сводка скопирована в буфер обмена');
            // Если браузер не дал доступ к буферу — показать сводку выделенной, чтобы скопировать вручную.
            const manual = () => { dialog({ title: 'Сводка пациента', body: `<p>Браузер не разрешил автоматическое копирование. Текст уже выделен — нажмите Ctrl+C (⌘C на Mac).</p><label class="sr-only" for="sumText">Сводка</label><textarea class="textarea" id="sumText" rows="6" readonly>${esc(text)}</textarea>`, actions: [{ label: 'Закрыть', kind: 'primary' }] }); const t = $('#sumText'); t.focus(); t.select(); };
            if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, manual);
            else manual();
        },
        assistant() { const p = currentPatient(); openAI(p ? p.id : null); },
        advance() {
            const p = currentPatient(); const lastEv = cur(p).events[cur(p).events.length - 1]; const ne = lastEv && !lastEv.actualDate ? { ev: lastEv } : null;
            const fact = lastEv && lastEv.actualDate ? lastEv : null;   // существующий факт перевод не меняет
            const nEv = Math.max(cur(p).events.length, 1);
            // С этапа «Направлен на ОК» — те же варианты перехода, что в карточке Stage 3.5; решение ОК здесь не фиксируется.
            const targets = p.stage === 'consult' ? ['treatment-prep', 'exam', 'dispensary-observation'] : [NEXT[p.stage]];
            dialog({
                title: targets.length > 1 ? 'Перевести на этап' : `Перевести на этап «${esc(STAGE(targets[0]))}»?`,
                body: `<p>Сейчас: <b>${esc(STAGE(p.stage))}</b>. Отметьте, когда фактически состоялось событие текущего этапа.</p>
                    ${targets.length > 1 ? `<fieldset class="field" style="border:0;padding:0;margin:0"><legend class="field__label">Новый этап</legend>${targets.map(t => `<label class="radio"><input type="radio" name="advTo" value="${t}"> ${esc(STAGE(t))}</label>`).join('')}</fieldset>` : ''}
                    <div class="row"><div class="field"><label class="field__label" for="advD">Фактическая дата</label><input class="input" id="advD" type="date" max="${TODAY}" value="${fact ? esc(fact.actualDate) : TODAY}" required></div>
                    <div class="field"><label class="field__label" for="advT">Время</label><input class="input" id="advT" type="time" value="${fact ? esc(fact.actualTime || '') : ne && ne.ev.time ? esc(ne.ev.time) : ''}"></div></div>
                    <p class="field__error" id="advErr" role="alert" hidden></p>
                    <p class="muted">${ne && ne.ev.date ? `По плану: ${fmtFull(ne.ev.date)}${ne.ev.time ? ', ' + esc(ne.ev.time) : ''}. Если даты различаются, событие будет отмечено «с отклонением».` : 'Плановая дата не была назначена.'}</p>
                    ${fact ? `<p class="banner banner--info" style="margin:var(--sp-2) 0"><span class="banner__body">Факт события ${nEv} из ${nEv} уже отмечен: <b>${esc(fmtFull(fact.actualDate))}${fact.actualTime ? ', ' + esc(fact.actualTime) : ''}</b>. Перевод его не изменит; другая дата или ${fact.actualTime ? 'другое время' : 'любое указанное время'} заблокируют перевод.</span></p>`
                        : `<p class="muted">Факт будет записан в событие ${nEv} из ${nEv} текущего этапа${cur(p).events.length ? '' : ' (событие будет создано)'}.</p>`}
                    ${p.stage === 'consult' ? '<p class="muted">Перевод этапа не создаёт и не меняет ручную отметку решения ОК и не связан с подписанным решением председателя. Отметка делается отдельно: «Отметить решение ОК».</p>' : ''}`,
                actions: [{ label: 'Отмена' }, { label: 'Перевести на этап', kind: 'primary', run: (dlg) => {
                    const d = $('#advD', dlg).value; const err = $('#advErr', dlg);
                    if (!d || d > TODAY) { err.hidden = false; err.textContent = !d ? 'Укажите фактическую дату.' : 'Фактическая дата не может быть в будущем.'; $('#advD', dlg).focus(); return false; }
                    const conflict = CORE.stageChangeConflict(p, { actualDate: d, actualTime: $('#advT', dlg).value });
                    if (conflict) { err.hidden = false; err.textContent = `У события ${conflict.eventIndex + 1} уже записан факт ${fmtFull(conflict.actualDate)}${conflict.actualTime ? ', ' + conflict.actualTime : ' (время не указано)'}. Перевод не изменяет существующий факт и не выполнен. Укажите дату ${fmtFull(conflict.actualDate)} и ${conflict.actualTime ? 'время ' + conflict.actualTime + ' или оставьте поле «Время» пустым' : 'оставьте поле «Время» пустым'}. Если факт неверен, его можно исправить в событии на вкладке «События и даты».`; $('#advD', dlg).focus(); return false; }
                    const picked = targets.length > 1 ? $('input[name=advTo]:checked', dlg) : null;
                    if (targets.length > 1 && !picked) { err.hidden = false; err.textContent = 'Выберите новый этап.'; $('input[name=advTo]', dlg).focus(); return false; }
                    const to = picked ? picked.value : targets[0];
                    moveStage(p, to, d, $('#advT', dlg).value);
                    toast(`Пациент переведён: ${STAGE(to)}`);
                } }],
            });
        },
        decision() {
            const p = currentPatient();
            const avail = CORE.decisionAvailability(p, S.role, scope().includes(p));
            if (!avail.available) {
                const why = { not_consult: 'Отметка доступна только на этапе «Направлен на ОК».', no_edit_right: 'Ваша роль не позволяет изменять карточку этого пациента.', no_events: 'У этапа «Направлен на ОК» нет событий. Добавьте событие во вкладке «События и даты».', all_marked: 'Во всех событиях этапа «Направлен на ОК» отметка уже сохранена. Повторная запись не выполняется: процедура изменения решения ещё не согласована (WP2.2).' }[avail.reason];
                const existing = avail.reason === 'all_marked' ? `<ul class="plain-list">${avail.events.map(({ ev, eventIndex }) => `<li>Событие ${eventIndex + 1}: ${esc(D.DECISIONS[ev.consiliumDecision])}${ev.decisionMark ? ` — ${esc(ev.decisionMark.by)}, ${esc(fmtStamp(ev.decisionMark.at))}` : ''}</li>`).join('')}</ul>` : '';
                dialog({ title: 'Отметить решение ОК', icon: ICON.lock, body: `<p>${why}</p>${existing}<p class="muted">Данные не изменены.</p>`, actions: [{ label: 'Понятно', kind: 'primary', autofocus: true }] });
                return;
            }
            const def = CORE.defaultDecisionEvent(avail);
            const evLabel = (ev) => [ev.noPlan ? 'без плана' : ev.date ? `план ${fmtFull(ev.date)}${ev.time ? ', ' + ev.time : ''}` : 'план не указан', ev.actualDate ? `факт ${fmtFull(ev.actualDate)}` : 'факт не отмечен', ev.consiliumDecision ? `сейчас: ${D.DECISIONS[ev.consiliumDecision]}` : 'отметки нет'].join(' · ');
            dialog({
                title: 'Отметить решение ОК',
                body: `<div class="banner banner--info" style="margin:0 0 var(--sp-3)"><span class="banner__body">Это <b>ручная отметка</b> в событии этапа. Она не переводит пациента на другой этап и не является подписанным решением председателя.</span></div>
                    <fieldset class="field" style="border:0;padding:0;margin:0 0 var(--sp-3)"><legend class="field__label">Событие этапа «Направлен на ОК»${avail.events.length > 1 ? ' — выберите' : ''}</legend>
                    ${avail.events.map(({ ev, eventIndex, marked }) => `<label class="radio${marked ? ' radio--disabled' : ''}"><input type="radio" name="decEv" value="${eventIndex}" ${def === eventIndex ? 'checked' : ''} ${marked ? 'disabled aria-describedby="decMarkedNote"' : ''}> Событие ${eventIndex + 1} из ${avail.events.length}: ${esc(evLabel(ev))}${marked && ev.decisionMark ? ` (${esc(ev.decisionMark.by)}, ${esc(fmtStamp(ev.decisionMark.at))})` : ''}</label>`).join('')}
                    ${avail.events.some(x => x.marked) ? '<p class="field__hint" id="decMarkedNote">В событиях с сохранённой отметкой повторная запись недоступна: процедура изменения решения ещё не согласована (WP2.2).</p>' : ''}</fieldset>
                    <fieldset class="field" style="border:0;padding:0;margin:0"><legend class="field__label">Решение ОК</legend>
                    ${Object.entries(D.DECISIONS).map(([k, v]) => `<label class="radio"><input type="radio" name="dec" value="${k}"> ${esc(v)}</label>`).join('')}</fieldset>
                    <p class="field__error" id="decErr" role="alert" hidden></p>`,
                actions: [{ label: 'Отмена' }, { label: 'Сохранить отметку', kind: 'primary', run: (dlg) => {
                    const evInput = $('input[name=decEv]:checked', dlg), decInput = $('input[name=dec]:checked', dlg), err = $('#decErr', dlg);
                    if (!evInput) { err.hidden = false; err.textContent = 'Выберите событие, к которому относится решение.'; $('input[name=decEv]', dlg).focus(); return false; }
                    if (!decInput) { err.hidden = false; err.textContent = 'Выберите решение ОК.'; $('input[name=dec]', dlg).focus(); return false; }
                    let res;
                    try { res = CORE.applyManualDecision(p, { role: S.role, inScope: scope().includes(p), eventIndex: Number(evInput.value), decision: decInput.value, at: now(), by: R().user.short }); }
                    catch (x) { err.hidden = false; err.textContent = /decision_exists/.test(x.message) ? 'В этом событии отметка уже сохранена. Повторная запись недоступна до согласования процедуры изменения решения (WP2.2).' : 'Отметку сохранить нельзя. Данные не изменены.'; return false; }
                    touch(p); render();
                    toast(`Отметка сохранена в событии ${res.eventIndex + 1}. Этап пациента не изменён.`);
                } }],
            });
        },
        'extra-exam'() { const p = currentPatient(); dialog({ title: 'Направить на дополнительное обследование?', body: '<p>Пациент вернётся на этап «На обследовании». Пройденные этапы и события сохранятся в маршруте.</p>', actions: [{ label: 'Отмена', autofocus: true }, { label: 'Направить на обследование', kind: 'primary', run: () => { moveStage(p, 'exam', ...implicitFact(p)); toast('Пациент направлен на дополнительное обследование'); } }] }); },
        'not-confirmed'() { const p = currentPatient(); dialog({ title: 'Диагноз не подтверждён?', icon: ICON.warn, danger: true, body: '<p>Маршрут пациента будет <b>завершён</b> со статусом «Диагноз не подтверждён». Дальнейшие этапы станут недоступны.</p>', actions: [{ label: 'Отмена', autofocus: true }, { label: 'Завершить маршрут', kind: 'danger', run: () => { moveStage(p, 'diagnosis-not-confirmed', ...implicitFact(p)); toast('Маршрут завершён: диагноз не подтверждён'); } }] }); },
        'change-doctor'() { const p = currentPatient(); dialog({ title: 'Сменить лечащего врача', body: `<div class="field"><label class="field__label" for="cdSel">Новый лечащий врач</label><select class="select" id="cdSel">${D.DOCTORS.map(d => `<option value="${d.id}" ${d.id === p.doctorId ? 'selected' : ''}>${esc(d.name)}</option>`).join('')}</select><span class="field__hint">Карточка станет доступна новому врачу и его заведующему.</span></div>`, actions: [{ label: 'Отмена' }, { label: 'Сменить врача', kind: 'primary', run: (dlg) => { const d = D.DOCTORS.find(x => x.id === $('#cdSel', dlg).value); Object.assign(p, { doctorId: d.id, doctor: d.short, doctorFull: d.name, dept: d.dept, head: D.HEADS[d.dept] }); touch(p); render(); toast(`Лечащий врач: ${d.short}`); } }] }); },
        'delete-patient'() {
            const p = currentPatient();
            const cons = D.consilium.some(c => c.patientId === p.id);
            const del = (S.deleted[p.id] || []).length;
            if (cons || del) {
                dialog({ title: 'Карточку нельзя удалить', icon: ICON.lock, body: `<p>${cons ? 'У пациента есть записи онкологического консилиума.' : 'У пациента есть удалённые события медицинской истории — они хранятся в базе.'} Такие карточки не удаляются, чтобы не потерять историю.</p><p class="muted">Данные не изменены.</p>`, actions: [{ label: 'Понятно', kind: 'primary', autofocus: true }] });
                return;
            }
            dialog({ title: 'Удалить пациента?', danger: true, icon: ICON.warn, body: `<p>Карточка <b>${esc(p.name)}</b> будет удалена вместе с историей маршрута, событиями и уведомлениями.</p><p><b>Это действие нельзя отменить.</b></p>`,
                actions: [{ label: 'Отмена', autofocus: true }, { label: 'Удалить пациента', kind: 'danger', run: () => { D.patients.splice(D.patients.indexOf(p), 1); location.hash = '#/patients'; toast('Карточка пациента удалена (только в этой вкладке)'); } }] });
        },
        'add-issue'() { const p = currentPatient(); dialog({ title: 'Добавить отметку внимания', body: `<div class="field"><label class="field__label" for="aiR">Причина</label><select class="select" id="aiR">${Object.entries(D.ATTENTION_REASONS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('')}</select></div><div class="field"><label class="field__label" for="aiC">Комментарий</label><textarea class="textarea" id="aiC" placeholder="Что произошло и что нужно сделать"></textarea></div>`, actions: [{ label: 'Отмена' }, { label: 'Добавить отметку', kind: 'primary', run: (dlg) => { p.attentionIssues.push({ id: `att-new-${Date.now()}`, reasonCode: $('#aiR', dlg).value, status: 'open', comment: $('#aiC', dlg).value || 'Без комментария', createdByName: R().user.short, createdAt: now() }); touch(p); render(); toast('Отметка добавлена'); } }] }); },
        'close-issue'(btn) { const p = currentPatient(); const x = p.attentionIssues.find(i => i.id === btn.dataset.issue); dialog({ title: 'Закрыть отметку внимания?', body: `<p>${esc(D.ATTENTION_REASONS[x.reasonCode])}</p><div class="field"><label class="field__label" for="ciC">Чем закончилось</label><textarea class="textarea" id="ciC" placeholder="Например: результаты получены"></textarea></div>`, actions: [{ label: 'Отмена' }, { label: 'Закрыть отметку', kind: 'primary', run: (dlg) => { Object.assign(x, { status: 'closed', closedAt: now(), closedByName: R().user.short, resolutionComment: $('#ciC', dlg).value }); touch(p); render(); toast('Отметка закрыта'); } }] }); },
        'edit-event'(btn) { S.editing = btn.dataset.key; render(); const f = $('#e-pd'); if (f) f.focus(); },
        'edit-sec'(btn) { const p = currentPatient(); S.editing = `data:${p.id}:${btn.dataset.sec}`; render(); const f = $('#dName') || $('#mIcd'); if (f) f.focus(); },
        'cancel-edit'() { S.editing = null; render(); },
        'add-event'(btn) { const p = currentPatient(); const i = Number(btn.dataset.occ); p.history[i].events.push({ date: '', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false }); S.editing = `${p.id}:${i}:${p.history[i].events.length - 1}`; render(); $('#e-pd').focus(); },
        'delete-event'(btn) {
            const p = currentPatient(); const [, i, j] = btn.dataset.key.split(':').map(Number); const ev = p.history[i].events[j];
            dialog({ title: 'Удалить событие?', body: `<p>${esc(STAGE(p.history[i].stage))}: ${ev.date ? 'план ' + fmtFull(ev.date) : 'без плановой даты'}${ev.actualDate ? ', факт ' + fmtFull(ev.actualDate) : ''}.</p><p>Событие исчезнет из маршрута, но данные сохранятся — его можно вернуть в разделе «Удалённые события».</p>`,
                actions: [{ label: 'Отмена', autofocus: true }, { label: 'Удалить событие', kind: 'danger', run: () => { p.history[i].events.splice(j, 1); (S.deleted[p.id] = S.deleted[p.id] || []).push({ occ: i, idx: j, ev, at: now(), by: R().user.short }); touch(p); render(); toast('Событие удалено. Его можно восстановить ниже.'); } }] });
        },
        'restore-event'(btn) { const p = currentPatient(); const d = S.deleted[p.id].splice(Number(btn.dataset.k), 1)[0]; p.history[d.occ].events.splice(Math.min(d.idx, p.history[d.occ].events.length), 0, d.ev); touch(p); render(); toast('Событие восстановлено на прежнее место в маршруте'); },
        'grant-consent'() { const p = currentPatient(); dialog({ title: 'Зафиксировать согласие на SMS', body: '<p>Подтвердите, что пациент дал согласие получать напоминания о посещениях.</p>', actions: [{ label: 'Отмена' }, { label: 'Зафиксировать согласие', kind: 'primary', autofocus: true, run: () => { Object.assign(p.notifications, { consent: 'granted', smsEnabled: true, consentAt: now(), consentBy: R().user.short }); touch(p); render(); toast('Согласие зафиксировано'); } }] }); },
        'revoke-consent'() { const p = currentPatient(); const pend = p.notifications.items.filter(i => i.status === 'pending').length; dialog({ title: 'Отозвать согласие на SMS?', danger: true, icon: ICON.warn, body: `<p>Пациент перестанет получать напоминания.${pend ? ` Ожидающие отправки напоминания (${pend}) будут отменены.` : ''}</p><p class="muted">Согласие можно будет зафиксировать снова.</p>`, actions: [{ label: 'Отмена', autofocus: true }, { label: 'Отозвать согласие', kind: 'danger', run: () => { p.notifications.consent = 'revoked'; p.notifications.smsEnabled = false; p.notifications.consentAt = now(); p.notifications.items.forEach(i => { if (i.status === 'pending') i.status = 'cancelled'; }); touch(p); render(); toast('Согласие отозвано'); } }] }); },
        book() { const p = currentPatient(); location.hash = `#/consilium/book${p ? '?patient=' + p.id : ''}`; },
        logout() { if (EXT.hooks.reset) EXT.hooks.reset(); closeAI(); resetAI(); S.an = { ...AN_DEFAULT }; S.anDetail = null; S.loggedOut = true; closeDialog && $('#layer').innerHTML && closeDialog(); render(); const r = $('input[name=role]:checked'); if (r) r.focus(); toast('Вы вышли из системы'); },
        'clear-complaint'() { const p = currentPatient(); dialog({ title: 'Очистить комментарий?', body: '<p>Текст комментария будет удалён из карточки.</p>', actions: [{ label: 'Отмена', autofocus: true }, { label: 'Очистить', kind: 'danger', run: () => { p.complaint = ''; touch(p); render(); toast('Комментарий очищен'); } }] }); },
        chat(btn) {
            const c = S.convs.find(x => x.id === btn.dataset.conv); const p = D.patients.find(x => x.id === c.patientId);
            c.unread = 0; renderShell();
            const who = { patient: p.name.split(' ').slice(0, 2).join(' '), doctor: 'Вы', system: 'Система' };
            const ro = c.status === 'read_only' || !R().write;
            dialog({ title: `Переписка: ${esc(p.name)}`, body: `<ol class="thread">${c.messages.map(m => `<li class="thread__msg thread__msg--${m.from}"><span class="thread__who">${esc(who[m.from])} · ${esc(fmtStampShort(m.at))}</span>${esc(m.text)}</li>`).join('')}</ol>
                ${ro ? '<p class="muted">Диалог доступен только для чтения.</p>' : '<div class="field"><label class="field__label" for="chatT">Ответ пациенту</label><textarea class="textarea" id="chatT" rows="3"></textarea><span class="field__hint">В прототипе сообщение остаётся во вкладке и никуда не отправляется.</span></div>'}`,
                actions: ro ? [{ label: 'Закрыть', kind: 'primary', autofocus: true }] : [{ label: 'Закрыть' }, { label: 'Отправить', kind: 'primary', run: (dlg) => { const t = $('#chatT', dlg).value.trim(); if (!t) { $('#chatT', dlg).focus(); return false; } c.messages.push({ id: `m-new-${Date.now()}`, from: 'doctor', at: now(), text: t, status: 'sent' }); render(); toast('Сообщение добавлено (демо, не отправлено)'); } }] });
        },
        'read-all'() { bellItems().forEach(i => S.read.add(i.id)); renderShell(); toggleBell(true); },
    };

    // Для действий без поля даты: существующий факт последнего события сохраняется, иначе — сегодняшняя дата (как раньше).
    const implicitFact = (p) => { const e = cur(p).events[cur(p).events.length - 1]; return e && e.actualDate ? [e.actualDate, e.actualTime || ''] : [TODAY, '']; };
    function moveStage(p, stage, actualDate, actualTime) {
        // core.applyStageChange: факт — в последнее событие текущего этапа; решения ОК не трогаются.
        CORE.applyStageChange(p, { to: stage, actualDate, actualTime, enteredAt: now(), scheduled: D.SCHEDULED.includes(stage) });
        touch(p);
        S.tabs[p.id] = 'overview';
        render();
    }

    function analyticsAction(a) {
        if (a === 'csv') {
            if (S.demo === 'error') { toast('Не удалось сформировать CSV: сервер не ответил.'); return; }
            if (anRangeInvalid()) { toast('Не удалось сформировать CSV: проверьте даты периода.'); return; }
            if (S.role === 'chair') return;
            const out = anCsv(anDashboard()); const link = document.createElement('a');
            link.href = URL.createObjectURL(out.blob); link.download = out.name; document.body.append(link); link.click();
            setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 0);
            toast(`CSV «${out.name}»: ${out.count} ${plural(out.count, 'показатель', 'показателя', 'показателей')} с учётом фильтров. Собран в браузере из синтетических данных.`);
            return;
        }
        if (a === 'reset') { S.an = { ...AN_DEFAULT }; S.anDetail = null; render(); $('#anPeriod').focus(); toast('Фильтры аналитики сброшены'); return; }
        if (a === 'refresh') { S.anCalcAt = new Date().toTimeString().slice(0, 5); render(); toast('Показатели пересчитаны'); return; }
        if (a === 'close-detail') { S.anDetail = null; render(); const h = $('#anNow'); if (h) { h.setAttribute('tabindex', '-1'); h.focus(); } }
    }
    /* ---------- Выпадающие списки фильтров ---------- */
    let DD = null;
    let ddType = { s: '', t: 0 };
    function ddOpen(btn) {
        ddClose();
        const list = document.getElementById(btn.getAttribute('aria-controls'));
        const opts = $$('[role=option]', list);
        list.hidden = false; btn.setAttribute('aria-expanded', 'true');
        DD = { btn, list, opts, idx: 0 };
        ddMove(Math.max(0, opts.findIndex(o => o.getAttribute('aria-selected') === 'true')));
    }
    function ddMove(i) {
        DD.idx = Math.max(0, Math.min(DD.opts.length - 1, i));
        DD.opts.forEach((o, k) => o.classList.toggle('is-active', k === DD.idx));
        const o = DD.opts[DD.idx]; DD.btn.setAttribute('aria-activedescendant', o.id); o.scrollIntoView({ block: 'nearest' });
    }
    function ddClose(focus) {
        if (!DD) return;
        const { btn, list } = DD; DD = null;
        list.hidden = true; btn.setAttribute('aria-expanded', 'false'); btn.removeAttribute('aria-activedescendant');
        if (focus) btn.focus();
    }
    function ddPick(i) {
        const { btn, opts } = DD; const v = opts[i].dataset.value; const key = btn.dataset.dd; const id = btn.id; const pop = btn.closest('.popover');
        const store = btn.dataset.store === 'an' ? S.an : btn.dataset.store === 'cons' ? S.cons : S.list;
        ddClose(true);
        if (store[key] === v) return;
        store[key] = v;
        if (store === S.an) { S.anDetail = null; if (key === 'period' && v === 'custom' && !S.an.from) Object.assign(S.an, { from: D.helpers.iso(D.helpers.addDays(parse(TODAY), -29)), to: TODAY }); }
        render();
        if (pop) $(`[data-menu="${pop.id}"]`).click();
        const again = document.getElementById(id); if (again) again.focus();
    }
    function ddTypeahead(ch) {
        const t = Date.now(); ddType.s = (t - ddType.t > 600 ? '' : ddType.s) + ch.toLowerCase(); ddType.t = t;
        const n = DD.opts.length; const start = ddType.s.length === 1 ? DD.idx + 1 : DD.idx;
        for (let k = 0; k < n; k++) { const o = DD.opts[(start + k) % n]; if (o.textContent.trim().toLowerCase().startsWith(ddType.s)) { ddMove((start + k) % n); return; } }
    }
    function ddKey(e, btn) {
        const open = DD && DD.btn === btn; const k = e.key;
        const printable = k.length === 1 && k !== ' ' && !e.ctrlKey && !e.metaKey && !e.altKey;
        if (!open) {
            if (['ArrowDown', 'ArrowUp', 'Enter', ' ', 'Home', 'End'].includes(k) || printable) {
                e.preventDefault(); ddOpen(btn);
                if (k === 'Home') ddMove(0); else if (k === 'End') ddMove(DD.opts.length - 1); else if (printable) ddTypeahead(k);
                return true;
            }
            return false;
        }
        if (k === 'ArrowDown') { e.preventDefault(); ddMove(DD.idx + 1); }
        else if (k === 'ArrowUp') { e.preventDefault(); if (e.altKey) ddPick(DD.idx); else ddMove(DD.idx - 1); }
        else if (k === 'Home' || k === 'PageUp') { e.preventDefault(); ddMove(0); }
        else if (k === 'End' || k === 'PageDown') { e.preventDefault(); ddMove(DD.opts.length - 1); }
        else if (k === 'Enter' || k === ' ') { e.preventDefault(); ddPick(DD.idx); }
        else if (k === 'Escape') { e.preventDefault(); ddClose(true); }
        else if (k === 'Tab') { ddClose(); return false; }
        else if (printable) { e.preventDefault(); ddTypeahead(k); }
        else return false;
        return true;
    }
    document.addEventListener('keyup', (e) => { if (e.key === ' ' && e.target.closest && e.target.closest('.dd__btn')) e.preventDefault(); });
    document.addEventListener('mousedown', (e) => { if (e.target.closest('.dd__opt')) e.preventDefault(); });
    document.addEventListener('mousemove', (e) => { const o = e.target.closest && e.target.closest('.dd__opt'); if (o && DD && DD.opts.includes(o)) { const i = DD.opts.indexOf(o); if (i !== DD.idx) ddMove(i); } });

    /* ---------- События ---------- */
    document.addEventListener('click', (e) => {
        const t = e.target;
        if (t.closest('#aiClose') || t.id === 'aiScrim') { closeAI(); return; }
        if (t.closest('#aiPanel')) {
            const sug = t.closest('[data-ai-suggest]');
            if (sug) { const i = $('#aiInput'); i.value = sug.dataset.aiSuggest; i.focus(); return; }
            if (t.closest('[data-ai-clearctx]')) { AI.ctx = null; renderAI(); $('#aiInput').focus(); return; }
            if (t.closest('[data-ai-clear]')) { cancelAIPending(); AI.messages = []; renderAI(); $('#aiInput').focus(); return; }
            if (t.closest('[data-ai-retry]')) { renderAI(); toast(aiAvailable() ? 'Помощник доступен' : 'Помощник по-прежнему недоступен'); return; }
            const open = t.closest('[data-ai-open]');
            // Панель закрывается, чтобы карточка была видна; история и контекст сохраняются.
            if (open) { AI.lastFocus = null; closeAI(); location.hash = `#/patient/${open.dataset.aiOpen}`; }
            return;
        }
        const anAct = t.closest('[data-an-act]');
        if (anAct) { analyticsAction(anAct.dataset.anAct); return; }
        const anD = t.closest('[data-an-detail]');
        if (anD) {
            closeMenus();
            S.anDetail = { type: anD.dataset.anDetail, title: anD.dataset.title, extra: JSON.parse(anD.dataset.extra || '{}') };
            render(); const d = $('#anDetail'); if (d) { d.scrollIntoView({ block: 'start' }); d.focus({ preventScroll: true }); }
            return;
        }
        const anC = t.closest('[data-an-clear]');
        if (anC) { S.an[anC.dataset.anClear] = ''; S.anDetail = null; render(); ($('[data-an-clear]') || $('#anPeriod')).focus(); return; }
        const opt = t.closest('.dd__opt');
        if (opt && DD) { ddPick(DD.opts.indexOf(opt)); return; }
        const ddb = t.closest('.dd__btn');
        if (ddb) { if (DD && DD.btn === ddb) ddClose(); else ddOpen(ddb); return; }
        if (DD && !t.closest('.dd')) ddClose();
        const menuBtn = t.closest('[data-menu]');
        if (menuBtn) { toggleMenu(menuBtn); return; }
        if (!t.closest('.menu') && !t.closest('#bellPanel') && !t.closest('#bellBtn')) { closeMenus(); toggleBell(false); }
        const act = t.closest('[data-act]');
        if (act && ACTIONS[act.dataset.act]) { if (act.tagName === 'A') e.preventDefault(); closeMenus(); ACTIONS[act.dataset.act](act, e); return; }
        const view = t.closest('[data-view]');
        if (view) { S.list.view = view.dataset.view; render(); $(`[data-view="${view.dataset.view}"]`).focus(); return; }
        const chip = t.closest('[data-clear]');
        if (chip) { S.list[chip.dataset.clear] = ''; render(); ($('.chip') || $('#listQ')).focus(); return; }
        const tab = t.closest('[data-tab]');
        if (tab) { selectTab(tab.dataset.tab); return; }
        const go = t.closest('[data-tab-go]');
        if (go) { e.preventDefault(); selectTab(go.dataset.tabGo, go.dataset.stageGo); return; }
        const step = t.closest('[data-stage]');
        if (step) { selectTab('events', step.dataset.stage); return; }
        const bell = t.closest('#bellBtn');
        if (bell) { toggleBell(); return; }
        const bi = t.closest('[data-bell]');
        if (bi) { S.read.add(bi.dataset.bell); toggleBell(false); }
        const row = t.closest('[data-row]');
        if (row && !t.closest('a, button')) location.hash = `#/patient/${row.dataset.row}`;
    });
    function selectTab(tab, stage) {
        const p = currentPatient(); if (!p) return;
        S.tabs[p.id] = tab; S.editing = null;
        history.replaceState(null, '', `#/patient/${p.id}?tab=${tab}${stage != null ? '&stage=' + stage : ''}`);
        render();
        if (stage == null) { const b = $(`#tab-${tab}`); if (b) b.focus(); } else { const s = document.getElementById(`stage-${stage}`); if (s) s.focus({ preventScroll: true }); }
    }
    document.addEventListener('keydown', (e) => {
        const ddb = e.target.closest && e.target.closest('.dd__btn');
        if (ddb && ddKey(e, ddb)) return;
        if (e.key === 'Escape') {
            if ($('#layer').innerHTML) { closeDialog(); return; }
            if (AI.open) { closeAI(); return; }
            const openMenu = $$('.menu').find(m => !m.hidden);
            if (openMenu) { const b = $(`[data-menu="${openMenu.id}"]`); closeMenus(); if (b) b.focus(); return; }
            if (!$('#bellPanel').hidden) { toggleBell(false); $('#bellBtn').focus(); return; }
            if ($('#side').classList.contains('is-open')) { toggleSide(false); return; }
        }
        if (e.key === '/' && !e.target.closest('input, textarea, select')) { e.preventDefault(); $('#globalQ').focus(); }
        const tab = e.target.closest('[role=tab]');
        if (tab && ['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) {
            e.preventDefault();
            const ids = TABS.map(t => t.id); let i = ids.indexOf(tab.dataset.tab);
            i = e.key === 'Home' ? 0 : e.key === 'End' ? ids.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + ids.length) % ids.length;
            selectTab(ids[i]);
        }
        const menu = e.target.closest('.menu[role=menu]');
        if (menu && ['ArrowDown', 'ArrowUp'].includes(e.key)) {
            e.preventDefault();
            const items = $$('[role=menuitem]', menu); let i = items.indexOf(e.target);
            items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
        }
        if (e.key === 'Enter' && !e.shiftKey && e.target.id === 'aiInput' && !e.isComposing) { e.preventDefault(); sendAI(e.target.value); return; }
        if (e.key === 'Tab' && AI.open && !$('#layer').innerHTML) {          // фокус не уходит из панели помощника
            const f = $$('#aiPanel button, #aiPanel textarea').filter(x => !x.disabled);
            if (!f.length) return;
            if (!$('#aiPanel').contains(document.activeElement)) { e.preventDefault(); f[0].focus(); }
            else if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
            else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
        }
        if (e.key === 'Tab' && $('#layer').innerHTML) {          // фокус не уходит из диалога
            const f = $$('#layer button, #layer input, #layer select, #layer textarea').filter(x => !x.disabled);
            if (!f.length) return;
            if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
            else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
        }
        const row = e.target.closest('[data-row]');
        if (row && e.key === 'Enter' && e.target === row) location.hash = `#/patient/${row.dataset.row}`;
    });
    document.addEventListener('mousedown', (e) => { if (e.target.matches('[data-scrim]')) closeDialog(); });
    let qTimer;
    document.addEventListener('input', (e) => {
        if (e.target.id === 'cText') { const c = $('#cCount'); if (c) c.textContent = `${e.target.value.length} из 2000 знаков`; }
        if (e.target.id === 'listQ') {
            clearTimeout(qTimer);
            qTimer = setTimeout(() => { const pos = e.target.selectionStart; S.list.q = e.target.value; render(); const i = $('#listQ'); i.focus(); i.setSelectionRange(pos, pos); }, 180);
        }
    });
    document.addEventListener('change', (e) => {
        const f = e.target.closest('[data-filter]');
        if (f) { S.list[f.dataset.filter] = f.value; const keep = f.id; const inPop = !!f.closest('.popover'); render(); if (inPop) { $('[data-menu="m-filters"]').click(); } const el = document.getElementById(keep); if (el) el.focus(); return; }
        if (e.target.id === 'demoRole') { if (EXT.hooks.reset) EXT.hooks.reset(); resetAI(); S.an = { ...AN_DEFAULT }; S.anDetail = null; S.role = e.target.value; localSet('staffux-role', S.role); S.list.doctor = ''; S.editing = null; const r = parseRoute(); if (r.name === 'patient' && !scope().some(p => p.id === r.id)) location.hash = '#/patients'; render(); if (AI.open) renderAI(); toast(`Роль: ${R().label}`); return; }
        if (e.target.id === 'anFrom' || e.target.id === 'anTo') { S.an[e.target.id === 'anFrom' ? 'from' : 'to'] = e.target.value; S.anDetail = null; const id = e.target.id; render(); const el = document.getElementById(id); if (el) el.focus(); return; }
        if (e.target.id === 'demoState') { S.demo = e.target.value; render(); if (AI.open) renderAI(); return; }
        const chk = e.target.closest('[data-check]');
        if (chk) { const p = currentPatient(); cur(p).checklist[chk.dataset.check] = chk.checked; touch(p); const name = chk.dataset.check; render(); const again = $$('[data-check]').find(x => x.dataset.check === name); if (again) again.focus(); toast(chk.checked ? 'Пункт отмечен' : 'Отметка снята'); }
    });
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (form.id === 'aiForm') { e.preventDefault(); sendAI($('#aiInput').value); return; }
        if (form.dataset.form === 'login') { e.preventDefault(); S.role = new FormData(form).get('role') || S.role; localSet('staffux-role', S.role); S.loggedOut = false; S.list.doctor = ''; location.hash = `#/${HOME[S.role]}`; render(); toast(`Вход выполнен (демо): ${R().label}`); const h = $('#main h1'); if (h) { h.setAttribute('tabindex', '-1'); h.focus(); } return; }
        if (form.id === 'globalSearch') { e.preventDefault(); S.list.q = $('#globalQ').value.trim(); S.list.view = 'all'; $('#globalQ').value = ''; location.hash = '#/patients'; render(); return; }
        e.preventDefault();
        const p = currentPatient(); if (!p) return;
        const fd = Object.fromEntries(new FormData(form).entries());
        if (form.dataset.form === 'event') {
            const [, i, j] = form.dataset.key.split(':').map(Number); const ev = p.history[i].events[j];
            const err = $('#e-err');
            if (fd.actualDate && fd.actualDate > TODAY) { err.hidden = false; err.textContent = 'Фактическая дата не может быть в будущем.'; $('#e-ad').focus(); return; }
            if (fd.actualTime && !fd.actualDate) { err.hidden = false; err.textContent = 'Укажите фактическую дату вместе со временем.'; $('#e-ad').focus(); return; }
            Object.assign(ev, { date: fd.date, time: fd.time, actualDate: fd.actualDate, actualTime: fd.actualTime });
            if ('treatmentType' in fd) ev.treatmentType = fd.treatmentType;
            ev.matchedPlan = ev.actualDate && ev.date ? ev.actualDate === ev.date && (ev.actualTime || '') === (ev.time || '') : null;
            S.editing = null; touch(p); render(); toast('Событие сохранено');
        } else if (form.dataset.form === 'note') {
            const i = Number(form.dataset.key.split(':')[2]); p.history[i].note = fd.note.trim(); S.editing = null; touch(p); render(); const t = document.getElementById(`n-${i}`); if (t) t.focus(); toast('Заметка сохранена');
        } else if (form.dataset.form === 'complaint') {
            p.complaint = fd.complaint.trim(); touch(p); render(); $('#cText').focus(); toast('Комментарий сохранён');
        } else if (form.dataset.form === 'medical') {
            const err = $('#m-err'); const code = fd.icd.trim().toUpperCase();
            const known = Object.values(window.ICD10 || {}).some(l => l.diagnoses.some(d => d.code === code));
            if (!code) { err.hidden = false; err.textContent = 'Укажите код диагноза МКБ-10.'; $('#mIcd').focus(); return; }
            if (!known) { err.hidden = false; err.textContent = 'Код не найден в справочнике МКБ-10. Выберите код из подсказок.'; $('#mIcd').focus(); return; }
            if (fd.pgiResultDate && fd.pgiResultDate > TODAY) { err.hidden = false; err.textContent = 'Дата результата ПГИ не может быть в будущем.'; $('#mPgiD').focus(); return; }
            if (code !== p.icd) p.diagnosisHistory.push({ changedAt: now(), oldDiagnosis: p.icd, newDiagnosis: code });
            const ref = (window.NOSOLOGY_REF || {})[fd.nosology];
            Object.assign(p, { icd: code, nosology: fd.nosology, nosologyLabel: ref && fd.nosology !== p.nosology ? ref.label : p.nosologyLabel, pgiResult: fd.pgiResult, pgiResultDate: fd.pgiResultDate, priority: fd.priority });
            S.editing = null; touch(p); render(); toast('Медицинские данные сохранены');
        } else if (form.dataset.form === 'personal') {
            const err = $('#d-err');
            if (!fd.name.trim()) { err.hidden = false; err.textContent = 'Укажите ФИО.'; $('#dName').focus(); return; }
            if (fd.policy && !/^\d{16}$/.test(fd.policy.replace(/\s/g, ''))) { err.hidden = false; err.textContent = 'Полис ОМС — 16 цифр.'; $('#dPolicy').focus(); return; }
            Object.assign(p, { name: fd.name.trim(), age: Number(fd.age) || p.age, phone: fd.phone, policy: fd.policy.replace(/\s/g, '') });
            S.editing = null; touch(p); render(); toast('Личные данные сохранены');
        } else if (form.dataset.form === 'sms') {
            if (p.notifications.consent !== 'granted') return;
            p.notifications.items.push({ id: `sms-new-${Date.now()}`, template: fd.template, scheduledAt: fd.at, source: 'manual', status: 'pending', reason: '' });
            touch(p); render(); toast('Напоминание создано (в прототипе ничего не отправляется)');
        }
    });

    /* ---------- Мобильное меню и тема ---------- */
    function toggleSide(open) { $('#side').classList.toggle('is-open', open); $('#menuBtn').setAttribute('aria-expanded', String(open)); if (open) { const a = $('#nav a'); if (a) a.focus(); } }
    $('#menuBtn').addEventListener('click', () => toggleSide(!$('#side').classList.contains('is-open')));
    $('#aiBtn').addEventListener('click', () => { if (AI.open) closeAI(); else openAI(null); });   // из шапки — общий контекст роли
    // На узком экране — короткая подсказка в поиске, чтобы она не обрезалась.
    const mq = window.matchMedia('(max-width: 600px)');
    const setPh = () => { $('#globalQ').placeholder = mq.matches ? 'Найти пациента' : 'Найти пациента: ФИО, полис ОМС или код МКБ-10'; };
    setPh(); mq.addEventListener('change', setPh);
    $('#nav').addEventListener('click', (e) => { if (e.target.closest('a')) toggleSide(false); });
    $('#themeBtn').addEventListener('click', () => { const t = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark'; document.documentElement.setAttribute('data-theme', t); localSet('staffux-theme', t); });

    /* Внутренний API для модулей прототипа (не публичный интерфейс системы). */
    window.StaffUx = { EXT, D, S, R, ROLES, TODAY, esc, $, $$, ICON, navIcon, dialog, closeDialog, toast, render, renderShell, scope, canEdit, currentPatient, parseRoute,
        STAGE, fmtFull, fmtShort, whenShort, fmtStamp, plural, now, demoGate, ddHtml, closeMenus, ACTIONS, CORE };
    window.addEventListener('hashchange', () => { S.editing = null; toggleBell(false); render(); const h = $('#main h1'); if (h && !parseRoute().params.get('stage')) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); } });
    render();
})();
