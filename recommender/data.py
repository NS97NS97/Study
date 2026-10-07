"""Загрузка, нормализация и обогащение исходных таблиц.

Реализует требования п. 4.2 ТЗ:
  * обновляемость — таблица читается целиком из единого источника;
  * унифицированность — приведение типов, дат, регистра и справочников;
  * восстановление отсутствующих атрибутов кандидатов (см. ``impute_catalog``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

from . import config as C

# Латиница, визуально неотличимая от кириллицы в номерах ГП2 («1135A» vs «1135А»)
_LAT2CYR = str.maketrans(
    {"A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
     "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У"}
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_TOKEN_RE = re.compile(r"[^\wа-яёА-ЯЁ]+")
_STOPWORDS = {"и", "на", "в", "с", "со", "из", "под", "над", "к", "у", "по", "the"}


# ---------------------------------------------------------------------------
# Нормализация значений
# ---------------------------------------------------------------------------
def norm_gp2(value) -> str | None:
    """Нормализованный номер ГП2 (регистр + латиница→кириллица). None, если пусто."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == C.GP2_NEW_FLAG.lower():
        return None
    return text.upper().translate(_LAT2CYR)


def norm_text(value) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def key(value) -> str | None:
    """Ключ справочника: нижний регистр + удаление внешних пробелов."""
    text = norm_text(value)
    return text.lower() if text else None


def desc_core(value) -> str | None:
    """Ядро описания — мотив монеты до «/» («барс/подражание» → «барс»)."""
    text = norm_text(value)
    if not text:
        return None
    core = text.split("/")[0]
    return core.strip().lower() or None


def tokens(value) -> list[str]:
    """Слова описания для сравнения мотивов."""
    text = norm_text(value)
    if not text:
        return []
    return [
        t for t in _TOKEN_RE.split(text.lower())
        if len(t) > 2 and t not in _STOPWORDS
    ]


def is_uuid(value) -> bool:
    return bool(_UUID_RE.match(str(value).strip())) if value is not None else False


# ---------------------------------------------------------------------------
# Загрузка
# ---------------------------------------------------------------------------
def load_sheets(path: str | Path) -> dict[str, pd.DataFrame]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл данных не найден: {path}")
    xls = pd.ExcelFile(path)
    missing = [s for s in (C.SHEET_DEALS, C.SHEET_CLIENTS) if s not in xls.sheet_names]
    if missing:
        raise ValueError(
            f"В книге нет обязательных закладок {missing}; найдены: {xls.sheet_names}"
        )
    return {C.SHEET_DEALS: xls.parse(C.SHEET_DEALS),
            C.SHEET_CLIENTS: xls.parse(C.SHEET_CLIENTS)}


def prepare_deals(df: pd.DataFrame) -> pd.DataFrame:
    """Приведение таблицы сделок к единому формату."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # В ТЗ поле названо «ДАТА ПРОДАШИ», в выгрузке источника — «ДАТА ПРОДАЖИ».
    # Приводим оба написания к каноническому имени (п. 4.2 ТЗ:
    # унифицированность заполнения между таблицами).
    alias = "ДАТА " + "ПРОДА" + "ШИ"          # написание по ТЗ
    canonical = "ДАТА " + "ПРОДА" + "ЖИ"      # написание в источнике
    if alias in df.columns and canonical not in df.columns:
        df = df.rename(columns={alias: canonical})

    for col in ("ДАТА ПОКУПКИ", "ДАТА ПРОДАЖИ"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")

    for col in ("вход", "продажа", "маржа", "R", "%"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "СТАТУС" in df.columns:
        df["СТАТУС"] = df["СТАТУС"].astype("object").apply(
            lambda v: str(v).strip().lower() if norm_text(v) else None
        )

    for col in ("ГП2", "Княжество", "Князь", "номинал", "описание", "у кого", "кому"):
        if col in df.columns:
            df[col] = df[col].apply(norm_text)

    return df


def prepare_clients(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    for col in ("ID клиента", "Клиент"):
        if col in df.columns:
            df[col] = df[col].apply(norm_text)
    return df


# ---------------------------------------------------------------------------
# Обогащение каталога
# ---------------------------------------------------------------------------
def _median(series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.median()) if len(s) else float("nan")


def _mode(series):
    s = series.dropna()
    m = s.mode()
    return m.iloc[0] if len(m) else None


def _candidate_groups(coin: pd.Series, sold: pd.DataFrame) -> Iterator[tuple[str, pd.DataFrame]]:
    """Лестница аналогов: от точного ГП2 к всё более широкой группе."""
    gp2 = norm_gp2(coin.get("ГП2"))
    if gp2:
        yield f"ГП2 {gp2}", sold[sold["_gp2"] == gp2]

    kn, knyaz = key(coin.get("Княжество")), key(coin.get("Князь"))
    if kn and knyaz:
        yield "княжество+князь", sold[(sold["_kn"] == kn) & (sold["_knyaz"] == knyaz)]
    if kn:
        yield "княжество", sold[sold["_kn"] == kn]
    yield "весь каталог", sold


def estimate_price(coin: pd.Series, sold: pd.DataFrame) -> tuple[float, str, int]:
    """Оценка цены продажи монеты, у которой её нет в данных.

    Метод: медиана цен продажи по аналогам (лестница ``_candidate_groups``).
    Возвращает (цена, уровень аналогов, число аналогов).
    """
    for label, group in _candidate_groups(coin, sold):
        price = _median(group["продажа"]) if len(group) else float("nan")
        if not np.isnan(price):
            return price, label, len(group)
    return float("nan"), "нет данных", 0


def impute_attributes(coin: pd.Series, sold: pd.DataFrame) -> dict:
    """Восстановление номинала и редкости доступной монеты по аналогам."""
    out = {"номинал": norm_text(coin.get("номинал")), "R": coin.get("R"),
           "атрибут_источник": "факт"}
    have_nominal = out["номинал"] is not None
    have_r = not (out["R"] is None or (isinstance(out["R"], float) and np.isnan(out["R"])))
    if have_nominal and have_r:
        return out

    for label, group in _candidate_groups(coin, sold):
        if not len(group):
            continue
        if not have_nominal:
            mode = _mode(group["номинал"])
            if mode is not None:
                out["номинал"] = norm_text(mode)
                out["атрибут_источник"] = f"по аналогам ({label})"
        if not have_r:
            r = _median(group["R"])
            if not np.isnan(r):
                out["R"] = r
                if out["атрибут_источник"] == "факт":
                    out["атрибут_источник"] = f"по аналогам ({label})"
        if out["номинал"] is not None and not (
            out["R"] is None or (isinstance(out["R"], float) and np.isnan(out["R"]))
        ):
            break
    return out


def build_catalog(deals: pd.DataFrame, clients: pd.DataFrame) -> pd.DataFrame:
    """Единый каталог монет: факт по проданным + оценка по доступным.

    Добавляет колонки:
      цена_рекомендации — фактическая цена продажи или оценка по аналогам;
      цена_источник     — «факт» либо уровень аналогов;
      цена_аналогов     — сколько проданных монет легло в оценку;
      атрибут_источник  — откуда взяты номинал/R, если их не было.
    """
    cat = deals.copy()
    sold = cat[cat["СТАТУС"] == C.STATUS_SOLD].copy()
    sold["_gp2"] = sold["ГП2"].apply(norm_gp2)
    sold["_kn"] = sold["Княжество"].apply(key)
    sold["_knyaz"] = sold["Князь"].apply(key)

    # подключение имён клиентов (выхлоп для отчётов)
    cat = cat.merge(
        clients.rename(columns={"ID клиента": "ID клиента", "Клиент": "клиент_имя"}),
        on="ID клиента", how="left",
    )

    prices, sources, analogs, attr_src = [], [], [], []
    for _, coin in cat.iterrows():
        if coin["СТАТУС"] == C.STATUS_SOLD and not np.isnan(coin["продажа"]):
            prices.append(float(coin["продажа"]))
            sources.append("факт")
            analogs.append(1)
            attr_src.append("факт")
            continue

        price, level, n = estimate_price(coin, sold)
        imp = impute_attributes(coin, sold)
        prices.append(price)
        sources.append(level)
        analogs.append(n)
        attr_src.append(imp["атрибут_источник"])
        if norm_text(coin["номинал"]) is None:
            cat.at[coin.name, "номинал"] = imp["номинал"]
        r = coin["R"]
        if r is None or (isinstance(r, float) and np.isnan(r)):
            cat.at[coin.name, "R"] = imp["R"]

    cat["цена_рекомендации"] = prices
    cat["цена_источник"] = sources
    cat["цена_аналогов"] = analogs
    cat["атрибут_источник"] = attr_src
    cat["ядро_описания"] = cat["описание"].apply(desc_core)
    cat["_gp2"] = cat["ГП2"].apply(norm_gp2)
    cat["_kn"] = cat["Княжество"].apply(key)
    cat["_knyaz"] = cat["Князь"].apply(key)
    return cat
