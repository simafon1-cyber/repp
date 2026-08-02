"""Точка входа программы Trader.

Запуск:  python main.py
или двойным щелчком по install\start-trader.bat
"""

import sys
from pathlib import Path

# Чтобы работали импорты core.* и ui.* при запуске из любой папки
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui.app import main  # noqa: E402

if __name__ == "__main__":
    main()
