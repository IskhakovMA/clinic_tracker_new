/* Чистая логика онкологического консилиума для UX-прототипа (без DOM).
   Источник правил — Stage 3.5 (prototype/redesign/app.js), который повторяет
   services/consilium.py и routes/consilium.py текущего приложения. Только
   синтетические данные; сервер не вызывается. Правила, которых нет в текущем
   backend (запланированы в WP2.3), помечены WP23 и включаются явно.
   Ни одна функция здесь не меняет этап пациента и не трогает события маршрута. */
(function (root) {
    'use strict';

    const pad = (n) => String(n).padStart(2, '0');
    const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const parse = (s) => { const [y, m, d] = s.slice(0, 10).split('-').map(Number); return new Date(y, m - 1, d); };
    const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return iso(d); };
    const toMin = (v) => { const [h, m] = v.split(':').map(Number); return h * 60 + m; };
    const fromMin = (m) => `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
    const isWeekend = (date) => { const g = parse(date).getDay(); return g === 0 || g === 6; };

    /* Статусы — как public_status / STATUS_LABELS в services/consilium.py. Новых состояний нет. */
    const STATUS = {
        recorded: { label: 'Записан', step: 'Ожидает рассмотрения председателем', tone: 'muted' },
        in_review: { label: 'На рассмотрении', step: 'Председатель рассматривает запись', tone: 'info' },
        needs_revision: { label: 'На доработке', step: 'Возвращена врачу: нужно исправить и отправить повторно', tone: 'warn' },
        revised: { label: 'Возвращён с доработки', step: 'Исправлена врачом, ожидает повторного рассмотрения', tone: 'info' },
        signed: { label: 'Решение подписано', step: 'Решение подписано председателем, изменения невозможны', tone: 'good' },
    };
    const EDITABLE = ['recorded', 'needs_revision', 'revised'];        // consEditable: правка и перенос автором
    const REVIEWABLE = ['recorded', 'revised'];                         // start_review
    const AWAITING = ['recorded', 'revised', 'in_review'];             // ещё нет подписанного решения и не на доработке

    /* Сетка времени текущей недели (как DEFAULT_TIMES Stage 3.5): 09:00–16:45, шаг 15 минут. */
    const DEFAULT_TIMES = []; for (let m = 9 * 60; m < 17 * 60; m += 15) DEFAULT_TIMES.push(fromMin(m));

    /* group_for_diagnosis — то же правило, что на сервере и в Stage 3.5. */
    function groupForDiagnosis(icd) {
        const m = String(icd || '').trim().toUpperCase().match(/^C(\d{2})(?:\.|$)/);
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
    const DIAG_RE = /^C(?:0[0-9]|[1-8][0-9]|9[0-7])(?:\.[0-9A-Z]{1,2})?$/;
    const validDiagnosis = (v, required = true) => { const x = String(v || '').trim().toUpperCase(); if (!x && !required) return ''; return DIAG_RE.test(x) ? x : null; };

    const weekDates = (today) => { const t = parse(today); const monday = iso(new Date(t.getFullYear(), t.getMonth(), t.getDate() - ((t.getDay() + 6) % 7))); return Array.from({ length: 7 }, (_, k) => addDays(monday, k)); };

    /* schedule_slots: сохранённые слоты председателя + автоматически открытая текущая неделя. */
    function allSlots(state, today, groups) {
        const out = state.slots.map(x => ({ ...x, isDefaultWeek: false }));
        const keys = new Set(out.map(x => `${x.date}|${x.time}|${x.group}`));
        for (const date of weekDates(today)) for (const group of groups) for (const time of DEFAULT_TIMES) {
            if (!keys.has(`${date}|${time}|${group}`)) out.push({ id: `week-${date}-${time}-${group}`, date, time, group, isDefaultWeek: true });
        }
        return out;
    }
    const recordAt = (state, date, time, group) => state.consilium.find(c => c.date === date && c.time === time && c.group === group);

    /* Состояние слота для записи. rules.wp23 включает целевые правила WP2.3 (в текущем backend не проверяются):
       нельзя записывать на прошедшее время; выходные — только явно открытые председателем слоты. */
    function slotState(state, slot, ctx) {
        const c = recordAt(state, slot.date, slot.time, slot.group);
        if (c) return { state: 'occupied', record: c, reason: 'Слот занят' };
        if (ctx.rules && ctx.rules.wp23) {
            if (`${slot.date}T${slot.time}` < ctx.now) return { state: 'past', reason: 'Время уже прошло' };
            if (slot.isDefaultWeek && isWeekend(slot.date)) return { state: 'weekend', reason: 'Выходной: запись только в слоты, явно открытые председателем' };
        }
        return { state: 'free', reason: '' };
    }

    /* Сводка по дням для полосы недели: свободные/занятые/недоступные слоты группы. */
    function daySummary(state, date, group, ctx) {
        const slots = allSlots(state, ctx.today, [group]).filter(x => x.date === date && x.group === group);
        const st = slots.map(x => slotState(state, x, ctx).state);
        const extra = state.consilium.filter(c => c.date === date && c.group === group && !slots.some(x => x.time === c.time)).length;
        return { total: slots.length, free: st.filter(x => x === 'free').length, occupied: st.filter(x => x === 'occupied').length + extra, unavailable: st.filter(x => x === 'past' || x === 'weekend').length, opened: slots.length > 0 };
    }

    /* Видимость записи (_slot_appointment): полные сведения — председателю/разработчику и автору
       по пациенту в своей области; заведующему по пациенту отделения — ФИО, врач и статус; остальным — «Занято». */
    function recordView(c, viewer) {
        const mine = c.createdBy === viewer.userId;
        if (viewer.perms.manageConsilium || viewer.perms.decideConsilium || (mine && viewer.inScope(c.patientId))) return { kind: 'full', mine };
        if (viewer.role === 'head' && viewer.inScope(c.patientId)) return { kind: 'department', mine: false };
        return { kind: 'busy', mine: false };
    }

    const fail = (code, message) => { const e = new Error(message); e.code = code; throw e; };
    /* Демонстрационный журнал: те же имена событий аудита, что в WP2.1 (audit.py); текст комментариев не пишется. */
    // changed: false — действие выполнено, но данные не изменились (например, все слоты уже были открыты).
    const log = (state, event, id, actor, ctx, detail, changed = true) => { if (state.audit) state.audit.push({ event, recordId: id, by: actor.name || actor.userId, at: ctx.now, detail: detail || '', changed }); };

    /* Проверка записи: те же условия, что create/update на сервере (+ правила WP2.3, если включены). */
    function bookingCheck(state, input, ctx) {
        const { actor, patient } = input;
        if (!actor.perms.bookConsilium) return { ok: false, code: 'forbidden_role', message: 'Запись на консилиум недоступна для вашей роли.' };
        if (!patient || !actor.canEditPatient(patient)) return { ok: false, code: 'forbidden_patient', message: 'Нет прав на запись этого пациента.' };
        if (groupForDiagnosis(patient.icd) !== input.group) return { ok: false, code: 'group_mismatch', message: 'Консилиумная группа должна соответствовать коду МКБ-10 из карточки пациента.' };
        const existing = state.consilium.find(c => c.patientId === patient.id && c.id !== input.recordId);
        if (existing) return { ok: false, code: 'already_booked', message: 'Пациент уже записан на онкологический консилиум. Используйте перенос существующей записи.', record: existing };
        if (!input.date || !input.time) return { ok: false, code: 'slot_required', message: 'Выберите свободный слот.' };
        const own = input.recordId ? state.consilium.find(c => c.id === input.recordId) : null;
        const sameSlot = own && own.date === input.date && own.time === input.time && own.group === input.group;
        if (!sameSlot) {
            const slot = allSlots(state, ctx.today, [input.group]).find(x => x.date === input.date && x.time === input.time && x.group === input.group);
            if (!slot) return { ok: false, code: 'slot_missing', message: 'Выбранный слот отсутствует в расписании председателя.' };
            const s = slotState(state, slot, ctx);
            if (s.state === 'occupied') return { ok: false, code: 'slot_occupied', message: 'Выбранный слот уже занят.' };
            if (s.state === 'past') return { ok: false, code: 'slot_past', message: 'Нельзя записать пациента на прошедшее время.' };
            if (s.state === 'weekend') return { ok: false, code: 'slot_weekend', message: 'Выходной день не открыт председателем для записи.' };
        }
        const primary = validDiagnosis(input.icd);
        if (!primary) return { ok: false, code: 'diagnosis', message: 'Укажите основной диагноз кодом МКБ-10 от C00 до C97.' };
        if (validDiagnosis(input.additionalDiagnosis, false) === null) return { ok: false, code: 'additional_diagnosis', message: 'Дополнительный диагноз должен быть кодом МКБ-10 от C00 до C97.' };
        if (!String(input.localization || '').trim()) return { ok: false, code: 'localization', message: 'Укажите локализацию.' };
        return { ok: true };
    }

    const FORM_FIELDS = ['date', 'time', 'policy', 'hasBenefit', 'diagnosisLocalization', 'icd', 'additionalDiagnosis', 'comment', 'additionalParticipant', 'uvRuv', 'needsSurgeon', 'needsRadiotherapist'];
    function pickFields(input) {
        return { date: input.date, time: input.time, policy: input.policy || '', hasBenefit: !!input.hasBenefit, diagnosisLocalization: String(input.localization || '').trim(), icd: validDiagnosis(input.icd),
            additionalDiagnosis: validDiagnosis(input.additionalDiagnosis, false) || '', comment: String(input.comment || '').trim(), additionalParticipant: String(input.additionalParticipant || '').trim(),
            uvRuv: !!input.uvRuv, needsSurgeon: !!input.needsSurgeon, needsRadiotherapist: !!input.needsRadiotherapist };
    }

    /* Создание записи (create). Статус «Записан». Этап и события пациента не меняются. */
    function book(state, input, ctx) {
        const chk = bookingCheck(state, { ...input, recordId: null }, ctx);
        if (!chk.ok) fail(chk.code, chk.message);
        const p = input.patient;
        const rec = { id: input.id || `c-${state.consilium.length + 1}-${Date.now()}`, patientId: p.id, patientName: p.name, localization: p.localization, group: input.group,
            doctor: input.doctorName || '', createdBy: input.actor.userId, status: 'recorded', treatment: null, scheme: null, goc: false, goc65: false, routeStage: null, revisions: [], archived: false,
            createdAt: ctx.now, ...pickFields(input) };
        state.consilium.push(rec);
        log(state, 'consilium_appointment_created', rec.id, input.actor, ctx);
        return rec;
    }

    /* Изменение и перенос (update) — только автором и только в статусах recorded / needs_revision / revised.
       Правка записи «На доработке» — повторная отправка председателю (→ «Возвращён с доработки»). */
    function update(state, id, input, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (c.createdBy !== input.actor.userId) fail('forbidden_author', 'Изменять запись может только её автор.');
        if (!EDITABLE.includes(c.status)) fail('not_editable', 'Запись на рассмотрении у председателя недоступна для редактирования.');
        const chk = bookingCheck(state, { ...input, recordId: id, group: c.group }, ctx);
        if (!chk.ok) fail(chk.code, chk.message);
        const resubmit = c.status === 'needs_revision';
        Object.assign(c, pickFields({ ...input, group: c.group }), { status: resubmit ? 'revised' : c.status, updatedAt: ctx.now, revisionSubmittedAt: resubmit ? ctx.now : c.revisionSubmittedAt });
        log(state, 'consilium_appointment_updated', c.id, input.actor, ctx, resubmit ? 'resubmitted=true' : '');
        return c;
    }

    /* Удаление своей записи (delete_owned): с историей возвратов — мягкое (история сохраняется). */
    function removeOwned(state, id, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (c.createdBy !== actor.userId || !EDITABLE.includes(c.status)) fail('not_editable', 'Удалить можно только свою запись до начала рассмотрения.');
        state.consilium.splice(state.consilium.indexOf(c), 1);
        if (c.revisions.length) state.deleted.push({ ...c, deletedAt: ctx.now });
        log(state, 'consilium_appointment_deleted', c.id, actor, ctx, c.revisions.length ? 'mode=soft;history_preserved=true' : 'mode=hard');
        return { soft: c.revisions.length > 0 };
    }

    /* Рассмотрение и решение — только председатель (decideConsilium). */
    function startReview(state, id, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (!actor.perms.decideConsilium) fail('forbidden_role', 'Рассматривать записи может только председатель консилиума.');
        if (!REVIEWABLE.includes(c.status)) fail('not_reviewable', 'Рассмотрение можно начать только для записи, ожидающей председателя.');
        c.status = 'in_review';
        log(state, 'consilium_review_started', c.id, actor, ctx || { now: '' });
        return c;
    }
    /* Подписание (sign): только из «На рассмотрении»; схема — только для химиотерапии.
       Этап маршрута фиксируется снимком (routeStage), но НЕ меняется; ручные отметки решения ОК не создаются. */
    function sign(state, id, decision, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (!actor.perms.decideConsilium) fail('forbidden_role', 'Подписать решение может только председатель консилиума.');
        if (c.status !== 'in_review') fail('not_in_review', 'Подписать можно только запись, рассмотрение которой начато.');
        if (!decision.treatment || !ctx.treatments.includes(decision.treatment)) fail('treatment_required', 'Выберите назначенное лечение.');
        if (decision.treatment === 'chemotherapy' && !String(decision.schemeName || '').trim()) fail('scheme_required', 'Для химиотерапевтического лечения укажите схему.');
        Object.assign(c, { status: 'signed', treatment: decision.treatment, scheme: decision.treatment === 'chemotherapy' ? { code: decision.schemeCode || '', name: String(decision.schemeName).trim() } : null,
            goc: !!decision.goc, goc65: !!decision.goc65, routeStage: ctx.patientStage || null, signedDoctor: ctx.doctorName || c.doctor, signedAt: ctx.now, signedBy: actor.name });
        log(state, 'consilium_signed', c.id, actor, ctx);   // подпись снимает текущий комментарий возврата; история возвратов остаётся
        return c;
    }
    /* Возврат на доработку (return_for_revision): из «На рассмотрении», с обязательным комментарием,
       отличающимся от предыдущего; раунд сохраняется навсегда. */
    function returnForRevision(state, id, text, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (!actor.perms.decideConsilium) fail('forbidden_role', 'Вернуть запись может только председатель консилиума.');
        if (c.status !== 'in_review') fail('not_in_review', 'Вернуть на доработку можно только запись, рассмотрение которой начато.');
        const t = String(text || '').trim();
        if (!t) fail('comment_required', 'Комментарий для врача обязателен.');
        const last = c.revisions[c.revisions.length - 1];
        if (last && last.text === t) fail('comment_repeated', 'Комментарий должен отличаться от предыдущего возврата.');
        c.revisions.push({ round: c.revisions.length + 1, author: actor.name, at: ctx.now, text: t });
        c.status = 'needs_revision';
        log(state, 'consilium_returned_for_revision', c.id, actor, ctx, `revision_round=${c.revisions.length}`);
        return c;
    }
    /* Архив (set_archived): только автор, только подписанная запись, роль с правом записи и не председатель. */
    const canArchive = (c, actor) => c.status === 'signed' && !!actor.perms.bookConsilium && !actor.perms.consiliumChair && c.createdBy === actor.userId;
    function setArchived(state, id, archived, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (!canArchive(c, actor)) fail('forbidden', 'Перенести в архив можно только свою подписанную запись.');
        if (c.archived === !!archived) fail('archive_state', archived ? 'Запись уже в архиве.' : 'Запись не находится в архиве.');
        c.archived = !!archived;
        log(state, archived ? 'consilium_archived' : 'consilium_restored', c.id, actor, ctx || { now: '' });
        return c;
    }
    /* Избранное председателя (закладка на пациента с записью; доступ к карточке не даёт). */
    function toggleFavorite(state, patientId, actor, ctx) {
        if (!actor.perms.useConsiliumFavorites) fail('forbidden_role', 'Избранное доступно только председателю консилиума.');
        if (!state.consilium.some(c => c.patientId === patientId)) fail('not_found', 'У пациента нет записи на консилиум.');
        const set = state.favorites[actor.userId] || (state.favorites[actor.userId] = []);
        const i = set.indexOf(patientId);
        if (i >= 0) set.splice(i, 1); else set.push(patientId);
        log(state, i >= 0 ? 'consilium_favorite_removed' : 'consilium_favorite_added', patientId, actor, ctx);
        return i < 0;
    }
    /* Отметка «Направление создано» в реестре хирургического лечения — только подписанные хирургические решения. */
    function setReferralCompleted(state, id, done, actor, ctx) {
        const c = state.consilium.find(x => x.id === id);
        if (!c) fail('not_found', 'Запись не найдена.');
        if (!actor.perms.markReferralCompletion) fail('forbidden_role', 'Отметку ставит председатель консилиума.');
        if (c.status !== 'signed' || c.treatment !== 'surgery') fail('not_surgery', 'Отметить можно только подписанное направление на хирургическое лечение.');
        c.referralCompleted = !!done;
        log(state, 'consilium_referral_completed', c.id, actor, ctx, `completed=${!!done}`);
        return c;
    }

    /* Слоты председателя (generate_schedule / delete_schedule_slot). */
    /* Как generate_schedule() в WP2.1: INSERT OR IGNORE — повторы пропускаются, существующие слоты и записи
       не меняются, уже открытый день можно расширять. Интервал — только 15/30/60 (Literal в ConsiliumScheduleGenerateIn).
       Сохранённый слот замещает автоматический с теми же датой, временем и группой (в т. ч. выходной текущей недели).
       Запрет прошедших дат — правило интерфейса приложения (сервер его не проверяет). Все проверки — до изменений. */
    const INTERVALS = [15, 30, 60];
    const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
    function generateSlots(state, g, actor, ctx) {
        if (!actor.perms.consiliumChair) fail('forbidden_role', 'Открывать слоты может только председатель консилиума.');
        const interval = String(g.interval ?? '').trim();
        if (!/^\d+$/.test(interval) || !INTERVALS.includes(Number(interval))) fail('interval_invalid', 'Интервал может быть только 15, 30 или 60 минут.');
        if (!g.date || !/^\d{4}-\d{2}-\d{2}$/.test(g.date) || iso(parse(g.date)) !== g.date) fail('date_invalid', 'Укажите дату.');
        if (g.date < ctx.today) fail('date_past', 'Нельзя открыть слоты на прошедшую дату.');
        if (!TIME_RE.test(g.start || '') || !TIME_RE.test(g.end || '') || toMin(g.end) <= toMin(g.start)) fail('time_order', 'Время окончания должно быть позже времени начала.');
        if (toMin(g.end) - toMin(g.start) > 12 * 60) fail('too_long', 'Сетка расписания не может быть длиннее 12 часов.');
        const step = Number(interval);
        const keys = new Set(state.slots.map(x => `${x.date}|${x.time}|${x.group}`));
        const auto = new Set(allSlots({ ...state, slots: [] }, ctx.today, [g.group]).filter(x => x.date === g.date).map(x => x.time));
        const created = []; let skipped = 0;
        for (let m = toMin(g.start); m < toMin(g.end); m += step) {
            const time = fromMin(m);
            if (keys.has(`${g.date}|${time}|${g.group}`)) { skipped++; continue; }   // INSERT OR IGNORE
            const slot = { id: `slot-${g.date}-${time}-${g.group}`, date: g.date, time, group: g.group, createdBy: actor.userId, replacesAuto: auto.has(time) };
            state.slots.push(slot); keys.add(`${g.date}|${time}|${g.group}`); created.push(slot);
        }
        // WP2.1 пишет аудит и при created=0; в журнале прототипа такая запись помечена «без изменений».
        log(state, 'consilium_schedule_generated', g.date, actor, ctx, `created=${created.length};interval=${step}`, created.length > 0);
        created.skipped = skipped;
        created.replacedAuto = created.filter(x => x.replacesAuto).length;
        return created;
    }
    /* Как delete_schedule_slot() в WP2.1: удаляется только сохранённая строка слота; занятый слот удалить нельзя.
       Для текущей недели schedule_slots() снова подставляет автоматический слот на то же время, поэтому
       удаление сохранённого слота НЕ закрывает время. Результат сообщает, что останется на этом времени:
       fallback = null — время закрыто; иначе — автоматический слот и его состояние для записи. Закрытие
       автоматического времени — новое правило, которого нет в WP2.1 (зависимость WP2.3). */
    function slotDeletionImpact(state, id, ctx) {
        const slot = state.slots.find(x => x.id === id);
        if (!slot) return null;
        const auto = weekDates(ctx.today).includes(slot.date) && DEFAULT_TIMES.includes(slot.time);
        if (!auto) return { slot, fallback: null };
        const fb = { id: `week-${slot.date}-${slot.time}-${slot.group}`, date: slot.date, time: slot.time, group: slot.group, isDefaultWeek: true };
        const rest = { ...state, slots: state.slots.filter(x => x.id !== id) };
        return { slot, fallback: { ...fb, ...slotState(rest, fb, ctx) } };
    }
    function deleteSlot(state, id, actor, ctx) {
        if (!actor.perms.consiliumChair) fail('forbidden_role', 'Удалять слоты может только председатель консилиума.');
        const slot = state.slots.find(x => x.id === id);
        if (!slot) fail('not_found', 'Слот автоматической недели удалить нельзя.');
        if (recordAt(state, slot.date, slot.time, slot.group)) fail('slot_occupied', 'Нельзя удалить занятый слот.');
        const impact = slotDeletionImpact(state, id, ctx);
        state.slots.splice(state.slots.indexOf(slot), 1);
        log(state, 'consilium_schedule_slot_deleted', slot.id, actor, ctx || { now: '' }, impact.fallback ? `auto_slot_remains=${impact.fallback.state}` : '');
        return impact;
    }

    /* Показатели заседания (день + группа): они же фильтры списка. */
    function sessionStats(records) {
        const by = (s) => records.filter(c => s.includes(c.status)).length;
        return { total: records.length, awaiting: by(REVIEWABLE), inReview: by(['in_review']), returned: by(['needs_revision']), signed: by(['signed']) };
    }
    const sortByTime = (a, b) => `${a.date}T${a.time || '99:99'}`.localeCompare(`${b.date}T${b.time || '99:99'}`) || a.patientName.localeCompare(b.patientName, 'ru');

    root.StaffUxConsilium = { STATUS, EDITABLE, REVIEWABLE, AWAITING, DEFAULT_TIMES, INTERVALS, groupForDiagnosis, validDiagnosis, weekDates, isWeekend, addDays, allSlots, recordAt, slotState, daySummary, recordView,
        bookingCheck, book, update, removeOwned, startReview, sign, returnForRevision, canArchive, setArchived, toggleFavorite, setReferralCompleted, generateSlots, deleteSlot, slotDeletionImpact, sessionStats, sortByTime, FORM_FIELDS };
})(typeof window !== 'undefined' ? window : globalThis);
