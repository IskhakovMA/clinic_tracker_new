/* Создание пациента (#/patients/new). Логика — newpatient-core.js; синтетические данные, сервер не вызывается.
   Форма повторяет состав и проверки формы приложения (addPatient / PatientCreateIn). */
(function () {
    'use strict';
    const X = window.StaffUx, N = window.StaffUxNewPatient;
    if (!X || !N) return;
    const { D, S, R, TODAY, esc, $, $$, dialog, toast, render, scope, STAGE, fmtShort, plural, now, ICON } = X;
    const T = window.StaffUxText, ui = T.ui;
    const ICD = window.ICD10 || {};                                   // справочник МКБ-10 приложения (17 групп, 726 кодов)
    const NOS = window.NOSOLOGY_REF || {};                            // клиентские пути приложения (21)
    const LOCS = Object.entries(ICD).map(([k, v]) => [k, ui(v.label)]);
    const ROLE_KEY = () => S.role;
    const PRIORITY_LABEL = D.PRIORITY;
    const PGI_LABEL = { '': 'Не указан', confirmed: 'Подтвержден', not_confirmed: 'Не подтвержден' };
    const FORM_HASH = '#/patients/new';

    // Клиентский путь по диагнозу — функция приложения suggestedNosologyForDiagnosis (client-path.js), без изменений.
    const nosologyFor = (code) => { const k = window.StaffUxClientPath.suggestedNosologyForDiagnosis(code); return NOS[k] ? k : ''; };
    // Контекст сеанса: роль, пользователь и поколение (увеличивается при смене роли и выходе).
    let GEN = 0;
    const sessionNow = () => ({ role: S.role, userId: R().user.id, gen: GEN, loggedOut: !!S.loggedOut });
    const ctxFor = () => {
        const u = R().user;
        const headDepts = S.role === 'head' ? [u.dept] : [];
        return { role: ROLE_KEY(), userId: u.id, userName: u.short, today: TODAY, now: now(), doctors: D.DOCTORS, headDepts,
            isKnownDiagnosis: (loc, code) => !!(ICD[loc] && ICD[loc].diagnoses.some(d => d.code === code)),
            nosologyFor, nosologyLabel: (k) => (NOS[k] || {}).label || '', localizationLabel: (k) => (ICD[k] || {}).label || '',
            newId: () => `p-new-${Date.now().toString(36)}`, offline: S.demo === 'error' };
    };
    const blank = () => ({ name: '', age: '', phone: '', policy: '', localization: '', diagnosis: '', stage: '', factDate: '', factTime: '', planDate: '', planTime: '', treatmentType: '',
        priority: 'normal', doctorId: S.role === 'doctor' || S.role === 'chair' ? R().user.id : '', head: S.role === 'doctor' || S.role === 'chair' || S.role === 'head' ? (D.HEADS[R().user.dept] || '') : '', pgiResult: '', pgiResultDate: '', complaint: '' });
    const fresh = () => ({ gen: GEN, f: blank(), errors: {}, touched: {}, summary: false, dupAck: false, inFlight: false, requestKey: `rk-${Date.now()}`, dirty: false, q: '', diagOpen: false, diagIdx: -1, netError: '', allowLeave: false });
    // Состояние создается лениво: хук reset вызывается до смены роли, а предзаполнение врача зависит от новой роли.
    S.np = null;
    const st = () => S.np || (S.np = fresh());

    /* ---------- Разметка ---------- */
    const req = '<span class="req" aria-hidden="true">*</span><span class="sr-only"> (обязательно)</span>';
    const err = (k) => st().errors[k] && (st().touched[k] || st().summary) ? `<span class="field__error" id="np-${k}-err">${esc(st().errors[k])}</span>` : '';
    const shown = (k) => !!(st().errors[k] && (st().touched[k] || st().summary));
    const inv = (k) => shown(k) ? 'aria-invalid="true"' : '';
    const described = (k, hint) => [hint ? `np-${k}-hint` : '', shown(k) ? `np-${k}-err` : ''].filter(Boolean).join(' ');
    // Подсказка и ошибка связаны с полем через aria-describedby; ошибка — сразу под полем.
    const field = (k, label, control, opts = {}) => {
        const d = described(k, opts.hint);
        const ctl = d ? control.replace(`id="np-${k}"`, `id="np-${k}" aria-describedby="${d}"`) : control;
        return `<div class="field${opts.wide ? ' field--wide' : ''}" data-np-field="${k}"><label class="field__label" for="np-${k}">${label}${opts.required ? ' ' + req : ''}</label>${ctl}${opts.hint ? `<span class="field__hint" id="np-${k}-hint">${opts.hint}</span>` : ''}${err(k)}</div>`;
    };
    const input = (k, attrs = '') => `<input class="input" id="np-${k}" name="${k}" value="${esc(st().f[k])}" ${inv(k)} ${attrs}>`;
    const select = (k, options, attrs = '') => `<select class="select" id="np-${k}" name="${k}" ${inv(k)} ${attrs}>${options.map(([v, t]) => `<option value="${esc(v)}" ${String(st().f[k]) === String(v) ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;

    function doctorControl() {
        const u = R().user;
        if (S.role === 'doctor' || S.role === 'chair') return field('doctorId', 'Лечащий врач', `<input class="input" id="np-doctorId" value="${esc(u.name)}" readonly>`, { hint: S.role === 'chair' ? 'Пациент закрепляется за вами для персонального отслеживания.' : 'Врач создает пациента только с закреплением за собой.' });
        if (S.role === 'head') { const docs = D.DOCTORS.filter(d => d.dept === u.dept); return field('doctorId', 'Лечащий врач', select('doctorId', [['', 'Выберите врача отделения'], ...docs.map(d => [d.id, d.name])]), { required: true }); }
        return field('doctorId', 'Лечащий врач', select('doctorId', [['', '— Не сопоставлен (назначить позже) —'], ...D.DOCTORS.map(d => [d.id, d.name])]), { hint: 'Без врача пациент попадет в очередь несопоставленных.' });
    }
    function diagnosisControl() {
        const s = st(); const loc = s.f.localization;
        const sel = s.f.diagnosis ? (ICD[loc] || { diagnoses: [] }).diagnoses.find(d => d.code === s.f.diagnosis) : null;
        const value = sel ? `${sel.code} — ${ui(sel.label)}` : s.q;
        return `<div class="np-combo"><input class="input" id="np-diagnosis" name="diagnosisSearch" role="combobox" aria-autocomplete="list" aria-expanded="${s.diagOpen}" aria-controls="np-diag-list" ${s.diagOpen && s.diagIdx >= 0 && diagOptions()[s.diagIdx] ? `aria-activedescendant="np-diag-o${s.diagIdx}"` : ''} autocomplete="off"
            placeholder="${loc ? 'Начните вводить код или название' : 'Сначала выберите локализацию'}" ${loc ? '' : 'disabled'} value="${esc(value)}" ${inv('diagnosis')}>
            <ul class="dd__list np-combo__list" id="np-diag-list" role="listbox" aria-label="Диагнозы МКБ-10" ${s.diagOpen ? '' : 'hidden'}>${diagOptions().map((d, i) => `<li class="dd__opt${i === s.diagIdx ? ' is-active' : ''}" role="option" id="np-diag-o${i}" data-code="${esc(d.code)}" aria-selected="${d.code === s.f.diagnosis}"><span class="mono">${esc(d.code)}</span><span class="np-combo__label">${esc(ui(d.label))}</span></li>`).join('') || '<li class="dd__opt" aria-disabled="true">Ничего не найдено</li>'}</ul></div>`;
    }
    const diagOptions = () => {
        const s = st(); return N.searchDiagnoses((ICD[s.f.localization] || { diagnoses: [] }).diagnoses, s.q);
    };
    function eventPreview() {
        const ev = N.plannedEvents(st().f);
        const stg = st().f.stage;
        if (!stg) return '';
        if (!ev.length) return `<p class="np-preview">${N.PLAN_STAGES.includes(stg) ? 'Событие не будет создано, пока не указана плановая дата (ее можно назначить позже в карточке).' : 'Для этого этапа событие не создается.'}</p>`;
        const e = ev[0];
        return `<p class="np-preview"><b>Будет создано событие:</b> ${e.noPlan ? `факт ${esc(fmtShort(e.actualDate))}${e.actualTime ? ', ' + esc(e.actualTime) : ''} (без плана)` : `план ${e.date ? esc(fmtShort(e.date)) : 'без даты'}${e.time ? ', ' + esc(e.time) : ''}`} — этап «${esc(STAGE(stg))}». Других событий и смены этапа не будет.</p>`;
    }
    function stageFields() {
        const s = st().f.stage;
        if (s === 'primary-appointment') return `<div class="edit-grid">${field('factDate', 'Фактическая дата приема', input('factDate', `type="date" max="${TODAY}"`), { required: true })}${field('factTime', 'Время', input('factTime', 'type="time"'))}</div>`;
        if (N.PLAN_STAGES.includes(s)) return `<div class="edit-grid">${field('planDate', 'Плановая дата', input('planDate', 'type="date"'))}${field('planTime', 'Время', input('planTime', 'type="time"'), { hint: 'Можно не указывать.' })}${N.TREATMENT_STAGES.includes(s) ? field('treatmentType', 'Вид лечения', select('treatmentType', [['', 'Не указан'], ...Object.entries(D.EVENT_TREATMENT)])) : ''}</div>`;
        return '';
    }
    function dupBanner() {
        const s = st(); if (!s.f.name.trim() && !s.f.phone.trim()) return ''; const nos = nosologyFor(s.f.diagnosis);
        const d = N.findDuplicates(s.f, scope(), nos);
        if (!d.length) return '';
        return `<div class="banner banner--warn np-dups" role="status"><span class="banner__body"><b>Возможный дубль.</b> Похожие пациенты уже есть в системе — проверьте, прежде чем создавать новую карточку.
            <ul class="plain-list">${d.slice(0, 5).map(x => `<li>${esc(x.patient.name)}, ${x.patient.age} ${plural(x.patient.age, 'год', 'года', 'лет')} · <span class="mono">${esc(x.patient.icd)}</span> — ${esc(x.reasons.join(', '))} · <a href="#/patient/${x.patient.id}">открыть карточку</a></li>`).join('')}</ul>
            <span class="muted">Признаки те же, что в центре качества данных. В приложении при создании такой проверки нет — это предупреждение, а не запрет.</span></span></div>`;
    }
    function summary() {
        const s = st(); if (!s.summary) return '';
        const v = N.validate(s.f, ctxFor());
        if (!v.order.length) return '';
        const labels = { name: 'ФИО', age: 'Возраст', phone: 'Телефон', policy: 'Полис ОМС', diagnosis: 'Диагноз МКБ-10', stage: 'Текущий этап', factDate: 'Фактическая дата', factTime: 'Время', planDate: 'Плановая дата', planTime: 'Время', treatmentType: 'Вид лечения', priority: 'Приоритет', doctorId: 'Лечащий врач', head: 'Заведующий', pgiResult: 'Результат ПГИ', pgiResultDate: 'Дата результата ПГИ', complaint: 'Комментарий' };
        return `<div class="banner banner--danger np-summary" role="alert" tabindex="-1" id="npSummary"><span class="banner__body"><b>Пациент не создан: исправьте ${v.order.length} ${plural(v.order.length, 'поле', 'поля', 'полей')}.</b>
            <ul class="plain-list">${v.order.map(k => `<li><a href="#np-${k === 'diagnosis' && !s.f.localization ? 'localization' : k}" data-np-jump="${k === 'diagnosis' && !s.f.localization ? 'localization' : k}">${labels[k]}: ${esc(v.errors[k])}</a></li>`).join('')}</ul></span></div>`;
    }
    function page() {
        if (!N.CAN_CREATE[S.role]) return `<nav class="crumbs" aria-label="Навигация"><a href="#/patients">${ICON.back}Пациенты</a></nav><div class="table-wrap"><div class="state"><h2>Создание пациента недоступно</h2><p>Ваша роль работает в режиме просмотра: создавать и изменять карточки могут врач, заведующий, председатель консилиума и разработчик.</p><a class="btn" href="#/patients">К списку пациентов</a></div></div>`;
        const s = st(); const f = s.f;
        const v = N.validate(f, ctxFor()); s.errors = v.errors;
        setTimeout(() => { if (onForm()) document.title = 'Новый пациент — Система планирования (прототип)'; }, 0);
        const nos = nosologyFor(f.diagnosis);
        const heads = [...new Set(Object.values(D.HEADS))];
        return `<nav class="crumbs" aria-label="Навигация"><a href="#/patients">${ICON.back}Пациенты</a><span aria-hidden="true">/</span><span>Новый пациент</span></nav>
            <div class="page-head"><div><h1>Новый пациент</h1><p class="page-head__sub">Поля со звездочкой ${req} обязательны. Карточка появится в списке после сохранения.</p></div></div>
            <div id="npSummaryWrap">${summary()}</div>
            ${DISCARDED.at && !s.dirty ? `<div class="banner banner--info" role="status"><span class="banner__body">${DISCARDED.cancelled ? '<b>Создание пациента отменено:</b> во время сохранения сменилась роль или выполнен выход. Пациент не создан. ' : ''}Черновик, начатый в другой роли или до выхода, сброшен — данные в новую роль не переносятся.</span></div>` : ''}
            ${s.netError ? `<div class="banner banner--danger" role="alert" id="npNet" tabindex="-1"><span class="banner__body">${esc(s.netError)} <span class="muted">В демо переключите «Состояние экрана» на «Данные».</span></span></div>` : ''}
            <form class="np-form" data-np-form novalidate>
                <section class="section" aria-labelledby="np-s1"><div class="section__head"><h2 id="np-s1">Пациент</h2></div>
                    <div class="edit-grid">${field('name', 'ФИО', input('name', `maxlength="${N.LIMITS.name}" autocomplete="off"`), { required: true, wide: true })}
                    ${field('age', 'Возраст', input('age', 'type="number" min="0" max="150" inputmode="numeric"'), { hint: 'Полных лет' })}
                    ${field('phone', 'Телефон', input('phone', `type="tel" maxlength="${N.LIMITS.phone}" placeholder="+7 900 000-00-00"`))}
                    ${field('policy', 'Полис ОМС', input('policy', `inputmode="numeric" maxlength="${N.LIMITS.policyInput}"`), { hint: 'Пробелы и дефисы уберутся автоматически' })}</div>
                    <div id="npDups">${dupBanner()}</div>
                </section>
                <section class="section" aria-labelledby="np-s2"><div class="section__head"><h2 id="np-s2">Диагноз</h2></div>
                    <div class="edit-grid">${field('localization', 'Локализация', select('localization', [['', 'Выберите локализацию'], ...LOCS]), { required: true })}
                    ${field('diagnosis', 'Диагноз (код МКБ-10)', diagnosisControl(), { required: true, hint: 'Выберите диагноз из списка — введенный вручную текст не сохраняется.' })}</div>
                    ${f.diagnosis ? `<p class="np-preview">Клиентский путь: <b>${nos ? esc(ui(NOS[nos].label)) : 'не определен'}</b> <span class="muted">— определяется по диагнозу</span></p>` : ''}
                </section>
                <section class="section" aria-labelledby="np-s3"><div class="section__head"><h2 id="np-s3">Маршрут</h2></div>
                    <div class="edit-grid">${field('stage', 'Текущий этап', select('stage', [['', 'Выберите этап'], ...N.STAGES.map(x => [x, STAGE(x)])]), { required: true, hint: 'Этап выбираете вы — система его не меняет.' })}</div>
                    ${stageFields()}<div id="npPreview">${eventPreview()}</div>
                </section>
                <section class="section" aria-labelledby="np-s4"><div class="section__head"><h2 id="np-s4">Ответственные и приоритет</h2></div>
                    <div class="edit-grid">${field('priority', 'Приоритет', select('priority', N.PRIORITIES.map(k => [k, PRIORITY_LABEL[k]])), { required: true, hint: 'Срочный и высокий приоритет сокращают регламентные сроки' })}
                    ${doctorControl()}
                    ${field('head', 'Заведующий', select('head', [['', 'Выберите заведующего'], ...heads.map(h => [h, h])]), { required: true })}</div>
                </section>
                <section class="section" aria-labelledby="np-s5"><div class="section__head"><h2 id="np-s5">Дополнительно</h2><span class="section__meta">необязательно</span></div>
                    <div class="edit-grid">${field('pgiResult', 'Результат ПГИ', select('pgiResult', Object.entries(PGI_LABEL)))}
                    ${f.pgiResult ? field('pgiResultDate', 'Дата результата ПГИ', input('pgiResultDate', `type="date" max="${TODAY}"`), { required: true }) : ''}
                    ${field('complaint', 'Комментарий', `<textarea class="textarea" id="np-complaint" name="complaint" rows="3" maxlength="${N.LIMITS.complaint}" ${inv('complaint')}>${esc(f.complaint)}</textarea>`, { wide: true, hint: `<span id="np-count">${f.complaint.length}</span> из ${N.LIMITS.complaint} знаков` })}</div>
                </section>
                <div class="np-actions"><button class="btn btn--primary" type="submit" ${s.inFlight ? 'disabled aria-busy="true"' : ''}>${s.inFlight ? 'Создание…' : 'Создать пациента'}</button><a class="btn" href="#/patients" data-np-cancel>Отмена</a>
                    <span class="muted np-actions__note" id="npNote" aria-live="polite">${s.inFlight ? 'Сохраняем карточку — повторное нажатие не создаст второго пациента.' : s.dirty ? 'Есть несохраненные изменения.' : ''}</span></div>
            </form>`;
    }

    /* ---------- Поведение ---------- */
    const onForm = () => location.hash.split('?')[0] === FORM_HASH;
    const keepFocus = (fn) => { const id = document.activeElement && document.activeElement.id; const pos = document.activeElement && document.activeElement.selectionStart; fn(); if (id) { const el = document.getElementById(id); if (el) { el.focus(); try { if (pos != null && el.setSelectionRange) el.setSelectionRange(pos, pos); } catch (e) { /* поле без выделения */ } } } };
    function setField(name, value) { const s = st(); if (s.f[name] === value) return; s.f[name] = value; s.dirty = true; DISCARDED.at = ''; DISCARDED.cancelled = false; s.dupAck = false; const n = $('#npNote'); if (n && !s.inFlight) n.textContent = 'Есть несохраненные изменения.'; }
    /* Точечное обновление без перерисовки страницы: ошибки у полей, предупреждение о дублях, сводка.
       Фокус и введенные значения не трогаются (полная перерисовка при уходе с поля сбила бы фокус на кнопках). */
    function sync() {
        const s = st(); s.errors = N.validate(s.f, ctxFor()).errors;
        $$('[data-np-field]').forEach(box => {
            const k = box.dataset.npField; const ctl = document.getElementById(`np-${k}`);
            let span = box.querySelector('.field__error');
            if (shown(k)) {
                if (!span) { span = document.createElement('span'); span.className = 'field__error'; span.id = `np-${k}-err`; box.appendChild(span); }
                span.textContent = s.errors[k];
                if (ctl) ctl.setAttribute('aria-invalid', 'true');
            } else { if (span) span.remove(); if (ctl) ctl.removeAttribute('aria-invalid'); }
            if (ctl) { const d = described(k, box.querySelector(`#np-${k}-hint`)); if (d) ctl.setAttribute('aria-describedby', d); else ctl.removeAttribute('aria-describedby'); }
        });
        const dups = $('#npDups'); if (dups) dups.innerHTML = dupBanner();
        const sm = $('#npSummaryWrap'); if (sm) sm.innerHTML = summary();
    }
    document.addEventListener('input', (e) => {
        const t = e.target; if (!t.closest || !t.closest('[data-np-form]')) return;
        if (t.id === 'np-diagnosis') { const s = st(); s.q = t.value; if (s.f.diagnosis) { s.f.diagnosis = ''; } s.diagOpen = true; s.diagIdx = 0; s.dirty = true; keepFocus(render); return; }
        if (t.name) setField(t.name, t.value);
        const pv = $('#npPreview'); if (pv) pv.innerHTML = eventPreview();
        if (st().touched[t.name] && st().errors[t.name]) sync();          // ошибка снимается, как только значение исправлено
        if (t.id === 'np-complaint') { const c = $('#np-count'); if (c) c.textContent = t.value.length; }
    });
    document.addEventListener('change', (e) => {
        const t = e.target; if (!t.closest || !t.closest('[data-np-form]') || !t.name || t.id === 'np-diagnosis') return;
        setField(t.name, t.value);
        // Текстовые поля и даты обновляются точечно (input/focusout); перерисовка при change сбила бы Tab-переход.
        if (t.tagName !== 'SELECT') return;
        const s = st();
        if (t.name === 'localization') { s.f.diagnosis = ''; s.q = ''; }
        if (t.name === 'stage') { Object.assign(s.f, { factDate: '', factTime: '', planDate: '', planTime: '', treatmentType: '' }); }
        if (t.name === 'doctorId' && S.role !== 'doctor') { const d = D.DOCTORS.find(x => x.id === t.value); if (d && D.HEADS[d.dept] && !s.f.head) s.f.head = D.HEADS[d.dept]; }
        if (t.name === 'pgiResult' && !t.value) s.f.pgiResultDate = '';
        s.touched[t.name] = true;
        keepFocus(render);
    });
    // Проверка поля при уходе с него (ui-ux-pro-max: validate on blur), без перехвата фокуса.
    document.addEventListener('focusout', (e) => {
        const t = e.target; if (!t.closest || !t.closest('[data-np-form]') || !t.name) return;
        const k = t.id === 'np-diagnosis' ? 'diagnosis' : t.name;
        if (t.id === 'np-diagnosis') {
            setTimeout(() => { if (document.activeElement && document.activeElement.closest('.np-combo')) return; const s = st(); s.diagOpen = false; s.touched.diagnosis = true;
                const l = $('#np-diag-list'); if (l) l.hidden = true; const i = $('#np-diagnosis'); if (i) { i.setAttribute('aria-expanded', 'false'); i.removeAttribute('aria-activedescendant'); } sync(); }, 120);
            return;
        }
        // Пустое необязательное поле при простом проходе Tab не помечается ошибкой.
        if (!st().touched[k] && String(st().f[k] || '') === '' && !st().summary) return;
        st().touched[k] = true; sync();
    });
    function pickDiagnosis(code) { const s = st(); s.f.diagnosis = code; s.q = ''; s.diagOpen = false; s.touched.diagnosis = true; s.dirty = true; render(); const n = $('#np-diagnosis'); if (n) n.focus(); }
    document.addEventListener('keydown', (e) => {
        if (e.target.id !== 'np-diagnosis') return;
        const s = st(); const opts = diagOptions();
        if (e.key === 'ArrowDown') { e.preventDefault(); s.diagOpen = true; s.diagIdx = Math.min(opts.length - 1, s.diagIdx + 1); keepFocus(render); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); s.diagIdx = Math.max(0, s.diagIdx - 1); keepFocus(render); }
        else if (e.key === 'Enter') { e.preventDefault(); if (s.diagOpen && opts[s.diagIdx]) pickDiagnosis(opts[s.diagIdx].code); }
        else if (e.key === 'Escape' && s.diagOpen) { e.preventDefault(); e.stopImmediatePropagation(); s.diagOpen = false; keepFocus(render); }
    }, true);
    document.addEventListener('mousedown', (e) => { const o = e.target.closest && e.target.closest('.np-combo__list [data-code]'); if (o) { e.preventDefault(); pickDiagnosis(o.dataset.code); } });
    document.addEventListener('click', (e) => {
        const j = e.target.closest && e.target.closest('[data-np-jump]');
        if (j) { e.preventDefault(); const el = document.getElementById(`np-${j.dataset.npJump}`); if (el) { el.focus(); el.scrollIntoView({ block: 'center' }); } }
    });

    async function submit() {
        const s = st();
        if (s.inFlight) return;                                   // повторное нажатие во время сохранения ничего не делает
        s.summary = true; s.netError = '';
        const ctx = ctxFor();
        const v = N.validate(s.f, ctx);
        if (v.order.length) { render(); const sm = $('#npSummary'); if (sm) sm.focus(); return; }
        const dups = N.findDuplicates(s.f, scope(), nosologyFor(s.f.diagnosis));
        if (dups.length && !s.dupAck) {
            dialog({ title: 'Возможный дубль пациента', icon: ICON.warn, body: `<p>Похожие пациенты уже есть в системе:</p><ul class="plain-list">${dups.slice(0, 5).map(x => `<li>${esc(x.patient.name)} — ${esc(x.reasons.join(', '))}</li>`).join('')}</ul><p>Если это другой человек, создайте карточку. Если тот же — откройте существующую карточку, чтобы не задвоить маршрут.</p>`,
                actions: [{ label: 'Вернуться к форме', autofocus: true }, { label: 'Все равно создать', kind: 'primary', run: () => { if (S.np !== s) return; s.dupAck = true; setTimeout(submit, 0); } }] });
            return;
        }
        // Контекст фиксируется здесь: данные формы, роль, пользователь и поколение сеанса на момент нажатия.
        ctx.session = sessionNow();
        const payload = { ...s.f, requestKey: s.requestKey };
        s.inFlight = true; render();
        await new Promise(r => setTimeout(r, 650));                // имитация запроса
        // Ответ пришел после смены роли или выхода — форма уже сброшена; сохранять нельзя.
        const stale = S.np !== s || s.gen !== GEN;
        try {
            const res = N.create(CREATED, payload, { ...ctx, now: now(), offline: S.demo === 'error' }, stale ? null : sessionNow());
            if (!res.repeated) D.patients.unshift(res.patient), D.helpers.derive(res.patient);
            S.np = fresh(); S.np.allowLeave = true;
            location.hash = `#/patient/${res.patient.id}`;
            toast(`Пациент «${res.patient.name}» создан. Открыта карточка.`);
        } catch (err) {
            s.inFlight = false;
            if (err.code === 'context_changed') { CANCELLED.push({ at: now(), by: ctx.session }); DISCARDED.cancelled = true; toast(err.message); if (S.np === s) render(); return; }
            if (S.np !== s) return;
            if (err.code === 'network') { s.netError = err.message; render(); const n = $('#npNet'); if (n) n.focus(); }
            else if (err.code === 'validation') { render(); const sm = $('#npSummary'); if (sm) sm.focus(); }
            else { toast(err.message); render(); }
        }
    }
    const CANCELLED = [];
    const CREATED = { created: [], done: {}, inFlight: false, audit: [] };
    document.addEventListener('submit', (e) => { if (e.target.matches && e.target.matches('[data-np-form]')) { e.preventDefault(); e.stopImmediatePropagation(); submit(); } }, true);

    /* Несохраненные изменения: ссылки, «Отмена», кнопка «Назад» и перезагрузка. */
    function confirmLeave(go) {
        dialog({ title: 'Уйти без сохранения?', body: '<p>Введенные данные нового пациента не сохранены и будут потеряны.</p>',
            actions: [{ label: 'Остаться', autofocus: true }, { label: 'Уйти без сохранения', kind: 'danger', run: () => { S.np = fresh(); S.np.allowLeave = true; go(); } }] });
    }
    document.addEventListener('click', (e) => {
        const a = e.target.closest && e.target.closest('a[href^="#/"]');
        if (!a || !onForm() || !st().dirty || st().inFlight) return;
        if (a.closest('.np-combo')) return;
        e.preventDefault(); e.stopImmediatePropagation();
        confirmLeave(() => { location.hash = a.getAttribute('href'); });
    }, true);
    let lastHash = location.hash;
    window.addEventListener('hashchange', () => {
        const prev = lastHash; lastHash = location.hash;
        if (prev.split('?')[0] === FORM_HASH && !onForm() && st().dirty && !st().allowLeave) {
            const target = location.hash; lastHash = FORM_HASH;
            history.replaceState(null, '', FORM_HASH); render();
            confirmLeave(() => { lastHash = target; location.hash = target; });
        } else if (onForm()) st().allowLeave = false;
    });
    window.addEventListener('beforeunload', (e) => { if (onForm() && st().dirty) { e.preventDefault(); e.returnValue = ''; } });

    /* Подключение: страница «Пациенты» с подмаршрутом new. */
    X.EXT.pages.patients = (r) => r.id === 'new' ? page() : X.pagePatients();
    const prevReset = X.EXT.hooks.reset;
    // Смена роли и выход: новое поколение сеанса (ожидающее создание будет отменено), черновик не переносится.
    X.EXT.hooks.reset = () => { if (prevReset) prevReset(); if (S.np && (S.np.dirty || S.np.inFlight)) DISCARDED.at = now(); GEN += 1; S.np = null; };
    const DISCARDED = { at: '', cancelled: false };
    window.StaffUxNewPatientState = CREATED;
    window.StaffUxNewPatientDebug = { CANCELLED, DISCARDED, gen: () => GEN };   // для проверок в браузере   // для автопроверок в браузере (синтетические данные вкладки)
    render();
})();
