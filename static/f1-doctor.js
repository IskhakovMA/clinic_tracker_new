/* «Мой день» — только ограниченные серверные выборки и обычная навигация карточки. */
'use strict';

const f1Doctor = (() => {
    const modes = {
        patients: {
            label: 'Мои пациенты',
            detail: 'Текущий этап, срок по последнему событию и активная отметка внимания.',
            empty: 'В этой выборке нет пациентов.',
        },
        events: {
            label: 'Плановые события',
            detail: 'Подтвержденные плановые даты без отмеченного факта. Сначала самые ранние.',
            empty: 'Плановых событий без отмеченного факта нет.',
        },
        checklists: {
            label: 'Обязательные пункты',
            detail: 'Обязательные пункты текущего этапа и их выполнение.',
            empty: 'Пациентов с обязательными пунктами нет.',
        },
    };
    const filters = [
        ['', 'Все сроки'],
        ['overdue', 'Просроченный плановый срок'],
        ['0-3', 'На сегодня и ближайшие 3 дня'],
        ['no-date', 'Плановый срок не указан'],
    ];
    const freshPage = () => ({ index: 0, cursors: [null] });
    const clinicDay = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow', dateStyle: 'short' });

    function permitted(app) {
        return app.user?.roleId === 'doctor' && !app.sessionExpired &&
            !app.sessionReconciling && app.currentView === 'myDay';
    }

    function stateFor(app) {
        return app.f1Doctor ||= {
            mode: 'patients',
            deadline: '',
            pages: { patients: freshPage(), events: freshPage(), checklists: freshPage() },
        };
    }

    function clear(app, forget = false) {
        if (app.f1Doctor) delete app.f1Doctor.ticket;
        if (forget) delete app.f1Doctor;
        document.getElementById('f1MyDay')?.replaceChildren();
    }

    function pathFor(mode, deadline, cursor) {
        const params = new URLSearchParams({ limit: '20' });
        let path = '/api/patients';
        if (mode === 'patients') {
            params.set('view', 'page');
            params.set('sort', 'name');
            params.set('include_terminal', 'true');
            if (deadline) params.set('deadline', deadline);
        } else {
            path += '/workspace/' + mode;
        }
        if (cursor) params.set('cursor', cursor);
        return path + '?' + params;
    }

    function retryable(error) {
        return [409, 410].includes(error.status) &&
            ['PATIENT_QUERY_CHANGED', 'CURSOR_EXPIRED'].includes(error.data?.code);
    }

    function button(label, action, props = {}) {
        return el('button', { type: 'button', class: 'btn btn--ghost btn--sm', text: label, ...props, on: { click: action } });
    }

    function trackFocus(body, anchor) {
        let owns = body.contains(document.activeElement) && !ui.topModal();
        const release = () => { owns = false; };
        if (owns) {
            anchor.focus({ preventScroll: true });
            owns = document.activeElement === anchor;
            if (owns) anchor.addEventListener('focusout', release, { once: true });
        }
        return {
            moveTo: element => {
                if (owns && document.activeElement === anchor && !ui.topModal() && element?.isConnected) {
                    element.focus({ preventScroll: true });
                }
            },
            stop: () => anchor.removeEventListener('focusout', release),
        };
    }

    function pager(page, stored, load) {
        if (!stored.index && !page.hasNext) return null;
        const pageRange = page.total ? `${page.offset + 1}–${Math.min(page.offset + page.limit, page.total)} из ${page.total}` : 'Нет результатов';
        return el('div', { class: 'pagination', role: 'group', 'aria-label': 'Страницы списка' }, [
            button('Назад', () => load(stored.index - 1, stored.cursors), { disabled: !stored.index }),
            el('span', { text: pageRange }),
            button('Далее', () => load(stored.index + 1, [...stored.cursors.slice(0, stored.index + 1), page.nextCursor]), {
                disabled: !page.hasNext || !page.nextCursor,
            }),
        ]);
    }

    function stageText(app, stage) {
        return app.stageNames[stage] || 'Этап не указан';
    }

    function deadlineStatus(row) {
        if (row.currentFact) return ['neutral', 'Факт последнего события отмечен'];
        const days = row.deadlineDays;
        if (!Number.isInteger(days)) return ['neutral', 'Плановый срок не указан'];
        if (days < 0) return ['danger', `Просрочка — ${Math.abs(days)} дн.`];
        if (!days) return ['accent', 'Плановый срок сегодня'];
        return ['neutral', `До планового срока — ${days} дн.`];
    }

    function eventStatus(row, today) {
        if (!row.date) return ['neutral', 'Плановый срок не указан'];
        if (row.date < today) return ['danger', 'Просрочено'];
        if (row.date === today) return ['accent', 'Сегодня'];
        return ['neutral', 'Запланировано'];
    }

    function dateText(row) {
        const date = typeof row.date === 'string' ? row.date.split('-').reverse().join('.') : 'Дата не указана';
        return row.time ? `${date} · ${row.time}` : date;
    }

    function replayAction(app, patient, action) {
        const index = (patient.history || []).findIndex(occurrence =>
            occurrence.id === action.occurrenceId && occurrence.stage === action.stage);
        const event = patient.history?.[index]?.events?.find(item => item.id === action.eventId);
        if (event?.actualDate) return 'Факт события уже отмечен.';
        if (!event?.date) return 'Плановое событие больше недоступно.';
        if (event.date !== action.date || (event.time || '') !== action.time) return 'Плановый срок изменился.';
        app.selectedTimelineNodes[patient.id] = 'occ:' + index;
        return null;
    }

    function focusSection(selector) {
        const target = document.querySelector(selector);
        target?.scrollIntoView({ block: 'nearest' });
        return target;
    }

    function focusAction(action) {
        const target = document.getElementById('patient-event-' + action.eventId);
        if (!target) return focusSection('#patientPanels .section-title');
        target.scrollIntoView({ block: 'nearest' });
        return target;
    }

    function checklistChanged(app, patient, row) {
        const current = patient.history?.at(-1);
        const nosology = app.nosologyData[patient.nosology];
        if (!current || current.stage !== row.stage || !nosology) return true;
        const required = (current.stage === 'exam' ? nosology.checklistRequired : nosology.checklistRequiredStage2) || [];
        const incomplete = required.filter(item => current.checklist?.[item] !== true).length;
        return required.length !== row.total_required || incomplete !== row.incomplete_required;
    }

    function patientAction(app, row, mode, live, refresh, readFailed) {
        const id = mode === 'patients' ? row.id : row.patientId || row.patient_id;
        const name = row.name || row.patientName || row.patient_name;
        let label = 'Открыть карточку';
        if (mode === 'events') label = row.stage === 'primary-appointment' ? 'К разделу приёма' : 'К событию';
        if (mode === 'checklists') {
            label = row.incomplete_required && app.can('editPatients') ? 'К чек-листу' : 'Посмотреть чек-лист';
        }
        const tab = mode === 'events' ? 'events' : mode === 'checklists' ? 'checklist' : 'overview';
        const action = mode === 'events' ? {
            kind: 'day-event', eventId: row.id, occurrenceId: row.historyEntryId, stage: row.stage,
            date: row.date, time: row.time || '',
        } : null;
        return button(label, () => {
            if (!live()) return;
            void app.openPatient(id, tab, {
                allowed: live,
                denied: () => refresh('Запись больше недоступна.'),
                readFailed,
                prepare: patient => {
                    let reason = action ? replayAction(app, patient, action) : null;
                    if (mode === 'checklists' && checklistChanged(app, patient, row)) reason = 'Текущий чек-лист изменился.';
                    if (reason) refresh(reason);
                    return !reason;
                },
                focus: action ? () => focusAction(action) : mode === 'checklists'
                    ? () => focusSection('#patientPanels .checkline .section-title') : undefined,
                historyAction: action,
            });
        }, { class: 'btn btn--ghost btn--sm f1-item__action', 'data-f1-doctor-action': '', 'aria-label': `${label} — ${name}` });
    }

    function item(app, mode, row, today, live, refresh, readFailed) {
        const name = row.name || row.patientName || row.patient_name;
        let detail, secondary, status, owner;
        if (mode === 'patients') {
            detail = row.attention?.active && row.attention.primaryReasonLabel
                ? `Требует внимания — ${row.attention.primaryReasonLabel}` : null;
            status = deadlineStatus(row);
            owner = row.treatingDoctorName || 'Не указан';
        } else if (mode === 'events') {
            detail = dateText(row);
            secondary = row.head ? `Заведующий — ${row.head}` : null;
            status = eventStatus(row, today);
            owner = app.user.fullName;
        } else {
            detail = `Обязательные пункты: ${row.completed_required} из ${row.total_required}`;
            status = row.incomplete_required ? ['warning', `Осталось — ${row.incomplete_required}`] : ['good', 'Обязательные пункты выполнены'];
            owner = row.treating_doctor_name || 'Не указан';
        }
        return el('li', { class: 'f1-item f1-item--doctor', 'data-f1-doctor-row': mode }, [
            el('div', { class: 'f1-item__identity' }, [
                el('h3', { class: 'f1-item__title', text: name }),
                el('p', { class: 'f1-kicker', text: mode === 'events' ? 'Этап события — ' + stageText(app, row.stage) : row.stage_label || stageText(app, row.stage) }),
                owner ? el('p', { class: 'f1-description', text: `Лечащий врач — ${owner}` }) : null,
            ]),
            el('div', { class: 'f1-item__context' }, [
                detail ? el('p', { class: 'f1-item__detail', text: detail }) : null,
                secondary ? el('p', { class: 'f1-description', text: secondary }) : null,
                el('span', { class: `chip chip--${status[0]}`, text: status[1] }),
            ]),
            patientAction(app, row, mode, live, refresh, readFailed),
        ]);
    }

    async function load(app, state, anchor, body, index, cursors, notice = '') {
        if (!permitted(app)) return;
        const mode = state.mode;
        const ticket = {};
        state.ticket = ticket;
        const owner = app.user.id;
        const epoch = app.sessionEpoch;
        const signature = app.accessSignature(app.user);
        const live = () => state.ticket === ticket && body.isConnected && permitted(app) &&
            owner === app.user.id && epoch === app.sessionEpoch && signature === app.accessSignature(app.user);
        const focus = trackFocus(body, anchor);
        body.setAttribute('aria-busy', 'true');
        body.replaceChildren(el('p', { class: 'f1-empty', role: 'status', text: 'Загрузка…' }));

        try {
            let data;
            try {
                data = await apiGetJson(pathFor(mode, state.deadline, cursors[index]));
            } catch (error) {
                if (!live() || !retryable(error)) throw error;
                cursors = [null];
                index = 0;
                notice = [notice, 'Список изменился. Показана первая страница.'].filter(Boolean).join(' ');
                data = await apiGetJson(pathFor(mode, state.deadline, null));
            }
            if (!live()) return;

            const rows = data[mode];
            const today = clinicDay.format(new Date());
            const displayPage = state.pages[mode] = { index, cursors };
            const status = el('p', {
                class: 'f1-description', role: 'status', tabindex: '-1',
                text: `${notice ? notice + ' ' : ''}В выборке — ${data.page.total}.`,
            });
            const nextPage = (nextIndex, nextCursors) => {
                if (live()) void load(app, state, anchor, body, nextIndex, nextCursors);
            };
            const refresh = message => {
                if (live()) void load(app, state, anchor, body, index, cursors, message);
            };
            const readFailed = () => {
                if (!live()) return;
                delete state.ticket;
                showError('Не удалось проверить карточку. Обновите список перед переходом.');
            };
            body.replaceChildren(status, ...[
                rows.length
                    ? el('ul', { class: 'f1-list f1-list--doctor' }, rows.map(row => item(app, mode, row, today, live, refresh, readFailed)))
                    : el('p', { class: 'f1-empty', text: modes[mode].empty }),
                pager(data.page, displayPage, nextPage),
            ].filter(Boolean));
            focus.moveTo(status);
        } catch (error) {
            if (!live() || error.obsolete) return;
            const message = retryable(error)
                ? 'Список снова изменился. Обновите его вручную.'
                : apiErrorMessage(error.status);
            showError(message, focus);
        } finally {
            if (live()) body.removeAttribute('aria-busy');
            focus.stop();
        }

        function showError(message, loadingFocus) {
            const returnFocus = !loadingFocus && body.contains(document.activeElement) && !ui.topModal();
            const status = el('p', { class: 'alert alert--warning', role: 'alert', tabindex: '-1', text: message });
            const retry = button('Обновить список', () => {
                if (retry.isConnected && permitted(app)) void load(app, state, anchor, body, 0, [null]);
            });
            body.replaceChildren(status, retry);
            if (returnFocus) status.focus({ preventScroll: true });
            loadingFocus?.moveTo(status);
        }
    }

    function render(app, notice = '') {
        const host = document.getElementById('f1MyDay');
        if (!host || !permitted(app)) return;
        const state = stateFor(app);
        const heading = el('h2', { class: 'section-title', tabindex: '-1' });
        const detail = el('p', { class: 'f1-description' });
        const body = el('div', {});
        const select = el('select', {
            class: 'input', 'aria-label': 'Фильтр по плановому сроку',
            on: { change: event => {
                if (!permitted(app)) return;
                state.deadline = event.currentTarget.value;
                state.pages.patients = freshPage();
                show();
            } },
        }, filters.map(([value, label]) => option(value, label, value === state.deadline)));
        const controls = Object.entries(modes).map(([key, mode]) => button(mode.label, () => {
            if (!permitted(app) || key === state.mode) return;
            state.mode = key;
            show();
        }, { 'data-f1-doctor-mode': key }));
        const registry = button('Мои пациенты в реестре', () => {
            const {mode, deadline} = state;
            const current = () => registry.isConnected && permitted(app) && app.f1Doctor === state &&
                state.mode === mode && state.deadline === deadline;
            if (!current()) return;
            void app.openWorkingRegistry('myDay', {
                deadline: mode === 'patients' ? deadline : '',
                sort: 'name', include_terminal: 'true',
            }, current);
        }, { id: 'f3MyDayRegistry' });
        host.replaceChildren(
            el('div', { class: 'f1-actions', role: 'group', 'aria-label': 'Разделы моего дня' }, controls),
            heading, detail, el('div', {class: 'f1-actions'}, [select, registry]), body,
        );

        function show(message = '') {
            for (const control of controls) {
                const selected = control.dataset.f1DoctorMode === state.mode;
                control.setAttribute('aria-pressed', String(selected));
                control.classList.toggle('btn--primary', selected);
                control.classList.toggle('btn--ghost', !selected);
            }
            heading.textContent = modes[state.mode].label;
            detail.textContent = modes[state.mode].detail;
            select.hidden = state.mode !== 'patients';
            const page = state.pages[state.mode];
            void load(app, state, heading, body, page.index, page.cursors, message);
        }
        show(notice);
    }

    return { render, clear, replayAction, focusAction };
})();
