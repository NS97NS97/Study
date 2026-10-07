"""Анализ чувствительности гиперпараметров (этап 4 ТЗ: «Валидация и настройка»).

Перебирает сетку ``config.SENSITIVITY_GRID`` по бюджетному коридору и
считает offline-метрики на той же исторической выборке. Таблица
чувствительности сохраняется в reports/sensitivity.csv и позволяет
обосновать выбранные значения, а не подгонять их «на глаз».
"""
from __future__ import annotations

import itertools

import pandas as pd

from . import config as C
from .evaluation import run_backtest

_MODEL_ROW = "Модель (3 критерия, строгие фильтры)"


def run_sensitivity(catalog: pd.DataFrame, clients: pd.DataFrame) -> pd.DataFrame:
    original = (C.BUDGET_IQR_K, C.BUDGET_MIN_HALF_WIDTH)
    rows: list[dict] = []
    try:
        for iqr_k, half_width in itertools.product(
            C.SENSITIVITY_GRID["BUDGET_IQR_K"],
            C.SENSITIVITY_GRID["BUDGET_MIN_HALF_WIDTH"],
        ):
            C.BUDGET_IQR_K, C.BUDGET_MIN_HALF_WIDTH = iqr_k, half_width
            _, metrics = run_backtest(catalog, clients)
            if metrics.empty:
                continue
            row = metrics[metrics["метод"] == _MODEL_ROW].iloc[0]
            rows.append({
                "BUDGET_IQR_K": iqr_k,
                "мин. полуширина коридора": half_width,
                "покрытие": round(float(row["покрытие (прошла фильтры)"]), 4),
                "HR@3": round(float(row["HR@3"]), 4),
                "HR@5": round(float(row["HR@5"]), 4),
                "HR@10": round(float(row["HR@10"]), 4),
                "MRR": round(float(row["MRR"]), 4),
                "выбрано": (iqr_k == original[0] and half_width == original[1]),
            })
    finally:
        C.BUDGET_IQR_K, C.BUDGET_MIN_HALF_WIDTH = original
    return pd.DataFrame(rows)
