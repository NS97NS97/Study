"""Аудит качества данных (этап 2 «Аудит и подготовка данных»).

Проверки соответствуют п. 4.2 ТЗ:
  1. обновляемость        — статус согласован с реквизитами сделки;
  2. фиксированные столбцы — состав колонок не изменился;
  3. обязательные поля    — нет пустых значений в обязательных атрибутах;
  4. унифицированность    — справочники, UUID, даты, форматы, производные поля.

Уровни: OK — замечаний нет; WARN — рекомендация; FAIL — нарушение требования.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config as C
from .data import is_uuid, norm_gp2, norm_text

LEVEL_OK, LEVEL_WARN, LEVEL_FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    код: str
    уровень: str
    проверка: str
    охвата_строк: int
    значение: str
    комментарий: str


def _fmt_pct(part: int, whole: int) -> str:
    if not whole:
        return "0/0"
    return f"{part}/{whole} ({part / whole:.0%})"


def run_audit(deals: pd.DataFrame, clients: pd.DataFrame) -> pd.DataFrame:
    """Возвращает отчёт по проверкам (по строке на проверку)."""
    checks: list[Check] = []
    n = len(deals)

    # --- 1. Фиксированный набор столбцов -----------------------------------
    missing = [c for c in C.DEAL_COLUMNS if c not in deals.columns]
    extra = [c for c in deals.columns
             if c not in C.DEAL_COLUMNS and c not in C.DERIVED_COLUMNS]
    if missing or extra:
        checks.append(Check(
            "STR-01", LEVEL_FAIL, "Фиксированный набор столбцов таблицы сделок", n,
            f"нет: {missing}; лишние: {extra}",
            "Состав атрибутов изменён — требуется согласование с РП (п. 4.2).",
        ))
    else:
        checks.append(Check(
            "STR-01", LEVEL_OK, "Фиксированный набор столбцов таблицы сделок", n,
            f"{len(deals.columns)} колонок — по спецификации", "",
        ))

    missing_c = [c for c in C.CLIENT_COLUMNS if c not in clients.columns]
    checks.append(Check(
        "STR-02",
        LEVEL_FAIL if missing_c else LEVEL_OK,
        "Фиксированный набор столбцов справочника клиентов", len(clients),
        f"нет: {missing_c}" if missing_c else f"{len(clients.columns)} колонок — по спецификации",
        "Структура справочника клиентов нарушена." if missing_c else "",
    ))

    # --- 2. Идентификаторы --------------------------------------------------
    bad_id = deals[~deals["ID монеты"].apply(is_uuid)]
    checks.append(Check(
        "ID-01", LEVEL_FAIL if len(bad_id) else LEVEL_OK,
        "ID монеты в формате UUID", n,
        _fmt_pct(len(bad_id), n),
        f"Строки: {list(bad_id.index)[:5]}" if len(bad_id) else "",
    ))

    dup = deals["ID монеты"].duplicated(keep=False)
    checks.append(Check(
        "ID-02", LEVEL_FAIL if dup.any() else LEVEL_OK,
        "Уникальность ID монеты", n, _fmt_pct(int(dup.sum()), n),
        "Есть повторяющиеся ID монет." if dup.any() else "",
    ))

    bad_client = deals[deals["ID клиента"].notna() & ~deals["ID клиента"].apply(
        lambda v: is_uuid(v) if v is not None else True
    )]
    unknown = deals[
        deals["ID клиента"].notna()
        & ~deals["ID клиента"].isin(clients["ID клиента"])
    ]
    lvl = LEVEL_FAIL if (len(bad_client) or len(unknown)) else LEVEL_OK
    checks.append(Check(
        "ID-03", lvl, "ID клиента — UUID и присутствует в справочнике", n,
        _fmt_pct(len(bad_client) + len(unknown), n),
        f"Не найдено в справочнике: {unknown['кому'].dropna().unique().tolist()}"
        if len(unknown) else "",
    ))

    unused = set(clients["ID клиента"]) - set(deals["ID клиента"].dropna())
    checks.append(Check(
        "ID-04", LEVEL_WARN if unused else LEVEL_OK,
        "Справочник клиентов актуален (все клиенты имеют сделки)", len(clients),
        f"без сделок: {len(unused)}",
        f"Клиенты без сделок: {sorted(unused)}" if unused else "",
    ))

    # --- 3. Обновляемость / консистентность статуса ------------------------
    bad_status = deals[~deals["СТАТУС"].isin(C.STATUSES)]
    checks.append(Check(
        "ST-01", LEVEL_FAIL if len(bad_status) else LEVEL_OK,
        "Значения СТАТУС из справочника (продана/доступна/резерв)", n,
        _fmt_pct(len(bad_status), n),
        f"Встречено: {sorted(set(bad_status['СТАТУС'].dropna()))}" if len(bad_status) else "",
    ))

    sold = deals[deals["СТАТУС"] == C.STATUS_SOLD]
    avail = deals[deals["СТАТУС"] == C.STATUS_AVAILABLE]
    sold_missing = sold[sold[["ID клиента", "ДАТА ПРОДАЖИ", "продажа"]].isna().any(axis=1)]
    avail_filled = avail[avail[["ID клиента", "ДАТА ПРОДАЖИ", "продажа"]].notna().any(axis=1)]
    lvl = LEVEL_FAIL if (len(sold_missing) or len(avail_filled)) else LEVEL_OK
    checks.append(Check(
        "ST-02", lvl, "Обновляемость: статус ↔ реквизиты сделки", n,
        f"«продана» без сделки: {len(sold_missing)}; «доступна» с реквизитами: {len(avail_filled)}",
        "Статус не соответствует наличию сделки." if lvl == LEVEL_FAIL else "",
    ))

    # --- 4. Обязательные поля ---------------------------------------------
    for i, col in enumerate(C.REQUIRED_ALWAYS, start=1):
        empty = int(deals[col].isna().sum()) if col in deals.columns else n
        checks.append(Check(
            f"REQ-{i:02d}", LEVEL_FAIL if empty else LEVEL_OK,
            f"Обязательное поле заполнено: {col}", n, _fmt_pct(empty, n),
            f"Пустых значений: {empty}" if empty else "",
        ))

    for i, col in enumerate(C.REQUIRED_SOLD, start=1):
        if col not in deals.columns:
            empty = len(sold)
        else:
            empty = int(sold[col].isna().sum())
        checks.append(Check(
            f"REQS-{i:02d}", LEVEL_FAIL if empty else LEVEL_OK,
            f"Обязательное поле в сделке: {col}", len(sold), _fmt_pct(empty, len(sold)),
            f"У {empty} проданных монет не заполнено «{col}» — "
            "расчёт KPI по этому полю невозможен." if empty else "",
        ))

    optional = avail[["ГП2", "Князь", "номинал", "описание", "R", "цена_рекомендации"]]
    empty_opt = optional.isna().sum()
    detail = ", ".join(f"{c}: {int(v)}" for c, v in empty_opt.items() if v)
    checks.append(Check(
        "REQA-01", LEVEL_WARN if empty_opt.sum() else LEVEL_OK,
        "Атрибуты доступных монет (кандидатов)", len(avail),
        f"пустых ячеек: {int(empty_opt.sum())} из {optional.size}",
        f"Не заполнено: {detail}. Цена и мотив восстанавливаются по аналогам "
        "(см. колонки цена_источник / атрибут_источник)." if empty_opt.sum() else "",
    ))

    # --- 5. Унифицированность ---------------------------------------------
    both = sold[sold["ДАТА ПОКУПКИ"].notna() & sold["ДАТА ПРОДАЖИ"].notna()]
    if len(both):
        bad_pair = int((both["ДАТА ПОКУПКИ"] > both["ДАТА ПРОДАЖИ"]).sum())
        checks.append(Check(
            "FMT-01", LEVEL_FAIL if bad_pair else LEVEL_OK,
            "Дата закупки ≤ даты продажи (формат д.м.г)", len(both),
            _fmt_pct(bad_pair, len(both)),
            "Закупка позже продажи — проверьте даты." if bad_pair else "",
        ))
    else:
        checks.append(Check(
            "FMT-01", LEVEL_WARN,
            "Дата закупки ≤ даты продажи (формат д.м.г)", 0, "0/0 пар дат",
            "Ни одна дата закупки не заполнена — проверить согласованность дат "
            "и рассчитать KPI «закупка → продажа» (п. 1 ТЗ, показатель Y) невозможно. "
            "Требование нарушено, см. проверку REQS-ДАТА ПОКУПКИ.",
        ))

    future = deals[deals["ДАТА ПРОДАЖИ"] > pd.Timestamp.today().normalize()]
    checks.append(Check(
        "FMT-02", LEVEL_WARN if len(future) else LEVEL_OK,
        "Дата продажи не в будущем", n, _fmt_pct(len(future), n),
        f"Даты: {[d.strftime('%d.%m.%y') for d in future['ДАТА ПРОДАЖИ']][:5]}"
        if len(future) else "",
    ))

    price_pos = int(((deals["вход"] <= 0) | (deals["продажа"] <= 0)).sum())
    checks.append(Check(
        "FMT-03", LEVEL_FAIL if price_pos else LEVEL_OK,
        "Цены положительные", n, _fmt_pct(price_pos, n),
        "Есть неположительные цены." if price_pos else "",
    ))

    margin_bad = sold[
        sold[["вход", "продажа", "маржа"]].notna().all(axis=1)
        & ((sold["продажа"] - sold["вход"] - sold["маржа"]).abs() > 1)
    ]
    checks.append(Check(
        "CALC-01", LEVEL_FAIL if len(margin_bad) else LEVEL_OK,
        "Проверка расчёта: маржа = продажа − вход", len(sold),
        _fmt_pct(len(margin_bad), len(sold)),
        f"Строки: {list(margin_bad.index)[:5]}" if len(margin_bad) else "",
    ))

    pct_bad = sold[
        sold[["вход", "маржа", "%"]].notna().all(axis=1)
        & (sold["вход"] > 0)
        & (((sold["маржа"] / sold["вход"]) - sold["%"]).abs() > 0.01)
    ]
    checks.append(Check(
        "CALC-02", LEVEL_FAIL if len(pct_bad) else LEVEL_OK,
        "Проверка расчёта: % = маржа / вход", len(sold), _fmt_pct(len(pct_bad), len(sold)),
        f"Строки: {list(pct_bad.index)[:5]}" if len(pct_bad) else "",
    ))

    r_bad = deals[deals["R"].notna() & ~deals["R"].between(C.R_MIN, C.R_MAX)]
    checks.append(Check(
        "DICT-01", LEVEL_WARN if len(r_bad) else LEVEL_OK,
        f"Редкость R в диапазоне {C.R_MIN}–{C.R_MAX}", n, _fmt_pct(len(r_bad), n),
        f"Значения: {sorted(r_bad['R'].dropna().unique().tolist())}" if len(r_bad) else "",
    ))

    nom = deals[deals["номинал"].notna()
                & ~deals["номинал"].str.lower().isin(C.NOMINALS)]
    checks.append(Check(
        "DICT-02", LEVEL_WARN if len(nom) else LEVEL_OK,
        "Номинал из справочника (денга/полуденга/пуло)", n, _fmt_pct(len(nom), n),
        f"Значения: {sorted(nom['номинал'].dropna().unique().tolist())}" if len(nom) else "",
    ))

    dup_gp2 = deals[deals["_gp2"].notna() & deals.duplicated(
        subset=["_gp2"], keep=False)]
    checks.append(Check(
        "DICT-03", LEVEL_WARN if len(dup_gp2) else LEVEL_OK,
        "Дубли номеров ГП2 (разные ID монет)", n, _fmt_pct(len(dup_gp2), n),
        "Проверьте, не одна ли это монета под двумя ID."
        if len(dup_gp2) else "",
    ))

    # --- 6. Актуальность каталога -----------------------------------------
    last = deals["ДАТА ПРОДАЖИ"].max()
    checks.append(Check(
        "ACT-01", LEVEL_OK, "Актуальность: последняя дата продажи", n,
        last.strftime("%d.%m.%Y") if pd.notna(last) else "нет данных",
        "",
    ))
    checks.append(Check(
        "ACT-02", LEVEL_OK if len(avail) else LEVEL_FAIL,
        "Каталог доступных монет непуст", len(avail),
        f"{len(avail)} доступно, {len(sold)} продано, {n} всего",
        "Каталог кандидатов пуст — рекомендации не сформировать."
        if not len(avail) else "",
    ))
    no_price = int(avail["цена_рекомендации"].isna().sum())
    checks.append(Check(
        "ACT-03", LEVEL_FAIL if no_price else LEVEL_OK,
        "У всех кандидатов есть цена (факт или оценка)", len(avail),
        _fmt_pct(no_price, len(avail)),
        "Для части монет нет даже аналогов для оценки цены." if no_price else "",
    ))

    return pd.DataFrame([c.__dict__ for c in checks])


def audit_summary(report: pd.DataFrame) -> str:
    counts = report["уровень"].value_counts().to_dict()
    return (
        f"Проверок: {len(report)} | OK: {counts.get(LEVEL_OK, 0)} | "
        f"WARN: {counts.get(LEVEL_WARN, 0)} | FAIL: {counts.get(LEVEL_FAIL, 0)}"
    )
