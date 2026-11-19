'use strict';

(() => {
    const mode = 'quality-v1';
    const limit = 50;
    const filterNames = ['severity', 'category', 'code', 'patient_id', 'doctor_user_id', 'department_id', 'search'];
    const parts = {
        patients: 'Пациенты', groups: 'Возможные дубли', members: 'Участники группы',
        doctors: 'Лечащие врачи', departments: 'Отделения', issues: 'Замечания к карточке',
    };

    function exactPage(collection) {
        if (!collection || !Array.isArray(collection.items) || !collection.page) return false;
        const page = collection.page;
        return page.limit === limit && Number.isSafeInteger(page.total) && Number.isSafeInteger(page.offset)
            && page.offset >= 0 && page.offset <= page.total
            && collection.items.length === Math.min(limit, page.total - page.offset)
            && page.has_next === (page.offset + collection.items.length < page.total)
            && (page.has_next ? typeof page.next_cursor === 'string' && !!page.next_cursor : page.next_cursor === null);
    }

    function validated(data, query, selector, root) {
        if (data?.mode !== mode || data.view !== selector.view || !data.snapshot || !data.query) return false;
        if (![data.scope_summary?.total_patients, data.totals?.patients, data.totals?.findings].every(
            value => Number.isSafeInteger(value) && value >= 0
        )) return false;
        const fields = ['token', 'revision', 'query_hash'];
        if (!fields.every(field => typeof data.snapshot[field] === 'string' && data.snapshot[field]
            && (!root || data.snapshot[field] === root[field]))) return false;
        const active = filterNames.filter(name => !!query[name]);
        if (data.query.sort !== (query.sort || 'severity') || data.query.has_search !== !!query.search
            || !Array.isArray(data.query.active_filters) || data.query.active_filters.length !== active.length
            || !active.every(name => data.query.active_filters.includes(name))) return false;
        if (data.view === 'page') {
            return [data.patients, data.duplicate_groups, data.facets?.doctors, data.facets?.departments].every(exactPage);
        }
        if (!data.selection || !exactPage(data.collection) || !['group_id', 'facet', 'owner_patient_id'].every(
            field => (data.selection[field] ?? null) === (selector[field] ?? null)
        )) return false;
        return data.view !== 'patient_issues' || (data.patient?.patient_id === selector.owner_patient_id
            && data.collection.items.every(item => item.patient_id === selector.owner_patient_id));
    }

    class Reader {
        constructor({read, context, visible, publish, onAccessLost, onOwnerUnavailable}) {
            Object.assign(this, {read, context, visible, publish, onAccessLost, onOwnerUnavailable});
            this.versions = new Map();
            this.generation = 0;
            this.root = null;
            this.query = Object.freeze({});
            this.owner = null;
        }

        clear(reason) {
            this.generation += 1;
            this.versions.clear();
            this.root = null;
            this.publish({state: 'reset', reason});
        }

        reset(query, owner = null) {
            this.query = Object.freeze({...query});
            this.owner = owner;
            this.clear();
        }

        async request(key, selector, cursor = null, allowRestart = true) {
            const context = this.context();
            const owner = this.owner;
            const primary = owner ? 'issues' : 'patients';
            const views = {patients: 'page', issues: 'patient_issues', groups: 'groups', members: 'members',
                doctors: 'facets', departments: 'facets'};
            const expectedOwner = owner && ['issues', 'groups', 'departments'].includes(key) ? owner : null;
            if (!views[key] || views[key] !== selector?.view || (selector.owner_patient_id ?? null) !== expectedOwner
                || (selector.view === 'facets' && selector.facet !== key)
                || (owner ? ['patients', 'doctors'].includes(key) : key === 'issues')) return false;
            if (!context || !this.visible() || (!this.root && (key !== primary || cursor))) return false;
            const query = this.query;
            const root = this.root;
            const generation = this.generation;
            const version = (this.versions.get(key) || 0) + 1;
            this.versions.set(key, version);
            selector = Object.freeze({...selector});
            let publishedRoot = root;
            const current = () => generation === this.generation && version === this.versions.get(key)
                && query === this.query && owner === this.owner && publishedRoot === this.root
                && this.visible() && context === this.context();
            this.publish({state: 'loading', key, selector, cursor});
            try {
                const data = await this.read({query, selector, snapshot: root?.token ?? null, cursor});
                if (!current()) return false;
                if (!validated(data, query, selector, root)) throw new Error('Invalid Quality envelope');
                if (!root) this.root = publishedRoot = Object.freeze({...data.snapshot});
                this.publish({state: 'ready', key, selector, cursor, data, firstRoot: !root});
                return true;
            } catch (error) {
                if (!current()) return false;
                const code = error?.data?.code;
                if (error?.status === 401 || error?.status === 403
                    || code === 'ACCESS_CONTEXT_CHANGED' || code === 'SESSION_IDENTITY_CHANGED') {
                    this.clear('access');
                    this.onAccessLost?.(error, owner);
                    this.publish({state: 'error', key: primary, error});
                    return false;
                }
                const changed = (error?.status === 409 && code === 'PATIENT_QUERY_CHANGED')
                    || (error?.status === 410 && code === 'CURSOR_EXPIRED');
                if (changed || error?.status === 503 || (owner && error?.status === 404) || !error?.status) {
                    this.clear(changed ? 'restart' : 'unavailable');
                    if (error?.status === 404 && selector.owner_patient_id === owner) {
                        this.onOwnerUnavailable?.(owner);
                    }
                    if (changed && allowRestart) {
                        const first = owner ? {view: 'patient_issues', owner_patient_id: owner} : {view: 'page'};
                        return this.request(primary, first, null, false);
                    }
                    key = primary;
                }
                this.publish({state: 'error', key, error, selector, cursor});
                return false;
            }
        }
    }

    function element(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    }

    function button(text, action, click, attributes = {}) {
        const node = element('button', text, 'btn btn--ghost btn--sm');
        node.type = 'button';
        node.dataset.qualityAction = action;
        Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
        if (click) node.addEventListener('click', click);
        return node;
    }

    function input(label, name, type = 'search') {
        const box = element('label', label, 'quality-v1__field');
        const control = element('input', undefined, 'input');
        Object.assign(control, {name, type, maxLength: 120, autocomplete: 'off'});
        box.append(control);
        return [box, control];
    }

    function select(label, name, options) {
        const box = element('label', label, 'quality-v1__field');
        const control = element('select', undefined, 'select');
        control.name = name;
        for (const [value, text] of options) {
            const option = element('option', text);
            option.value = value;
            control.append(option);
        }
        box.append(control);
        return box;
    }

    function requestPath(endpoint, {query, selector, snapshot, cursor}) {
        const params = new URLSearchParams({mode, limit: String(limit), view: selector.view});
        const headers = {};
        for (const name of ['sort', ...filterNames]) {
            if (name !== 'search' && query[name]) params.set(name, query[name]);
        }
        for (const name of ['facet', 'group_id', 'owner_patient_id']) {
            if (selector[name]) params.set(name, selector[name]);
        }
        for (const [name, value] of [['X-Search', query.search], ['X-Quality-Facet-Search', selector.facet_search]]) {
            if (!value) continue;
            const encoded = encodeURIComponent(value.trim());
            if (encoded.length > 512 || /[\u0000-\u001f\u007f]/.test(value)) {
                const error = new Error('Invalid search'); error.status = 422; throw error;
            }
            if (encoded) headers[name] = encoded;
        }
        if (snapshot) params.set('snapshot', snapshot);
        if (cursor) params.set('cursor', cursor);
        return [endpoint + '?' + params, {headers}];
    }

    class Panel {
        constructor(host, config, owner = null) {
            this.host = host;
            this.config = config;
            this.owner = owner;
            this.primary = owner ? 'issues' : 'patients';
            this.sections = new Map();
            this.history = new Map();
            this.loadVersion = new Map();
            this.filters = {};
            this.selectedFacets = new Map();
            this.heading = element(owner ? 'h2' : 'h1', 'Качество данных', owner ? 'section-title' : 'pagehead__title');
            this.header = element('div', undefined, 'pagehead');
            this.header.append(this.heading);
            this.stamp = element('p', undefined, 'muted quality-v1__summary');
            this.live = element('p', undefined, 'quality-v1__status');
            this.live.setAttribute('role', 'status');
            host.replaceChildren(this.header, this.stamp, this.live);
            host.classList.add('quality-v1');
            if (!owner) this.createFilters();
            const order = owner ? ['issues', 'groups', 'members', 'departments']
                : ['patients', 'groups', 'members', 'doctors', 'departments'];
            for (const part of order) this.makeSection(part);
            this.reader = new Reader({
                read: config.read, context: config.context,
                visible: () => host.isConnected && config.visible(owner),
                publish: event => this.publish(event),
                onAccessLost: config.onAccessLost, onOwnerUnavailable: config.onOwnerUnavailable,
            });
        }

        createFilters() {
            const form = this.form = element('form', undefined, 'quality-v1__filters');
            const [searchBox] = input('Поиск по ФИО', 'search');
            const apply = button('Применить', 'apply');
            apply.type = 'submit';
            const reset = button('Сбросить', 'reset', () => {
                this.selectedFacets.clear();
                this.start({});
            });
            form.append(
                searchBox,
                select('Серьезность', 'severity', [['', 'Все'], ['critical', 'Критическая'], ['warning', 'Требует проверки'], ['info', 'Информация']]),
                select('Категория', 'category', [['', 'Все'], ...Object.entries(this.config.categories)]),
                select('Тип замечания', 'code', [['', 'Все'], ...Object.entries(this.config.codes)]),
                select('Сортировка', 'sort', [['severity', 'По серьезности'], ['issues', 'По числу замечаний'], ['completeness', 'По заполненности']]),
                apply, reset,
            );
            this.applied = element('div', undefined, 'quality-v1__chips');
            this.host.append(form, this.applied);
            form.addEventListener('submit', event => {
                event.preventDefault();
                this.start(this.readFilterForm());
            });
        }

        readFilterForm() {
            const values = Object.fromEntries(new FormData(this.form).entries());
            const query = {...this.filters, ...values, search: values.search.trim()};
            return Object.fromEntries(Object.entries(query).filter(([, value]) => !!value));
        }

        makeSection(part) {
            const box = element('section', undefined, 'panel panel--pad quality-v1__part');
            box.dataset.qualityPart = part;
            box.hidden = part !== this.primary;
            const title = element(this.owner ? 'h3' : 'h2', parts[part], 'section-title');
            title.tabIndex = -1;
            box.append(title);
            const body = element('div', undefined, 'quality-v1__body');
            const sectionStatus = element('p', undefined, 'muted quality-v1__status');
            sectionStatus.setAttribute('role', 'status');
            box.append(sectionStatus, body);
            this.sections.set(part, {box, title, body, status: sectionStatus});
            this.host.append(box);
        }

        start(filters = this.filters) {
            this.filters = {...filters};
            if (this.form) {
                for (const node of this.form.elements) {
                    if (node.name) node.value = filters[node.name] || (node.name === 'sort' ? 'severity' : '');
                }
                this.renderApplied();
            }
            this.reader.reset(this.owner ? {patient_id: this.owner} : this.filters, this.owner);
            return this.reader.request(this.primary, this.firstSelector());
        }

        firstSelector() {
            return this.owner ? {view: 'patient_issues', owner_patient_id: this.owner} : {view: 'page'};
        }

        forget() {
            this.filters = {};
            this.selectedFacets.clear();
            if (this.form) this.form.reset();
            this.applied?.replaceChildren();
            this.reader.reset({}, this.owner);
        }

        clear(forget) {
            if (forget) this.forget();
            else this.reader.clear();
        }

        publish({state, key, selector, cursor, data, error, reason, firstRoot}) {
            if (state === 'reset') {
                this.history.clear();
                this.loadVersion.clear();
                this.stamp.textContent = '';
                if (this.form) this.renderApplied();
                this.live.textContent = reason === 'restart' ? 'Данные изменились. Открываем первую страницу.' : '';
                for (const [part, section] of this.sections) {
                    section.body.replaceChildren();
                    section.status.textContent = '';
                    section.box.removeAttribute('aria-busy');
                    section.box.hidden = part !== this.primary;
                }
                return;
            }
            const section = this.sections.get(key);
            if (!section) return;
            section.box.hidden = false;
            if (state === 'loading') {
                this.loadVersion.set(key, (this.loadVersion.get(key) || 0) + 1);
                if (section.body.contains(document.activeElement)) section.title.focus({preventScroll: true});
                section.body.replaceChildren();
                section.box.setAttribute('aria-busy', 'true');
                section.status.textContent = 'Загрузка…';
                return;
            }
            section.box.removeAttribute('aria-busy');
            section.status.textContent = '';
            if (state === 'error') {
                section.status.textContent = this.errorText(error);
                section.body.replaceChildren();
                if (![401, 403].includes(error?.status)) {
                    section.body.append(button('Обновить данные', 'retry', () => this.start()));
                }
                return;
            }
            if (key === 'patients') {
                this.stamp.textContent = `В доступной области: ${data.scope_summary.total_patients}. По фильтрам: ${data.totals.patients}. Замечаний в выборке: ${data.totals.findings}.`;
                this.renderCollection(key, data.patients, selector, cursor);
                if (firstRoot) {
                    this.renderCollection('groups', data.duplicate_groups, {view: 'groups'});
                    for (const facet of ['doctors', 'departments']) {
                        this.renderCollection(facet, data.facets[facet], {view: 'facets', facet});
                    }
                }
                return;
            }
            if (key === 'issues') {
                const {completeness, departments} = data.patient;
                this.stamp.textContent = `Заполненность обязательных полей: ${completeness.score}%.`;
                if (departments.preview.length) {
                    this.stamp.append(element('span',
                        ` Отделения: ${departments.preview.map(item => item.display_name).join(', ')}.`));
                }
                const token = this.reader.root;
                if (departments.has_more) {
                    this.stamp.append(button(`Все отделения (${departments.total})`, 'show-departments', () => {
                        if (token !== this.reader.root || !this.config.visible(this.owner)) return;
                        this.history.delete('departments');
                        void this.reader.request('departments', {view: 'facets', facet: 'departments', owner_patient_id: this.owner});
                    }));
                }
            }
            this.renderCollection(key, data.collection, selector, cursor);
            if (key === 'issues' && firstRoot) {
                void this.reader.request('groups', {view: 'groups', owner_patient_id: this.owner});
            }
        }

        errorText(error) {
            switch (error?.status) {
                case 401: return 'Сеанс завершен. Войдите снова.';
                case 403: return 'Данные недоступны для текущих полномочий.';
                case 404: return 'Запрошенные данные недоступны.';
                case 409:
                case 410: return 'Данные изменились. Обновите список.';
                case 422: return 'Проверьте поисковый запрос и фильтры.';
                case 429: return 'Слишком много запросов. Повторите позже.';
                default: return 'Не удалось загрузить данные. Обновите список.';
            }
        }

        renderApplied() {
            this.applied.replaceChildren();
            const fields = [['doctor_user_id', 'Фильтр по лечащему врачу'], ['department_id', 'Фильтр по отделению']];
            for (const [key, label] of fields) {
                if (!this.filters[key]) continue;
                const selected = this.selectedFacets.get(key);
                const confirmed = this.reader.root && selected?.id === this.filters[key]
                    && selected.revision === this.reader.root.revision;
                const text = confirmed ? `${label}: ${selected.label}` : label;
                this.applied.append(button(`${text} · Снять`, 'facet-clear', () => {
                    const filters = this.readFilterForm();
                    delete filters[key];
                    this.selectedFacets.delete(key);
                    this.start(filters);
                }));
            }
        }

        renderCollection(key, data, selector, cursor = null) {
            const section = this.sections.get(key);
            section.box.hidden = false;
            const stamp = this.reader.root;
            const context = this.config.context();
            const version = this.loadVersion.get(key);
            const current = () => context && context === this.config.context()
                && this.config.visible(this.owner) && stamp === this.reader.root
                && version === this.loadVersion.get(key) && section.body.isConnected;
            const guarded = callback => event => { if (current()) callback(event, current); };
            const content = document.createDocumentFragment();
            if (key === 'doctors' || key === 'departments') {
                if (!this.owner) {
                    const [field, idKey] = key === 'doctors' ? ['doctor_user_id', 'user_id'] : ['department_id', 'department_id'];
                    const selected = data.items.find(item => item[idKey] === this.filters[field]);
                    if (selected) {
                        this.selectedFacets.set(field, {id: selected[idKey], label: selected.display_name,
                            revision: this.reader.root.revision});
                        this.renderApplied();
                    }
                }
                const form = element('form', undefined, 'quality-v1__facet-search');
                const [field, control] = input(key === 'doctors' ? 'Поиск лечащего врача' : 'Поиск отделения', 'facet_search');
                control.value = selector.facet_search || '';
                const search = button('Найти', 'facet-search');
                search.type = 'submit';
                form.append(field, search);
                form.addEventListener('submit', guarded(event => {
                    event.preventDefault();
                    this.history.delete(key);
                    void this.reader.request(key, {...selector, facet_search: control.value.trim()});
                }));
                content.append(form);
            }
            if (data.items.length) {
                const rows = element('ul', undefined, 'quality-v1__items');
                for (const row of data.items) rows.append(this.row(key, row, guarded));
                content.append(rows);
            } else {
                content.append(element('p', 'Нет данных для отображения.', 'empty'));
            }

            const pages = this.history.get(key) || [];
            const pager = element('div', undefined, 'quality-v1__pager');
            const back = button('Назад', 'previous', guarded(() => {
                const target = pages.pop();
                void this.reader.request(key, selector, target);
            }), {'aria-label': `Назад · ${parts[key]}`});
            back.disabled = !pages.length;
            const next = button('Далее', 'next', guarded(() => {
                pages.push(cursor);
                this.history.set(key, pages);
                void this.reader.request(key, selector, data.page.next_cursor);
            }), {'aria-label': `Далее · ${parts[key]}`});
            next.disabled = !data.page.has_next;
            const count = data.items.length ? `${data.page.offset + 1}–${data.page.offset + data.items.length} из ${data.page.total}` : '0';
            pager.append(back, element('span', count, 'muted'), next);
            content.append(pager);
            section.body.replaceChildren(content);
        }

        row(key, row, guarded) {
            const item = element('li', undefined, 'quality-v1__item');
            if (key === 'patients' || key === 'members') {
                item.append(button(row.patient_name, 'patient', guarded((_, current) =>
                    this.config.navigate(row.patient_id, 'overview', current)
                ), {'data-patient-id': row.patient_id}));
                const completeness = row.completeness?.score ?? row.completeness_score;
                const details = [`Заполненность ${completeness}%`];
                if (key === 'patients') {
                    details.push(`Замечаний: ${row.issue_count}`);
                    if (row.treating_doctor.display_name) details.push(row.treating_doctor.display_name);
                    for (const department of row.departments.preview) details.push(department.display_name);
                    if (row.departments.has_more) details.push(`Еще отделений: ${row.departments.total - row.departments.preview.length}`);
                    if (row.top_issues.length) item.append(element('p', row.top_issues.map(issue => issue.title).join(' · ')));
                } else {
                    details.push(row.matches_query ? 'Соответствует фильтрам' : 'Участник из доступной области');
                }
                item.append(element('p', details.join(' · '), 'muted'));
                return item;
            }
            if (key === 'groups') {
                item.append(element('strong', row.reason),
                    element('p', `Участников: ${row.member_total}, по фильтрам: ${row.matching_member_total}. Требуется ручная проверка.`, 'muted'),
                    button('Показать участников', 'group', guarded(() => {
                        this.history.delete('members');
                        void this.reader.request('members', {view: 'members', group_id: row.group_id});
                    }), {'data-group-id': row.group_id}));
                return item;
            }
            if (key === 'issues') {
                const action = row.suggested_action;
                item.append(element('strong', row.title), element('p', row.description),
                    element('p', this.config.categories[row.category] || row.category, 'muted'));
                const fix = button(action.label, 'finding', guarded((_, current) =>
                    this.config.navigate(row.patient_id, action.tab, current, action)
                ));
                fix.disabled = !action.can_write;
                item.append(fix);
                if (!action.can_write) item.append(element('span', 'Доступно только чтение.', 'muted'));
                return item;
            }
            const field = key === 'doctors' ? 'doctor_user_id' : 'department_id';
            const id = row.user_id || row.department_id;
            if (this.owner) item.append(element('strong', row.display_name));
            else {
                item.append(button(row.display_name, 'facet-apply', guarded(() => {
                    this.selectedFacets.set(field, {id, label: row.display_name, revision: this.reader.root.revision});
                    this.start({...this.readFilterForm(), [field]: id});
                }), {'data-facet-id': id, 'aria-pressed': this.filters[field] === id}));
            }
            item.append(element('span', `Пациентов: ${row.patient_count}`, 'muted'));
            return item;
        }
    }

    globalThis.ClinicQualityV1 = Object.freeze({Panel, Reader, requestPath});
})();
