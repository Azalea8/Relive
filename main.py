import os
import sys

# Add project root to PATH so the bundled libmpv-2.dll is found.
_project_dir = os.path.dirname(os.path.abspath(__file__))
os.environ["PATH"] = _project_dir + os.pathsep + os.path.join(_project_dir, "bin") + os.pathsep + os.environ["PATH"]

from PyQt6.QtGui import QIcon             # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from src.ui.main_window import MainWindow  # noqa: E402


def main():
    from src.logger import get as _log
    log = _log("app")
    log.info("=== ReLive starting ===")

    app = QApplication(sys.argv)
    app.setApplicationName("ReLive")
    app.setWindowIcon(QIcon(os.path.join(_project_dir, "Relive.ico")))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
