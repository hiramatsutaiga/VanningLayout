"""`python -m vanning_eval ...` での実行を可能にするエントリポイント。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
