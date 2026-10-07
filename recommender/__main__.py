"""CLI: ``python -m recommender``.

Основные сценарии::

    python -m recommender                        # расчёт по локальному файлу
    python -m recommender --google --dashboard   # то же + Excel-дашборд из Google Таблицы
    python -m recommender --google --dashboard "D:\\Дашборд.xlsx"
"""
from __future__ import annotations

import argparse

from . import config as C
from .pipeline import run_pipeline


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
                        help="перед расчётом скачать свежую выгрузку из Google Таблицы "
                             "(при недоступности сети берётся локальная копия)")
    parser.add_argument("--dashboard", nargs="?", const=C.DASHBOARD_PATH, default=None,
                        metavar="PATH",
                        help=f"собрать Excel-дашборд для пользователя таблицы "
                             f"(по умолчанию: {C.DASHBOARD_PATH})")
    args = parser.parse_args(argv)

    if args.dashboard:
        # Дашборд: выгрузка (если --google) → расчёт → сборка книги Excel
        from .dashboard import run_dashboard
        run_dashboard(input_path=args.input, out_path=args.dashboard,
                      use_google=args.google, top_k=args.top_k,
                      out_dir=args.out)
        return 0

    input_path = args.input
    if args.google:
        from .dashboard import refresh_source
        input_path, source_note = refresh_source(dest=args.input)
        print(f"Источник данных: {source_note}")

    run_pipeline(input_path=input_path, out_dir=args.out, top_k=args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
