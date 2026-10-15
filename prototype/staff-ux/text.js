/* Единое написание и поиск. Подключается после справочников и данных, до остальных модулей.
   ui()   — отображение подписей из подключаемых справочников (этапы, МКБ-10, клиентские пути)
            по стандарту Staff UX: «е» вместо «ё». Исходные справочники и записи не меняются.
            ФИО и тексты, введенные людьми (заметки, сообщения), не нормализуются.
   fold() — ключ для поиска: регистр и «е/ё» не различаются; сравниваются обе стороны. */
(function (root) {
    'use strict';
    const ui = (s) => (s === null || s === undefined ? s : String(s).replace(/ё/g, 'е').replace(/Ё/g, 'Е'));
    const fold = (s) => String(s ?? '').toLowerCase().replace(/ё/g, 'е');
    const hasFold = (hay, q) => fold(hay).includes(fold(q));
    root.StaffUxText = { ui, fold, hasFold };
})(typeof window !== 'undefined' ? window : globalThis);
