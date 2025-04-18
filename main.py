import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from gui import ReminderApp

if __name__ == '__main__':
    # Enable high DPI scaling
    if hasattr(Qt, 'AA_EnableHighDpiScaling'):
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    if hasattr(Qt, 'AA_UseHighDpiPixmaps'):
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    ex = ReminderApp()
    ex.show()
    sys.exit(app.exec())
