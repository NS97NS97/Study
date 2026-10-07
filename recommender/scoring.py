"""Формирование ранжированного перечня монет-кандидатов.

Алгоритм (ТЗ, п. 2.1–2.2):
  1. Кандидаты — только монеты со статусом «доступна» (кроме backtest,
     где пул восстанавливается по историческим датам).
  2. Жёсткие фильтры «и»:
       новизна  — аналога (ГП2 / мотив) нет среди покупок клиента;
       бюджет   — цена внутри комфортного коридора клиента.
  3. Ранжирование по итоговой релевантности:
       score = w_нов·Новизна + w_проф·Профиль + w_бюдж·Бюджет.
  4. Если строгих кандидатов меньше K — бюджет расширяется до
     ±BUDGET_ESCALATION от медианы (эскалация), такое решение помечается
     в выдаче отдельно; критерий новизны никогда не ослабляется.
  5. Для каждой рекомендации формируется текстовое обоснование.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C
from .profile import ClientProfile

WEIGHTS = C.CRITERIA_WEIGHTS


def score_coin(profile: ClientProfile, coin: pd.Series) -> dict:
    """Полный расчёт по трём критериям для одной монеты."""
    nov_ok, nov_reason = profile.novelty(coin)
    bud_ok, bud_score, bud_dev = profile.budget(coin.get("цена_рекомендации"))
    prof_score, prof_parts = profile.profile_score(coin)

    total = (
        WEIGHTS["novelty"] * (1.0 if nov_ok else 0.0)
        + WEIGHTS["profile"] * prof_score
        + WEIGHTS["budget"] * bud_score
    )
    return {
        "новизна_ok": nov_ok,
        "новизна_причина": nov_reason,
        "бюджет_ok": bud_ok,
        "бюджет_оценка": bud_score,
        "бюджет_отклонение": bud_dev,
        "профиль": prof_score,
        "профиль_вклады": prof_parts,
        "итог": float(total),
    }


def _attr_label(attr: str, value) -> str:
    return {"Княжество": "княжество", "Князь": "князь",
            "номинал": "номинал", "R": "редкость"}.get(attr, attr)


def explain(profile: ClientProfile, coin: pd.Series, res: dict) -> str:
    """Человекочитаемое обоснование релевантности."""
    parts: list[str] = []

    # 1. Профиль — атрибуты с наибольшим вкладом
    weights = dict(C.PROFILE_WEIGHTS)
    ranked = sorted(
        ((a, s) for a, s in res["профиль_вклады"].items()),
        key=lambda kv: weights.get(kv[0], 0) * kv[1],
        reverse=True,
    )
    facts = []
    for attr, score in ranked[:3]:
        value = coin.get(attr)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            continue
        share = (profile.shares.get(attr) or {}).get(str(value).strip().lower())
        if attr == "описание":
            toks = [t for t in str(value).lower().split() if profile.token_counts.get(t)]
            if toks:
                best = max(toks, key=lambda t: profile.token_counts.get(t, 0))
                facts.append(f"мотив «{best}» вы уже покупали")
            continue
        if attr == "R":
            facts.append(f"R={int(value)} при вашей медиане "
                         f"{profile.median_r:g}" if profile.median_r else "")
            continue
        if share is not None:
            facts.append(f"{_attr_label(attr, value)} {value} — {share:.0%} ваших покупок")
    if facts:
        parts.append("Профиль: " + "; ".join(f for f in facts if f))

    # 2. Бюджет
    price = coin.get("цена_рекомендации")
    lo, hi = profile.budget_corridor()
    if price is not None and not (isinstance(price, float) and np.isnan(price)):
        if res["бюджет_ok"]:
            parts.append(
                f"Бюджет: ~{price:,.0f} ₽ внутри коридора "
                f"{lo:,.0f} – {hi:,.0f} ₽ (медиана ваших покупок "
                f"{profile.price_median:,.0f} ₽)"
            )
        else:
            dev = res["бюджет_отклонение"]
            parts.append(
                f"Бюджет: ~{price:,.0f} ₽ вне коридора {lo:,.0f} – {hi:,.0f} ₽ "
                f"({dev:+.0%} к вашей медиане) — требуется допуск"
            )

    # 3. Новизна
    parts.append("Новизна: " + res["новизна_причина"])

    return " | ".join(parts)


def recommend(
    profile: ClientProfile,
    catalog: pd.DataFrame,
    top_k: int = C.TOP_K,
    candidates: pd.DataFrame | None = None,
    strict_only: bool = False,
) -> pd.DataFrame:
    """Ранжированный список рекомендаций для клиента.

    Parameters
    ----------
    catalog : полный каталог (для фильтра «доступна» по умолчанию)
    candidates : готовый пул кандидатов (используется в offline-оценке)
    strict_only : не применять эскалацию бюджета
    """
    pool = candidates if candidates is not None else catalog
    pool = pool[pool["СТАТУС"] != C.STATUS_SOLD] if candidates is None else pool

    rows = []
    for _, coin in pool.iterrows():
        res = score_coin(profile, coin)
        if not res["новизна_ok"]:
            continue  # критерий 1 — жёсткий фильтр, не ослабляется
        rows.append({"монета": coin, "res": res})
    if not rows:
        return _empty_result()

    strict = [r for r in rows if r["res"]["бюджет_ok"]]

    if len(strict) >= top_k:
        chosen, escalated = strict, False
    elif strict_only:
        chosen, escalated = strict, False
    else:
        # эскалация: коридор расширяется, критерий помечается в выдаче
        rest = sorted(
            (r for r in rows if not r["res"]["бюджет_ok"]),
            key=lambda r: r["res"]["итог"], reverse=True,
        )
        chosen = strict + rest[: max(top_k - len(strict), 0)]
        escalated = bool(rest) and len(chosen) > len(strict)

    chosen = sorted(chosen, key=lambda r: r["res"]["итог"], reverse=True)[:top_k]

    out = []
    for rank, item in enumerate(chosen, start=1):
        coin, res = item["монета"], item["res"]
        out.append({
            "ранг": rank,
            "ID монеты": coin.get("ID монеты"),
            "ГП2": coin.get("ГП2"),
            "Княжество": coin.get("Княжество"),
            "Князь": coin.get("Князь"),
            "номинал": coin.get("номинал"),
            "описание": coin.get("описание"),
            "R": coin.get("R"),
            "цена, ₽": coin.get("цена_рекомендации"),
            "источник цены": coin.get("цена_источник"),
            "Новизна": 1.0,
            "Профиль": res["профиль"],
            "Бюджет": res["бюджет_оценка"],
            "Итог": res["итог"],
            "Бюджет коридор": "ок" if res["бюджет_ok"]
                              else f"эскалация {res['бюджет_отклонение']:+.0%}",
            "Обоснование": explain(profile, coin, res),
            "_escalated": (not res["бюджет_ok"]),
        })
    df = pd.DataFrame(out)
    df.attrs["escalated"] = escalated
    df.attrs["strict_pool"] = len(strict)
    df.attrs["candidate_pool"] = len(rows)
    return df


def _empty_result() -> pd.DataFrame:
    df = pd.DataFrame()
    df.attrs["escalated"] = False
    df.attrs["strict_pool"] = 0
    df.attrs["candidate_pool"] = 0
    return df


# ---------------------------------------------------------------------------
# Offline-оценка: расчёт для произвольного пула (backtest)
# ---------------------------------------------------------------------------
def rank_pool(profile: ClientProfile, pool: pd.DataFrame) -> pd.DataFrame:
    """Скоринг и ранжирование произвольного пула кандидатов (без обрезки).

    Колонки: ID монеты, итог, профиль, бюджет, бюджет_ok, новизна_ok, ранг.
    Строка с наихудшим приоритетом получает ранг = len(pool).
    """
    records = []
    for _, coin in pool.iterrows():
        res = score_coin(profile, coin)
        records.append({
            "ID монеты": coin.get("ID монеты"),
            "итог": res["итог"],
            "профиль": res["профиль"],
            "бюджет": res["бюджет_оценка"],
            "бюджет_ok": res["бюджет_ok"],
            "новизна_ok": res["новизна_ok"],
            "причина": res["новизна_причина"],
        })
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df = df.sort_values("итог", ascending=False).reset_index(drop=True)
    df["ранг"] = np.arange(1, len(df) + 1)
    return df
