/* Тема до первой отрисовки: сохранённый выбор, иначе системная.
   Отдельный файл, а не встроенный скрипт, — совместимо с CSP приложения
   (script-src 'self'). Подключается синхронно в <head>. */
(function () {
    'use strict';
    var t = null;
    try { t = localStorage.getItem('proto-theme'); } catch (e) { /* без хранилища */ }
    if (t !== 'light' && t !== 'dark') t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', t);
})();
