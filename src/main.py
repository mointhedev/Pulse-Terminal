# main.py

import sys
from PySide6.QtWidgets import QApplication
from terminal import Terminal


if __name__ == "__main__":

    app = QApplication(sys.argv)  # 1. Create the app
    window = Terminal()        # 2. Create a window
    window.show()                  # 3. Show it
    sys.exit(app.exec())           # 4. Start the event loop






















