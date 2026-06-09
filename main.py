import os
import sys

# Resolve the base directory for PATH and icon.
if getattr(sys, 'frozen', False):
    _exe_dir = os.path.dirname(sys.executable)
    os.environ["PATH"] = _exe_dir + os.pathsep + os.path.join(_exe_dir, "bin") + os.pathsep + os.environ["PATH"]
else:
    _exe_dir = os.path.dirname(os.path.abspath(__file__))
    os.environ["PATH"] = _exe_dir + os.pathsep + os.path.join(_exe_dir, "bin") + os.pathsep + os.environ["PATH"]

from PySide6.QtGui import QIcon             # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402


def main():
    from src.logger import get as _log
    log = _log("app")
    log.info("=== ReLive starting ===")

    app = QApplication(sys.argv)
    app.setApplicationName("ReLive")
    app.setWindowIcon(QIcon(os.path.join(_exe_dir, "Relive.ico")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
