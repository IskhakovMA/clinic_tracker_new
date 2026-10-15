/* «Расписание» — плановые события пациентов (не расписание консилиума).
   Логика — events-core.js; данные — синтетические пациенты вкладки; сервер не вызывается.
   Только просмотр и переход в карточку, как в приложении: даты меняются во вкладке «События и даты». */
(function () {
    'use strict';
    const X = window.StaffUx, E = window.StaffUxEvents, K = window.StaffUxCore;
    if (!X || !E) return;
    const { D, S, R, TODAY, esc, $, scope, STAGE, fmtShort, plural, demoGate, ddHtml, ICON, render } = X;
    const DOW = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const DOW_FULL = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота'];
    const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
    const monday = (s) => { const d = parse(s); return addDays(s, -((d.getDay() + 6) % 7)); };
    const daysBetween = (a, b) => Math.round((parse(b) - parse(a)) / 86400000);
    const dayTitle = (d) => `${DOW_FULL[parse(d).getDay()]}, ${fmtShort(d)}`;
    const eventTitleOf = (stage) => ({ 'primary-appointment': 'Первичный прием', exam: 'Обследование', appointment: 'Прием врача', consult: 'Онкоконсилиум', 'treatment-prep': 'Лечение', treatment: 'Лечение' }[stage] || 'Событие');
    const shortDoctor = (p) => p.doctor || (p.doctorFull || '—');
    const showsDoctor = () => S.role !== 'doctor' && S.role !== 'chair';

    const defaults = () => ({ day: TODAY, week: monday(TODAY), q: '', doctor: '', stage: '', status: '' });
    S.ev = defaults();

    function statusHtml(x) {
        if (x.status === 'done') {
            const how = { noplan: 'без плана', match: 'по плану', mismatch: 'с отклонением от плана', unknown: 'соответствие плану не определено' }[x.planMatch];
            return `<span class="status status--good">выполнено</span><span class="arow__sub">${esc(fmtShort(x.actualDate))}${x.actualTime ? ', ' + esc(x.actualTime) : ''} · ${how}</span>`;
        }
        if (x.status === 'overdue') { const n = daysBetween(x.plannedDate, TODAY); return `<span class="status status--danger">просрочено на ${n} ${plural(n, 'день', 'дня', 'дней')}</span><span class="arow__sub">факт не отмечен</span>`; }
        if (x.status === 'today') return '<span class="status status--info">сегодня</span><span class="arow__sub">факт не отмечен</span>';
        if (x.status === 'nodate') return '<span class="status status--warn">дата не назначена</span>';
        if (x.status === 'unclosed') return `<span class="status status--muted">факт не отмечен</span><span class="arow__sub">${K.UNCLOSED_REASON[x.reason]}</span>`;
        const n = daysBetween(TODAY, x.plannedDate);
        return `<span class="status status--muted">через ${n} ${plural(n, 'день', 'дня', 'дней')}</span>`;
    }
    function row(x, opts = {}) {
        const occ = x.p.history[x.occIndex];
        const many = occ.events.length > 1 ? ` · событие ${x.eventIndex + 1} из ${occ.events.length}` : '';
        const timeCell = x.noPlan ? '<span class="arow__main muted">без плана</span>'
            : opts.withDate ? `<span class="arow__main mono">${esc(fmtShort(x.plannedDate))}</span><span class="arow__sub">${x.plannedTime ? esc(x.plannedTime) : 'время не указано'}</span>`
            : x.status === 'nodate' ? '<span class="arow__main muted">—</span>'
            : x.plannedTime ? `<span class="arow__main mono">${esc(x.plannedTime)}</span>` : '<span class="arow__main ev-notime">время не указано</span>';
        const planSub = x.status === 'done' && !x.noPlan && x.plannedDate && x.plannedDate !== x.actualDate ? ` · план ${esc(fmtShort(x.plannedDate))}` : '';
        return `<li><a class="crow ev-row" href="${E.eventHref(x)}" aria-label="${esc(x.p.name)}: ${esc(eventTitleOf(x.stage))}, открыть события в карточке">
            <span class="arow__cell">${timeCell}</span>
            <span class="arow__cell"><span class="arow__main arow__name">${esc(x.p.name)}</span><span class="arow__sub">${esc(eventTitleOf(x.stage))} · этап «${esc(STAGE(x.stage))}»${many}${planSub}</span></span>
            <span class="arow__cell"><span class="arow__main">${esc(shortDoctor(x.p))}</span><span class="arow__sub mono">${esc(x.p.icd || '')}</span></span>
            <span class="arow__cell arow__status">${statusHtml(x)}</span></a></li>`;
    }
    const head = '<li class="crow crow--head" aria-hidden="true"><span>Время</span><span>Пациент и событие</span><span>Лечащий врач</span><span>Статус</span></li>';
    const list = (xs, opts, label) => `<ul class="agenda__list crows ev-list" aria-label="${esc(label)}">${head}${xs.map(x => row(x, opts)).join('')}</ul>`;

    function page() {
        const f = S.ev;
        const all = E.scheduleItems(scope(), TODAY, D.TERMINAL);
        const items = E.filterItems(all, f);
        const v = E.dayView(items, f.day);
        const dates = Array.from({ length: 7 }, (_, k) => addDays(f.week, k));
        const counts = E.weekCounts(items, dates);
        const docs = []; scope().forEach(p => { if (p.doctorId && !docs.some(d => d[0] === p.doctorId)) docs.push([p.doctorId, p.doctor]); });
        const stages = Object.keys(D.STAGES).filter(st => all.some(x => x.stage === st));
        const filtersOn = ['q', 'doctor', 'stage', 'status'].filter(k => f[k]).length;
        const gate = demoGate('Плановых событий нет');
        const sub = S.role === 'doctor' ? 'ваших пациентов' : S.role === 'head' ? 'пациентов отделения' : 'всех пациентов организации';
        return `<div class="page-head"><div><h1>Расписание</h1><p class="page-head__sub">Плановые события ${sub}. Расписание онкоконсилиума — в разделе «Онкологический консилиум».</p></div></div>
            <div class="toolbar ev-toolbar" role="group" aria-label="Поиск и фильтры">
                <div class="search">${ICON.search}<label class="sr-only" for="evQ">Поиск по ФИО или коду МКБ-10</label><input class="input" id="evQ" type="search" placeholder="ФИО или код МКБ-10" value="${esc(f.q)}" autocomplete="off"></div>
                ${showsDoctor() ? ddHtml('evDoctor', 'Врач', 'doctor', [['', 'Все врачи'], ...docs.sort((a, b) => a[1].localeCompare(b[1], 'ru'))], f.doctor, false, 'ev') : ''}
                ${ddHtml('evStage', 'Этап', 'stage', [['', 'Все этапы'], ...stages.map(st => [st, STAGE(st)])], f.stage, false, 'ev')}
                ${ddHtml('evStatus', 'Статус', 'status', [['', 'Все'], ['open', 'Не выполнено'], ['done', 'Выполнено']], f.status, false, 'ev')}
                ${filtersOn ? '<button class="btn btn--text btn--sm" type="button" data-ev-act="reset">Сбросить фильтры</button>' : ''}
            </div>
            ${gate || `
            ${v.overdue.length ? `<section class="ev-block" aria-labelledby="evOver"><div class="section__head"><h2 id="evOver">Просрочено</h2><span class="count">${v.overdue.length}</span><span class="section__meta">плановая дата прошла, факт не отмечен</span></div>${list(v.overdue, { withDate: true }, 'Просроченные события')}</section>` : ''}
            <section class="ev-block" aria-labelledby="evDayTitle">
                <div class="cs-weeknav"><button class="btn btn--icon" type="button" data-ev-week="-7" aria-label="Предыдущая неделя">‹</button><span class="cs-weeknav__label">${esc(fmtShort(dates[0]))} — ${esc(fmtShort(dates[6]))}</span><button class="btn btn--icon" type="button" data-ev-week="7" aria-label="Следующая неделя">›</button>
                    ${f.day !== TODAY || f.week !== monday(TODAY) ? '<button class="btn btn--text btn--sm" type="button" data-ev-act="today">Сегодня</button>' : ''}
                    <label class="sr-only" for="evDate">Перейти к дате</label><input class="input ev-date" type="date" id="evDate" value="${esc(f.day)}"></div>
                <div class="cs-week" role="group" aria-label="Дни недели">${counts.map(c => {
                    const label = c.open ? `${c.open} ${plural(c.open, 'событие', 'события', 'событий')}` : c.done ? `выполнено ${c.done}` : 'нет событий';
                    const tone = c.overdue ? 'full' : c.open ? 'free' : 'off';
                    return `<button class="cs-day-btn cs-day-btn--${tone}${c.date === TODAY ? ' is-today' : ''}" type="button" data-ev-day="${c.date}" aria-pressed="${c.date === f.day}" aria-label="${esc(dayTitle(c.date))}: ${esc(label)}${c.overdue ? `, просрочено ${c.overdue}` : ''}${c.date === TODAY ? ', сегодня' : ''}"><span class="cs-day-btn__wd">${DOW[parse(c.date).getDay()]}</span><span class="cs-day-btn__d">${parse(c.date).getDate()}</span><span class="cs-day-btn__s">${esc(label)}</span></button>`;
                }).join('')}</div>
                <div class="section__head"><h2 id="evDayTitle">${esc(dayTitle(f.day))}</h2><span class="section__meta">${v.day.length + v.overdueOnDay.length ? `${v.day.length + v.overdueOnDay.length} ${plural(v.day.length + v.overdueOnDay.length, 'событие', 'события', 'событий')}` : ''}</span></div>
                ${v.overdueOnDay.length ? `<p class="an-rule">Просроченные события этого дня (${v.overdueOnDay.length}) — в блоке «Просрочено» выше.</p>` : ''}
                ${v.day.length ? list(v.day, {}, `События: ${dayTitle(f.day)}`) : `<div class="cs-empty"><b>${filtersOn ? 'По выбранным условиям событий нет.' : 'На этот день плановых событий нет.'}</b><p>${filtersOn ? 'Измените фильтры или сбросьте их.' : 'Выберите другой день в полосе недели или дату в календаре.'}</p></div>`}
            </section>
            ${v.nodate.length ? `<details class="deleted ev-nodate"><summary>Без плановой даты<span class="count">${v.nodate.length}</span></summary><p class="an-rule">Плановая дата не назначена — это допустимое состояние; назначить дату можно во вкладке «События и даты» карточки.</p>${list(v.nodate, {}, 'События без плановой даты')}</details>` : ''}
            ${v.unclosed.length ? `<details class="deleted ev-nodate ev-unclosed"><summary>Не отмечены после завершения этапа или маршрута<span class="count">${v.unclosed.length}</span></summary><p class="an-rule">У этих событий есть плановая дата, но нет факта, а этап уже пройден или маршрут завершен. По правилу «Рабочего дня» они не считаются открытыми и просроченными; прежний календарь показывал их в общем списке. Правило не меняется — проверьте событие в карточке: отметьте факт или удалите его.</p>${list(v.unclosed, { withDate: true }, 'События, не отмеченные после завершения этапа или маршрута')}</details>` : ''}
            <p class="an-rule">Расписание только показывает события: плановые и фактические даты меняются в карточке пациента («События и даты»). Бронирование кабинетов, оборудования и специалистов в системе нет.</p>`}`;
    }

    document.addEventListener('click', (e) => {
        const t = e.target;
        const d = t.closest('[data-ev-day]');
        if (d) { S.ev.day = d.dataset.evDay; render(); $(`[data-ev-day="${S.ev.day}"]`).focus(); return; }
        const w = t.closest('[data-ev-week]');
        if (w) { S.ev.week = addDays(S.ev.week, Number(w.dataset.evWeek)); S.ev.day = S.ev.week <= TODAY && TODAY <= addDays(S.ev.week, 6) ? TODAY : S.ev.week; render(); $(`[data-ev-week="${w.dataset.evWeek}"]`).focus(); return; }
        const a = t.closest('[data-ev-act]');
        if (!a) return;
        if (a.dataset.evAct === 'today') { S.ev.day = TODAY; S.ev.week = monday(TODAY); render(); $(`[data-ev-day="${TODAY}"]`).focus(); }
        if (a.dataset.evAct === 'reset') { Object.assign(S.ev, { q: '', doctor: '', stage: '', status: '' }); render(); $('#evQ').focus(); }
    });
    document.addEventListener('change', (e) => {
        if (e.target.id === 'evDate' && e.target.value) { S.ev.day = e.target.value; S.ev.week = monday(e.target.value); render(); $('#evDate').focus(); }
    });
    let qT;
    document.addEventListener('input', (e) => {
        if (e.target.id !== 'evQ') return;
        clearTimeout(qT); qT = setTimeout(() => { const pos = e.target.selectionStart; S.ev.q = e.target.value; render(); const n = $('#evQ'); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }, 180);
    });

    X.EXT.pages.calendar = page;
    const prevReset = X.EXT.hooks.reset;
    X.EXT.hooks.reset = () => { if (prevReset) prevReset(); S.ev = defaults(); };
    render();
})();
