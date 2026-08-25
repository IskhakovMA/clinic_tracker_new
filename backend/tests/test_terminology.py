"""Терминология продукта, видимая пользователю.

Тесты держат договорённости по названиям: если кто-то вернёт старое имя
продукта, англицизм или техническую команду на экран входа — здесь упадёт.
Внутренние идентификаторы (поле `nosology` в API и БД) намеренно не трогаем.
"""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HTML = PROJECT_ROOT / "index.html"
JS = PROJECT_ROOT / "static" / "app.js"
CSS = PROJECT_ROOT / "static" / "styles.css"
README = PROJECT_ROOT / "README.md"
DESIGN = PROJECT_ROOT / "DESIGN_SYSTEM.md"

FRONTEND = (HTML, JS, CSS)
USER_FACING = (HTML, JS)
DOCS = (README, DESIGN, PROJECT_ROOT / "PROJECT_BRIEF.md", PROJECT_ROOT / "PROJECT_BRIEF_V2.md")

PRODUCT_NAME = "Система планирования"
PATHWAY_LABEL = "Локализация/клиентский путь"


def test_old_product_name_is_gone_from_frontend():
    for path in FRONTEND:
        assert "Clinic Tracker" not in path.read_text(encoding="utf-8"), path.name


def test_new_product_name_is_present():
    html = HTML.read_text(encoding="utf-8")
    assert f"<title>{PRODUCT_NAME}" in html
    assert f'<h1 class="login__title">{PRODUCT_NAME}</h1>' in html
    assert f'<span class="sidebar__name">{PRODUCT_NAME}</span>' in html


def test_login_subtitle():
    html = HTML.read_text(encoding="utf-8")
    assert "Планирование маршрута лечения и контроль этапов" in html


def test_sidebar_name_is_readable_by_screen_reader():
    """Название переносится средствами CSS, а не <br>: иначе доступное имя
    склеивается в «Системапланирования»."""
    html = HTML.read_text(encoding="utf-8")
    assert "Система<br>планирования" not in html
    assert "<br>" not in html.split('class="sidebar__name"')[1].split("</span>")[0]


def test_user_facing_nosology_term_is_replaced():
    """В видимых текстах — локализация/клиентский путь; слова «нозология»
    в пользовательских строках остаться не должно."""
    for path in USER_FACING:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[Нн]озолог", text), f"{path.name}: остался термин «нозология»"
    assert "Локализация" in HTML.read_text(encoding="utf-8")
    assert PATHWAY_LABEL in JS.read_text(encoding="utf-8")


def test_internal_nosology_field_is_preserved():
    """Внутренний контракт не ломаем: поле `nosology` остаётся в API, БД и JS —
    переименование потребовало бы миграции схемы и сломало бы старые выгрузки."""
    js = JS.read_text(encoding="utf-8")
    assert "'nosology'" in js
    assert "nosologyData" in js

    models = (PROJECT_ROOT / "backend" / "app" / "models.py").read_text(encoding="utf-8")
    assert "nosology:" in models

    schema = (PROJECT_ROOT / "backend" / "app" / "db.py").read_text(encoding="utf-8")
    assert "nosology TEXT" in schema


def test_anglicisms_replaced():
    for path in USER_FACING:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"[Сс]аммари", text), f"{path.name}: осталось «саммари»"
    js = JS.read_text(encoding="utf-8")
    assert "Скопировать сводку" in js
    assert "Сводка для отчётности" in js


def test_admin_command_not_shown_to_user():
    """Команда администратора не должна раскрывать внутреннее устройство
    на экране входа."""
    html = HTML.read_text(encoding="utf-8")
    assert "backend.cli" not in html
    assert "set-password" not in html
    assert "Для получения доступа обратитесь к администратору системы." in html


def test_admin_command_still_documented_in_readme():
    readme = README.read_text(encoding="utf-8")
    # set-password ушёл вместе с общим паролем; bootstrap-команда документирована
    assert "python -m backend.cli create-developer" in readme
    assert "set-password" not in readme


def test_docs_use_new_terminology():
    for path in (README, DESIGN):
        text = path.read_text(encoding="utf-8")
        assert PRODUCT_NAME in text, f"{path.name}: нет нового названия"


def test_docs_do_not_use_old_user_facing_labels():
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        assert "Clinic Tracker" not in text, f"{path.name}: осталось старое название продукта"
        assert "Нозология" not in text, f"{path.name}: остался старый label"
        assert "Скопировать саммари" not in text, f"{path.name}: осталась старая кнопка"
        assert "Саммари для отчётности" not in text, f"{path.name}: остался старый заголовок"


def test_theme_toggle_has_accessible_name():
    js = JS.read_text(encoding="utf-8")
    assert "Переключить на ${target} тему" in js
    assert "'aria-label'" in js


@pytest.mark.parametrize("token", ["--border-field", "--accent-text"])
def test_contrast_tokens_defined_in_both_themes(token):
    css = CSS.read_text(encoding="utf-8")
    dark, light = css.split(':root[data-theme="light"]')
    assert token in dark, f"{token} нет в тёмной теме"
    assert token in light, f"{token} нет в светлой теме"


def _contrast(hex_a: str, hex_b: str) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    la, lb = luminance(hex_a), luminance(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(theme: str) -> dict:
    css = CSS.read_text(encoding="utf-8")
    dark, light = css.split(':root[data-theme="light"]')
    block = dark if theme == "dark" else light
    return {
        name: value
        for name, value in re.findall(r"(--[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block)
    }


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_text_contrast_meets_wcag_aa(theme):
    """Основной и вторичный текст, а также подписи — не ниже 4.5:1."""
    t = _tokens(theme)
    for text_token in ("--text", "--text-2", "--text-3"):
        for surface_token in ("--bg", "--surface", "--surface-2"):
            ratio = _contrast(t[text_token], t[surface_token])
            assert ratio >= 4.5, f"{theme}: {text_token} на {surface_token} = {ratio:.2f}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_field_border_contrast_meets_wcag_aa(theme):
    """Границы полей ввода — не ниже 3:1 (требование к контролам)."""
    t = _tokens(theme)
    for surface_token in ("--bg", "--surface", "--surface-2"):
        ratio = _contrast(t["--border-field"], t[surface_token])
        assert ratio >= 3.0, f"{theme}: --border-field на {surface_token} = {ratio:.2f}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_accent_text_contrast_meets_wcag_aa(theme):
    t = _tokens(theme)
    ratio = _contrast(t["--accent-text"], t["--surface"])
    assert ratio >= 4.5, f"{theme}: --accent-text на --surface = {ratio:.2f}"


def test_mobile_layout_controls_exist():
    """Мобильная раскладка списка: «Данные», «Фильтры» и панели-листы."""
    html = HTML.read_text(encoding="utf-8")
    for element_id in ("openDataSheetBtn", "openFiltersSheetBtn", "dataSheet", "filtersSheet", "filtersBtnLabel"):
        assert f'id="{element_id}"' in html, element_id
    assert "Импортировать" in html
    assert "Экспортировать" in html

    css = CSS.read_text(encoding="utf-8")
    assert ".only-narrow" in css and ".only-wide" in css
    # статистика на узком экране — сетка 2×2, а не колонка
    narrow_block = css.split("@media (max-width: 560px)")[1]
    assert "grid-template-columns: repeat(2, 1fr)" in narrow_block


def test_tabs_wrap_instead_of_hidden_scroll():
    """Восемь вкладок на узких экранах переносятся, а не прячутся в прокрутку."""
    css = CSS.read_text(encoding="utf-8")
    block = css.split("@media (max-width: 1200px)")[1].split("@media")[0]
    assert "flex-wrap: wrap" in block
