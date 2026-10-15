/* Онкологический консилиум и его расписание в Staff UX (интерфейс).
   Логика — consilium-core.js; данные — синтетические (../redesign/data.js), живут в памяти вкладки.
   Сервер не вызывается. В приложении модуль консилиума реализован в ветке WP2.1, а целевые
   правила расписания (прошедшее время, выходные) запланированы в WP2.3. */
(function () {
    'use strict';
    const X = window.StaffUx;
    const K = window.StaffUxConsilium;
    if (!X || !K) return;
    const { D, S, R, TODAY, fold, esc, $, $$, dialog, toast, render, scope, canEdit, STAGE, fmtFull, fmtShort, fmtStamp, plural, now, demoGate, ddHtml, closeMenus, ICON } = X;

    /* ---------- Данные и роли ---------- */
    D.consilium.forEach(c => { if (c.diagnosisLocalization === undefined) c.diagnosisLocalization = c.localization || ''; if (!c.revisions) c.revisions = []; });
    const CS = { consilium: D.consilium, slots: (D.consSlots || []).map(x => ({ ...x })), deleted: [], audit: [], favorites: {} };
    const GROUPS = Object.keys(D.GROUPS);
    // Права консилиума — как policy.permissions_payload в WP2.1 / PERMS Stage 3.5 (RBAC не меняется).
    const PERMS = {
        doctor: { accessConsilium: true, bookConsilium: true },
        head: { accessConsilium: true, bookConsilium: true },
        chair: { accessConsilium: true, manageConsilium: true, decideConsilium: true, consiliumChair: true, useConsiliumFavorites: true, markReferralCompletion: true },
        developer: { accessConsilium: true, bookConsilium: true, manageConsilium: true },
        deputy: {}, caop: {},
    };
    const perms = () => PERMS[S.role] || {};
    const isChair = () => !!perms().consiliumChair;
    const actor = () => ({ userId: R().user.id, name: R().user.short, role: S.role, perms: perms(), canEditPatient: (p) => canEdit(p) });
    const viewer = () => ({ userId: R().user.id, role: S.role, perms: perms(), inScope: (pid) => scope().some(p => p.id === pid) });
    const ctx = (extra) => ({ today: TODAY, now: now(), rules: { wp23: S.cons.wp23 }, treatments: Object.keys(D.TREATMENTS), ...(extra || {}) });
    const patientOf = (c) => D.patients.find(p => p.id === c.patientId);
    const doctorOf = (c) => c.status === 'signed' && c.signedDoctor ? c.signedDoctor : ((patientOf(c) || {}).doctorFull || c.doctor || '');
    const specialists = (c) => [c.needsSurgeon && 'Хирург', c.needsRadiotherapist && 'Лучевой терапевт'].filter(Boolean);
    const WD = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
    const WD_FULL = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
    const dow = (d) => { const [y, m, dd] = d.split('-').map(Number); return new Date(y, m - 1, dd).getDay(); };
    const dayTitle = (d) => `${WD_FULL[dow(d)][0].toUpperCase()}${WD_FULL[dow(d)].slice(1)}, ${fmtShort(d)}`;
    const statusHtml = (c) => { const s = K.STATUS[c.status] || { label: c.status, tone: 'muted' }; return `<span class="status status--${s.tone}">${s.label}</span>`; };
    const when = (c) => c.time ? `${fmtShort(c.date)}, ${c.time}` : `${fmtShort(c.date)}, время не назначено`;

    const defaults = () => ({
        group: '', date: TODAY, filter: 'all', q: '', sel: null,
        sgroup: '', week: K.weekDates(TODAY)[0], day: TODAY, onlyFree: false, wp23: true,
        hist: { from: K.addDays(TODAY, -90), to: K.addDays(TODAY, 60), status: '', group: '', q: '' },
        arch: { q: '', date: '' }, regDate: TODAY, drafts: {}, bk: null, gen: { date: '', start: '09:00', end: '12:00', interval: '30' },
    });
    S.cons = defaults();
    const myGroup = () => {
        const mine = scope().filter(p => canEdit(p)).map(p => K.groupForDiagnosis(p.icd));
        const top = mine.sort((a, b) => mine.filter(x => x === b).length - mine.filter(x => x === a).length)[0];
        return top || 'thoracic';
    };
    const sGroup = () => S.cons.sgroup || (S.cons.sgroup = isChair() ? 'thoracic' : myGroup());

    /* ---------- Каркас раздела ---------- */
    const TABS = () => isChair()
        ? [['session', 'Заседание'], ['schedule', 'Расписание и слоты'], ['registries', 'Реестры'], ['history', 'История решений']]
        : [['mine', 'Мои записи'], ['schedule', 'Расписание'], ['archive', 'Архив']];
    function page(r) {
        if (!perms().accessConsilium) return `<div class="state"><h2>Раздел недоступен</h2><p>Онкологический консилиум недоступен для вашей роли.</p></div>`;
        const tabs = TABS();
        const sub = r.id || tabs[0][0];
        if (r.params.get('rec')) S.cons.sel = r.params.get('rec');
        const body = { session: viewSession, schedule: viewSchedule, registries: viewRegistries, history: viewHistory, mine: viewMine, archive: viewArchive, book: viewBook }[sub];
        if (!body || (sub !== 'book' && !tabs.some(t => t[0] === sub))) return `<div class="state"><h2>Раздел не найден</h2><a class="btn" href="#/consilium">К консилиуму</a></div>`;
        const head = `<div class="page-head"><div><h1>Онкологический консилиум</h1><p class="page-head__sub">${isChair() ? 'Заседания, рассмотрение и подписание решений' : S.role === 'head' ? 'Записи ваших пациентов и пациентов отделения' : 'Записи ваших пациентов на рассмотрение консилиумной группой'}</p></div>
            ${perms().bookConsilium && sub !== 'book' ? `<div class="page-head__actions"><a class="btn btn--primary" href="#/consilium/book">${ICON.plus}Записать пациента</a></div>` : ''}</div>`;
        const nav = sub === 'book' ? `<nav class="crumbs" aria-label="Навигация"><a href="#/consilium">${ICON.back}Онкологический консилиум</a></nav>`
            : `<nav class="views cs-tabs" aria-label="Разделы консилиума">${tabs.map(([id, label]) => `<a class="view" href="#/consilium/${id}" ${id === sub ? 'aria-current="page" aria-pressed="true"' : 'aria-pressed="false"'}>${label}${tabCount(id)}</a>`).join('')}</nav>`;
        const gate = demoGate('Записей на консилиум нет');
        return `${sub === 'book' ? '' : head}${nav}${gate || body(r)}
            <p class="an-rule cs-proto">Прототип: синтетические данные, действия сохраняются только в этой вкладке. В приложении модуль консилиума реализован в ветке WP2.1 (в основной ветке его нет).</p>`;
    }
    function tabCount(id) {
        const me = R().user.id;
        if (id === 'mine') { const n = CS.consilium.filter(c => c.createdBy === me && c.status === 'needs_revision').length; return n ? `<span class="view__n view__n--warn" aria-label="на доработке: ${n}">${n}</span>` : ''; }
        if (id === 'session') { const n = CS.consilium.filter(c => c.date === TODAY && K.REVIEWABLE.includes(c.status)).length; return n ? `<span class="view__n" aria-label="ожидают рассмотрения сегодня: ${n}">${n}</span>` : ''; }
        return '';
    }

    /* ---------- Строка записи (общая сетка) ---------- */
    function recRow(c, opts = {}) {
        const v = K.recordView(c, viewer());
        const sp = specialists(c);
        const fav = isChair() && (CS.favorites[R().user.id] || []).includes(c.patientId);
        const sel = S.cons.sel === c.id;
        return `<li><button class="crow cs-row${sel ? ' is-selected' : ''}" type="button" data-cs-sel="${c.id}" ${sel ? 'aria-current="true"' : ''}>
            <span class="arow__cell crow__time">${c.time ? `<span class="arow__main mono">${esc(c.time)}</span>` : '<span class="arow__main crow__notime">Время не назначено</span>'}<span class="arow__sub">${opts.withDate ? esc(fmtShort(c.date)) + ' · ' : ''}${esc(D.GROUPS[c.group] || '')}</span></span>
            <span class="arow__cell"><span class="arow__main arow__name">${fav ? '<span aria-label="в избранном">★ </span>' : ''}${esc(c.patientName)}</span><span class="arow__sub">${v.kind === 'full' ? `<span class="mono">${esc(c.icd)}</span> · ` : ''}врач ${esc(shortDoctor(c))}</span></span>
            <span class="arow__cell"><span class="arow__main">${sp.length ? esc(sp.join(', ')) : '<span class="muted">Не требуются</span>'}</span>${c.additionalParticipant ? `<span class="arow__sub">+ ${esc(c.additionalParticipant)}</span>` : ''}</span>
            <span class="arow__cell arow__status">${statusHtml(c)}<span class="arow__sub">${esc(rowHint(c))}${c.archived ? ' · в архиве автора' : ''}</span></span></button></li>`;
    }
    const shortDoctor = (c) => { const full = doctorOf(c); const p = full.split(' '); return p.length === 3 ? `${p[0]} ${p[1][0]}. ${p[2][0]}.` : (c.doctor || full || '—'); };
    // Подсказка о следующем действии в строке списка (по роли).
    const rowHint = (c) => isChair()
        ? { recorded: 'нужно рассмотреть', revised: 'рассмотреть повторно', in_review: 'оформить решение', needs_revision: 'ждет врача', signed: 'готово' }[c.status]
        : c.createdBy === R().user.id ? { recorded: 'ждет председателя', revised: 'ждет председателя', in_review: 'изменения недоступны', needs_revision: 'нужна доработка', signed: 'готово' }[c.status] : '';
    const listHead = '<li class="crow crow--head" aria-hidden="true"><span>Время</span><span>Пациент</span><span>Специалисты</span><span>Статус</span></li>';

    /* ---------- Подробности записи ---------- */
    function detailBlock(c, list) {
        const v = K.recordView(c, viewer());
        const sp = specialists(c);
        const st = K.STATUS[c.status];
        const idx = list ? list.findIndex(x => x.id === c.id) : -1;
        const nav = list && list.length > 1 ? `<div class="cs-pager"><button class="btn btn--sm" type="button" data-cs-sel="${list[(idx - 1 + list.length) % list.length].id}" aria-label="Предыдущий пациент">‹</button><span class="muted">${idx + 1} из ${list.length}</span><button class="btn btn--sm" type="button" data-cs-sel="${list[(idx + 1) % list.length].id}" aria-label="Следующий пациент">›</button></div>` : '';
        const lastReturn = c.revisions[c.revisions.length - 1];
        const canSeeHistory = perms().decideConsilium || c.createdBy === R().user.id;
        const p = patientOf(c);
        const field = (k, val) => `<div><dt>${k}</dt><dd>${val}</dd></div>`;
        return `<section class="cs-detail" aria-labelledby="csDetailTitle" tabindex="-1" id="csDetail">
            <div class="cs-detail__top"><button class="btn btn--sm cs-back" type="button" data-cs-sel="">${ICON.back}К списку</button>${nav}</div>
            <h2 id="csDetailTitle">${esc(c.patientName)}</h2>
            <p class="cs-detail__meta">${statusHtml(c)} <span class="muted">${esc(when(c))} · ${esc(D.GROUPS[c.group] || '')}</span></p>
            <p class="cs-next"><b>Что дальше:</b> ${esc(nextStepText(c))}</p>
            ${c.status === 'needs_revision' && lastReturn && canSeeHistory ? `<div class="banner banner--warn" role="note"><span class="banner__body"><b>Причина возврата:</b> ${esc(lastReturn.text)}<br><span class="muted">вернул(а) ${esc(lastReturn.author)}, ${esc(fmtStamp(lastReturn.at))} · раунд ${lastReturn.round}</span></span></div>` : ''}
            ${v.kind === 'full' ? `<dl class="dl">
                ${field('Лечащий врач', esc(doctorOf(c) || '—'))}
                ${field('Полис ОМС', c.policy ? `<span class="mono">${esc(c.policy)}</span>` : '<span class="muted">не указан</span>')}
                ${field('Основной диагноз', `<span class="mono">${esc(c.icd)}</span> · ${esc(c.diagnosisLocalization || '')}`)}
                ${c.additionalDiagnosis ? field('Дополнительный диагноз', `<span class="mono">${esc(c.additionalDiagnosis)}</span>`) : ''}
                ${field('Участники', esc([...sp, c.additionalParticipant].filter(Boolean).join(', ') || 'Не требуются'))}
                ${field('Отметки', esc([c.uvRuv && 'УВ/РУВ', c.hasBenefit && 'Льгота'].filter(Boolean).join(', ') || 'Нет'))}
                ${field('Комментарий врача', c.comment ? esc(c.comment) : '<span class="muted">нет</span>')}
            </dl>` : `<p class="muted">Подробности записи видны автору записи и председателю консилиума.</p>`}
            ${c.status === 'signed' ? signedBlock(c) : ''}
            ${actionsFor(c)}
            ${canSeeHistory && c.revisions.length ? `<details class="deleted" ${c.status === 'needs_revision' ? 'open' : ''}><summary>История возвратов<span class="count">${c.revisions.length}</span></summary><ol class="hist">${c.revisions.map(r => `<li><div class="hist__head"><b>Раунд ${r.round}</b><time datetime="${esc(r.at)}">${esc(fmtStamp(r.at))} · ${esc(r.author)}</time></div><p>${esc(r.text)}</p></li>`).join('')}</ol></details>` : ''}
            ${auditBlock(c)}
            ${p && scope().includes(p) ? `<p><a href="#/patient/${p.id}">Открыть карточку пациента</a></p>` : v.kind === 'full' ? '<p class="muted">Карточка пациента вне вашей области доступа — ее открывает лечащий врач.</p>' : ''}
        </section>`;
    }
    function nextStepText(c) {
        const mine = c.createdBy === R().user.id;
        if (isChair()) return { recorded: 'Начните рассмотрение, чтобы оформить решение.', revised: 'Врач исправил запись — рассмотрите ее повторно.', in_review: 'Заполните решение и подпишите его или верните запись врачу.', needs_revision: 'Ожидается доработка врачом. Повторное рассмотрение станет доступно после отправки.', signed: 'Решение подписано. Изменения невозможны.' }[c.status];
        if (mine) return { recorded: 'Ожидает председателя. Пока рассмотрение не начато, запись можно изменить, перенести или удалить.', revised: 'Отправлена повторно. Ожидает рассмотрения председателем.', in_review: 'Председатель рассматривает запись — изменения сейчас недоступны.', needs_revision: 'Исправьте запись по комментарию председателя и отправьте ее повторно.', signed: 'Решение подписано. Запись можно перенести в архив.' }[c.status];
        return K.STATUS[c.status].step;
    }
    function signedBlock(c) {
        return `<div class="cs-signed"><h3>Подписанное решение председателя</h3><dl class="dl">
            <div><dt>Назначенное лечение</dt><dd>${esc(D.TREATMENTS[c.treatment] || '—')}</dd></div>
            ${c.scheme ? `<div><dt>Схема лечения</dt><dd>${c.scheme.code ? `<span class="mono">${esc(c.scheme.code)}</span> — ` : ''}${esc(c.scheme.name)}</dd></div>` : ''}
            <div><dt>Отметки ГОК</dt><dd>${esc([c.goc && 'ГОК', c.goc65 && 'ГОК 65+'].filter(Boolean).join(', ') || 'Нет')}</dd></div>
            <div><dt>Подписано</dt><dd>${c.signedAt ? `${esc(fmtStamp(c.signedAt))}${c.signedBy ? ', ' + esc(c.signedBy) : ''}` : 'председателем консилиума'}</dd></div>
            <div><dt>Этап маршрута на момент подписи</dt><dd>${c.routeStage ? esc(STAGE(c.routeStage)) : 'не установлен'} <span class="muted">· только снимок, этап пациента не меняется</span></dd></div>
        </dl><p class="an-rule">Подписанное решение — отдельная сущность: оно не создает ручную отметку решения ОК в карточке и не переводит пациента на другой этап.</p></div>`;
    }
    function auditBlock(c) {
        const rows = CS.audit.filter(a => a.recordId === c.id || a.recordId === c.patientId);
        if (!rows.length) return '';
        const L = { consilium_appointment_created: 'Запись создана', consilium_appointment_updated: 'Запись изменена', consilium_review_started: 'Начато рассмотрение', consilium_signed: 'Решение подписано', consilium_returned_for_revision: 'Возвращена на доработку', consilium_archived: 'Перенесена в архив', consilium_restored: 'Восстановлена из архива', consilium_favorite_added: 'Добавлена в избранное', consilium_favorite_removed: 'Убрана из избранного', consilium_referral_completed: 'Отметка о направлении' };
        return `<details class="deleted"><summary>Журнал действий в этой вкладке<span class="count">${rows.length}</span></summary><ul class="feed">${rows.map(a => `<li><time>${esc(fmtStamp(a.at))}</time><span>${esc(L[a.event] || a.event)} · ${esc(a.by)} <span class="muted mono">${esc(a.event)}</span></span></li>`).join('')}</ul><p class="an-rule">В приложении эти действия записываются в журнал аудита с теми же кодами событий; текст комментариев в аудит не попадает.</p></details>`;
    }
    function actionsFor(c) {
        const me = R().user.id;
        const out = [];
        if (perms().decideConsilium) {
            if (K.REVIEWABLE.includes(c.status)) out.push(`<button class="btn btn--primary" type="button" data-cs-act="review" data-id="${c.id}">${c.status === 'revised' ? 'Рассмотреть повторно' : 'Начать рассмотрение'}</button>`);
            if (c.status === 'in_review') return decisionForm(c) + favButton(c);
            out.push(favButton(c));
            return `<div class="cs-actions">${out.join('')}</div>`;
        }
        if (c.createdBy === me && perms().bookConsilium) {
            if (c.status === 'needs_revision') out.push(`<a class="btn btn--primary" href="#/consilium/book?id=${c.id}">Доработать и отправить</a>`);
            if (K.EDITABLE.includes(c.status) && c.status !== 'needs_revision') out.push(`<a class="btn" href="#/consilium/book?id=${c.id}">Изменить или перенести</a>`);
            if (K.EDITABLE.includes(c.status)) out.push(`<button class="btn btn--danger" type="button" data-cs-act="delete" data-id="${c.id}">Удалить запись</button>`);
            if (K.canArchive(c, actor())) out.push(`<button class="btn" type="button" data-cs-act="${c.archived ? 'restore' : 'archive'}" data-id="${c.id}">${c.archived ? 'Восстановить из архива' : 'Перенести в архив'}</button>`);
            if (c.status === 'in_review') out.push('<p class="muted">Изменение и удаление недоступны, пока идет рассмотрение.</p>');
        }
        return out.length ? `<div class="cs-actions">${out.join('')}</div>` : '';
    }
    function favButton(c) {
        if (!perms().useConsiliumFavorites) return '';
        const on = (CS.favorites[R().user.id] || []).includes(c.patientId);
        return `<button class="btn" type="button" data-cs-act="fav" data-id="${c.id}" aria-pressed="${on}">${on ? '★ В избранном' : '☆ В избранное'}</button>`;
    }
    function decisionForm(c) {
        const d = S.cons.drafts[c.id] || (S.cons.drafts[c.id] = { treatment: '', schemeName: '', schemeCode: '', goc: false, goc65: false });
        return `<form class="cs-decision" data-cs-form="decision" data-id="${c.id}" novalidate aria-labelledby="csDecTitle">
            <h3 id="csDecTitle">Решение консилиума</h3>
            <p class="an-rule">Черновик решения хранится только в этой вкладке и не сохраняется до подписания.</p>
            <div class="field"><label class="field__label" for="csTreat">Назначенное лечение <span class="req">*</span></label><select class="select" id="csTreat" name="treatment"><option value="">Выберите лечение</option>${Object.entries(D.TREATMENTS).map(([k, t]) => `<option value="${k}" ${d.treatment === k ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div>
            ${d.treatment === 'chemotherapy' ? `<div class="field"><label class="field__label" for="csScheme">Схема химиотерапии <span class="req">*</span></label><input class="input" id="csScheme" name="schemeName" list="csSchemes" autocomplete="off" maxlength="1000" value="${esc(d.schemeName)}" placeholder="Название или код схемы"><datalist id="csSchemes">${(D.SCHEMES || []).map(s => `<option value="${esc(s.name)}">${esc(s.code)}</option>`).join('')}</datalist><span class="field__hint">${d.schemeCode ? `Код схемы: <span class="mono">${esc(d.schemeCode)}</span>` : 'Код подставится, если схема есть в справочнике; можно ввести свой вариант.'}</span></div>` : ''}
            <fieldset class="field cs-checks"><legend class="field__label">Отметки</legend><label class="radio"><input type="checkbox" name="goc" ${d.goc ? 'checked' : ''}> ГОК</label><label class="radio"><input type="checkbox" name="goc65" ${d.goc65 ? 'checked' : ''}> ГОК 65+</label></fieldset>
            <p class="field__error" id="csDecErr" role="alert" hidden></p>
            <div class="cs-actions"><button class="btn btn--primary" type="submit">Подписать решение</button><button class="btn" type="button" data-cs-act="return" data-id="${c.id}">Вернуть на доработку</button></div>
        </form>`;
    }

    /* ---------- Заседание (председатель) ---------- */
    function sessionRecords() {
        const f = S.cons; const q = fold(f.q.trim());
        return CS.consilium.filter(c => c.date === f.date && (!f.group || c.group === f.group) && (!q || fold(c.patientName).includes(q) || (c.policy || '').includes(q))).sort(K.sortByTime);
    }
    const FILTERS = [['all', 'Все', () => true], ['awaiting', 'Ожидают рассмотрения', c => K.REVIEWABLE.includes(c.status)], ['in_review', 'На рассмотрении', c => c.status === 'in_review'], ['needs_revision', 'На доработке', c => c.status === 'needs_revision'], ['signed', 'Подписано', c => c.status === 'signed']];
    function viewSession() {
        const f = S.cons;
        const all = sessionRecords();
        const flt = FILTERS.find(x => x[0] === f.filter) || FILTERS[0];
        const list = all.filter(flt[2]);
        const sel = list.find(c => c.id === f.sel) || all.find(c => c.id === f.sel);
        const groupsToday = GROUPS.map(g => [g, D.GROUPS[g], CS.consilium.filter(c => c.date === f.date && c.group === g).length]);
        return `<div class="toolbar cs-toolbar" role="group" aria-label="Выбор заседания">
                <div class="cs-day"><button class="btn btn--icon" type="button" data-cs-act="day" data-d="-1" aria-label="Предыдущий день">‹</button>
                    <label class="sr-only" for="csDate">Дата заседания</label><input class="input" type="date" id="csDate" value="${esc(f.date)}">
                    <button class="btn btn--icon" type="button" data-cs-act="day" data-d="1" aria-label="Следующий день">›</button>
                    ${f.date !== TODAY ? '<button class="btn btn--text btn--sm" type="button" data-cs-act="today">Сегодня</button>' : ''}</div>
                ${ddHtml('csGroup', 'Группа', 'group', [['', 'Все группы', CS.consilium.filter(c => c.date === f.date).length], ...groupsToday], f.group, false, 'cons')}
                <div class="search">${ICON.search}<label class="sr-only" for="csQ">Поиск по ФИО или полису</label><input class="input" id="csQ" type="search" placeholder="ФИО или полис" value="${esc(f.q)}" autocomplete="off"></div>
            </div>
            <h2 class="cs-session-title">Заседание: ${esc(dayTitle(f.date))}${f.group ? ' · ' + esc(D.GROUPS[f.group]) : ' · все группы'} <span class="count">${all.length} ${plural(all.length, 'запись', 'записи', 'записей')}</span></h2>
            <div class="views cs-filters" role="group" aria-label="Статус записей">${FILTERS.map(([id, label, fn]) => { const n = all.filter(fn).length; return `<button class="view" type="button" data-cs-filter="${id}" aria-pressed="${f.filter === id}">${label}<span class="view__n${id === 'needs_revision' && n ? ' view__n--warn' : ''}">${n}</span></button>`; }).join('')}</div>
            ${!all.length ? `<div class="table-wrap"><div class="state"><h2>На этот день записей нет</h2><p>${f.group ? 'Попробуйте выбрать «Все группы» или другой день.' : 'Выберите другой день или откройте слоты во вкладке «Расписание и слоты».'}</p></div></div>`
            : `<div class="cs-split${sel ? ' cs-split--detail' : ''}">
                <div class="cs-list-wrap">${list.length ? `<ul class="agenda__list crows cs-list" aria-label="Пациенты заседания">${listHead}${list.map(c => recRow(c)).join('')}</ul>` : '<div class="agenda__list"><p class="agenda__empty">Записей с таким статусом нет.</p></div>'}</div>
                <div class="cs-detail-wrap">${sel ? detailBlock(sel, list.length ? list : all) : '<div class="cs-placeholder"><p>Выберите пациента в списке, чтобы увидеть подробности и действия.</p></div>'}</div>
            </div>`}`;
    }

    /* ---------- Мои записи и архив (врач, заведующий, разработчик) ---------- */
    function ownRecords(archived) {
        const me = R().user.id;
        return CS.consilium.filter(c => c.createdBy === me && !!c.archived === archived && scope().some(p => p.id === c.patientId)).sort((a, b) => {
            const rank = (c) => c.status === 'needs_revision' ? 0 : c.status === 'signed' ? 2 : 1;
            return rank(a) - rank(b) || K.sortByTime(a, b);
        });
    }
    function viewMine() {
        const list = ownRecords(false);
        const sel = list.find(c => c.id === S.cons.sel);
        const ret = list.filter(c => c.status === 'needs_revision').length;
        if (!list.length) return `<div class="table-wrap"><div class="state"><h2>Записей на консилиум пока нет</h2><p>Запишите пациента: выберите свободный слот в расписании или нажмите «Записать пациента».</p><a class="btn btn--primary" href="#/consilium/book">Записать пациента</a></div></div>`;
        return `${ret ? `<div class="banner banner--warn" role="note"><span class="banner__title">${ICON.warn} На доработке: ${ret}</span><span class="banner__body">Председатель вернул ${plural(ret, 'запись', 'записи', 'записей')}. Откройте запись, чтобы увидеть причину и отправить ее повторно.</span></div>` : ''}
            <div class="cs-split${sel ? ' cs-split--detail' : ''}">
                <div class="cs-list-wrap"><ul class="agenda__list crows cs-list" aria-label="Мои записи на консилиум">${listHead}${list.map(c => recRow(c, { withDate: true })).join('')}</ul></div>
                <div class="cs-detail-wrap">${sel ? detailBlock(sel, list) : '<div class="cs-placeholder"><p>Выберите запись, чтобы увидеть ее статус, причину возврата и доступные действия.</p></div>'}</div>
            </div>`;
    }
    function viewArchive() {
        const a = S.cons.arch; const q = fold(a.q.trim());
        const list = ownRecords(true).filter(c => (!a.date || c.date === a.date) && (!q || fold(c.patientName).includes(q) || (c.policy || '').includes(q)));
        const sel = list.find(c => c.id === S.cons.sel);
        return `<p class="lead">Архив — подписанные решения, которые вы перенесли из списка «Мои записи». Архив не меняет решение и не удаляет историю; запись можно восстановить.</p>
            <div class="toolbar"><div class="search">${ICON.search}<label class="sr-only" for="csArQ">Поиск по ФИО или полису</label><input class="input" id="csArQ" type="search" placeholder="ФИО или полис" value="${esc(a.q)}" autocomplete="off"></div>
                <label class="sr-only" for="csArDate">Дата консилиума</label><input class="input" type="date" id="csArDate" value="${esc(a.date)}">${a.date || a.q ? '<button class="btn btn--text btn--sm" type="button" data-cs-act="arch-clear">Сбросить</button>' : ''}</div>
            ${list.length ? `<div class="cs-split${sel ? ' cs-split--detail' : ''}"><div class="cs-list-wrap"><ul class="agenda__list crows cs-list" aria-label="Архив">${listHead}${list.map(c => recRow(c, { withDate: true })).join('')}</ul></div>
                <div class="cs-detail-wrap">${sel ? detailBlock(sel, list) : '<div class="cs-placeholder"><p>Выберите запись.</p></div>'}</div></div>`
            : '<div class="table-wrap"><div class="state"><h2>В архиве ничего не найдено</h2><p>Подписанное решение можно перенести в архив из списка «Мои записи».</p></div></div>'}`;
    }

    /* ---------- История решений (председатель) ---------- */
    function viewHistory() {
        const h = S.cons.hist; const q = fold(h.q.trim());
        const list = CS.consilium.filter(c => c.date >= h.from && c.date <= h.to && (!h.status || c.status === h.status) && (!h.group || c.group === h.group) && (!q || fold(c.patientName).includes(q) || (c.policy || '').includes(q)))
            .sort((a, b) => K.sortByTime(b, a));
        const sel = list.find(c => c.id === S.cons.sel);
        return `<p class="lead">Все записи консилиума, включая архивные отметки авторов. Подписанное решение изменить нельзя; история возвратов хранится в каждой записи. Удаленные врачами записи в интерфейсе не показываются (в системе их история сохраняется).</p>
            <div class="toolbar cs-toolbar" role="group" aria-label="Фильтры истории">
                <div class="an-dates"><label class="sr-only" for="csHFrom">С даты</label><input class="input" type="date" id="csHFrom" value="${esc(h.from)}"><span aria-hidden="true">—</span><label class="sr-only" for="csHTo">По дату</label><input class="input" type="date" id="csHTo" value="${esc(h.to)}"></div>
                ${ddHtml('csHStatus', 'Статус', 'hstatus', [['', 'Любой'], ...Object.entries(K.STATUS).map(([k, v]) => [k, v.label])], h.status, false, 'cons')}
                ${ddHtml('csHGroup', 'Группа', 'hgroup', [['', 'Все группы'], ...GROUPS.map(g => [g, D.GROUPS[g]])], h.group, false, 'cons')}
                <div class="search">${ICON.search}<label class="sr-only" for="csHQ">Поиск по ФИО или полису</label><input class="input" id="csHQ" type="search" placeholder="ФИО или полис" value="${esc(h.q)}" autocomplete="off"></div>
            </div>
            <p class="result-line">Найдено: ${list.length}</p>
            ${list.length ? `<div class="cs-split${sel ? ' cs-split--detail' : ''}"><div class="cs-list-wrap"><ul class="agenda__list crows cs-list" aria-label="История решений">${listHead}${list.map(c => recRow(c, { withDate: true })).join('')}</ul></div>
                <div class="cs-detail-wrap">${sel ? detailBlock(sel, list) : '<div class="cs-placeholder"><p>Выберите запись, чтобы увидеть решение, историю возвратов и журнал действий.</p></div>'}</div></div>`
            : '<div class="table-wrap"><div class="state"><h2>Записей по условиям не найдено</h2><p>Расширьте период или сбросьте фильтры.</p></div></div>'}`;
    }

    /* ---------- Реестры (председатель) ---------- */
    function viewRegistries() {
        const date = S.cons.regDate;
        const signed = CS.consilium.filter(c => c.status === 'signed' && (!date || c.date === date)).sort(K.sortByTime);
        const reg = (title, rows, extraHead, cell, empty) => `<section class="section"><div class="section__head"><h2>${title}</h2><span class="section__meta">${rows.length}</span></div>
            ${rows.length ? `<div class="table-scroll"><table class="htable"><thead><tr><th>Дата и время</th><th>Пациент</th><th>Диагноз</th><th>${extraHead}</th></tr></thead><tbody>${rows.map(c => `<tr><td data-label="Дата">${esc(when(c))}</td><td data-label="Пациент">${esc(c.patientName)}<br><span class="muted">${esc(doctorOf(c))}</span></td><td data-label="Диагноз" class="mono">${esc(c.icd)}</td><td data-label="${extraHead}">${cell(c)}</td></tr>`).join('')}</tbody></table></div>` : `<p class="an-empty">${empty}</p>`}</section>`;
        const treat = (c) => esc(D.TREATMENTS[c.treatment] || '—') + (c.scheme ? ': ' + esc(c.scheme.name) : '');
        return `<div class="toolbar"><label class="field__label" for="csRegDate">Дата заседания</label><input class="input" type="date" id="csRegDate" value="${esc(date)}"></div>
            <p class="an-rule">Реестры формируются автоматически из подписанных решений. Экспорт в Excel в прототип не перенесен — он работает в <a href="../redesign/#/consilium">Stage 3.5</a>.</p>
            ${reg('Хирургическое лечение', signed.filter(c => c.treatment === 'surgery'), 'Направление создано', c => `<label class="radio"><input type="checkbox" data-cs-ref="${c.id}" ${c.referralCompleted ? 'checked' : ''} ${perms().markReferralCompletion ? '' : 'disabled'}> ${c.referralCompleted ? 'Отмечено' : 'Отметить'}</label>`, 'Хирургических назначений за этот день нет.')}
            ${reg('Химиотерапевтическое лечение', signed.filter(c => c.treatment === 'chemotherapy'), 'Схема', c => c.scheme ? `${c.scheme.code ? `<span class="mono">${esc(c.scheme.code)}</span> — ` : ''}${esc(c.scheme.name)}` : '—', 'Химиотерапевтических назначений за этот день нет.')}
            ${reg('ГОК', signed.filter(c => c.goc), 'Лечение', treat, 'Случаев ГОК за этот день нет.')}
            ${reg('ГОК 65+', signed.filter(c => c.goc65), 'Лечение', treat, 'Случаев ГОК 65+ за этот день нет.')}`;
    }

    /* ---------- Расписание и слоты ---------- */
    function weekStrip(group, selDay, weekStart, attr) {
        const days = K.weekDates(weekStart);
        return `<div class="cs-week" role="group" aria-label="Дни недели">${days.map(d => {
            const s = K.daySummary(CS, d, group, ctx());
            const past = d < TODAY;
            const label = !s.opened ? (s.occupied ? `${s.occupied} занято` : 'не открыт') : past ? 'прошел' : s.free ? `${s.free} своб.` : s.unavailable === s.total ? (K.isWeekend(d) && S.cons.wp23 ? 'выходной' : 'недоступен') : 'нет своб.';
            const tone = !s.opened || past ? 'off' : s.free ? 'free' : 'full';
            return `<button class="cs-day-btn cs-day-btn--${tone}${d === TODAY ? ' is-today' : ''}" type="button" ${attr}="${d}" aria-pressed="${d === selDay}" aria-label="${esc(dayTitle(d))}: ${esc(label)}${d === TODAY ? ', сегодня' : ''}">
                <span class="cs-day-btn__wd">${WD[dow(d)]}</span><span class="cs-day-btn__d">${Number(d.slice(8))}</span><span class="cs-day-btn__s">${esc(label)}</span></button>`;
        }).join('')}</div>`;
    }
    function weekControls() {
        const days = K.weekDates(S.cons.week);
        const cur = K.weekDates(TODAY)[0];
        return `<div class="cs-weeknav"><button class="btn btn--icon" type="button" data-cs-act="week" data-d="-7" aria-label="Предыдущая неделя">‹</button>
            <span class="cs-weeknav__label">${esc(fmtShort(days[0]))} — ${esc(fmtShort(days[6]))}</span>
            <button class="btn btn--icon" type="button" data-cs-act="week" data-d="7" aria-label="Следующая неделя">›</button>
            ${S.cons.week !== cur ? '<button class="btn btn--text btn--sm" type="button" data-cs-act="thisweek">Текущая неделя</button>' : '<span class="muted cs-weeknav__note">текущая неделя</span>'}</div>`;
    }
    function slotButtons(group, date, mode) {
        const slots = K.allSlots(CS, TODAY, [group]).filter(x => x.date === date && x.group === group).sort((a, b) => a.time.localeCompare(b.time));
        const noTime = CS.consilium.filter(c => c.date === date && c.group === group && !c.time);
        const offSlot = CS.consilium.filter(c => c.date === date && c.group === group && c.time && !slots.some(x => x.time === c.time));
        const items = slots.map(slot => {
            const st = K.slotState(CS, slot, ctx());
            if (st.state === 'occupied') {
                const c = st.record; const v = K.recordView(c, viewer());
                const who = v.kind === 'full' ? `${esc(c.patientName)} · ${K.STATUS[c.status].label}` : v.kind === 'department' ? `${esc(c.patientName)} · ${K.STATUS[c.status].label}` : 'Занято';
                const inner = `<span class="slot__time">${esc(slot.time)}</span><span class="slot__txt">${who}</span>${v.mine ? '<span class="slot__tag">моя запись</span>' : ''}`;
                return { st: 'busy', html: v.kind === 'full' && (v.mine || isChair()) ? `<button class="slot slot--busy" type="button" data-cs-open="${c.id}" aria-label="${esc(slot.time)}: занято, ${esc(c.patientName)} — открыть запись">${inner}</button>` : `<div class="slot slot--busy">${inner}</div>` };
            }
            if (st.state !== 'free') return { st: 'off', html: `<div class="slot slot--off" title="${esc(st.reason)}"><span class="slot__time">${esc(slot.time)}</span><span class="slot__txt">${esc(st.state === 'past' ? 'Время прошло' : 'Выходной')}</span><span class="sr-only">: ${esc(st.reason.replace(/^Выходной:\s*/, ''))}</span></div>` };
            const keepsAuto = isChair() && !slot.isDefaultWeek && K.weekDates(TODAY).includes(slot.date) && K.DEFAULT_TIMES.includes(slot.time);
            const del = isChair() && !slot.isDefaultWeek ? `<button class="slot__del" type="button" data-cs-act="delslot" data-id="${slot.id}" aria-label="Удалить сохраненный слот ${esc(slot.time)}${keepsAuto ? ' (время останется открытым автоматически)' : ''}" title="${keepsAuto ? 'Время останется открытым автоматически' : 'Удалить слот'}">${ICON.x}</button>` : '';
            if (mode === 'pick') {
                const on = S.cons.bk && S.cons.bk.date === date && S.cons.bk.time === slot.time;
                return { st: 'free', html: `<button class="slot slot--free${on ? ' is-selected' : ''}" type="button" data-cs-pick="${slot.time}" data-date="${date}" aria-pressed="${!!on}"><span class="slot__time">${esc(slot.time)}</span><span class="slot__txt">${on ? 'Выбрано' : 'Свободно'}</span></button>` };
            }
            if (perms().bookConsilium) return { st: 'free', html: `<a class="slot slot--free" href="#/consilium/book?group=${group}&date=${date}&time=${slot.time}" aria-label="${esc(slot.time)}: свободно — записать пациента"><span class="slot__time">${esc(slot.time)}</span><span class="slot__txt">Свободно</span></a>` };
            return { st: 'free', html: `<div class="slot slot--free${del ? ' slot--with-del' : ''}"><span class="slot__time">${esc(slot.time)}</span><span class="slot__txt">Свободно</span>${del}</div>` };
        });
        const shown = mode === 'view' && S.cons.onlyFree ? items.filter(i => i.st === 'free') : items;
        const counts = { free: items.filter(i => i.st === 'free').length, busy: items.filter(i => i.st === 'busy').length + offSlot.length + noTime.length, off: items.filter(i => i.st === 'off').length };
        return { counts, opened: slots.length > 0, html: `${shown.length ? `<div class="slots" role="list">${shown.map(i => `<div role="listitem" class="slot-wrap">${i.html}</div>`).join('')}</div>` : ''}
            ${offSlot.length || noTime.length ? `<p class="an-rule">${noTime.length ? `Записи без назначенного времени: ${noTime.map(c => esc(K.recordView(c, viewer()).kind === 'busy' ? 'занято' : c.patientName)).join(', ')} — время не назначено. ` : ''}${offSlot.length ? `Записи вне сетки слотов: ${offSlot.length}.` : ''}</p>` : ''}` };
    }
    function viewSchedule() {
        const g = sGroup(); const d = S.cons.day;
        const r = slotButtons(g, d, 'view');
        const past = d < TODAY;
        const empty = !r.opened ? `<div class="cs-empty"><b>${past ? 'Этот день прошел.' : 'Расписание на этот день не открыто.'}</b><p>${isChair() ? 'Откройте слоты ниже — до этого записаться на этот день нельзя.' : 'Председатель еще не открыл этот день для записи. Пустое расписание не означает, что можно записаться на любое время.'}</p></div>`
            : S.cons.onlyFree && !r.counts.free ? '<div class="cs-empty"><b>Свободных слотов нет.</b><p>Выберите другой день или снимите фильтр «Только свободные».</p></div>' : '';
        return `<div class="toolbar cs-toolbar" role="group" aria-label="Группа и неделя">
                ${ddHtml('csSGroup', 'Группа', 'sgroup', GROUPS.map(k => [k, D.GROUPS[k]]), g, true, 'cons')}
                ${weekControls()}
                <button class="btn btn--sm" type="button" data-cs-act="onlyfree" aria-pressed="${S.cons.onlyFree}">Только свободные</button>
            </div>
            ${weekStrip(g, S.cons.day, S.cons.week, 'data-cs-day')}
            <section class="cs-dayview" aria-labelledby="csDayTitle">
                <div class="section__head"><h2 id="csDayTitle">${esc(dayTitle(d))} · ${esc(D.GROUPS[g])}</h2><span class="section__meta">${r.opened ? `свободно ${r.counts.free} · занято ${r.counts.busy}${r.counts.off ? ` · недоступно ${r.counts.off}` : ''}` : ''}</span></div>
                <ul class="cs-legend" aria-label="Обозначения"><li><span class="slot-dot slot-dot--free"></span>свободно</li><li><span class="slot-dot slot-dot--busy"></span>занято</li><li><span class="slot-dot slot-dot--off"></span>недоступно</li></ul>
                ${empty}${r.html}
            </section>
            ${isChair() ? genForm(g) : ''}
            ${rulesNote()}`;
    }
    function rulesNote() {
        return `<details class="deleted cs-rules"><summary>Правила записи в прототипе</summary>
            <ul class="plain-list">
                <li>Текущая неделя открыта автоматически (09:00–16:45, шаг 15 минут, все группы), следующие — только слотами председателя. Одна запись на слот; у пациента — одна действующая запись. Группа определяется кодом МКБ-10.</li>
                <li><label class="radio"><input type="checkbox" data-cs-wp23 ${S.cons.wp23 ? 'checked' : ''}> Показывать целевые правила WP2.3</label> Прошедшее время и выходные автоматической недели недоступны для записи. <b>Текущий backend эти проверки не выполняет</b>, а при пустой таблице слотов принимает любое время — это запланировано в WP2.3. Оформление записи задним числом председателем (с причиной и аудитом) в прототипе не реализовано.</li>
            </ul></details>`;
    }
    function genForm(group) {
        const g = S.cons.gen;
        return `<details class="deleted cs-gen" ${g.open ? 'open' : ''}><summary>Открыть слоты на дату · ${esc(D.GROUPS[group])}</summary>
            <form class="cs-gen__form" data-cs-form="gen" novalidate>
                <div class="field"><label class="field__label" for="genDate">Дата</label><input class="input" type="date" id="genDate" name="date" min="${TODAY}" value="${esc(g.date)}"></div>
                <div class="field"><label class="field__label" for="genStart">Начало</label><input class="input" type="time" id="genStart" name="start" value="${esc(g.start)}"></div>
                <div class="field"><label class="field__label" for="genEnd">Окончание</label><input class="input" type="time" id="genEnd" name="end" value="${esc(g.end)}"></div>
                <div class="field"><label class="field__label" for="genInt">Интервал</label><select class="select" id="genInt" name="interval">${['15', '30', '60'].map(v => `<option value="${v}" ${g.interval === v ? 'selected' : ''}>${v} минут</option>`).join('')}</select></div>
                <div class="cs-gen__submit"><button class="btn btn--primary" type="submit">Открыть слоты</button></div>
                <p class="field__error" id="genErr" role="alert" ${g.error ? '' : 'hidden'}>${esc(g.error || '')}</p>
                <p class="field__hint">Можно добавить слоты на уже открытый день и явно открыть выходной. Уже существующие слоты пропускаются, записи не меняются. Прошедшие даты недоступны. Занятый слот удалить нельзя; слоты автоматической недели не удаляются.</p>
            </form></details>`;
    }

    /* ---------- Запись пациента ---------- */
    function bookState(r) {
        const key = r.params.toString();
        if (S.cons.bk && S.cons.bk.key === key) return S.cons.bk;
        const id = r.params.get('id');
        const rec = id ? CS.consilium.find(c => c.id === id) : null;
        const pid = rec ? rec.patientId : r.params.get('patient') || '';
        const p = D.patients.find(x => x.id === pid);
        const group = rec ? rec.group : p ? K.groupForDiagnosis(p.icd) : r.params.get('group') || '';
        const date = rec ? rec.date : r.params.get('date') || TODAY;
        S.cons.bk = { key, id: rec ? rec.id : '', patientId: pid, group, date, time: rec ? rec.time : r.params.get('time') || '', week: K.weekDates(date)[0], error: '', errorAt: '', conflictDemo: false,
            f: rec ? { policy: rec.policy, hasBenefit: rec.hasBenefit, localization: rec.diagnosisLocalization, icd: rec.icd, additionalDiagnosis: rec.additionalDiagnosis, comment: rec.comment, additionalParticipant: rec.additionalParticipant, uvRuv: rec.uvRuv, needsSurgeon: rec.needsSurgeon, needsRadiotherapist: rec.needsRadiotherapist }
                : fieldsFor(p) };
        return S.cons.bk;
    }
    const icdLoc = (code) => { for (const v of Object.values(window.ICD10 || {})) if (v.diagnoses.some(d => d.code === code)) return X.ui(v.label); return ''; };
    const fieldsFor = (p) => p ? { policy: p.policy || '', hasBenefit: false, localization: icdLoc(p.icd) || p.localization || '', icd: K.validDiagnosis(p.icd, false) || '', additionalDiagnosis: '', comment: '', additionalParticipant: '', uvRuv: false, needsSurgeon: false, needsRadiotherapist: false } : { policy: '', hasBenefit: false, localization: '', icd: '', additionalDiagnosis: '', comment: '', additionalParticipant: '', uvRuv: false, needsSurgeon: false, needsRadiotherapist: false };
    function viewBook(r) {
        if (!perms().bookConsilium) return `<div class="table-wrap"><div class="state"><h2>Запись на консилиум недоступна</h2><p>${isChair() ? 'Председатель рассматривает и подписывает записи, но не создает их. Записывают пациентов лечащий врач, заведующий отделением и разработчик.' : 'У вашей роли нет права записи на консилиум.'}</p><a class="btn" href="#/consilium">К консилиуму</a></div></div>`;
        const b = bookState(r);
        const rec = b.id ? CS.consilium.find(c => c.id === b.id) : null;
        if (b.id && (!rec || rec.createdBy !== R().user.id || !K.EDITABLE.includes(rec.status))) return `<div class="table-wrap"><div class="state"><h2>Запись недоступна для изменения</h2><p>${rec ? (rec.status === 'in_review' ? 'Председатель рассматривает запись — изменения недоступны.' : rec.status === 'signed' ? 'Решение подписано — запись изменить нельзя.' : 'Изменять запись может только ее автор.') : 'Запись не найдена.'}</p><a class="btn" href="#/consilium/mine">К моим записям</a></div></div>`;
        const pool = scope().filter(p => canEdit(p)).sort((a, c) => a.name.localeCompare(c.name, 'ru'));
        const p = D.patients.find(x => x.id === b.patientId);
        const existing = p && !rec ? CS.consilium.find(c => c.patientId === p.id) : null;
        const group = rec ? rec.group : p ? K.groupForDiagnosis(p.icd) : '';
        if (group) b.group = group;
        const paramGroup = r.params.get('group');
        const groupWarn = !rec && p && paramGroup && paramGroup !== group ? `<div class="banner banner--warn" role="note"><span class="banner__body">Выбранный в расписании слот относится к группе «${esc(D.GROUPS[paramGroup])}», а пациенту по коду МКБ-10 подходит группа «${esc(D.GROUPS[group])}». Выберите слот этой группы.</span></div>` : '';
        const f = b.f;
        const title = rec ? (rec.status === 'needs_revision' ? 'Доработка записи на консилиум' : 'Изменение и перенос записи') : 'Запись пациента на консилиум';
        const err = (at) => b.error && b.errorAt === at ? `<div class="banner banner--danger" role="alert" id="bkErr"><span class="banner__body">${esc(b.error)}</span></div>` : '';
        const yn = (name, label, val) => `<label class="radio"><input type="checkbox" name="${name}" ${val ? 'checked' : ''}> ${label}</label>`;
        let slotsHtml = '';
        if (group && !existing) {
            const sr = slotButtons(group, b.date, 'pick');
            const selDays = K.weekDates(b.week);
            slotsHtml = `<div class="cs-weeknav"><button class="btn btn--icon" type="button" data-bk-week="-7" aria-label="Предыдущая неделя">‹</button><span class="cs-weeknav__label">${esc(fmtShort(selDays[0]))} — ${esc(fmtShort(selDays[6]))}</span><button class="btn btn--icon" type="button" data-bk-week="7" aria-label="Следующая неделя">›</button></div>
                ${weekStrip(group, b.date, b.week, 'data-bk-day')}
                <h3 class="cs-subhead">${esc(dayTitle(b.date))}</h3>
                ${!sr.opened ? `<div class="cs-empty"><b>${b.date < TODAY ? 'Этот день прошел.' : 'Председатель не открыл этот день для записи.'}</b><p>Выберите другой день недели или следующую неделю.</p></div>` : !sr.counts.free ? '<div class="cs-empty"><b>Свободных слотов нет.</b><p>Все слоты этого дня заняты или недоступны. Выберите другой день.</p></div>' : ''}
                ${sr.html}`;
        }
        const ready = p && group && !existing;
        return `<h1>${title}</h1>
            <form class="cs-book" data-cs-form="book" novalidate>
            <section class="section" aria-labelledby="bk1"><div class="section__head"><h2 id="bk1">1. Пациент</h2></div>
                ${rec ? `<p><b>${esc(rec.patientName)}</b> · группа «${esc(D.GROUPS[rec.group])}»</p>${rec.status === 'needs_revision' && rec.revisions.length ? `<div class="banner banner--warn" role="note"><span class="banner__body"><b>Причина возврата:</b> ${esc(rec.revisions[rec.revisions.length - 1].text)}</span></div>` : ''}`
                : `${!p && paramGroup && b.time ? `<div class="banner banner--info" role="note"><span class="banner__body">Выбрано время: <b>${esc(dayTitle(b.date))}, ${esc(b.time)}</b> · группа «${esc(D.GROUPS[paramGroup])}». Выберите пациента с кодом МКБ-10 этой группы.</span></div>` : ''}
                    <div class="field"><label class="field__label" for="bkPatient">Пациент</label><select class="select" id="bkPatient" name="patient"><option value="">Выберите пациента</option>${pool.map(x => { const has = CS.consilium.some(c => c.patientId === x.id); const gx = K.groupForDiagnosis(x.icd); return `<option value="${x.id}" ${x.id === b.patientId ? 'selected' : ''}>${esc(x.name)} · ${esc(x.icd)} · ${esc(D.GROUPS[gx])}${has ? ' — уже записан' : ''}</option>`; }).join('')}</select>
                    <span class="field__hint">Показаны пациенты, которых вы можете редактировать. Консилиумная группа определяется по коду МКБ-10 из карточки.</span></div>
                    ${p ? `<p>Группа: <b>${esc(D.GROUPS[group])}</b> <span class="muted">· по коду <span class="mono">${esc(p.icd)}</span></span></p>` : ''}`}
                ${existing ? `<div class="banner banner--info" role="note"><span class="banner__body">Пациент уже записан: ${esc(when(existing))}, «${esc(D.GROUPS[existing.group])}», статус «${esc(K.STATUS[existing.status].label)}». Новую запись создать нельзя — используйте перенос существующей.</span><span class="banner__actions">${existing.createdBy === R().user.id ? `<a class="btn btn--sm" href="#/consilium/mine?rec=${existing.id}">Открыть запись</a>` : ''}</span></div>` : ''}
                ${err('patient')}${groupWarn}
            </section>
            ${ready || rec ? `<section class="section" aria-labelledby="bk2"><div class="section__head"><h2 id="bk2" tabindex="-1">2. Время заседания</h2><span class="section__meta">${b.time ? `выбрано: ${esc(fmtShort(b.date))}, ${esc(b.time)}` : 'выберите свободный слот'}</span></div>${err('slot')}${slotsHtml}</section>
            <section class="section" aria-labelledby="bk3"><div class="section__head"><h2 id="bk3">3. Сведения для консилиума</h2></div>
                <div class="edit-grid">
                    <div class="field"><label class="field__label" for="bkLoc">Локализация <span class="req">*</span></label><input class="input" id="bkLoc" name="localization" list="bkLocs" value="${esc(f.localization)}" autocomplete="off"><datalist id="bkLocs">${Object.values(window.ICD10 || {}).map(x => `<option value="${esc(X.ui(x.label))}"></option>`).join('')}</datalist></div>
                    <div class="field"><label class="field__label" for="bkIcd">Основной диагноз МКБ-10 (C00–C97) <span class="req">*</span></label><input class="input mono" id="bkIcd" name="icd" value="${esc(f.icd)}" autocomplete="off"></div>
                    <div class="field"><label class="field__label" for="bkAdd">Дополнительный диагноз</label><input class="input mono" id="bkAdd" name="additionalDiagnosis" value="${esc(f.additionalDiagnosis)}" autocomplete="off"></div>
                    <div class="field"><label class="field__label" for="bkPolicy">Полис ОМС</label><input class="input mono" id="bkPolicy" name="policy" value="${esc(f.policy)}" autocomplete="off"></div>
                    <div class="field" style="grid-column:1/-1"><label class="field__label" for="bkComment">Комментарий для консилиума</label><textarea class="textarea" id="bkComment" name="comment" rows="3" maxlength="4000">${esc(f.comment)}</textarea></div>
                    <div class="field"><label class="field__label" for="bkPart">Дополнительный участник</label><input class="input" id="bkPart" name="additionalParticipant" maxlength="200" value="${esc(f.additionalParticipant)}"></div>
                    <fieldset class="field cs-checks" style="grid-column:1/-1"><legend class="field__label">Участники и отметки</legend>${yn('needsSurgeon', 'Нужен хирург', f.needsSurgeon)}${yn('needsRadiotherapist', 'Нужен лучевой терапевт', f.needsRadiotherapist)}${yn('uvRuv', 'УВ/РУВ', f.uvRuv)}${yn('hasBenefit', 'Есть льгота', f.hasBenefit)}</fieldset>
                </div>${err('fields')}
            </section>
            <section class="section cs-review" aria-labelledby="bk4"><div class="section__head"><h2 id="bk4">4. Проверьте и подтвердите</h2></div>
                <dl class="dl"><div><dt>Пациент</dt><dd>${esc(p ? p.name : rec.patientName)}</dd></div><div><dt>Группа</dt><dd>${esc(D.GROUPS[group] || '—')}</dd></div>
                    <div><dt>Дата и время</dt><dd>${b.time ? esc(`${dayTitle(b.date)}, ${b.time}`) : '<span class="status status--warn">слот не выбран</span>'}</dd></div>
                    <div><dt>Лечащий врач</dt><dd>${esc((p || {}).doctorFull || '—')}</dd></div></dl>
                ${err('submit')}
                <label class="radio cs-demo"><input type="checkbox" name="conflictDemo" ${b.conflictDemo ? 'checked' : ''}> Демо: другой сотрудник займет выбранный слот перед подтверждением</label>
                <div class="cs-actions"><button class="btn btn--primary" type="submit">${rec ? (rec.status === 'needs_revision' ? 'Отправить на повторное рассмотрение' : 'Сохранить изменения') : 'Записать пациента'}</button><a class="btn" href="${rec ? `#/consilium/mine?rec=${rec.id}` : '#/consilium'}">Отмена</a></div>
            </section>` : ''}
            </form>`;
    }
    function readBookForm() {
        const form = $('[data-cs-form=book]'); if (!form) return;
        const b = S.cons.bk; const fd = new FormData(form);
        ['localization', 'icd', 'additionalDiagnosis', 'policy', 'comment', 'additionalParticipant'].forEach(k => { if (fd.has(k)) b.f[k] = String(fd.get(k)); });
        if (form.querySelector('[name=needsSurgeon]')) ['needsSurgeon', 'needsRadiotherapist', 'uvRuv', 'hasBenefit'].forEach(k => { b.f[k] = fd.has(k); });
        b.conflictDemo = fd.has('conflictDemo');
    }
    function submitBook() {
        readBookForm();
        const b = S.cons.bk; const rec = b.id ? CS.consilium.find(c => c.id === b.id) : null;
        const p = D.patients.find(x => x.id === (rec ? rec.patientId : b.patientId));
        b.error = ''; b.errorAt = '';
        if (!b.time) { b.error = 'Выберите свободный слот в шаге 2.'; b.errorAt = 'slot'; render(); focusErr(); return; }
        // Демонстрация гонки: слот занимают между выбором и подтверждением.
        if (b.conflictDemo && !CS.consilium.some(c => c.date === b.date && c.time === b.time && c.group === b.group)) {
            CS.consilium.push({ id: `c-demo-${Date.now()}`, patientId: 'demo-other', patientName: 'Пациент другого врача (демо)', group: b.group, date: b.date, time: b.time, createdBy: 'u-demo-other', status: 'recorded', revisions: [], archived: false, icd: 'C34.1', doctor: 'Другой врач', diagnosisLocalization: '', comment: '' });
            b.conflictDemo = false;
        }
        const input = { actor: actor(), patient: p, group: b.group, date: b.date, time: b.time, ...b.f, doctorName: p ? p.doctorFull : '' };
        try {
            const c = rec ? K.update(CS, rec.id, input, ctx()) : K.book(CS, input, ctx());
            const wasRet = rec && c.status === 'revised';
            S.cons.bk = null; S.cons.sel = c.id;
            location.hash = `#/consilium/mine?rec=${c.id}`;
            toast(rec ? (wasRet ? 'Запись отправлена на повторное рассмотрение' : 'Изменения сохранены') : `Пациент записан: ${fmtShort(c.date)}, ${c.time}. Статус — «Записан».`);
        } catch (e) {
            b.error = e.message; b.errorAt = ['slot_occupied', 'slot_missing', 'slot_past', 'slot_weekend', 'slot_required'].includes(e.code) ? 'slot' : ['already_booked', 'forbidden_patient', 'group_mismatch', 'forbidden_role'].includes(e.code) ? 'patient' : ['diagnosis', 'additional_diagnosis', 'localization'].includes(e.code) ? 'fields' : 'submit';
            if (e.code === 'slot_occupied') { b.error = 'Пока вы заполняли форму, этот слот занял другой сотрудник. Выберите другое время — остальные данные сохранены.'; b.time = ''; }
            render(); focusErr();
        }
    }
    const focusErr = () => { const e = $('#bkErr'); if (e) { e.setAttribute('tabindex', '-1'); e.focus(); e.scrollIntoView({ block: 'center' }); } };

    /* ---------- Действия ---------- */
    const run = (fn, ok) => { try { const res = fn(); if (ok) toast(typeof ok === 'function' ? ok(res) : ok); render(); return res; } catch (e) { toast(e.message); render(); return null; } };
    const recById = (id) => CS.consilium.find(c => c.id === id);
    function signDialog(c) {
        const d = S.cons.drafts[c.id];
        const err = $('#csDecErr');
        if (!d.treatment) { err.hidden = false; err.textContent = 'Выберите назначенное лечение.'; $('#csTreat').focus(); return; }
        if (d.treatment === 'chemotherapy' && !d.schemeName.trim()) { err.hidden = false; err.textContent = 'Для химиотерапевтического лечения укажите схему.'; $('#csScheme').focus(); return; }
        const p = patientOf(c);
        dialog({ title: 'Подписать решение консилиума?', icon: ICON.lock,
            body: `<p><b>${esc(c.patientName)}</b> · ${esc(when(c))} · ${esc(D.GROUPS[c.group])}</p>
                <dl class="dl"><div><dt>Назначенное лечение</dt><dd>${esc(D.TREATMENTS[d.treatment])}</dd></div>${d.treatment === 'chemotherapy' ? `<div><dt>Схема</dt><dd>${esc(d.schemeName)}</dd></div>` : ''}<div><dt>Отметки</dt><dd>${esc([d.goc && 'ГОК', d.goc65 && 'ГОК 65+'].filter(Boolean).join(', ') || 'Нет')}</dd></div></dl>
                <p><b>После подписания решение изменить нельзя.</b> Этап маршрута пациента не изменится, ручная отметка решения ОК в карточке не создается.</p>`,
            actions: [{ label: 'Отмена', autofocus: true }, { label: 'Подписать решение', kind: 'primary', run: () => {
                run(() => K.sign(CS, c.id, d, actor(), ctx({ patientStage: p ? p.stage : null, doctorName: doctorOf(c) })), 'Решение подписано. Этап пациента не изменен.');
                delete S.cons.drafts[c.id];
            } }] });
    }
    /* Удаление сохраненного слота. В WP2.1 удаляется только сохраненная строка; на текущей неделе
       то же время снова открыто автоматически — интерфейс предупреждает об этом и не называет это закрытием. */
    const fallbackText = (fb) => fb.state === 'free' ? 'останется открытым для записи: текущая неделя открыта автоматически'
        : fb.state === 'weekend' ? 'вернется к автоматическому слоту выходного. Сейчас он недоступен только по демонстрационному правилу WP2.3 — текущий backend такой слот не закрывает'
        : fb.state === 'past' ? 'вернется к автоматическому слоту; время уже прошло' : 'вернется к автоматическому слоту';
    function deleteSlotFlow(id) {
        const impact = K.slotDeletionImpact(CS, id, ctx());
        if (!impact) { toast('Слот не найден'); return; }
        const t = impact.slot.time;
        const doDelete = () => {
            try { K.deleteSlot(CS, id, actor(), ctx()); render(); toast(impact.fallback ? `Сохраненный слот ${t} удален. Время ${fallbackText(impact.fallback)}.` : `Слот ${t} удален — это время закрыто для записи.`); }
            catch (e) { render(); toast(e.message); }
        };
        if (!impact.fallback) { doDelete(); return; }
        dialog({ title: `Удалить сохраненный слот ${esc(t)}?`, icon: ICON.warn,
            body: `<p>${esc(dayTitle(impact.slot.date))}, ${esc(t)} · ${esc(D.GROUPS[impact.slot.group])}.</p>
                <div class="banner banner--warn" role="note"><span class="banner__body"><b>Время не закроется.</b> После удаления время ${esc(t)} ${esc(fallbackText(impact.fallback))}.</span></div>
                <p class="muted">Закрыть время автоматической недели нельзя: в текущей версии (WP2.1) такой операции нет, это потребует нового правила расписания (WP2.3).</p>`,
            actions: [{ label: 'Отмена', autofocus: true }, { label: 'Удалить сохраненный слот', kind: 'danger', run: () => doDelete() }] });
    }
    function returnDialog(c) {
        const last = c.revisions[c.revisions.length - 1];
        dialog({ title: 'Вернуть запись на доработку', body: `<p>${esc(c.patientName)} · ${esc(when(c))}</p>${last ? `<p class="muted">Предыдущий возврат (раунд ${last.round}): ${esc(last.text)}</p>` : ''}
            <div class="field"><label class="field__label" for="retText">Что нужно исправить <span class="req">*</span></label><textarea class="textarea" id="retText" rows="4" maxlength="4000" placeholder="Например: приложите заключение КТ с контрастом"></textarea><span class="field__hint">Комментарий увидит автор записи. Он должен отличаться от предыдущего возврата.</span></div>
            <p class="field__error" id="retErr" role="alert" hidden></p>`,
            actions: [{ label: 'Отмена' }, { label: 'Вернуть врачу', kind: 'primary', run: (dlg) => {
                try { K.returnForRevision(CS, c.id, $('#retText', dlg).value, actor(), ctx()); delete S.cons.drafts[c.id]; render(); toast('Запись возвращена на доработку'); }
                catch (e) { const er = $('#retErr', dlg); er.hidden = false; er.textContent = e.message; $('#retText', dlg).focus(); return false; }
            } }] });
    }
    function deleteDialog(c) {
        dialog({ title: 'Удалить запись на консилиум?', danger: true, icon: ICON.warn, body: `<p>${esc(c.patientName)} · ${esc(when(c))}. Слот освободится для новой записи.</p>${c.revisions.length ? '<p>У записи есть история возвратов — она сохранится в системе.</p>' : ''}`,
            actions: [{ label: 'Отмена', autofocus: true }, { label: 'Удалить запись', kind: 'danger', run: () => { run(() => K.removeOwned(CS, c.id, actor(), ctx()), 'Запись удалена, слот снова свободен'); S.cons.sel = null; } }] });
    }
    document.addEventListener('click', (e) => {
        const t = e.target;
        const sel = t.closest('[data-cs-sel]');
        if (sel) {
            const prev = S.cons.sel; S.cons.sel = sel.dataset.csSel || null; render();
            if (S.cons.sel) { const d = $('#csDetail'); if (d) { d.focus({ preventScroll: true }); if (window.innerWidth <= 1023) d.scrollIntoView({ block: 'start' }); } }
            else { const r = prev && $(`.cs-row[data-cs-sel="${prev}"]`); (r || $('.cs-row') || $('#main h1')).focus(); }
            return;
        }
        const flt = t.closest('[data-cs-filter]');
        if (flt) { S.cons.filter = flt.dataset.csFilter; render(); $(`[data-cs-filter="${S.cons.filter}"]`).focus(); return; }
        const open = t.closest('[data-cs-open]');
        if (open) { const c = recById(open.dataset.csOpen); S.cons.sel = c.id; S.cons.date = c.date; S.cons.filter = 'all'; location.hash = isChair() ? '#/consilium/session' : `#/consilium/${c.archived ? 'archive' : 'mine'}`; return; }
        const day = t.closest('[data-cs-day]');
        if (day) { S.cons.day = day.dataset.csDay; render(); $(`[data-cs-day="${S.cons.day}"]`).focus(); return; }
        const bday = t.closest('[data-bk-day]');
        if (bday) { readBookForm(); S.cons.bk.date = bday.dataset.bkDay; S.cons.bk.time = ''; S.cons.bk.error = ''; render(); $(`[data-bk-day="${S.cons.bk.date}"]`).focus(); return; }
        const bweek = t.closest('[data-bk-week]');
        if (bweek) { readBookForm(); const b = S.cons.bk; b.week = K.addDays(b.week, Number(bweek.dataset.bkWeek)); b.date = b.week < TODAY && K.weekDates(b.week).includes(TODAY) ? TODAY : b.week; b.time = ''; render(); $(`[data-bk-week="${bweek.dataset.bkWeek}"]`).focus(); return; }
        const pick = t.closest('[data-cs-pick]');
        if (pick) { readBookForm(); const b = S.cons.bk; b.time = pick.dataset.csPick; b.date = pick.dataset.date; b.error = ''; render(); const again = $(`[data-cs-pick="${b.time}"]`); if (again) again.focus(); return; }
        const ref = t.closest('[data-cs-ref]');
        if (ref) { run(() => K.setReferralCompleted(CS, ref.dataset.csRef, ref.checked, actor(), ctx()), ref.checked ? 'Отмечено: направление создано' : 'Отметка снята'); return; }
        const wp = t.closest('[data-cs-wp23]');
        if (wp) { S.cons.wp23 = wp.checked; render(); const w = $('[data-cs-wp23]'); if (w) { w.closest('details').open = true; w.focus(); } return; }
        const a = t.closest('[data-cs-act]');
        if (!a) return;
        const act = a.dataset.csAct; const c = a.dataset.id ? recById(a.dataset.id) : null;
        if (act === 'day') { S.cons.date = K.addDays(S.cons.date, Number(a.dataset.d)); S.cons.sel = null; render(); $(`[data-cs-act="day"][data-d="${a.dataset.d}"]`).focus(); }
        else if (act === 'today') { S.cons.date = TODAY; S.cons.sel = null; render(); $('#csDate').focus(); }
        else if (act === 'week') { S.cons.week = K.addDays(S.cons.week, Number(a.dataset.d)); S.cons.day = K.weekDates(S.cons.week).includes(TODAY) ? TODAY : S.cons.week; render(); $(`[data-cs-act="week"][data-d="${a.dataset.d}"]`).focus(); }
        else if (act === 'thisweek') { S.cons.week = K.weekDates(TODAY)[0]; S.cons.day = TODAY; render(); $('[data-cs-act="week"]').focus(); }
        else if (act === 'onlyfree') { S.cons.onlyFree = !S.cons.onlyFree; render(); $('[data-cs-act="onlyfree"]').focus(); }
        else if (act === 'review') { run(() => K.startReview(CS, c.id, actor(), ctx()), 'Рассмотрение начато'); const tr = $('#csTreat'); if (tr) tr.focus(); }
        else if (act === 'return') returnDialog(c);
        else if (act === 'delete') deleteDialog(c);
        else if (act === 'archive' || act === 'restore') { run(() => K.setArchived(CS, c.id, act === 'archive', actor(), ctx()), act === 'archive' ? 'Запись перенесена в архив' : 'Запись восстановлена из архива'); S.cons.sel = null; }
        else if (act === 'fav') { run(() => K.toggleFavorite(CS, c.patientId, actor(), ctx()), (on) => on ? 'Пациент добавлен в избранное' : 'Пациент убран из избранного'); const f = $('[data-cs-act="fav"]'); if (f) f.focus(); }
        else if (act === 'delslot') deleteSlotFlow(a.dataset.id);
        else if (act === 'arch-clear') { S.cons.arch = { q: '', date: '' }; render(); }
    });
    document.addEventListener('change', (e) => {
        const t = e.target;
        if (t.id === 'csDate' && t.value) { S.cons.date = t.value; S.cons.sel = null; render(); $('#csDate').focus(); }
        else if (t.id === 'csArDate') { S.cons.arch.date = t.value; render(); }
        else if (t.id === 'csRegDate') { S.cons.regDate = t.value; render(); $('#csRegDate').focus(); }
        else if (t.id === 'csHFrom' || t.id === 'csHTo') { S.cons.hist[t.id === 'csHFrom' ? 'from' : 'to'] = t.value; render(); $(`#${t.id}`).focus(); }
        else if (t.id === 'bkPatient') {
            readBookForm(); const b = S.cons.bk; const p = D.patients.find(x => x.id === t.value);
            const g = p ? K.groupForDiagnosis(p.icd) : '';
            if (g !== b.group) { b.time = ''; b.date = b.date < TODAY ? TODAY : b.date; }   // слот другой группы не переносится
            b.patientId = t.value; b.f = fieldsFor(p); b.error = ''; b.group = g || b.group; render(); $('#bkPatient').focus();
        }
        else if (t.closest('[data-cs-form=decision]')) {
            const form = t.closest('form'); const d = S.cons.drafts[form.dataset.id];
            if (t.name === 'treatment') { d.treatment = t.value; if (t.value !== 'chemotherapy') { d.schemeName = ''; d.schemeCode = ''; } render(); $('#csTreat').focus(); }
            else if (t.name === 'goc' || t.name === 'goc65') d[t.name] = t.checked;
            else if (t.name === 'schemeName') { d.schemeName = t.value; const s = (D.SCHEMES || []).find(x => x.name.toLowerCase() === t.value.trim().toLowerCase() || x.code.toLowerCase() === t.value.trim().toLowerCase()); d.schemeCode = s ? s.code : ''; if (s) d.schemeName = s.name; render(); $('#csScheme').focus(); }
        }
        else if (t.closest('[data-cs-form=gen]')) { const g = S.cons.gen; g[t.name] = t.value; g.open = true; }
    });
    let qT;
    document.addEventListener('input', (e) => {
        const t = e.target; const map = { csQ: ['q', null], csArQ: ['q', 'arch'], csHQ: ['q', 'hist'] };
        if (map[t.id]) { clearTimeout(qT); qT = setTimeout(() => { const [k, sub] = map[t.id]; (sub ? S.cons[sub] : S.cons)[k] = t.value; const id = t.id, pos = t.selectionStart; render(); const n = $(`#${id}`); if (n) { n.focus(); n.setSelectionRange(pos, pos); } }, 180); }
        if (t.name === 'schemeName' && t.closest('[data-cs-form=decision]')) S.cons.drafts[t.closest('form').dataset.id].schemeName = t.value;
    });
    document.addEventListener('submit', (e) => {
        const form = e.target.closest('[data-cs-form]');
        if (!form) return;
        e.preventDefault();
        const kind = form.dataset.csForm;
        if (kind === 'book') submitBook();
        else if (kind === 'decision') signDialog(recById(form.dataset.id));
        else if (kind === 'gen') {
            const g = S.cons.gen; const fd = new FormData(form);
            Object.assign(g, { date: String(fd.get('date') || ''), start: String(fd.get('start') || ''), end: String(fd.get('end') || ''), interval: String(fd.get('interval') || '30'), open: true });
            try {
                const created = K.generateSlots(CS, { ...g, group: sGroup() }, actor(), ctx()); g.error = ''; S.cons.day = g.date; S.cons.week = K.weekDates(g.date)[0]; g.date = ''; render();
                toast(created.length ? `Добавлено слотов: ${created.length}${created.skipped ? `, уже были открыты: ${created.skipped}` : ''}${created.replacedAuto ? `; из них заменили автоматические: ${created.replacedAuto}` : ''}` : `Новых слотов нет: ${created.skipped} ${plural(created.skipped, 'слот уже был открыт', 'слота уже были открыты', 'слотов уже были открыты')}`);
            }
            catch (err) { g.error = err.message; render(); const n = $('#genDate'); if (n) n.focus(); }
        }
    }, true);

    /* ---------- Подключение к оболочке ---------- */
    // Выпадающие списки раздела используют общий компонент (store 'cons'); перевод ключей истории.
    const bindAccessors = () => {
        Object.defineProperty(S.cons, 'hstatus', { get() { return S.cons.hist.status; }, set(v) { S.cons.hist.status = v; }, configurable: true });
        Object.defineProperty(S.cons, 'hgroup', { get() { return S.cons.hist.group; }, set(v) { S.cons.hist.group = v; }, configurable: true });
    };
    bindAccessors();
    X.EXT.pages.consilium = page;
    X.EXT.hooks.reset = () => { S.cons = defaults(); bindAccessors(); };
    X.EXT.hooks.patientConsilium = (p) => {
        const rec = CS.consilium.find(c => c.patientId === p.id);
        if (rec) {
            const v = K.recordView(rec, viewer());
            if (v.kind !== 'full') return '';
            const href = isChair() ? `#/consilium/session?rec=${rec.id}` : `#/consilium/${rec.archived ? 'archive' : 'mine'}?rec=${rec.id}`;
            return `<p class="cs-card-link"><a class="btn btn--sm" href="${href}" data-cs-goto="${rec.id}" data-date="${rec.date}">Открыть запись на консилиум</a>${rec.status === 'needs_revision' && rec.createdBy === R().user.id ? ' <span class="status status--warn">возвращена на доработку</span>' : ''}</p>`;
        }
        if (perms().bookConsilium && canEdit(p)) return `<p class="cs-card-link"><a class="btn btn--sm" href="#/consilium/book?patient=${p.id}">Записать на консилиум</a> <span class="muted">группа «${esc(D.GROUPS[K.groupForDiagnosis(p.icd)])}» по коду МКБ-10</span></p>`;
        return '';
    };
    document.addEventListener('click', (e) => { const g = e.target.closest('[data-cs-goto]'); if (g) { S.cons.sel = g.dataset.csGoto; S.cons.date = g.dataset.date; S.cons.filter = 'all'; } }, true);
    X.EXT.hooks.bellItems = () => {
        if (!perms().bookConsilium) return [];
        return CS.consilium.filter(c => c.createdBy === R().user.id && c.status === 'needs_revision').map(c => ({ id: `cr-${c.id}-${c.revisions.length}`, p: { id: c.patientId, name: c.patientName }, title: 'Консилиум: запись возвращена на доработку', note: (c.revisions[c.revisions.length - 1] || {}).text || '', tone: 'warn', href: `#/consilium/mine?rec=${c.id}` }));
    };
    window.StaffUxConsiliumState = CS;   // для автотестов в браузере: только синтетические данные вкладки
    render();
})();
