"""Excel-дашборд для пользователя таблицы (без знания программирования).

Дашборд — обычная книга Excel: формулы и внешние ссылки между книгами не
нужны. В ней лист «Дашборд» с карточками KPI, таблицей рекомендаций и
диаграммами, детальные листы и инструкция по обновлению.

Источник данных — Google Таблица (п. 4.1 ТЗ): книга выгружается по ссылке
``export?format=xlsx``, затем выполняется полный расчёт; при недоступности
сети берётся последняя локальная копия.

Запуск:  ``python -m recommender --google --dashboard``
или     ``Обновить_дашборд.bat`` (двойной клик в проводнике).
"""
from __future__ import annotations

import re
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import config as C
from .pipeline import run_pipeline

# --- оформление -----------------------------------------------------------
BRAND = "1F4E79"      # тёмно-синий, заголовки
HEADER_BG = "2E75B6"  # шапка таблиц
CARD_BG = "DDEBF7"    # фон карточек
SPACER = "FFFFFF"
GREY = "595959"
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

F_TITLE = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
F_SUB = Font(name="Calibri", size=10, italic=True, color=GREY)
F_CARD_LABEL = Font(name="Calibri", size=9, bold=True, color=GREY)
F_CARD_VALUE = Font(name="Calibri", size=20, bold=True, color=BRAND)
F_SECTION = Font(name="Calibri", size=12, bold=True, color=BRAND)
F_HEAD = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
F_BODY = Font(name="Calibri", size=10)


# ---------------------------------------------------------------------------
# Загрузка из Google Таблицы
# ---------------------------------------------------------------------------
def google_export_url(sheet_url: str = C.GOOGLE_SHEET_URL) -> str:
    """Любая ссылка Google Таблицы → прямая ссылка на выгрузку xlsx."""
    match = re.search(r"/d/([A-Za-z0-9-_]+)", sheet_url)
    if not match:
        raise ValueError(f"Не похоже на ссылку Google Таблицы: {sheet_url}")
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=xlsx"


def refresh_source(dest: str | Path = C.EXCEL_PATH,
                   sheet_url: str = C.GOOGLE_SHEET_URL) -> tuple[Path, str]:
    """Скачать свежую выгрузку из Google Таблицы.

    Возвращает (путь, описание источника). При недоступности сети используется
    последняя локальная копия — расчёт не останавливается.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = google_export_url(sheet_url)
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; coin-recommender/1.0)"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            payload = resp.read()
        dest.write_bytes(payload)
        return dest, f"Google Таблица (обновлено {datetime.now():%d.%m.%Y %H:%M})"
    except Exception as exc:  # noqa: BLE001 — мягкий фолбэк на локальный файл
        if dest.exists():
            return dest, (f"локальная копия — Google Таблица недоступна "
                          f"({type(exc).__name__})")
        raise


# ---------------------------------------------------------------------------
# Вспомогательные функции выписки
# ---------------------------------------------------------------------------
def _write_df(ws, df: pd.DataFrame, row: int = 1, col: int = 1,
              title: str | None = None) -> int:
    """Записать DataFrame с шапкой и рамками. Возвращает следующую свободную строку."""
    start_col = col
    if title:
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = F_SECTION
        row += 1

    for j, name in enumerate(df.columns):
        cell = ws.cell(row=row, column=start_col + j, value=str(name))
        cell.font = F_HEAD
        cell.fill = PatternFill("solid", fgColor=HEADER_BG)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX
    head_row = row

    for i, (_, record) in enumerate(df.iterrows(), start=1):
        for j, name in enumerate(df.columns):
            value = record[name]
            if pd.isna(value):
                value = None
            elif isinstance(value, (pd.Timestamp,)):
                value = value.to_pydatetime()
            elif hasattr(value, "item"):
                value = value.item()
            cell = ws.cell(row=head_row + i, column=start_col + j, value=value)
            cell.font = F_BODY
            cell.border = BOX
            cell.alignment = Alignment(vertical="center",
                                       horizontal="left" if isinstance(value, str) else "right")
            cell.number_format = _num_format(str(name), value)

    _autosize(ws, df, head_row, start_col)
    return head_row + len(df) + 2


def _num_format(name: str, value) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "General"
    low = name.lower()
    if name == "%" or "покрытие" in low:
        return "0.0%"
    if any(k in low for k in ("цена", "бюджет", "₽", "вход", "продажа", "маржа")):
        return "#,##0"
    if any(k in low for k in ("итог", "профиль", "новизна", "mrr", "ndcg")) \
            or name in ("HR@1", "HR@3", "HR@5", "HR@10"):
        return "0.000"
    return "General"


def _autosize(ws, df: pd.DataFrame, head_row: int, start_col: int) -> None:
    for j, name in enumerate(df.columns):
        letter = get_column_letter(start_col + j)
        values = [str(name)] + [str(v) for v in df[name].dropna().astype(str).head(200)]
        width = min(max((len(v) for v in values), default=8) + 3, 46)
        current = ws.column_dimensions[letter].width or 0
        ws.column_dimensions[letter].width = max(current, width)


def _card(ws, row: int, col: int, span: int, label: str, value: str) -> None:
    """Карточка KPI: подпись сверху, крупное число снизу."""
    label_rng = f"{get_column_letter(col)}{row}:{get_column_letter(col + span - 1)}{row}"
    value_rng = (f"{get_column_letter(col)}{row + 1}:"
                 f"{get_column_letter(col + span - 1)}{row + 1}")
    ws.merge_cells(label_rng)
    ws.merge_cells(value_rng)

    lc = ws.cell(row=row, column=col, value=label)
    lc.font = F_CARD_LABEL
    lc.fill = PatternFill("solid", fgColor=CARD_BG)
    lc.alignment = Alignment(horizontal="center", vertical="center")

    vc = ws.cell(row=row + 1, column=col, value=value)
    vc.font = Font(name="Calibri", size=20 if len(value) <= 7 else 13,
                   bold=True, color=BRAND)
    vc.fill = PatternFill("solid", fgColor=CARD_BG)
    vc.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    vc.border = BOX
    lc.border = BOX
    ws.row_dimensions[row + 1].height = 30


def _put_block(ws, row: int, col: int, headers: list[str],
               rows: list[list]) -> tuple[int, int, int, int]:
    """Записать блок данных для диаграммы. Возвращает (col, row, max_row, n_cols)."""
    for j, h in enumerate(headers):
        ws.cell(row=row, column=col + j, value=h)
    for i, record in enumerate(rows, start=1):
        for j, v in enumerate(record):
            ws.cell(row=row + i, column=col + j, value=v)
    return col, row, row + len(rows), len(headers)


# ---------------------------------------------------------------------------
# Построение дашборда
# ---------------------------------------------------------------------------
def build_dashboard(result: dict, out_path: str | Path,
                    source_note: str = "") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cat = result["каталог"]
    recs = result["рекомендации"]
    audit = result["аудит"]
    profiles = result["профили"]
    metrics = result["метрики"]
    avail = cat[cat["СТАТУС"] == C.STATUS_AVAILABLE]
    sold = cat[cat["СТАТУС"] == C.STATUS_SOLD]
    counts = audit["уровень"].value_counts().to_dict()
    model = metrics[metrics["метод"].str.startswith("Модель (3")].iloc[0]
    coverage = float(model["покрытие (прошла фильтры)"])
    hr5 = float(model["HR@5"])

    wb = Workbook()
    ws = wb.active
    ws.title = "Дашборд"
    ws.sheet_view.showGridLines = False

    last_row = _build_dashboard_sheet(ws, result, {
        "catalog": len(cat), "avail": len(avail), "sold": len(sold),
        "clients": len(profiles), "recs": len(recs), "coverage": coverage,
        "hr5": hr5, "ok": counts.get("OK", 0), "warn": counts.get("WARN", 0),
        "fail": counts.get("FAIL", 0), "source": source_note,
    })
    data_ws = _build_data_sheet(wb, result)
    _add_charts(ws, data_ws, start_row=last_row + 3)
    _write_df(wb.create_sheet("Рекомендации"), recs.drop(columns=["_escalated"], errors="ignore"),
              title="Рекомендации топ-K по каждому клиенту (с обоснованием)")
    _write_df(wb.create_sheet("Профили клиентов"), profiles,
              title="Профили: интересы и бюджетный коридор")
    _write_df(wb.create_sheet("Каталог монет"), cat[
        [c for c in C.DEAL_COLUMNS if c in cat.columns]
        + ["цена_рекомендации", "цена_источник", "цена_аналогов", "атрибут_источник"]
    ], title="Каталог: проданные и доступные монеты (цены кандидатов оценены по аналогам)")
    _write_df(wb.create_sheet("Качество данных"), audit,
              title="Аудит данных — 30 проверок (OK / WARN / FAIL)")
    _write_df(wb.create_sheet("Метрики"), metrics,
              title="Качество рекомендаций на исторических данных")
    if "чувствительность" in result and len(result["чувствительность"]):
        _write_df(wb.create_sheet("Метрики (настройка)"), result["чувствительность"],
                  title="Анализ чувствительности бюджетного коридора (этап 4 ТЗ)")

    _write_instructions(wb.create_sheet("Как обновить"))

    # служебный лист с исходными данными диаграмм убираем в конец книги
    hidden = wb["_графики"]
    wb._sheets.remove(hidden)
    wb._sheets.append(hidden)

    wb.active = 0
    wb.save(out_path)
    return out_path


def _build_dashboard_sheet(ws, result: dict, kpi: dict) -> None:
    # --- шапка -------------------------------------------------------------
    ws.merge_cells("A1:I1")
    t = ws["A1"]
    t.value = "РЕКОМЕНДАТЕЛЬНАЯ СИСТЕМА «МОНЕТЫ ДЛЯ КЛИЕНТА»"
    t.font = F_TITLE
    t.fill = PatternFill("solid", fgColor=BRAND)
    t.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    ws.merge_cells("A2:I2")
    s = ws["A2"]
    s.value = (f"Источник: {kpi['source'] or 'локальный файл data/coins.xlsx'}  ·  "
               f"Сформировано: {datetime.now():%d.%m.%Y %H:%M}  ·  "
               f"Обновить: двойной клик по файлу «Обновить_дашборд.bat»")
    s.font = F_SUB
    s.alignment = Alignment(horizontal="center", vertical="center")

    # --- карточки KPI ------------------------------------------------------
    cards = [
        ("МОНЕТ В КАТАЛОГЕ", str(kpi["catalog"])),
        ("ДОСТУПНО К ПРОДАЖЕ", str(kpi["avail"])),
        ("КЛИЕНТОВ", str(kpi["clients"])),
        ("ПРОДАННЫХ СДЕЛОК", str(kpi["sold"])),
        ("РЕКОМЕНДАЦИЙ СГЕНЕРИРОВАНО", str(kpi["recs"])),
        ("ПРОШЛИ ФИЛЬТРЫ (ИСТОРИЯ)", f"{kpi['coverage']:.0%}"),
        ("ТОЧНОСТЬ ТОП-5, HR@5", f"{kpi['hr5']:.1%}"),
        ("КАЧЕСТВО ДАННЫХ",
         f"{kpi['ok']} OK · {kpi['warn']} WARN · {kpi['fail']} FAIL"),
    ]
    positions = [(4, 1), (4, 4), (4, 7),
                 (7, 1), (7, 4), (7, 7),
                 (10, 1), (10, 4)]
    for (label, value), (row, col) in zip(cards, positions):
        _card(ws, row, col, 2, label, value)

    # --- таблица рекомендаций ---------------------------------------------
    ws.merge_cells("A13:I13")
    h = ws["A13"]
    h.value = ("РЕКОМЕНДАЦИИ ДЛЯ КАЖДОГО КЛИЕНТА  ·  "
               "полное обоснование каждой монеты — на листе «Рекомендации»")
    h.font = F_SECTION

    table = recs_dashboard_table(result["рекомендации"])
    next_row = _write_df(ws, table, row=14, col=1)

    if int(kpi["fail"]) > 0:
        ws.merge_cells(f"A{next_row}:I{next_row}")
        w = ws[f"A{next_row}"]
        w.value = ("⚠ ВНИМАНИЕ: есть критичные замечания по данным "
                   "(лист «Качество данных»). До их исправления KPI «закупка → продажа» "
                   "не считается, а часть рекомендаций строится по оценённым ценам.")
        w.font = Font(name="Calibri", size=10, bold=True, color="9C0006")
        w.fill = PatternFill("solid", fgColor="FFC7CE")
        w.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[next_row].height = 30
        next_row += 1

    for col, width in {"A": 16, "B": 5, "C": 11, "D": 14, "E": 11,
                       "F": 12, "G": 13, "H": 9, "I": 52}.items():
        ws.column_dimensions[col].width = width

    return next_row


def recs_dashboard_table(recs: pd.DataFrame) -> pd.DataFrame:
    cols = ["Клиент", "ранг", "ГП2", "Княжество", "Князь", "номинал",
            "цена, ₽", "Итог", "Обоснование"]
    out = recs[[c for c in cols if c in recs.columns]].copy()
    if "Обоснование" in out.columns:
        out["Обоснование"] = out["Обоснование"].astype(str).str.slice(0, 160)
    return out


def _build_data_sheet(wb, result: dict) -> None:
    """Служебный лист с исходными данными для диаграмм."""
    ws = wb.create_sheet("_графики")
    cat, recs = result["каталог"], result["рекомендации"]
    audit, profiles, metrics = result["аудит"], result["профили"], result["метрики"]
    avail = cat[cat["СТАТУС"] == C.STATUS_AVAILABLE]
    counts = audit["уровень"].value_counts().to_dict()

    status_rows = [[k, int(v)] for k, v in
                   cat["СТАТУС"].value_counts().items()]
    _put_block(ws, 1, 2, ["Статус", "Монет"], status_rows)

    kn = (avail["Княжество"].fillna("не указано").value_counts()
          .rename_axis("Княжество").reset_index(name="Доступно"))
    _put_block(ws, 1, 5, ["Княжество", "Доступно"],
               [[r.Княжество, int(r.Доступно)] for r in kn.itertuples()])

    avg_price = (recs.groupby("Клиент")["цена, ₽"].mean()
                 .round(0).rename("Средняя цена рекомендаций"))
    budget_rows = []
    for _, p in profiles.iterrows():
        budget_rows.append([
            p["Клиент"],
            float(p["Бюджет, медиана"]),
            float(avg_price.get(p["Клиент"], 0)),
        ])
    _put_block(ws, 1, 8, ["Клиент", "Бюджет (медиана, ₽)", "Средняя цена рекомендаций, ₽"],
               budget_rows)

    short = {
        "Модель (3 критерия, строгие фильтры)": "Модель",
        "Бейслайн: популярность (те же фильтры)": "Популярность",
        "Бейслайн: наугад (те же фильтры)": "Наугад",
    }
    picked = metrics[metrics["метод"].isin(short)].copy()
    picked["метка"] = picked["метод"].map(short)
    metric_rows = []
    for key in ("HR@1", "HR@3", "HR@5", "HR@10"):
        row = [key]
        for label in ("Модель", "Популярность", "Наугад"):
            val = picked.loc[picked["метка"] == label, key]
            row.append(round(float(val.iloc[0]), 3) if len(val) else 0.0)
        metric_rows.append(row)
    _put_block(ws, 1, 12, ["Метрика", "Модель", "Популярность", "Наугад"],
               metric_rows)

    audit_rows = [[lvl, int(counts.get(lvl, 0))] for lvl in ("OK", "WARN", "FAIL")]
    _put_block(ws, 1, 17, ["Аудит", "Проверок"], audit_rows)

    ws.sheet_state = "hidden"
    return ws


def _block_rows(ws, col: int) -> int:
    """Сколько строк данных в блоке (заголовок в строке 1)."""
    n = 0
    while ws.cell(row=2 + n, column=col).value not in (None, ""):
        n += 1
    return n


def _labels(value: bool = True, percent: bool = False,
            category: bool = False) -> DataLabelList:
    """Подписи точек без лишнего мусора (только значения/категории)."""
    labels = DataLabelList()
    labels.showVal = value
    labels.showPercent = percent
    labels.showCatName = category
    labels.showSerName = False
    labels.showLegendKey = False
    labels.showBubbleSize = False
    return labels


def _add_charts(dash_ws, data_ws, start_row: int = 44) -> None:
    """Диаграммы на листе «Дашборд» (исходные данные — на листе _графики)."""
    def at(i: int) -> str:
        return f"A{start_row + 16 * i}"

    # 1. Статусы монет (круговая): категории в колонке B, значения в колонке C
    n_status = _block_rows(data_ws, 2)
    pie = PieChart()
    pie.title = "Статусы монет в каталоге"
    pie.add_data(Reference(data_ws, min_col=3, min_row=1, max_row=1 + n_status),
                 titles_from_data=True)
    pie.set_categories(Reference(data_ws, min_col=2, min_row=2, max_row=1 + n_status))
    pie.height, pie.width = 7, 11
    pie.dataLabels = _labels(value=False, percent=True, category=True)
    dash_ws.add_chart(pie, at(0))

    # 2. Доступные монеты по княжествам
    n_kn = _block_rows(data_ws, 5)
    bar_kn = BarChart()
    bar_kn.type = "col"
    bar_kn.title = "Какие княжества сейчас в наличии"
    bar_kn.add_data(Reference(data_ws, min_col=6, min_row=1, max_row=1 + n_kn),
                    titles_from_data=True)
    bar_kn.set_categories(Reference(data_ws, min_col=5, min_row=2, max_row=1 + n_kn))
    bar_kn.height, bar_kn.width = 7, 11
    bar_kn.legend = None
    bar_kn.dataLabels = _labels(value=True)
    dash_ws.add_chart(bar_kn, at(1))

    # 3. Бюджет клиента vs цена рекомендаций
    n_cl = _block_rows(data_ws, 8)
    bar_b = BarChart()
    bar_b.type = "col"
    bar_b.title = "Бюджет клиента и цена рекомендуемых монет, ₽"
    bar_b.add_data(Reference(data_ws, min_col=9, max_col=10, min_row=1,
                             max_row=1 + n_cl), titles_from_data=True)
    bar_b.set_categories(Reference(data_ws, min_col=8, min_row=2, max_row=1 + n_cl))
    bar_b.height, bar_b.width = 7, 11
    bar_b.legend.position = "b"
    bar_b.legend.overlay = False
    bar_b.y_axis.numFmt = "#,##0"
    dash_ws.add_chart(bar_b, at(2))

    # 4. Качество рекомендаций против бейслайнов
    n_met = _block_rows(data_ws, 12)
    bar_m = BarChart()
    bar_m.type = "col"
    bar_m.title = "Качество рекомендаций на истории: HitRate@K"
    bar_m.add_data(Reference(data_ws, min_col=13, max_col=15, min_row=1,
                             max_row=1 + n_met), titles_from_data=True)
    bar_m.set_categories(Reference(data_ws, min_col=12, min_row=2, max_row=1 + n_met))
    bar_m.height, bar_m.width = 7, 11
    bar_m.dataLabels = _labels(value=True)
    bar_m.legend.position = "b"
    bar_m.legend.overlay = False
    dash_ws.add_chart(bar_m, at(3))

    # 5. Результаты аудита данных
    n_aud = _block_rows(data_ws, 17)
    pie_a = PieChart()
    pie_a.title = "Качество исходных данных"
    pie_a.add_data(Reference(data_ws, min_col=18, min_row=1, max_row=1 + n_aud),
                   titles_from_data=True)
    pie_a.set_categories(Reference(data_ws, min_col=17, min_row=2, max_row=1 + n_aud))
    pie_a.height, pie_a.width = 7, 11
    pie_a.dataLabels = _labels(value=False, percent=True, category=True)
    dash_ws.add_chart(pie_a, at(4))


def _write_instructions(ws) -> None:
    """Лист «Как обновить» — инструкция для пользователя таблицы."""
    ws.column_dimensions["A"].width = 100
    ws.sheet_view.showGridLines = False
    lines = [
        # (текст, размер, bold, italic, цвет, фон)
        ("КАК ОБНОВИТЬ ДАШБОРД", 16, True, False, "FFFFFF", BRAND),
        ("", 8, False, False, None, None),
        ("1. Откройте Google Таблицу и поправьте данные: статусы монет, сделки, клиентов.",
         11, False, False, None, None),
        ("", 6, False, False, None, None),
        ("2. Дважды кликните файл «Обновить_дашборд.bat» в папке проекта.",
         11, False, False, None, None),
        ("", 6, False, False, None, None),
        ("3. Откройте файл reports\\Дашборд.xlsx — он пересобран по свежим данным.",
         11, False, False, None, None),
        ("", 6, False, False, None, None),
        ("ЧТО ГДЕ СМОТРЕТЬ", 13, True, False, BRAND, None),
        ("  • «Дашборд» — карточки показателей, топ-5 рекомендаций для каждого клиента, графики.",
         11, False, False, None, None),
        ("  • «Рекомендации» — полный список монет с обоснованием каждой.",
         11, False, False, None, None),
        ("  • «Профили клиентов» — кто что коллекционирует и какой бюджетный коридор.",
         11, False, False, None, None),
        ("  • «Каталог монет» — все монеты; у доступных указана оценка цены по аналогам.",
         11, False, False, None, None),
        ("  • «Качество данных» — 30 автоматических проверок; строки FAIL надо закрыть.",
         11, False, False, None, None),
        ("  • «Метрики» — насколько точно система угадывала реальные покупки на истории.",
         11, False, False, None, None),
        ("", 6, False, False, None, None),
        ("ЕСЛИ ЧТО-ТО НЕ ТАК", 13, True, False, BRAND, None),
        ("  • Написано «локальная копия» — нет интернета, взяли последнюю сохранённую выгрузку.",
         11, False, False, None, None),
        ("  • Красная плашка на дашборде — критичные ошибки данных, лист «Качество данных».",
         11, False, False, None, None),
        ("  • Не открылся файл — он открыт в другом окне Excel, закройте и повторите обновление.",
         11, False, False, None, None),
        ("", 6, False, False, None, None),
        ("Подробности расчёта: README.md, docs/Методика.md.", 10, False, True, GREY, None),
    ]
    row = 1
    for text, size, bold, italic, color, fill in lines:
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = Font(name="Calibri", size=size, bold=bold, italic=italic, color=color)
        if fill:
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.alignment = Alignment(horizontal="left", vertical="center")
            ws.row_dimensions[row].height = 26
        else:
            cell.alignment = Alignment(vertical="center")
            ws.row_dimensions[row].height = 8 if size <= 8 else 18
        row += 1


# ---------------------------------------------------------------------------
# Полный запуск: выгрузка → расчёт → дашборд
# ---------------------------------------------------------------------------
def run_dashboard(input_path: str | Path = C.EXCEL_PATH,
                  out_path: str | Path = C.DASHBOARD_PATH,
                  use_google: bool = True,
                  top_k: int = C.TOP_K,
                  out_dir: str | Path | None = None,
                  verbose: bool = True) -> dict:
    """Один вызов: выгрузка из Google Таблицы → расчёт → сборка книги.

    ``out_dir`` — куда писать отчёты конвейера; по умолчанию — каталог, в
    котором лежит дашборд.
    """
    source = f"локальный файл {input_path}"
    path = Path(input_path)
    if use_google:
        path, source = refresh_source(dest=path)
    if verbose:
        print(f"Источник данных: {source}")

    result = run_pipeline(input_path=path,
                          out_dir=Path(out_dir) if out_dir else Path(out_path).parent,
                          top_k=top_k, verbose=verbose)
    dash = build_dashboard(result, out_path, source_note=source)
    result["дашборд"] = dash
    if verbose:
        print(f"Дашборд готов: {dash}")
    return result
