"""CLI: ``python -m recommender [--input FILE] [--out DIR] [--top-k N]``."""
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
    args = parser.parse_args(argv)

    run_pipeline(input_path=args.input, out_dir=args.out, top_k=args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
