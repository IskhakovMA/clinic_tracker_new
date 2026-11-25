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
            detail: 'Плановые даты без отмеченного факта, начиная с самых ранних.',
            empty: 'Плановых событий без отмеченного факта нет.',
        },
        checklists: {
            label: 'Обязательные пункты',
            detail: 'Состояние обязательных пунктов текущего этапа.',
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

    function deadlineText(row) {
        if (!Number.isInteger(row.deadlineDays)) return 'Плановый срок не указан';
        if (row.currentFact) return 'Факт последнего события отмечен';
        if (row.deadlineDays < 0) return `Просрочка — ${Math.abs(row.deadlineDays)} дн.`;
        if (!row.deadlineDays) return 'Плановый срок сегодня';
        return `До планового срока — ${row.deadlineDays} дн.`;
    }

    function dateText(row) {
        const date = typeof row.date === 'string' ? row.date.split('-').reverse().join('.') : 'Дата не указана';
        return row.time ? `${date} · ${row.time}` : date;
    }

    function patientAction(app, row, mode, live, refresh) {
        const id = mode === 'patients' ? row.id : row.patientId || row.patient_id;
        const name = row.name || row.patientName || row.patient_name;
        const label = mode === 'events' ? 'К событию' : mode === 'checklists' ? 'К чек-листу' : 'Открыть карточку';
        const tab = mode === 'events' ? 'events' : mode === 'checklists' ? 'checklist' : 'overview';
        return button(label, () => {
            if (!live()) return;
            void app.openPatient(id, tab, {
                allowed: live,
                denied: () => { if (live()) clearUnavailable(); },
                prepare: patient => {
                    if (mode !== 'events') return true;
                    const index = (patient.history || []).findIndex(occurrence =>
                        occurrence.id === row.historyEntryId &&
                        occurrence.events.some(event => event.id === row.id && !event.actualDate));
                    if (index < 0) {
                        clearUnavailable();
                        return false;
                    }
                    app.selectedTimelineNodes[id] = 'occ:' + index;
                    return true;
                },
                focus: mode === 'events' ? () => {
                    const target = document.getElementById('patient-event-' + row.id);
                    target?.scrollIntoView({ block: 'nearest' });
                    return target;
                } : undefined,
            });
        }, { 'aria-label': `${label} — ${name}` });

        function clearUnavailable() {
            refresh('Запись больше недоступна. Список обновлён.');
        }
    }

    function item(app, mode, row, live, refresh) {
        const name = row.name || row.patientName || row.patient_name;
        let notes;
        if (mode === 'patients') {
            notes = [
                deadlineText(row),
                row.treatingDoctorName ? `Лечащий врач — ${row.treatingDoctorName}` : 'Лечащий врач не указан',
                row.attention?.active && row.attention.primaryReasonLabel ? `Требует внимания — ${row.attention.primaryReasonLabel}` : null,
            ];
        } else if (mode === 'events') {
            notes = [dateText(row), row.head ? `Заведующий — ${row.head}` : null];
        } else {
            notes = [
                `Выполнено ${row.completed_required} из ${row.total_required}`,
                row.incomplete_required ? `Осталось обязательных пунктов — ${row.incomplete_required}` : 'Обязательные пункты выполнены',
                row.treating_doctor_name ? `Лечащий врач — ${row.treating_doctor_name}` : null,
            ];
        }
        return el('li', { class: 'f1-item', 'data-f1-doctor-row': mode }, [
            el('div', {}, [
                el('h3', { class: 'f1-item__title', text: name }),
                el('p', { class: 'f1-kicker', text: mode === 'events' ? 'Этап события — ' + stageText(app, row.stage) : row.stage_label || stageText(app, row.stage) }),
                ...notes.filter(Boolean).map(text => el('p', { class: 'f1-description', text })),
            ]),
            patientAction(app, row, mode, live, refresh),
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
                notice = 'Список изменился. Показана первая страница.';
                data = await apiGetJson(pathFor(mode, state.deadline, null));
            }
            if (!live()) return;

            const rows = data[mode];
            const displayPage = state.pages[mode] = { index, cursors };
            const status = el('p', {
                class: 'f1-description', role: 'status', tabindex: '-1',
                text: `${notice ? notice + ' ' : ''}В выборке — ${data.page.total}.`,
            });
            const nextPage = (nextIndex, nextCursors) => {
                if (live()) void load(app, state, anchor, body, nextIndex, nextCursors);
            };
            const refresh = message => {
                if (live()) void load(app, state, anchor, body, 0, [null], message);
            };
            body.replaceChildren(status, ...[
                rows.length
                    ? el('ul', { class: 'f1-list' }, rows.map(row => item(app, mode, row, live, refresh)))
                    : el('p', { class: 'f1-empty', text: modes[mode].empty }),
                pager(data.page, displayPage, nextPage),
            ].filter(Boolean));
            focus.moveTo(status);
        } catch (error) {
            if (!live() || error.obsolete) return;
            const message = retryable(error)
                ? 'Список снова изменился. Обновите его вручную.'
                : apiErrorMessage(error.status);
            const status = el('p', { class: 'alert alert--warning', role: 'alert', tabindex: '-1', text: message });
            body.replaceChildren(status, button('Обновить список', () => {
                if (live()) void load(app, state, anchor, body, 0, [null]);
            }));
            focus.moveTo(status);
        } finally {
            if (live()) body.removeAttribute('aria-busy');
            focus.stop();
        }
    }

    function render(app) {
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
        host.replaceChildren(
            el('div', { class: 'f1-actions', role: 'group', 'aria-label': 'Разделы моего дня' }, controls),
            heading, detail, select, body,
        );

        function show() {
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
            void load(app, state, heading, body, page.index, page.cursors);
        }
        show();
    }

    return { render, clear };
})();
