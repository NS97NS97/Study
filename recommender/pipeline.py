"""Оркестрация: аудит → профили → рекомендации → offline-оценка → отчёты.

Запуск: ``python -m recommender`` (см. recommender/__main__).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config as C
from .data import build_catalog, load_sheets, prepare_clients, prepare_deals
from .evaluation import run_backtest
from .profile import build_all_profiles
from .scoring import recommend
from .sensitivity import run_sensitivity
from .validation import audit_summary, run_audit


def build_recommendations(catalog: pd.DataFrame, profiles: dict,
                          top_k: int = C.TOP_K) -> pd.DataFrame:
    """Рекомендации всех клиентов одной таблицей."""
    frames = []
    for cid, profile in profiles.items():
        rec = recommend(profile, catalog, top_k=top_k)
        if rec.empty:
            frames.append(pd.DataFrame([{
                "Клиент": profile.name, "ранг": None,
                "Обоснование": "Нет кандидатов, прошедших фильтр новизны",
            }]))
            continue
        rec.insert(0, "Клиент", profile.name)
        rec.insert(1, "ID клиента", cid)
        rec["пул кандидатов"] = rec.attrs.get("candidate_pool", 0)
        rec["прошло бюджет"] = rec.attrs.get("strict_pool", 0)
        frames.append(rec.drop(columns=["_escalated"], errors="ignore"))
    return pd.concat(frames, ignore_index=True)


def run_pipeline(
    input_path: str | Path = C.EXCEL_PATH,
    out_dir: str | Path = "reports",
    top_k: int = C.TOP_K,
    verbose: bool = True,
) -> dict:
    input_path, out_dir = Path(input_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (input_path.parent).mkdir(parents=True, exist_ok=True)

    # 1. Загрузка и нормализация -------------------------------------------
    sheets = load_sheets(input_path)
    deals = prepare_deals(sheets[C.SHEET_DEALS])
    clients = prepare_clients(sheets[C.SHEET_CLIENTS])

    # 2. Обогащение каталога (цена и атрибуты кандидатов по аналогам) ------
    catalog = build_catalog(deals, clients)
    catalog.to_csv(out_dir / "catalog_enriched.csv", index=False, encoding="utf-8-sig")

    # 3. Аудит качества данных --------------------------------------------
    audit = run_audit(catalog, clients)
    audit.to_csv(out_dir / "data_quality_report.csv", index=False, encoding="utf-8-sig")

    # 4. Профили клиентов ---------------------------------------------------
    profiles = build_all_profiles(catalog, clients)
    profiles_df = pd.DataFrame([p.summary() for p in profiles.values()])
    profiles_df.to_csv(out_dir / "client_profiles.csv", index=False, encoding="utf-8-sig")

    # 5. Рекомендации -------------------------------------------------------
    recs = build_recommendations(catalog, profiles, top_k=top_k)

    # 6. Offline-оценка на исторических данных ------------------------------
    details, metrics = run_backtest(catalog, clients)
    details.to_csv(out_dir / "backtest_details.csv", index=False, encoding="utf-8-sig")
    metrics.to_csv(out_dir / "metrics.csv", index=False, encoding="utf-8-sig")

    # 6а. Анализ чувствительности бюджетного коридора (этап 4 ТЗ) ----------
    sensitivity = run_sensitivity(catalog, clients)
    sensitivity.to_csv(out_dir / "sensitivity.csv", index=False, encoding="utf-8-sig")

    # 7. Сводная книга для заказчика ---------------------------------------
    with pd.ExcelWriter(out_dir / "recommendations.xlsx", engine="openpyxl") as xl:
        recs.to_excel(xl, sheet_name="Рекомендации", index=False)
        profiles_df.to_excel(xl, sheet_name="Профили клиентов", index=False)
        audit.to_excel(xl, sheet_name="Аудит данных", index=False)
        metrics.to_excel(xl, sheet_name="Метрики offline", index=False)
        sensitivity.to_excel(xl, sheet_name="Чувствительность", index=False)
        details.to_excel(xl, sheet_name="Тест на истории", index=False)

    result = {
        "каталог": catalog,
        "аудит": audit,
        "профили": profiles_df,
        "рекомендации": recs,
        "метрики": metrics,
        "чувствительность": sensitivity,
        "детали": details,
        "пути": {
            "отчёты": out_dir,
            "сводная": out_dir / "recommendations.xlsx",
        },
    }

    if verbose:
        _print_summary(result, top_k)
    return result


def _print_summary(result: dict, top_k: int) -> None:
    cat = result["каталог"]
    sold = int((cat["СТАТУС"] == C.STATUS_SOLD).sum())
    avail = int((cat["СТАТУС"] == C.STATUS_AVAILABLE).sum())

    print("=" * 78)
    print("РЕКОМЕНДАТЕЛЬНАЯ СИСТЕМА «МОНЕТЫ ДЛЯ КЛИЕНТА» — СВОДКА ЗАПУСКА")
    print("=" * 78)
    print(f"Каталог: {len(cat)} монет (продано {sold}, доступно {avail}); "
          f"клиентов: {len(result['профили'])}")
    print(f"\n{audit_summary(result['аудит'])}")
    fails = result["аудит"][result["аудит"]["уровень"] == "FAIL"]
    if len(fails):
        print("  Критичные замечания:")
        for _, row in fails.iterrows():
            print(f"   - [{row['код']}] {row['проверка']}: {row['значение']}")

    print("\nПРОФИЛИ КЛИЕНТОВ")
    cols = ["Клиент", "Сделок", "Любимое княжество", "Любимый номинал",
            "Бюджет, медиана"]
    print(result["профили"][cols].to_string(index=False,
          formatters={"Бюджет, медиана": lambda v: f"{v:,.0f} ₽"}))

    print(f"\nРЕКОМЕНДАЦИИ (топ-{top_k} на клиента)")
    show = ["Клиент", "ранг", "ГП2", "Княжество", "Князь", "номинал",
            "цена, ₽", "Итог", "Бюджет коридор"]
    fmt = {"цена, ₽": lambda v: f"{v:,.0f}" if pd.notna(v) else "",
           "Итог": lambda v: f"{v:.3f}"}
    print(result["рекомендации"][show].to_string(index=False, formatters=fmt))

    print("\nКАЧЕСТВО НА ИСТОРИЧЕСКИХ ДАННЫХ (temporal leave-one-out)")
    print(result["метрики"].to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    sens = result.get("чувствительность")
    if sens is not None and len(sens):
        chosen = sens[sens["выбрано"]]
        print("\nЧУВСТВИТЕЛЬНОСТЬ БЮДЖЕТНОГО КОРИДОРА (выбранная строка отмечена)")
        print(sens.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
        if len(chosen):
            row = chosen.iloc[0]
            print(f"  -> принято: IQR×{row['BUDGET_IQR_K']:g}, "
                  f"мин. полуширина {row['мин. полуширина коридора']:.0%} "
                  f"(покрытие {row['покрытие']:.1%}, HR@5 {row['HR@5']:.3f})")

    print("\nФайлы отчётов:", result["пути"]["отчёты"])
    print("=" * 78)
