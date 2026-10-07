"""Профиль коллекционных предпочтений клиента.

Профиль строится из истории покупок (этап 2 панельных данных) и хранит:
  * доли покупок по каждому справочному атрибуту (княжество, князь, номинал,
    мотив описания, редкость) — это «интересы» (критерий 2);
  * частоты слов в описаниях — сходство по мотиву монеты;
  * бюджетный коридор по ценам покупок — критерий 3;
  * список купленных монет и их аналогов — проверка новизны (критерий 1).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as C
from .data import desc_core, key, norm_gp2, norm_text, tokens


def _share_counter(values: pd.Series) -> dict[str, float]:
    keys = [key(v) for v in values]
    keys = [k for k in keys if k]
    if not keys:
        return {}
    total = len(keys)
    counter = Counter(keys)
    return {k: v / total for k, v in counter.items()}


@dataclass
class ClientProfile:
    client_id: str
    name: str
    n_deals: int
    first_deal: pd.Timestamp | None
    last_deal: pd.Timestamp | None

    shares: dict[str, dict[str, float]] = field(default_factory=dict)
    token_counts: dict[str, int] = field(default_factory=dict)
    token_max: int = 0
    median_r: float | None = None

    price_median: float = float("nan")
    price_lo: float = float("nan")
    price_hi: float = float("nan")
    price_min: float = float("nan")
    price_max: float = float("nan")

    owned_gp2: set[str] = field(default_factory=set)
    owned_cores: set[str] = field(default_factory=set)
    owned_ids: set[str] = field(default_factory=set)
    top_tokens: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Критерий 2 — соответствие интересам
    # ------------------------------------------------------------------
    def attribute_score(self, attr: str, value) -> float | None:
        """Доля покупок клиента с таким значением, отнормированная на максимум.

        1.0 — монета попадает в самую любимую категорию клиента,
        0.0 — категория клиенту раньше не встречалась,
        None — атрибут неизвестен, вес перераспределяется между остальными.
        """
        k = key(value)
        if k is None:
            return None
        shares = self.shares.get(attr) or {}
        if not shares:
            return None
        top = max(shares.values())
        if top <= 0:
            return None
        return float(min(1.0, shares.get(k, 0.0) / top))

    def description_score(self, value) -> float | None:
        """Сходство по мотиву: лучшее совпадение слова описания."""
        toks = tokens(value)
        if not toks or not self.token_counts or self.token_max <= 0:
            return None
        best = max(self.token_counts.get(t, 0) for t in toks)
        return float(min(1.0, best / self.token_max)) if best else 0.0

    def rarity_score(self, value) -> float | None:
        """Близость редкости к медианной редкости покупок клиента."""
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        if self.median_r is None:
            return None
        span = max(C.R_MAX - C.R_MIN, 1)
        return float(max(0.0, 1.0 - abs(float(value) - self.median_r) / span))

    def profile_score(self, coin: pd.Series) -> tuple[float, dict[str, float]]:
        """Взвешенная оценка соответствия профилю + вклад каждого атрибута."""
        parts: dict[str, float] = {}
        weights = {
            "Княжество": self.attribute_score("Княжество", coin.get("Княжество")),
            "Князь": self.attribute_score("Князь", coin.get("Князь")),
            "номинал": self.attribute_score("номинал", coin.get("номинал")),
            "описание": self.description_score(coin.get("описание")),
            "R": self.rarity_score(coin.get("R")),
        }
        used_weight = 0.0
        total = 0.0
        for attr, score in weights.items():
            if score is None:
                continue
            w = C.PROFILE_WEIGHTS[attr]
            used_weight += w
            total += w * score
            parts[attr] = score
        if used_weight <= 0:
            return 0.0, {}
        return float(total / used_weight), parts

    # ------------------------------------------------------------------
    # Критерий 3 — соответствие бюджету
    # ------------------------------------------------------------------
    def budget_corridor(self) -> tuple[float, float]:
        return self.price_lo, self.price_hi

    def budget(self, price: float) -> tuple[bool, float, float]:
        """(в_коридоре, оценка 0..1, отклонение от медианы в %)."""
        if price is None or np.isnan(price) or np.isnan(self.price_median):
            return False, 0.0, float("nan")
        dev = (price - self.price_median) / self.price_median
        inside = bool(self.price_lo <= price <= self.price_hi)
        half = max((self.price_hi - self.price_lo) / 2.0, 1e-9)
        score = max(0.0, 1.0 - abs(price - self.price_median) / half)
        if not inside:
            # мягкое значение для выдачи с эскалацией бюджета
            score = max(0.0, 1.0 - abs(dev) / C.BUDGET_ESCALATION)
        return inside, float(score), float(dev)

    # ------------------------------------------------------------------
    # Критерий 1 — новизна
    # ------------------------------------------------------------------
    def novelty(self, coin: pd.Series) -> tuple[bool, str]:
        gp2 = norm_gp2(coin.get("ГП2"))
        if gp2 and gp2 in self.owned_gp2:
            return False, f"ГП2 {gp2} — такая монета уже есть в коллекции"
        core = desc_core(coin.get("описание"))
        if core and core in self.owned_cores:
            return False, f"мотив «{core}» уже встречался в ваших покупках"
        if gp2:
            return True, f"ГП2 {gp2} в коллекции отсутствует"
        if core:
            return True, f"мотив «{core}» в коллекции отсутствует"
        return True, "полный аналог в коллекции не найден"

    def summary(self) -> dict:
        return {
            "ID клиента": self.client_id,
            "Клиент": self.name,
            "Сделок": self.n_deals,
            "Первая сделка": self.first_deal,
            "Последняя сделка": self.last_deal,
            "Бюджет, низ": self.price_lo,
            "Бюджет, медиана": self.price_median,
            "Бюджет, верх": self.price_hi,
            "Диапазон цен": f"{self.price_min:,.0f} – {self.price_max:,.0f}"
            if not np.isnan(self.price_min) else "",
            "Любимое княжество": self.top_share("Княжество"),
            "Любимый князь": self.top_share("Князь"),
            "Любимый номинал": self.top_share("номинал"),
            "Частый мотив": ", ".join(self.top_tokens[:3]),
            "Медианная редкость R": self.median_r,
        }

    def top_share(self, attr: str) -> str:
        shares = self.shares.get(attr) or {}
        if not shares:
            return ""
        name, value = max(shares.items(), key=lambda kv: kv[1])
        return f"{name} ({value:.0%})"


def build_profile(history: pd.DataFrame, client_id: str, name: str) -> ClientProfile:
    """Профиль клиента по его истории покупок (``history`` — уже отфильтрована)."""
    hist = history.dropna(subset=["ДАТА ПРОДАЖИ"]).sort_values("ДАТА ПРОДАЖИ")
    if hist.empty:
        return ClientProfile(client_id=client_id, name=name, n_deals=0,
                             first_deal=None, last_deal=None)

    counter: Counter = Counter()
    for text in hist["описание"]:
        counter.update(tokens(text))
    token_max = max(counter.values()) if counter else 0
    top_tokens = [t for t, _ in counter.most_common(5)]

    prices = pd.to_numeric(hist["продажа"], errors="coerce").dropna()
    if len(prices):
        q25, med, q75 = (float(np.percentile(prices, p)) for p in (25, 50, 75))
        iqr = q75 - q25
        lo = min(q25 - C.BUDGET_IQR_K * iqr, med * (1 - C.BUDGET_MIN_HALF_WIDTH))
        hi = max(q75 + C.BUDGET_IQR_K * iqr, med * (1 + C.BUDGET_MIN_HALF_WIDTH))
        lo, hi = max(lo, 0.0), max(hi, lo)
        pmin, pmax = float(prices.min()), float(prices.max())
    else:
        q25 = med = lo = hi = pmin = pmax = float("nan")

    r_vals = pd.to_numeric(hist["R"], errors="coerce").dropna()
    median_r = float(r_vals.median()) if len(r_vals) else None

    cores = {c for c in hist["ядро_описания"].dropna().tolist() if c}
    gp2s = {g for g in hist["_gp2"].dropna().tolist() if g}

    return ClientProfile(
        client_id=client_id,
        name=name,
        n_deals=len(hist),
        first_deal=hist["ДАТА ПРОДАЖИ"].min(),
        last_deal=hist["ДАТА ПРОДАЖИ"].max(),
        shares={
            "Княжество": _share_counter(hist["Княжество"]),
            "Князь": _share_counter(hist["Князь"]),
            "номинал": _share_counter(hist["номинал"]),
        },
        token_counts=dict(counter),
        token_max=token_max,
        median_r=median_r,
        price_median=med, price_lo=lo, price_hi=hi,
        price_min=pmin, price_max=pmax,
        owned_gp2=gp2s, owned_cores=cores,
        owned_ids=set(hist["ID монеты"].dropna().tolist()),
        top_tokens=top_tokens,
    )


def build_all_profiles(catalog: pd.DataFrame, clients: pd.DataFrame) -> dict[str, ClientProfile]:
    """Профили всех клиентов по полной истории (для выдачи рекомендаций)."""
    sold = catalog[catalog["СТАТУС"] == C.STATUS_SOLD]
    profiles: dict[str, ClientProfile] = {}
    for _, row in clients.iterrows():
        cid, name = row["ID клиента"], row["Клиент"]
        history = sold[sold["ID клиента"] == cid]
        profiles[cid] = build_profile(history, cid, name)
    return profiles
