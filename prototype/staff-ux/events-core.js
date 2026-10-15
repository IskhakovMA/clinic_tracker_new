/* Расписание плановых событий пациентов (не консилиум) — чистая логика без DOM.
   Открытые события и просрочки определяются тем же правилом, что «Рабочий день», список пациентов
   и аналитика (core.js: openEvents). Плановая и фактическая даты не смешиваются:
   день события — плановая дата; выполненное без плана — по фактической дате. Только синтетические данные. */
(function (root) {
    'use strict';
    const C = root.StaffUxCore;
    // Поиск без различия «е/ё» (обе стороны сравнения нормализуются; данные не меняются).
    const fold = (s) => String(s ?? '').toLowerCase().replace(/ё/g, 'е');

    /* Все события, видимые в расписании, для набора пациентов (уже ограниченного областью доступа роли). */
    function scheduleItems(patients, today, terminal) {
        const out = [];
        patients.forEach(p => {
            p.history.forEach((occ, occIndex) => occ.events.forEach((ev, eventIndex) => {
                // Состояние — то же, что в карточке пациента (core.js: eventState поверх openEvents).
                const st = C.eventState(p, occIndex, eventIndex, today, terminal);
                const base = { p, patientId: p.id, occIndex, eventIndex, stage: occ.stage, ev, plannedDate: ev.date || '', plannedTime: ev.time || '', actualDate: ev.actualDate || '', actualTime: ev.actualTime || '', noPlan: !!ev.noPlan };
                if (st.kind === 'done') out.push({ ...base, status: 'done', day: ev.noPlan || !ev.date ? ev.actualDate : ev.date, matchedPlan: ev.noPlan ? null : (ev.matchedPlan ?? null), planMatch: st.planMatch });
                else if (st.kind === 'nodate') { if (st.open) out.push({ ...base, status: 'nodate', day: '' }); }
                // Маршрут завершен или этап пройден: не просрочка, а «факт не отмечен» — отдельный блок, правило не меняется.
                else if (st.kind === 'unclosed') out.push({ ...base, status: 'unclosed', reason: st.reason, day: '' });
                else out.push({ ...base, status: st.kind, day: ev.date });
            }));
        });
        return out;
    }
    const timeKey = (x) => `${x.plannedTime || x.actualTime || '99:99'}`;
    const sortItems = (a, b) => a.day.localeCompare(b.day) || timeKey(a).localeCompare(timeKey(b)) || a.p.name.localeCompare(b.p.name, 'ru') || a.occIndex - b.occIndex || a.eventIndex - b.eventIndex;

    /* Фильтры: поиск (ФИО, МКБ-10), лечащий врач, этап, статус (open — еще не выполнено, done — выполнено). */
    function filterItems(items, f) {
        const q = fold(String(f.q || '').trim());
        return items.filter(x => (!q || fold(`${x.p.name} ${x.p.icd || ''}`).includes(q))
            && (!f.doctor || x.p.doctorId === f.doctor)
            && (!f.stage || x.stage === f.stage)
            && (!f.status || (f.status === 'done' ? x.status === 'done' : x.status !== 'done')));   // «Не выполнено» — все без факта, включая «факт не отмечен»
    }

    /* Данные для экрана: события выбранного дня, все просроченные (никогда не скрываются) и без плановой даты. */
    function dayView(items, day) {
        return {
            day: items.filter(x => x.day === day && x.status !== 'overdue').sort(sortItems),
            overdueOnDay: items.filter(x => x.day === day && x.status === 'overdue').sort(sortItems),
            overdue: items.filter(x => x.status === 'overdue').sort(sortItems),
            nodate: items.filter(x => x.status === 'nodate').sort((a, b) => a.p.name.localeCompare(b.p.name, 'ru')),
            unclosed: items.filter(x => x.status === 'unclosed').sort((a, b) => a.plannedDate.localeCompare(b.plannedDate) || a.p.name.localeCompare(b.p.name, 'ru')),
        };
    }
    /* Счетчики для полосы недели: еще не выполненные и выполненные события дня. */
    function weekCounts(items, dates) {
        return dates.map(d => {
            const xs = items.filter(x => x.day === d);   // «unclosed» не имеют дня в расписании
            return { date: d, open: xs.filter(x => x.status !== 'done').length, overdue: xs.filter(x => x.status === 'overdue').length, done: xs.filter(x => x.status === 'done').length };
        });
    }
    /* Ссылка на событие в карточке пациента: вкладка «События и даты», нужный этап. */
    const eventHref = (x) => `#/patient/${x.patientId}?tab=events&stage=${x.occIndex}`;

    root.StaffUxEvents = { scheduleItems, filterItems, dayView, weekCounts, sortItems, eventHref };
})(typeof window !== 'undefined' ? window : globalThis);
