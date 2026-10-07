"""Offline-тестирование качества рекомендаций на исторических данных.

Методика «temporal leave-one-out»:
  для каждой исторической сделки (клиент, монета, дата T)
    1) профиль клиента строится ТОЛЬКО по сделкам раньше T;
    2) пул кандидатов = монеты, не проданные до T (то есть на момент T
       теоретически ещё доступные), минус монеты уже в коллекции клиента;
    3) система ранжирует пул и оценивается, на каком месте оказалась
       реально купленная монета.

Метрики: покрытие (доля сделок, где цель прошла фильтры), HitRate@K,
MRR, NDCG@K. Бейслайны: скоринг без фильтров, популярность, «наугад»
(аналитическое матожидание).

Ограничения (зафиксировано в документации):
  * «ДАТА ПОКУПКИ» в источнике не заполнена → момент поступления монеты
    в каталог неизвестен, поэтому в пул включаются все монеты,
    не проданные на момент T;
  * цены кандидатов, не проданных на момент T, берутся из источника
    «факт» (это цена, по которой монета в итоге продалась) либо оценка.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .profile import build_profile
from .scoring import rank_pool


def _build_pool(catalog: pd.DataFrame, before: pd.Timestamp, owned: set[str]) -> pd.DataFrame:
    sold_before = set(
        catalog.loc[
            (catalog["СТАТУС"] == C.STATUS_SOLD)
            & catalog["ДАТА ПРОДАЖИ"].notna()
            & (catalog["ДАТА ПРОДАЖИ"] < before),
            "ID монеты",
        ]
    )
    pool = catalog[~catalog["ID монеты"].isin(sold_before)]
    pool = pool[~pool["ID монеты"].isin(owned)]
    return pool


def _popularity_map(catalog: pd.DataFrame) -> pd.Series:
    sold = catalog[catalog["СТАТУС"] == C.STATUS_SOLD]
    grp = sold.groupby(["Княжество", "Князь"], dropna=False)["ID монеты"].count()
    return grp


def _popularity_rank(pool: pd.DataFrame, pop: pd.Series) -> pd.DataFrame:
    scores = []
    for _, coin in pool.iterrows():
        scores.append(float(pop.get((coin.get("Княжество"), coin.get("Князь")), 0)))
    df = pool[["ID монеты"]].copy()
    df["популярность"] = scores
    df = df.sort_values(["популярность", "ID монеты"],
                        ascending=[False, True]).reset_index(drop=True)
    df["ранг"] = np.arange(1, len(df) + 1)
    return df


def _rank_of(ranked: pd.DataFrame, target_id: str) -> int | None:
    hit = ranked.loc[ranked["ID монеты"] == target_id, "ранг"]
    return int(hit.iloc[0]) if len(hit) else None


def _random_expectations(n: int, ks: tuple[int, ...]) -> dict:
    """Ожидаемые метрики случайного ранжирования (аналитика)."""
    out = {}
    for k in ks:
        out[f"HR@{k}"] = min(k, n) / n
        out[f"NDCG@{k}"] = sum(1 / np.log2(r + 1) for r in range(1, min(k, n) + 1)) / n
    out["MRR"] = sum(1 / r for r in range(1, n + 1)) / n
    return out


def run_backtest(catalog: pd.DataFrame, clients: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Возвращает (детали по сделкам, сводные метрики)."""
    sold = catalog[(catalog["СТАТУС"] == C.STATUS_SOLD)
                   & catalog["ДАТА ПРОДАЖИ"].notna()].copy()
    sold = sold.sort_values("ДАТА ПРОДАЖИ").reset_index(drop=True)
    names = dict(zip(clients["ID клиента"], clients["Клиент"]))
    pop = _popularity_map(catalog)

    details: list[dict] = []
    for _, deal in sold.iterrows():
        t = deal["ДАТА ПРОДАЖИ"]
        cid, target = deal["ID клиента"], deal["ID монеты"]
        if not cid:
            continue
        history = sold[(sold["ID клиента"] == cid) & (sold["ДАТА ПРОДАЖИ"] < t)]
        if history.empty:
            details.append({
                "дата": t, "клиент": names.get(cid, cid), "цель": target,
                "ГП2 цели": deal.get("ГП2"), "княжество цели": deal.get("Княжество"),
                "статус": "нет истории (первая покупка)",
            })
            continue

        profile = build_profile(history, cid, names.get(cid, cid))
        pool = _build_pool(catalog, t, profile.owned_ids)
        if target not in set(pool["ID монеты"]):
            details.append({
                "дата": t, "клиент": profile.name, "цель": target,
                "ГП2 цели": deal.get("ГП2"), "княжество цели": deal.get("Княжество"),
                "статус": "цель недоступна в пуле",
            })
            continue

        ranked_all = rank_pool(profile, pool)
        row = ranked_all[ranked_all["ID монеты"] == target].iloc[0]
        strict = ranked_all[ranked_all["новизна_ok"] & ranked_all["бюджет_ok"]].copy()
        rank_strict = _rank_of(strict, target)

        pop_ranked = _popularity_rank(pool, pop)
        pop_strict = pop_ranked[pop_ranked["ID монеты"].isin(
            ranked_all.loc[ranked_all["новизна_ok"] & ranked_all["бюджет_ok"], "ID монеты"]
        )].copy()
        pop_strict["ранг"] = np.arange(1, len(pop_strict) + 1)
        rand = _random_expectations(len(pool), C.EVAL_K)
        # ожидания «наугад» внутри строгого пула: если цель не прошла фильтры,
        # попадание невозможно — ожидание обнуляется (честное сравнение)
        if rank_strict is None:
            rand_strict = ({f"HR@{k}": 0.0 for k in C.EVAL_K}
                           | {f"NDCG@{k}": 0.0 for k in C.EVAL_K}
                           | {"MRR": 0.0})
        elif len(pop_strict):
            rand_strict = _random_expectations(len(pop_strict), C.EVAL_K)
        else:
            rand_strict = ({f"HR@{k}": 0.0 for k in C.EVAL_K}
                           | {f"NDCG@{k}": 0.0 for k in C.EVAL_K}
                           | {"MRR": 0.0})

        details.append({
            "дата": t,
            "клиент": profile.name,
            "цель": target,
            "ГП2 цели": deal.get("ГП2"),
            "княжество цели": deal.get("Княжество"),
            "номинал цели": deal.get("номинал"),
            "цена цели, ₽": deal.get("продажа"),
            "статус": "ok",
            "размер пула": len(pool),
            "строгий пул": len(pop_strict),
            "профиль цели": row["профиль"],
            "бюджет_ok": bool(row["бюджет_ok"]),
            "новизна_ok": bool(row["новизна_ok"]),
            "причина фильтра": "" if row["новизна_ok"] else row["причина"],
            "ранг (модель с фильтрами)": rank_strict,
            "ранг (модель без фильтров)": int(row["ранг"]),
            "ранг (популярность)": _rank_of(pop_ranked, target),
            "ранг (популярность с фильтрами)": _rank_of(pop_strict, target),
            **{f"рандом {k}": v for k, v in rand.items()},
            **{f"рандом-фильтр {k}": v for k, v in rand_strict.items()},
        })

    details_df = pd.DataFrame(details)
    evaluated = details_df[details_df["статус"] == "ok"]
    n_total = len(details_df)
    n_eval = len(evaluated)
    if not n_eval:
        return details_df, pd.DataFrame()

    def _agg(rank_col: str) -> dict:
        src = evaluated
        ranks = pd.to_numeric(src[rank_col], errors="coerce")
        out = {"оценено сделок": len(src)}
        for k in C.EVAL_K:
            # сделка, не прошедшая фильтры, считается промахом
            out[f"HR@{k}"] = float((ranks.fillna(np.inf) <= k).mean())
            out[f"NDCG@{k}"] = float(
                (1.0 / np.log2(ranks + 1))
                .where(ranks.notna() & (ranks <= k), 0.0)
                .mean()
            )
        reciprocal = (1.0 / ranks).where(ranks.notna(), 0.0)
        out["MRR"] = float(reciprocal.mean())
        out["покрытие (прошла фильтры)"] = float(ranks.notna().mean())
        return out

    rows = []

    m = _agg("ранг (модель с фильтрами)")
    m["метод"] = "Модель (3 критерия, строгие фильтры)"
    rows.append(m)

    m = _agg("ранг (модель без фильтров)")
    m["метод"] = "Модель (скоринг без фильтров)"
    rows.append(m)

    m = _agg("ранг (популярность)")
    m["метод"] = "Бейслайн: популярность категории"
    rows.append(m)

    m = _agg("ранг (популярность с фильтрами)")
    m["метод"] = "Бейслайн: популярность (те же фильтры)"
    rows.append(m)

    # «наугад» — математическое ожидание по размерам пулов
    rand_row = {"метод": "Бейслайн: наугад", "оценено сделок": n_eval,
                "покрытие (прошла фильтры)": 1.0}
    for c in [c for c in evaluated.columns if c.startswith("рандом ")]:
        rand_row[c.replace("рандом ", "")] = float(evaluated[c].mean())
    rows.append(rand_row)

    # «наугад» внутри строгого пула — нижняя граница качества ранжирования
    rand_f_row = {"метод": "Бейслайн: наугад (те же фильтры)",
                  "оценено сделок": n_eval,
                  "покрытие (прошла фильтры)": float(
                      pd.to_numeric(evaluated["ранг (модель с фильтрами)"],
                                    errors="coerce").notna().mean())}
    for c in [c for c in evaluated.columns if c.startswith("рандом-фильтр ")]:
        rand_f_row[c.replace("рандом-фильтр ", "")] = float(evaluated[c].mean())
    rows.append(rand_f_row)

    metrics = pd.DataFrame(rows)
    metrics.attrs["всего_сделок"] = n_total
    metrics.attrs["оценено"] = n_eval
    metrics.attrs["пропущено"] = n_total - n_eval
    return details_df, metrics
