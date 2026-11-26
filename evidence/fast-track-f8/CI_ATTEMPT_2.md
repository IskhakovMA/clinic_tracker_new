# F8 — GitHub Actions, попытка 2

Кандидат `cc1404452fb65f23d08d3945ee1bd0e7b0412de7`, дерево `bb7f162097f36e1536b04418e1177a03a1ac9a12`. Оба запуска выполнили шаги. Первый отказ при выдаче runner остался в истории попыток.

| Проверка | Итог |
| --- | --- |
| [Backend/security CI](https://github.com/IskhakovMA/clinic_tracker_new/actions/runs/45783379149/attempts/2) | 15/15 jobs success. 2842 passed, 0 failed, 0 skipped, каждый ID выполнен один раз. Шарды 474 / 474 / 474 / 474 / 473 / 473. Backend-tests-verify и release-gate success. |
| [Hosted Chromium](https://github.com/IskhakovMA/clinic_tracker_new/actions/runs/45783378935/attempts/2) | Основной F8 Chromium, F7 и F6 прошли. Весь workflow завершился failure в регрессии WP-I Quality при запуске Chromium, `tools/tests/wp_i_quality/browser.cjs:329:36`. Первопричина запуска браузера в опубликованном логе отсутствует. Публикация машинных квитанций job пропущена. |

План CI собрал 2842 теста в 122 файлах. SHA-256 списка — `9beb662926e4644205a2fc6cf6cb44a483cfec90add0e602c3f375538597e920`. Staff-UX 81/81, CSP/XSS 35 passed, CI policy 53 passed.

Статус технического закрытия F8 остается NO-GO до зеленого hosted Chromium. Исходники, продуктовая ветка и workflow на этой попытке не менялись.
