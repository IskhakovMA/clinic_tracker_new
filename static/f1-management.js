'use strict';

const f1Management = (() => {
    const sessions = new WeakMap();
    const pageSize = 20;
    const number = new Intl.NumberFormat('ru-RU');
    const plurals = new Intl.PluralRules('ru-RU');
    const units = {
        'пациенты': ['пациент', 'пациента', 'пациентов'],
        'события': ['событие', 'события', 'событий'],
        'замечания': ['замечание', 'замечания', 'замечаний'],
        'обязательные пункты': ['обязательный пункт', 'обязательных пункта', 'обязательных пунктов'],
        'уведомления': ['уведомление', 'уведомления', 'уведомлений'],
    };
    const metrics = new Map([
        ['total_patients', {type: 'patients', unit: 'пациенты', tab: 'overview'}],
        ['overdue_events', {type: 'overdue_events', unit: 'события', tab: 'events'}],
        ['data_quality_issues', {type: 'quality_actionable', unit: 'замечания', tab: 'overview'}],
        ['incomplete_required_checklists', {
            type: 'incomplete_checklists', unit: 'обязательные пункты', detailUnit: 'пациенты', tab: 'checklist',
            label: 'Невыполненные обязательные пункты', heading: 'Пациенты с невыполненными обязательными пунктами',
        }],
        ['pending_notifications', {type: 'notifications_pending', unit: 'уведомления', tab: 'notifications'}],
        ['patients_need_attention', {type: 'attention_patients', unit: 'пациенты', tab: 'overview'}],
    ]);
    // Analytics exposes the rule code, but not the patient detail's suggested action or its authority.
    const qualityTabs = new Map([
        ['missing_required_field', 'data'], ['missing_diagnosis', 'data'], ['invalid_phone', 'data'],
        ['missing_policy_number', 'data'], ['missing_treating_doctor', 'data'],
        ['missing_stage', 'events'], ['stage_sequence_violation', 'events'],
        ['actual_date_in_future', 'events'], ['overdue_event', 'events'],
        ['incomplete_required_checklist', 'checklist'],
    ]);
    const actions = {
        overview: 'Открыть обзор', events: 'К событиям и датам', data: 'Открыть данные',
        checklist: 'К чек-листу', notifications: 'К уведомлениям',
    };
    const scopes = {
        global: 'Все доступные пациенты',
        global_readonly: 'Все доступные пациенты · чтение',
        by_department: 'Ваши отделения',
    };

    function available(app) {
        return !!app.user && !app.sessionExpired && !app.sessionReconciling &&
            app.navigationVisibility().pulse && app.currentView === 'pulse';
    }

    function accessMatches(app, state) {
        return !!app.user && state.owner === app.user.id && state.epoch === (app.sessionEpoch || 0) &&
            state.access === app.accessSignature(app.user);
    }

    function current(app, state) {
        return sessions.get(app) === state && available(app) && accessMatches(app, state) &&
            state.host === document.getElementById('f1Pulse');
    }

    function message(text, error = false) {
        return el('p', {class: 'f1-empty', role: error ? 'alert' : 'status', text});
    }

    function params(state) {
        const query = new URLSearchParams();
        if (state.stage) query.set('stage', state.stage);
        return query;
    }

    function serverTime(meta) {
        if (typeof meta?.computed_at !== 'string') return '';
        const timestamp = new Date(meta.computed_at);
        if (Number.isNaN(timestamp.valueOf())) return '';
        try {
            return new Intl.DateTimeFormat('ru-RU', {
                dateStyle: 'short', timeStyle: 'short', timeZone: meta.timezone,
            }).format(timestamp) + ' · ' + meta.timezone;
        } catch (error) {
            if (!(error instanceof RangeError)) throw error;
            return '';
        }
    }

    function errorMessage(error) {
        if ([403, 404].includes(error.status)) return 'Данные в текущей области доступа недоступны.';
        if ([409, 410].includes(error.status)) return 'Данные изменились. Обновите выборку.';
        return 'Не удалось получить актуальные данные. Попробуйте обновить выборку.';
    }

    function clear(app, reset = false) {
        const state = sessions.get(app);
        if (!state) return;
        state.summaryRead = null;
        state.detailRead = null;
        state.cards.clear();
        state.host?.replaceChildren();
        state.host = null;
        if (reset) sessions.delete(app);
        else delete state.nodes;
    }

    function render(app) {
        if (!available(app)) return;
        const host = document.getElementById('f1Pulse');
        if (!host) return;
        let state = sessions.get(app);
        if (!state || !accessMatches(app, state)) {
            state = {
                owner: app.user.id, epoch: app.sessionEpoch || 0, access: app.accessSignature(app.user),
                stage: '', selected: null, offset: 0, cards: new Map(),
            };
            sessions.set(app, state);
        }

        state.host = host;
        const stage = el('select', {class: 'select', id: 'f1PulseStage', on: {change: () => {
            if (!current(app, state)) return;
            state.stage = stage.value;
            state.offset = 0;
            void loadSummary(app, state);
        }}}, [
            el('option', {value: '', text: 'Все этапы'}),
            ...(app.stageOrder || []).map(key => el('option', {value: key, text: app.stageNames[key] || key})),
        ]);
        stage.value = state.stage;
        const refresh = el('button', {
            type: 'button', class: 'btn btn--ghost', id: 'f1PulseRefresh', text: 'Обновить показатели',
            on: {click: () => { if (current(app, state)) void loadSummary(app, state); }},
        });
        const summary = el('div', {class: 'f1-grid f1-pulse-kpis', id: 'f1PulseSummary'});
        const meta = el('p', {class: 'muted', id: 'f1PulseScope', role: 'status'});
        const detailTitle = el('h2', {class: 'panel__title', id: 'f1PulseDetailsTitle', tabindex: '-1'});
        const definition = el('p', {class: 'f1-description', id: 'f1PulseDefinition'});
        const details = el('div', {id: 'f1PulseDetails'});
        const detailPanel = el('section', {
            class: 'panel panel--pad', 'aria-labelledby': 'f1PulseDetailsTitle', hidden: true,
        }, [detailTitle, definition, details]);
        host.replaceChildren(
            el('div', {class: 'f1-actions'}, [el('label', {for: stage.id, text: 'Этап маршрута'}), stage, refresh]),
            meta, summary, detailPanel,
        );
        state.nodes = {summary, meta, details, detailTitle, definition, detailPanel, refresh};
        void loadSummary(app, state);
    }

    async function loadSummary(app, state) {
        const token = {};
        state.summaryRead = token;
        state.detailRead = null;
        state.cards.clear();
        const {summary, meta, details, detailPanel} = state.nodes;
        summary.setAttribute('aria-busy', 'true');
        summary.replaceChildren(message('Загружаем показатели…'));
        meta.textContent = '';
        details.replaceChildren();
        detailPanel.hidden = true;
        let ready = false;
        try {
            const data = await apiGetJson('/api/analytics/summary?' + params(state));
            if (!current(app, state) || state.summaryRead !== token) return;
            for (const card of data.cards) {
                const metric = metrics.get(card.code);
                if (metric && metric.type === card.detail_type && card.kind === 'current' &&
                    Number.isSafeInteger(card.value) && card.value >= 0) state.cards.set(card.code, card);
            }
            if (!state.cards.size) throw new Error('Показатели недоступны');
            const scope = scopes[data.meta.scope] || 'Текущая область доступа';
            const calculated = serverTime(data.meta);
            meta.textContent = [scope, 'Источник: серверная аналитика', calculated ? 'На ' + calculated : 'На момент запроса'].join(' · ');
            summary.replaceChildren(...Array.from(state.cards.values(), card => metricButton(app, state, card)));
            ready = true;
        } catch (error) {
            if (!current(app, state) || state.summaryRead !== token || error.obsolete) return;
            state.cards.clear();
            summary.replaceChildren(message(errorMessage(error), true));
        } finally {
            if (current(app, state) && state.summaryRead === token) summary.setAttribute('aria-busy', 'false');
        }
        if (ready && state.cards.has(state.selected)) await loadDetails(app, state);
    }

    function metricButton(app, state, card) {
        const descriptionId = 'f1PulseMetricDescription-' + card.code;
        const metric = metrics.get(card.code);
        const label = metric.label || card.label;
        const value = number.format(card.value);
        const unit = units[metric.unit][{one: 0, few: 1}[plurals.select(card.value)] ?? 2];
        return el('button', {
            type: 'button', class: 'stat stat--button f1-item', dataset: {f1Metric: card.code},
            'aria-pressed': String(state.selected === card.code), 'aria-describedby': descriptionId,
            'aria-label': label + ': ' + value + ' ' + unit,
            on: {click: () => {
                if (!current(app, state) || state.cards.get(card.code) !== card) return;
                state.selected = card.code;
                state.offset = 0;
                void loadDetails(app, state, true);
            }},
        }, [
            el('span', {class: 'f1-item__title', text: label}),
            el('span', {class: 'f1-metric-reading'}, [
                el('span', {class: 'f1-value', text: value}),
                el('span', {class: 'f1-metric-unit', text: unit}),
            ]),
            el('span', {class: 'visually-hidden', id: descriptionId, text: card.definition}),
        ]);
    }

    async function loadDetails(app, state, moveFocus = false, adjustPage = true) {
        if (!current(app, state)) return;
        const code = state.selected;
        const card = state.cards.get(code);
        const metric = metrics.get(code);
        if (!card || !metric) return;
        const {summary, details, detailTitle, definition, detailPanel} = state.nodes;
        const offset = state.offset;
        const query = params(state);
        query.set('type', metric.type);
        query.set('limit', String(pageSize));
        query.set('offset', String(offset));
        const token = {};
        state.detailRead = token;
        const detailCurrent = () => current(app, state) && state.detailRead === token;
        detailTitle.textContent = metric.heading || card.label;
        definition.textContent = card.definition;
        detailPanel.hidden = false;
        if (moveFocus) detailTitle.focus();
        for (const btn of summary.querySelectorAll('[data-f1-metric]')) {
            btn.setAttribute('aria-pressed', String(btn.dataset.f1Metric === code));
        }
        details.setAttribute('aria-busy', 'true');
        details.replaceChildren(message('Загружаем список…'));
        try {
            const data = await apiGetJson('/api/analytics/details?' + query);
            if (!detailCurrent()) return;
            if (data.detail_type !== metric.type || data.offset !== offset || data.limit !== pageSize ||
                !Number.isSafeInteger(data.total) || data.total < 0 || !Array.isArray(data.items) ||
                data.items.length > pageSize) throw new Error('Список недоступен');
            if (adjustPage && offset > 0 && data.total <= offset) {
                state.offset = Math.floor(Math.max(0, data.total - 1) / pageSize) * pageSize;
                return await loadDetails(app, state, false, false);
            }
            drawDetails(app, state, data, metric, detailCurrent);
        } catch (error) {
            if (!detailCurrent() || error.obsolete) return;
            showDetailsFailure(app, state, errorMessage(error));
        } finally {
            if (detailCurrent()) details.setAttribute('aria-busy', 'false');
        }
    }

    function showDetailsFailure(app, state, text) {
        const token = {};
        state.detailRead = token;
        const {details} = state.nodes;
        const restoreFocus = details.contains(document.activeElement);
        const retry = el('button', {
            type: 'button', class: 'btn btn--ghost', text: 'Обновить список',
            on: {click: () => {
                if (current(app, state) && state.detailRead === token) void loadDetails(app, state, true);
            }},
        });
        details.setAttribute('aria-busy', 'false');
        details.replaceChildren(message(text, true), retry);
        if (restoreFocus) retry.focus();
    }

    function rowContext(data, metric, item) {
        const plan = item.planned_date || item.context?.planned_date;
        const dateParts = typeof plan === 'string' && /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(plan);
        const scheduled = item.scheduled_at && serverTime({computed_at: item.scheduled_at, timezone: data.meta.timezone});
        const isQuality = metric.type === 'quality_actionable';
        const reason = item.title || item.event_type || {
            incomplete_checklists: 'Не выполнены обязательные пункты',
            notifications_pending: 'Уведомление в очереди',
            attention_patients: 'Активная отметка внимания',
        }[metric.type];
        const severity = isQuality && {critical: 'Критическое замечание', warning: 'Предупреждение'}[item.severity];
        const overdue = Number.isSafeInteger(item.days_overdue) && item.days_overdue > 0;
        const details = [
            isQuality ? ['Качество данных', severity, item.code_label !== reason ? item.code_label : ''].filter(Boolean).join(' · ') : null,
            dateParts ? 'По плану: ' + dateParts.slice(1).reverse().join('.') :
                metric.type === 'overdue_events' ? 'Плановый срок не указан' : null,
            overdue ? 'Просрочка: ' + item.days_overdue + ' дн.' : null,
            Number.isSafeInteger(item.incomplete_required) ? 'Не выполнено: ' + item.incomplete_required +
                (Number.isSafeInteger(item.total_required) ? ' из ' + item.total_required : '') : null,
            scheduled ? 'Запланировано: ' + scheduled : null,
        ].filter(Boolean);
        return {reason, details, tab: isQuality ? qualityTabs.get(item.code) || metric.tab : metric.tab};
    }

    function detailRow(app, state, data, item, metric, detailCurrent) {
        const {reason, details, tab} = rowContext(data, metric, item);
        const {patient_name: name, patient_id: id} = item;
        const patientContext = [item.stage_label, item.treating_doctor_name].filter(Boolean);
        const attention = metric.type === 'attention_patients';
        const action = attention ? 'К отметкам внимания' : actions[tab];
        return el('li', {class: 'f1-item f1-detail-row'}, [
            el('div', {class: 'f1-detail-cell'}, [
                el('p', {class: 'f1-item__title', text: name || 'Запись'}),
                patientContext.length ? el('p', {class: 'f1-description', text: patientContext.join(' · ')}) : null,
            ]),
            reason || details.length ? el('div', {class: 'f1-detail-cell'}, [
                reason ? el('p', {class: 'f1-detail-reason', text: reason}) : null,
                details.length ? el('p', {class: 'f1-description', text: details.join(' · ')}) : null,
            ]) : null,
            id && name ? el('button', {
                type: 'button', class: 'btn btn--ghost btn--sm f1-detail-action', text: action,
                dataset: {f1OpenPatient: 'true'}, 'aria-label': action + ': ' + name,
                on: {click: () => {
                    if (!detailCurrent()) return;
                    void app.openPatient(id, tab, {
                        allowed: detailCurrent,
                        prepare: patient => {
                            if (state.stage && patient.stage !== state.stage) {
                                ui.toast('Этап изменился. Обновляем выборку.', 'warning');
                                state.nodes.refresh.focus();
                                void loadSummary(app, state);
                                return false;
                            }
                            // Analytics gives a plan date, not stable event or occurrence IDs.
                            if (tab === 'events') delete app.selectedTimelineNodes[id];
                            return true;
                        },
                        focus: attention ? () => {
                            const block = document.getElementById('patientAttentionBlock');
                            block?.scrollIntoView({block: 'center'});
                            return block;
                        } : undefined,
                        denied: () => {
                            if (!detailCurrent()) return;
                            state.nodes.refresh.focus();
                            void loadSummary(app, state);
                        },
                        readFailed: () => {
                            if (!detailCurrent()) return;
                            showDetailsFailure(app, state, 'Не удалось проверить карточку. Обновите список перед переходом.');
                        },
                    });
                }},
            }) : null,
        ]);
    }

    function drawDetails(app, state, data, metric, detailCurrent) {
        const content = data.items.map(item => detailRow(app, state, data, item, metric, detailCurrent));
        const start = data.offset + 1;
        const end = data.offset + content.length;
        const summary = content.length ? String(start) + '–' + end + ' из ' + number.format(data.total) : '';
        const action = (text, offset) => el('button', {
            type: 'button', class: 'btn btn--ghost', text, on: {click: () => {
                if (!detailCurrent()) return;
                state.offset = offset;
                void loadDetails(app, state, true);
            }},
        });
        const attention = metric.type === 'attention_patients';
        const exactFilter = attention || metric.type === 'patients';
        const stage = state.stage;
        const registry = el('button', {
            id: 'f3PulseRegistry', type: 'button', class: 'btn btn--ghost btn--sm',
            text: attention ? 'Пациенты с отметкой внимания в реестре' : stage ? 'Реестр по выбранному этапу' : 'Открыть общий реестр',
            on: {click: () => {
                if (detailCurrent()) void app.openWorkingRegistry('pulse', {
                    stage, attention: attention ? 'active' : '', include_terminal: 'true',
                }, detailCurrent);
            }},
        });
        const analytics = el('button', {
            id: 'f5PulseAnalytics', type: 'button', class: 'btn btn--ghost btn--sm',
            text: 'Открыть в аналитике',
            on: {click: () => {
                if (detailCurrent()) void app.openWorkingAnalytics('pulse', {
                    stage, type: metric.type, title: metric.heading || metric.label || state.cards.get(state.selected)?.label,
                }, detailCurrent);
            }},
        });
        state.nodes.details.replaceChildren(
            content.length ? el('p', {class: 'muted f1-pulse-result', role: 'status', text: summary + ' · ' + (metric.detailUnit || metric.unit)}) :
                message(data.total ? 'На этой странице записей нет.' : 'По выбранным условиям нет доступных записей.'),
            el('div', {class: 'f1-actions'}, [registry, analytics, !exactFilter ? el('span', {
                class: 'f1-description', text: stage ? 'Все доступные пациенты этого этапа.' : 'Все доступные пациенты.',
            }) : null]),
            content.length ? el('ul', {class: 'f1-list f1-pulse-list'}, content) : null,
            data.offset > 0 || end < data.total ? el('div', {class: 'f1-actions'}, [
                data.offset > 0 ? action('Предыдущая страница', Math.max(0, data.offset - pageSize)) : null,
                end < data.total ? action('Следующая страница', data.offset + pageSize) : null,
            ]) : null,
        );
    }

    return {clear, render};
})();
