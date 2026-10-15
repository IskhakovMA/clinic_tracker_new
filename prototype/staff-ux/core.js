/* Чистая логика UX-прототипа (без DOM): используется app.js и проверяется
   тестами tests/core.test.mjs. Только синтетические данные; правил
   приоритизации здесь нет — события упорядочиваются по дате и времени. */
(function (root) {
    'use strict';

    const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    const parse = (s) => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
    const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
    const daysBetween = (a, b) => Math.round((parse(b) - parse(a)) / 86400000);
    const when = (e) => `${e.date}T${e.time || '99:99'}`;   // событие без времени — в конце своего дня

    /* Открытые (без факта) плановые события пациента.
       Просроченные — из всех этапов маршрута (как правило overdue_event качества данных),
       предстоящие и без даты — только из текущего этапа. Для завершённого маршрута — нет. */
    function openEvents(p, today, terminal) {
        if (!p || !p.history || !p.history.length || terminal.includes(p.stage)) return [];
        const last = p.history.length - 1;
        const out = [];
        p.history.forEach((occ, occIndex) => occ.events.forEach((ev, eventIndex) => {
            if (ev.actualDate || ev.noPlan) return;
            if (ev.date && ev.date < today) out.push({ ev, occIndex, eventIndex, stage: occ.stage });
            else if (occIndex === last) out.push({ ev, occIndex, eventIndex, stage: occ.stage });
        }));
        return out;
    }
    const overdueEvents = (p, today, terminal) => openEvents(p, today, terminal).filter(x => x.ev.date && x.ev.date < today);

    /* Ближайшее событие — самое раннее по дате и времени среди открытых событий с датой.
       Поэтому просроченное событие никогда не скрывается более поздним предстоящим.
       Если дат нет — первое открытое событие текущего этапа («дата не назначена»). */
    function pickNextEvent(p, today, terminal) {
        const open = openEvents(p, today, terminal);
        const dated = open.filter(x => x.ev.date).sort((a, b) => when(a.ev).localeCompare(when(b.ev)) || a.occIndex - b.occIndex || a.eventIndex - b.eventIndex);
        if (dated.length) {
            const x = dated[0]; const d = daysBetween(today, x.ev.date);
            const kind = d < 0 ? 'overdue' : d === 0 ? 'today' : d <= 3 ? 'soon' : 'planned';
            return { ...x, kind, d, overdueTotal: dated.filter(y => y.ev.date < today).length };
        }
        return open.length ? { ...open[0], kind: 'nodate', d: null, overdueTotal: 0 } : null;
    }

    /* Итоги периода считаются по всему периоду; график — по последним 90 дням
       периода (ограничение только для читаемости графика). */
    const TREND_MAX_DAYS = 90;
    function periodSummary(list, from, to) {
        const inRange = (s) => s && s.slice(0, 10) >= from && s.slice(0, 10) <= to;
        const added = list.filter(p => inRange(p.createdAt)).length;
        const completed = list.reduce((n, p) => n + p.history.filter(o => o.stage === 'completed' && inRange(o.enteredAt)).length, 0);
        const total = daysBetween(from, to) + 1;
        const start = total > TREND_MAX_DAYS ? addDays(to, -(TREND_MAX_DAYS - 1)) : from;
        const trend = [];
        for (let k = 0; k <= daysBetween(start, to); k++) {
            const day = addDays(start, k);
            trend.push({ day, label: `${day.slice(8, 10)}.${day.slice(5, 7)}`,
                added: list.filter(p => (p.createdAt || '').slice(0, 10) === day).length,
                completed: list.reduce((n, p) => n + p.history.filter(o => o.stage === 'completed' && (o.enteredAt || '').slice(0, 10) === day).length, 0) });
        }
        return { from, to, days: total, totals: { added, completed }, trend, trendFrom: start, truncated: start !== from };
    }

    /* Подписанные химиотерапевтические схемы: одна строка на пару «код + название». */
    function groupSchemes(signed) {
        const map = new Map();
        signed.filter(c => c.treatment === 'chemotherapy' && c.scheme && c.scheme.name).forEach(c => {
            const code = (c.scheme.code || '').trim(); const name = c.scheme.name.trim();
            const key = `${code}\u0000${name}`;
            if (!map.has(key)) map.set(key, { code, name, label: `${code ? code + ' — ' : ''}${name}`, count: 0 });
            map.get(key).count++;
        });
        return [...map.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'ru'));
    }

    /* ---------- Решение ОК и перевод этапа: две самостоятельные операции ----------
       Утверждено владельцем проекта: ручная отметка решения ОК не переводит пациента,
       перевод этапа не создаёт и не меняет отметку, подписанное решение председателя —
       отдельная сущность (записи консилиума), ручная отметка его не создаёт.
       Новых медицинских правил здесь нет: проверяются только этап «Направлен на ОК»,
       право редактирования, выбор события и допустимое значение решения. */
    const DECISION_CODES = ['treatment_strategy_defined', 'treatment_strategy_changed', 'dispensary_observation'];
    // Право редактирования карточки по ролям прототипа (как ROLES.write в app.js; RBAC не меняется).
    const ROLE_CAN_EDIT = { doctor: true, head: true, chair: true, deputy: false, caop: false, developer: true };
    const canEditPatient = (role, inScope) => Boolean(ROLE_CAN_EDIT[role]) && Boolean(inScope);

    /* Доступность «Отметить решение ОК»: этап «Направлен на ОК» и право редактирования этого пациента. */
    function decisionAvailability(p, role, inScope) {
        if (!p || p.stage !== 'consult') return { available: false, reason: 'not_consult' };
        if (!canEditPatient(role, inScope)) return { available: false, reason: 'no_edit_right' };
        const occ = p.history[p.history.length - 1];
        if (!occ || occ.stage !== 'consult' || !occ.events.length) return { available: false, reason: 'no_events' };
        // Событие с уже сохранённой отметкой повторно не отмечается: процедура изменения решения — в WP2.2.
        const events = occ.events.map((ev, eventIndex) => ({ ev, eventIndex, marked: Boolean(ev.consiliumDecision) }));
        if (events.every(x => x.marked)) return { available: false, reason: 'all_marked', occIndex: p.history.length - 1, events };
        return { available: true, reason: '', occIndex: p.history.length - 1, events };
    }
    /* Событие по умолчанию — только если оно единственное; при нескольких выбор обязателен. */
    const defaultDecisionEvent = (avail) => (avail && avail.available && avail.events.length === 1 && !avail.events[0].marked ? avail.events[0].eventIndex : null);

    /* Сохраняет ручную отметку в выбранном событии этапа ОК. Меняет только это событие. */
    function applyManualDecision(p, { role, inScope, eventIndex, decision, at, by }) {
        const avail = decisionAvailability(p, role, inScope);
        if (!avail.available) throw new Error(`decision_unavailable:${avail.reason}`);
        if (eventIndex === null || eventIndex === undefined || eventIndex === '') throw new Error('event_required');
        const idx = Number(eventIndex);
        if (!Number.isInteger(idx) || !avail.events.some(x => x.eventIndex === idx)) throw new Error('event_not_in_consult_stage');
        if (!DECISION_CODES.includes(decision)) throw new Error('decision_required');
        const ev = p.history[avail.occIndex].events[idx];
        if (ev.consiliumDecision) throw new Error('decision_exists');   // без молчаливой перезаписи прежней отметки
        ev.consiliumDecision = decision;
        ev.decisionMark = { source: 'manual', at: at || '', by: by || '' };   // ручная отметка, не подписанное решение
        return { occIndex: avail.occIndex, eventIndex: idx, stage: p.stage };
    }

    /* Перевод этапа. Факт перевода пишется в последнее событие текущего этапа (как change_stage);
       поля решения ОК ни в одном событии не создаются и не меняются. */
    const ALLOWED_FROM_CONSULT = ['treatment-prep', 'exam', 'dispensary-observation', 'diagnosis-not-confirmed'];
    /* Конфликт с уже записанным фактом последнего события текущего этапа.
       Перевод не изменяет существующую фактическую дату или время. */
    function stageChangeConflict(p, { actualDate, actualTime }) {
        const occ = p.history[p.history.length - 1];
        const idx = occ.events.length - 1;
        const ev = idx >= 0 ? occ.events[idx] : null;
        if (!ev || !ev.actualDate) return null;
        const dateDiffers = actualDate !== ev.actualDate;
        // Пустое введённое время — изменение не запрошено. Любое непустое время, отличное от записанного
        // (в том числе когда записанное время пустое), — конфликт: факт не меняется и не игнорируется молча.
        const timeDiffers = Boolean(actualTime) && actualTime !== (ev.actualTime || '');
        return dateDiffers || timeDiffers ? { eventIndex: idx, total: occ.events.length, actualDate: ev.actualDate, actualTime: ev.actualTime || '' } : null;
    }
    function applyStageChange(p, { to, actualDate, actualTime, enteredAt, scheduled }) {
        if (!to || to === p.stage) throw new Error('stage_unchanged');
        if (p.stage === 'consult' && !ALLOWED_FROM_CONSULT.includes(to)) throw new Error('stage_not_allowed');
        if (!actualDate) throw new Error('actual_date_required');
        if (stageChangeConflict(p, { actualDate, actualTime })) throw new Error('fact_conflict');
        const occ = p.history[p.history.length - 1];
        if (!occ.events.length) occ.events.push({ date: '', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false });
        const ev = occ.events[occ.events.length - 1];
        // Факт пишется, только если его ещё нет; совпадающий существующий факт остаётся без изменений.
        if (!ev.actualDate) {
            ev.actualDate = actualDate; ev.actualTime = actualTime || ev.time || '';
            ev.matchedPlan = ev.date ? ev.date === actualDate && (ev.time || '') === (ev.actualTime || '') : null;
        }
        p.history.push({ stage: to, enteredAt: enteredAt || '', events: scheduled ? [{ date: '', time: '', actualDate: '', actualTime: '', matchedPlan: null, noPlan: false }] : [], note: '', checklist: {} });
        p.stage = to;
        return { factEventIndex: occ.events.length - 1 };
    }

    /* Записи консилиума пациента: подписанные решения отдельно, текущая (последняя) запись — отдельно.
       Неподписанная запись не подменяет подписанное решение; ничего не синхронизируется и не меняется. */
    function consiliumSummary(consilium, patientId) {
        const key = (c) => `${c.date || ''}T${c.time || '99:99'}`;
        const own = consilium.filter(c => c.patientId === patientId).slice().sort((a, b) => key(b).localeCompare(key(a)));
        return {
            signed: own.filter(c => c.status === 'signed').map(c => ({ id: c.id, treatment: c.treatment, scheme: c.scheme || null, date: c.date, time: c.time || '', archived: Boolean(c.archived) })),
            current: own.length ? { id: own[0].id, status: own[0].status, date: own[0].date, time: own[0].time || '', archived: Boolean(own[0].archived) } : null,
            total: own.length,
        };
    }

    /* Ручные отметки — только из событий маршрута; подписанные решения — только из записей консилиума. */
    const manualDecisions = (p) => p.history.flatMap((o, occIndex) => o.events.map((e, eventIndex) => ({ occIndex, eventIndex, stage: o.stage, decision: e.consiliumDecision, mark: e.decisionMark || null })).filter(x => x.decision));
    const signedDecisions = (consilium, patientId) => consilium.filter(c => c.patientId === patientId && c.status === 'signed').map(c => ({ id: c.id, treatment: c.treatment, scheme: c.scheme || null, date: c.date, time: c.time }));

    root.StaffUxCore = { openEvents, overdueEvents, pickNextEvent, periodSummary, groupSchemes, TREND_MAX_DAYS, daysBetween, addDays,
        DECISION_CODES, ROLE_CAN_EDIT, ALLOWED_FROM_CONSULT, canEditPatient, decisionAvailability, defaultDecisionEvent, applyManualDecision, stageChangeConflict, applyStageChange, consiliumSummary, manualDecisions, signedDecisions };
})(typeof window !== 'undefined' ? window : globalThis);
