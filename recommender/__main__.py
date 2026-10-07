"""CLI: ``python -m recommender``.

Для пользователя таблицы запуск — один двойной клик по файлу
``Запустить.bat`` (эквивалент)::

    python -m recommender --google --dashboard --open

Он делает всё сразу: свежая выгрузка из Google Таблицы → расчёт →
файл с рекомендациями для ручной вставки во вкладку «Рекомендации» →
Excel-дашборд → открытие результатов и инструкция «что делать дальше».

Отдельные флаги для разработки::

    python -m recommender                       # расчёт по локальному файлу
    python -m recommender --dashboard           # + Excel-дашборд
    python -m recommender --google              # + свежая выгрузка
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import config as C
from .pipeline import run_pipeline


def _open_files(*paths) -> None:
    """Открыть готовые файлы в соответствующих программах (Windows)."""
    import os

    for path in paths:
        if not path:
            continue
        path = Path(path)
        if not path.exists():
            continue
        try:
            os.startfile(str(path.resolve()))  # noqa: S606 — локальный файл
        except OSError as exc:
            print(f"  Не удалось открыть {path.name}: {exc}")


def _print_next_steps(stats: dict, dashboard: Path | None, out_dir: Path) -> None:
    """Инструкция «что делать дальше» — по-русски, без терминов."""
    print("=" * 78)
    print("ГОТОВО. ЧТО ДЕЛАТЬ ДАЛЬШЕ")
    print("=" * 78)
    if stats["новых"]:
        print("ШАГ 1. Вставить новые рекомендации в Google Таблицу")
        print(f"  новых строк: {stats['новых']}   "
              f"(уже есть в таблице: {stats['уже_в_таблице']}, "
              f"строк во вкладке сейчас: {stats['строк_уже_в_вкладке']})")
        print("  • файл «Рекомендации_для_вставки.xlsx» → вкладка «Копировать»")
        print("    → клик на ячейку A1 → Ctrl+A → Ctrl+C "
              "(копируются 10 колонок — без ручных)")
        print("  • Google Таблица → вкладка «Рекомендации» → ячейка "
              f"A{stats['строка_вставки']} → Ctrl+V")
        print("    (прежние строки остаются на месте — новые идут в конец)")
        print("  • колонки «за сколько» и «предложено» не заполняются — "
              "это ручные")
        if stats.get("пустые_поля"):
            print("  • атрибуты скопированы из «Сделки» как есть; у части монет "
                  "там пусто:")
            print(f"    {', '.join(stats['пустые_поля'])} — оставлены пустыми "
                  f"(значения не выдумываем)")
    else:
        print("ШАГ 1. Новых рекомендаций нет — вставлять нечего.")
        print("       (все пары «монета + клиент» уже есть во вкладке "
              "«Рекомендации»)")

    if dashboard:
        print(f"\nШАГ 2. Дашборд с графиками и обоснованиями: {dashboard}")
    else:
        print(f"\nШАГ 2. Дашборд: python -m recommender --dashboard")
    print(f"\nШАГ 3. Все файлы отчётов: {out_dir}")
    print("\nСледующий пересчёт — двойной клик по файлу «Запустить.bat».")
    print("=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m recommender",
        description="Рекомендательная система: аудит данных, профили клиентов, "
                    "ранжированные рекомендации и offline-оценка.",
    )
    parser.add_argument("--input", default=C.EXCEL_PATH,
                        help=f"Путь к книге Excel с закладками «{C.SHEET_DEALS}» "
                             f"и «{C.SHEET_CLIENTS}» (по умолчанию: {C.EXCEL_PATH})")
    parser.add_argument("--out", default="reports", help="Каталог для отчётов")
    parser.add_argument("--top-k", type=int, default=C.TOP_K,
                        help="Сколько рекомендаций на клиента")
    parser.add_argument("--google", action="store_true",
                        help="перед расчётом скачать свежую выгрузку из Google "
                             "Таблицы (при недоступности сети берётся локальная "
                             "копия)")
    parser.add_argument("--dashboard", nargs="?", const=C.DASHBOARD_PATH,
                        default=None, metavar="PATH",
                        help=f"собрать Excel-дашборд (по умолчанию: "
                             f"{C.DASHBOARD_PATH})")
    parser.add_argument("--open", action="store_true", dest="open_results",
                        help="открыть готовые файлы (используется в "
                             "Запустить.bat)")
    args = parser.parse_args(argv)

    # 1. Источник данных -----------------------------------------------------
    source_path, source_note = Path(args.input), f"локальный файл {args.input}"
    if args.google:
        from .dashboard import refresh_source
        source_path, source_note = refresh_source(dest=args.input)
        print(f"Источник данных: {source_note}")

    # 2. Расчёт ---------------------------------------------------------------
    result = run_pipeline(input_path=source_path, out_dir=args.out,
                          top_k=args.top_k)
    out_dir = Path(args.out)

    # 3. Файл с рекомендациями для вставки во вкладку «Рекомендации» ---------
    from .sheet_export import build_for_sheet
    sheet_path, stats = build_for_sheet(result["рекомендации"], source_path,
                                        out_dir=out_dir)

    # 4. Дашборд --------------------------------------------------------------
    dashboard_path = None
    if args.dashboard:
        from .dashboard import build_dashboard
        dashboard_path = build_dashboard(result, args.dashboard,
                                         source_note=source_note)

    # 5. Что делать дальше ----------------------------------------------------
    _print_next_steps(stats, dashboard_path, out_dir)

    if args.open_results:
        _open_files(sheet_path, dashboard_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
