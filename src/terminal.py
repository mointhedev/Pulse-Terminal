# terminal.py
import subprocess
import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QMainWindow, QTextEdit, QLineEdit, QListWidget
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QKeyEvent
from PySide6.QtCore import Qt, QEvent
import glob

class Terminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cwd = os.path.expanduser("~")
        self.history = []
        self.i = 0

        self.setWindowTitle("Pulse")
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #0d0d0d;")
        self.central = QWidget()
        self.setCentralWidget(self.central)
        self.dropdown = QListWidget(self.central)

        self.dropdown.setStyleSheet("""
            QListWidget {
                background-color: #1a1a1a;
                color: #00ff99;
                border: 1px solid #333;
                font-family: Menlo;
                font-size: 13px;
            }
            QListWidget::item:selected {
                background-color: #1a3d2e;
            }
            QScrollBar:vertical {
                background: #1a1a1a;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #1a3d2e;
                border-radius: 3px;
                min-height: 10px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00ff99;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        self.dropdown.hide()



        layout = QVBoxLayout(self.central)

        layout.setContentsMargins(8, 8, 8, 8)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Menlo", 13))
        self.output.setStyleSheet("""
            QTextEdit {
                background-color: #0d0d0d;
                color: #e0e0e0;
                border: none;
            }
            QScrollBar:vertical {
                background: #0d0d0d;
                width: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: #1a3d2e;
                border-radius: 3px;
                min-height: 10px;
            }
            QScrollBar::handle:vertical:hover {
                background: #00ff99;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        bold_format = QTextCharFormat()
        bold_font = QFont("Menlo", 13)
        bold_font.setBold(True)
        bold_format.setFont(bold_font)
        bold_format.setForeground(QColor("#00ff99"))

        cursor = self.output.textCursor()
        cursor.setCharFormat(bold_format)
        cursor.insertText("Pulse Terminal v0.1 ⚡\n")
        self.output.setTextCursor(cursor)

        default_format = QTextCharFormat()
        default_format.setFont(QFont("Menlo", 13))
        default_format.setForeground(QColor("#e0e0e0"))
        cursor.setCharFormat(default_format)
        self.output.setTextCursor(cursor)

        layout.addWidget(self.output)

        self.input = QLineEdit()
        self.input.setPlaceholderText(f"{self.cwd} $")
        self.input.setStyleSheet(
            "QLineEdit { background-color: #0d0d0d; color: #00ff99; border: none; border-top: 1px solid #222; padding: 8px; }")
        self.input.setFont(QFont("Menlo", 13))
        self.input.returnPressed.connect(self.run_command)
        self.input.textChanged.connect(self.update_dropdown)
        self.input.installEventFilter(self)

        layout.addWidget(self.input)


    def run_command(self):
        cmd = self.input.text().strip()

        if not cmd:
            return

        self.output.append(f"> {cmd}")
        self.history.append(cmd)
        self.i = len(self.history)
        # Handle cd separately
        if cmd.startswith("cd"):
            parts = cmd.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else os.path.expanduser("~")
            path = os.path.expanduser(path)

            if not os.path.isabs(path):
                path = os.path.join(self.cwd, path)
            try:
                os.chdir(path)
                self.cwd = os.getcwd()
                self.input.setPlaceholderText(f"{self.cwd} $")
            except FileNotFoundError:
                self.output.setTextColor(QColor("#ff4444"))
                self.output.append(f"{cmd} : No such file or directory")
                self.output.setTextColor(QColor("#e0e0e0"))

            self.input.clear()
            return

        if cmd == "clear":
            self.output.clear()
            self.input.clear()
            return

        result = subprocess.run(
            cmd,
            cwd=self.cwd,
            shell=True,
            capture_output=True,
            text=True
        )

        if result.stdout:
            self.output.append(result.stdout)
        if result.stderr:
            self.output.setTextColor(QColor("#ff4444"))
            self.output.append(result.stderr)
            self.output.setTextColor(QColor("#e0e0e0"))

        self.input.clear()


    def eventFilter(self, source, event):
        if source == self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                if self.dropdown.isVisible():
                    row = self.dropdown.currentRow()
                    self.dropdown.setCurrentRow(max(0, row - 1))
                    return True
                if self.history and self.i > 0:
                    self.i -= 1
                    self.input.setText(self.history[self.i])
                return True
            elif event.key() == Qt.Key.Key_Down:
                if self.dropdown.isVisible():
                    row = self.dropdown.currentRow()
                    self.dropdown.setCurrentRow(min(self.dropdown.count() - 1, row + 1))
                    return True
                if self.i < len(self.history) - 1:
                    self.i += 1
                    self.input.setText(self.history[self.i])
                else:
                    self.i = len(self.history)  # reset past the end
                    self.input.clear()
                return True
            elif event.key() == Qt.Key.Key_Return:
                if self.dropdown.isVisible() and self.dropdown.currentRow() >= 0:
                    self.select_completion()
                    return True
                return False  # let returnPressed signal fire normally → run_command

            elif event.key() == Qt.Key.Key_Tab:
                if self.dropdown.isVisible() and self.dropdown.currentRow() >= 0:
                    self.select_completion()
                    return True
                text = self.input.text()
                self.dropdown.clear()
                self.dropdown.hide()

                if not text.strip():
                    return True
                matches = glob.glob(os.path.join(self.cwd, text.split()[-1] + "*"))
                if matches:
                    if len(matches) == 1:
                        completion = matches[0]
                        completion = completion.split("/")[-1]
                        partial = text.split()[-1]
                        self.input.setText(text + completion[len(partial):])

                    else:

                        names = [m.split("/")[-1] for m in matches]
                        self.dropdown.addItems(names)

                        item_height = 19  # correct height per item
                        dropdown_h = min(len(names), 6) * item_height
                        self.dropdown.setFixedHeight(dropdown_h)  # set height FIRST
                        self.dropdown.setFixedWidth(self.input.width())  # set width to match input
                        input_rect = self.input.geometry()
                        dropdown_x = input_rect.x()
                        dropdown_y = input_rect.y() - dropdown_h - 1  # -1 for clean gap
                        self.dropdown.move(dropdown_x, dropdown_y)
                        self.dropdown.raise_()
                        self.dropdown.show()

                return True
            elif event.key() == Qt.Key.Key_Escape:
                if self.dropdown.isVisible():
                    self.dropdown.hide()
                    return True
        return super().eventFilter(source, event)

    def update_dropdown(self, text):
        self.dropdown.clear()

        if not text.strip() or len(text.split()) < 2:
            self.dropdown.clear()
            self.dropdown.hide()
            return

        parts = text.split()
        last_word = parts[-1] if parts else ""

        # Only show dropdown if last word looks like a path
        if not (last_word.startswith(("./", "/", "~", "../")) or "/" in last_word) and not self.dropdown.isVisible():
            self.dropdown.clear()
            self.dropdown.hide()
            return

        matches = glob.glob(os.path.join(self.cwd, text.split()[-1] + "*"))
        if matches:
            names = [m.split("/")[-1] for m in matches]
            if len(matches) == 1 and names[0] == text.split()[-1]:
                self.dropdown.hide()
                return

            self.dropdown.addItems(names)
            item_height = 19
            dropdown_h = min(len(names), 6) * item_height
            self.dropdown.setFixedHeight(dropdown_h)
            self.dropdown.setFixedWidth(self.input.width())
            input_rect = self.input.geometry()
            self.dropdown.move(input_rect.x(), input_rect.y() - dropdown_h - 1)
            self.dropdown.raise_()
            self.dropdown.show()

    def select_completion(self):
        item = self.dropdown.currentItem()
        if not item:
            return
        text = self.input.text()
        parts = text.split()
        last_word = parts[-1]
        # Replace only the last path component
        parent = "/".join(last_word.split("/")[:-1])
        if parent:
            parts[-1] = parent + "/" + item.text()
        else:
            parts[-1] = item.text()
        self.input.setText(" ".join(parts))
        self.dropdown.hide()


