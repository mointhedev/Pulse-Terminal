# terminal.py
import subprocess
import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QMainWindow, QTextEdit, QLineEdit
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QKeyEvent
from PySide6.QtCore import Qt, QEvent


class Terminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cwd = os.path.expanduser("~")
        self.history = []
        self.i = 0

        self.setWindowTitle("Pulse")
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #0d0d0d;")
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        layout.setContentsMargins(8, 8, 8, 8)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Menlo", 13))
        self.output.setStyleSheet("QTextEdit { background-color: #0d0d0d; color: #e0e0e0; border: none; }")
        # Bold format
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
                if self.history and self.i > 0:
                    self.i -= 1
                    self.input.setText(self.history[self.i])
                return True
            elif event.key() == Qt.Key.Key_Down:
                if self.i < len(self.history) - 1:
                    self.i += 1
                    self.input.setText(self.history[self.i])
                else:
                    self.i = len(self.history)  # reset past the end
                    self.input.clear()
                return True
        return super().eventFilter(source, event)


