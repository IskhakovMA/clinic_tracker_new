/* Определение клиентского пути по коду МКБ-10 — функция suggestedNosologyForDiagnosis,
   перенесена без изменений логики из static/app.js основного приложения (ветка feat/executive-dashboard-redesign, a3fbe00;
   в WP2.1 функция совпадает). Приложение отправляет результат на сервер как nosology. Справочник, не данные пациентов. */
window.StaffUxClientPath = {
    suggestedNosologyForDiagnosis(code) {
        const normalized = String(code || '').trim().toUpperCase();
        const exact = {
            'D00.0': 'headneckConfirmed',
            'D00.2': 'stomach',
            'D01.0': 'colon',
            'D01.1': 'colon',
            'D01.2': 'colon',
            'D01.5': 'liver',
            'D02.0': 'headneckConfirmed',
            'D02.2': 'lung12',
            'D07.0': 'uterus',
            D05: 'breast',
            D06: 'cervixCytology',
            'D07.5': 'prostate',
            'D09.0': 'bladder',
            'D09.1': 'upperUrinary',
            'D09.3': 'thyroidConfirmed',
            'D37.0': 'headneckConfirmed',
            'D37.1': 'stomach',
            'D37.3': 'colon',
            'D37.4': 'colon',
            'D37.5': 'colon',
            'D37.6': 'liver',
            'D38.0': 'headneckConfirmed',
            'D38.1': 'lung12',
            'D39.0': 'uterus',
            'D39.1': 'ovary',
            'D40.0': 'prostate',
            'D41.0': 'kidney',
            'D41.1': 'upperUrinary',
            'D41.2': 'upperUrinary',
            'D41.4': 'bladder',
            'D44.0': 'thyroidSuspected',
            'D48.5': 'skin',
            'D48.6': 'breast',
        };
        if (exact[normalized]) return exact[normalized];
        const prefixes = [
            ['C61', 'prostate'], ['C50', 'breast'], ['C16', 'stomach'],
            ['C18', 'colon'], ['C19', 'colon'], ['C20', 'colon'], ['C21', 'colon'],
            ['C56', 'ovary'], ['C54', 'uterus'], ['C55', 'uterus'],
            ['C44', 'skin'], ['C43', 'melanoma'], ['C34', 'lung12'],
            ['C67', 'bladder'], ['C22', 'liver'], ['C25', 'pancreas'],
            ['C73', 'thyroidSuspected'], ['C53', 'cervixInvasive'],
            ['C64', 'kidney'], ['C65', 'upperUrinary'], ['C66', 'upperUrinary'],
            ['C00', 'headneckConfirmed'], ['C01', 'headneckConfirmed'], ['C02', 'headneckConfirmed'],
            ['C03', 'headneckConfirmed'], ['C04', 'headneckConfirmed'], ['C05', 'headneckConfirmed'],
            ['C06', 'headneckConfirmed'], ['C07', 'headneckConfirmed'], ['C08', 'headneckConfirmed'],
            ['C09', 'headneckConfirmed'], ['C10', 'headneckConfirmed'], ['C11', 'headneckConfirmed'],
            ['C12', 'headneckConfirmed'], ['C13', 'headneckConfirmed'], ['C14', 'headneckConfirmed'],
            ['C30', 'headneckConfirmed'], ['C31', 'headneckConfirmed'], ['C32', 'headneckConfirmed'],
        ];
        const byPrefix = prefixes.find(([prefix]) => normalized === prefix || normalized.startsWith(`${prefix}.`) || normalized.startsWith(prefix));
        if (byPrefix) return byPrefix[1];
        if (normalized.startsWith('D03.')) return 'melanoma';
        if (normalized.startsWith('D04.')) return 'skin';
        if (normalized.startsWith('D05.')) return 'breast';
        if (normalized.startsWith('D06.')) return 'cervixCytology';
        return '';
    },
};
