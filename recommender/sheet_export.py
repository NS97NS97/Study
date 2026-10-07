"""Книга с рекомендациями для ручной вставки во вкладку «Рекомендации».

Запись в Google Таблицу из кода не выполняется — на запись нужен разрешённый
канал (API-ключ или публикация скрипта), поэтому система готовит файл,
повторяющий структуру вкладки (gid=115404378):

* одна строка = одно предложение «монета → клиенту»;
* атрибуты монеты (ГП2, княжество, князь, номинал, описание, R, вход) берутся
  **из вкладки «Сделки»** — как есть, без пересчёта;
* ручные колонки «за сколько» и «предложено» остаются пустыми: их заполняет
  человек;
* прежние строки в таблице не трогаются — новые добавляются в конец, поэтому
  из файла исключаются пары «монета + клиент», уже существующие в вкладке.

Запуск: ``python -m recommender`` (см. recommender/__main__).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import config as C

SHEET_TARGET = "Рекомендации"          # вкладка Google Таблицы (gid=115404378)
OUTPUT_NAME = "Рекомендации_для_вставки.xlsx"

# Шапка вкладки по умолчанию — на случай, если вкладки в выгрузке нет
DEFAULT_HEADER = [
    "ID монеты", "ГП2", "Княжество", "Князь", "номинал", "описание",
    "R", "вход", "ID клиента", "кому", "за сколько", "предложено",
]
# Атрибуты монеты, которые копируются из вкладки «Сделки»
COIN_ATTRS = ("ГП2", "Княжество", "Князь", "номинал", "описание", "R", "вход")
# Ручные колонки — не заполняются
MANUAL = ("за сколько", "предложено")
# Варианты названий колонки с именем клиента
_CLIENT_NAMES = ("кому", "кто", "клиент")

_BRAND = "1F4E79"


# ---------------------------------------------------------------------------
# Мелкие преобразования
# ---------------------------------------------------------------------------
def _norm(name) -> str:
    """Название колонки к нижнему регистру без лишних пробелов."""
    return str(name).strip().lower() if name is not None else ""


def _cell(value):
    """Пустая ячейка → None, целое без дробной части → int, иначе как есть."""
    if value is None:
        return None
    try:                                    # NaN / NaT / pd.NA
        if pd.isna(value):
            return None
    except (TypeError, ValueError):         # не скаляр — оставляем как есть
        return None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return value


def _key(value) -> str:
    """Идентификатор строки для проверки на дубль."""
    cell = _cell(value)
    return "" if cell is None else str(cell).strip()


def _filled(value) -> bool:
    return _cell(value) is not None


# ---------------------------------------------------------------------------
# Чтение целевой вкладки
# ---------------------------------------------------------------------------
def read_target(source_path: str | Path) -> dict:
    """Состояние вкладки «Рекомендации» из выгрузки Google Таблицы.

    Возвращает словарь: шапка, занятые пары «монета + клиент», строка первой
    пустой ячейки. Если вкладки нет (локальный файл без выгрузки) — считаем,
    что вкладка пуста, и включаем стандартную шапку.
    """
    info = {
        "header": list(DEFAULT_HEADER),
        "pairs": set(),
        "first_row": 2,
        "found": False,
        "filled_rows": 0,
    }
    try:
        raw = pd.read_excel(source_path, sheet_name=SHEET_TARGET, dtype=object)
    except (FileNotFoundError, ValueError, KeyError):
        return info                            # файла или вкладки нет

    header = [str(c).strip() for c in raw.columns if _filled(c)]
    if header:
        info["header"] = header
    info["found"] = True

    names = [_norm(h) for h in info["header"]]
    i_coin = names.index("id монеты") if "id монеты" in names else None
    i_client = names.index("id клиента") if "id клиента" in names else None

    last_used = 1                              # строка 1 занята шапкой
    for pos, (_, row) in enumerate(raw.iterrows(), start=2):
        if not any(_filled(v) for v in row.values):
            continue
        last_used = pos
        info["filled_rows"] += 1
        if i_coin is not None or i_client is not None:
            coin = _key(row.values[i_coin]) if i_coin is not None else ""
            client = _key(row.values[i_client]) if i_client is not None else ""
            if coin or client:
                info["pairs"].add((coin, client))

    info["first_row"] = last_used + 1
    return info


# ---------------------------------------------------------------------------
# Сборка строк под шапку вкладки
# ---------------------------------------------------------------------------
def build_rows(recs: pd.DataFrame, source_path: str | Path) -> tuple[pd.DataFrame, dict]:
    """Строки для вставки: ровно колонки вкладки «Рекомендации».

    Возвращает (DataFrame без заголовка, статистика).
    """
    target = read_target(source_path)

    # Атрибуты монет — из вкладки «Сделки» свежей выгрузки
    attrs: dict[str, dict] = {}
    deals_found = False
    try:
        deals = pd.read_excel(source_path, sheet_name=C.SHEET_DEALS, dtype=object)
        deals_found = True
        id_col = next((c for c in deals.columns if _norm(c) == "id монеты"), None)
        if id_col is not None:
            for _, row in deals.iterrows():
                coin_id = _key(row[id_col])
                if coin_id and coin_id not in attrs:
                    attrs[coin_id] = {
                        name: next((row[c] for c in deals.columns
                                    if _norm(c) == _norm(name)), None)
                        for name in COIN_ATTRS
                    }
    except (FileNotFoundError, ValueError, KeyError):
        pass

    stats = {
        "всего_рекомендаций": 0,
        "новых": 0,
        "уже_в_таблице": 0,
        "без_id": 0,
        "нет_в_сделках": 0,
        "строка_вставки": target["first_row"],
        "строк_уже_в_вкладке": target["filled_rows"],
        "вкладка_найдена": target["found"],
        "сделки_найдены": deals_found,
        "пустые_поля": [],
        "неизвестные_колонки": [],
        "шапка": list(target["header"]),
    }
    empty_fields: set[str] = set()

    header = target["header"]
    names = [_norm(h) for h in header]
    unknown = [h for h, n in zip(header, names)
               if n not in ({"id монеты", "id клиента"} | {a.lower() for a in COIN_ATTRS}
                            | set(MANUAL) | set(_CLIENT_NAMES))]
    stats["неизвестные_колонки"] = unknown

    rows = []
    for _, rec in recs.iterrows():
        coin_id, client_id = _key(rec.get("ID монеты")), _key(rec.get("ID клиента"))
        if not coin_id or not client_id:
            stats["без_id"] += 1              # строка-заглушка «нет кандидатов»
            continue
        stats["всего_рекомендаций"] += 1

        pair = (coin_id, client_id)
        if pair in target["pairs"]:
            stats["уже_в_таблице"] += 1
            continue

        coin_attrs = attrs.get(coin_id)
        if coin_attrs is None:
            stats["нет_в_сделках"] += 1
            coin_attrs = {name: rec.get(name) for name in COIN_ATTRS}

        values = []
        for name in header:
            n = _norm(name)
            if n in MANUAL:
                values.append(None)           # ручные колонки не заполняем
            elif n == "id монеты":
                values.append(coin_id)
            elif n == "id клиента":
                values.append(client_id)
            elif n in {a.lower() for a in COIN_ATTRS}:
                title = next(a for a in COIN_ATTRS if a.lower() == n)
                value = _cell(coin_attrs.get(title))
                if value is None and title in COIN_ATTRS:
                    empty_fields.add(title)
                values.append(value)
            elif n in _CLIENT_NAMES:
                values.append(_cell(rec.get("Клиент")))
            else:
                values.append(None)           # неизвестная колонка — пропускаем
        rows.append(values)
        stats["новых"] += 1

    stats["пустые_поля"] = [name for name in COIN_ATTRS if name in empty_fields]
    out = pd.DataFrame(rows, columns=range(len(header))) if rows \
        else pd.DataFrame(columns=range(len(header)))
    return out, stats


# ---------------------------------------------------------------------------
# Запись книги для вставки
# ---------------------------------------------------------------------------
def _write_instruction(ws, stats: dict) -> None:
    """Лист «Как вставить» — пошаговая инструкция с актуальными цифрами."""
    ws.column_dimensions["A"].width = 4
    ws.column_dimensions["B"].width = 108
    ws.sheet_view.showGridLines = False

    brand = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    head = Font(name="Calibri", size=12, bold=True, color=_BRAND)
    body = Font(name="Calibri", size=11)
    muted = Font(name="Calibri", size=10, italic=True, color="595959")
    manual_font = Font(name="Calibri", size=11, bold=True, color="9C0006")

    def line(row: int, text: str, font=body, fill: str | None = None,
             height: int = 18) -> None:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = font
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        if fill:
            cell.fill = PatternFill("solid", fgColor=fill)
            ws.row_dimensions[row].height = 28
        else:
            ws.row_dimensions[row].height = height

    line(1, "РЕКОМЕНДАЦИИ ДЛЯ ВСТАВКИ В GOOGLE ТАБЛИЦУ", brand, _BRAND, 30)
    if stats["новых"]:
        line(3, f"Новых строк к вставке: {stats['новых']}    ·    "
                f"уже есть в таблице: {stats['уже_в_таблице']}    ·    "
                f"строк сейчас во вкладке: {stats['строк_уже_в_вкладке']}",
             head)
    else:
        line(3, "Новых рекомендаций нет — всё уже вставлено в таблицу.", head)

    if not stats["вкладка_найдена"]:
        line(4, "Вкладка «Рекомендации» в выгрузке не найдена: свежую выгрузку "
                "делает файл «Запустить.bat» (флаг --google).", muted)

    line(6, "ЧТО ДЕЛАТЬ", head)
    line(7, "1. Вкладка «Копировать» этого файла → клик на ячейку A1 → "
            "Ctrl+A → Ctrl+C. Копируется блок строк: первые 10 колонок "
            "(«за сколько» и «предложено» в файл не входят — они ваши).")
    line(8, f"2. Google Таблица → вкладка «Рекомендации» → ячейка "
            f"A{stats['строка_вставки']} → Ctrl+V. Прежние строки остаются "
            f"на месте — новые добавляются в конец.")
    line(9, "3. Колонки «за сколько» и «предложено» остаются пустыми: их "
            "заполняете вы вручную.", manual_font)
    if stats["пустые_поля"]:
        line(10, "Атрибуты скопированы из вкладки «Сделки» как есть. "
                 "У части монет там пусто: " + ", ".join(stats["пустые_поля"])
                 + " — в рекомендациях эти ячейки тоже пустые (значения не "
                 "вымышляем).", muted, height=30)
        line(11, "Готово. Следующий пересчёт — двойной клик по файлу «Запустить.bat».",
             muted)
        gap = 1
    else:
        line(10, "Готово. Следующий пересчёт — двойной клик по файлу «Запустить.bat».",
             muted)
        gap = 0

    line(12 + gap, "ПОРЯДОК КОЛОНОК (12, как в Google Таблице)", head)
    row = 13 + gap
    for i, name in enumerate(stats["шапка"], start=1):
        n = str(name).strip().lower()
        what = "заполняется вручную" if n in MANUAL else \
               "атрибут монеты из вкладки «Сделки»" if n in {a.lower() for a in COIN_ATTRS} else \
               "клиент" if n in _CLIENT_NAMES else \
               "идентификатор"
        line(row, f"{i:>2}. {name} — {what}",
             manual_font if n in MANUAL else body, height=16)
        row += 1

    if stats["нет_в_сделках"]:
        line(row + 1, f"Внимание: {stats['нет_в_сделках']} монет не найдено в "
                      f"вкладке «Сделки» — атрибуты взяты из расчёта.", muted)


def write_workbook(rows: pd.DataFrame, stats: dict,
                   out_path: str | Path) -> Path:
    """Собрать файл «Рекомендации_для_вставки.xlsx»."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    instr = wb.active
    instr.title = "Как вставить"
    _write_instruction(instr, stats)

    # Вкладка строго под шапку вкладки Google Таблицы: только данные, начиная
    # с A1 — тогда Ctrl+A выделяет ровно блок строк без лишнего
    copy_ws = wb.create_sheet("Копировать")
    header = stats["шапка"]
    for j, _name in enumerate(header, start=1):
        letter = get_column_letter(j)
        copy_ws.column_dimensions[letter].width = 16
    for i, values in enumerate(rows.values, start=1):
        for j, value in enumerate(values, start=1):
            copy_ws.cell(row=i, column=j, value=_cell(value))
    copy_ws.sheet_view.showGridLines = False

    wb.save(out_path)
    return out_path


def build_for_sheet(recs: pd.DataFrame, source_path: str | Path,
                    out_dir: str | Path = "reports") -> tuple[Path, dict]:
    """Собрать файл для вставки во вкладку «Рекомендации»."""
    rows, stats = build_rows(recs, source_path)
    path = write_workbook(rows, stats, Path(out_dir) / OUTPUT_NAME)
    return path, stats
