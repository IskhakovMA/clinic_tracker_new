/* Тема до первой отрисовки. Темная — по умолчанию (как в Stage 3.5),
   светлая — по выбору. Отдельный файл: совместимо с CSP приложения
   (script-src 'self'). */
(function () {
    'use strict';
    var t = null;
    try { t = localStorage.getItem('staffux-theme'); } catch (e) { /* без хранилища */ }
    document.documentElement.setAttribute('data-theme', t === 'light' ? 'light' : 'dark');
})();
