# terminal.py
import subprocess
import os
import re

from PySide6.QtWidgets import QWidget, QVBoxLayout, QMainWindow, QTextEdit, QLineEdit, QListWidget, QDialog, QLabel, QPushButton, QHBoxLayout, QLineEdit as QLE
from PySide6.QtGui import QFont, QColor, QTextCharFormat, QKeyEvent
from PySide6.QtCore import Qt, QEvent
import glob

from pulse_overlay import PulseOverlay
from command_thread import CommandThread
from ssh_manager import SSHThread, UploadThread, DownloadThread, get_connection, save_connection

CMD_COLOR    = "#00ff99"  # input commands > ls
STATUS_COLOR = "#5fba8a"  # info/status messages
SUCCESS_COLOR = "#00ff99" # ✓ success
ERROR_COLOR  = "#ff4444"  # errors


class ConflictDialog(QDialog):
    def __init__(self, filename, parent=None):
        super().__init__(parent)
        self.action = None
        self.new_name = None
        self.setWindowTitle("File Conflict")
        self.setStyleSheet("background-color: #1a1a1a; color: #e0e0e0; font-family: Menlo; font-size: 13px;")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self._label = QLabel(f'"{filename}" already exists on the server.\nWhat would you like to do?')
        layout.addWidget(self._label)

        self._name_input = QLE(filename)
        self._name_input.setStyleSheet("background-color: #0d0d0d; color: #00ff99; border: 1px solid #333; padding: 4px;")
        self._name_input.hide()
        self._name_input.returnPressed.connect(self._confirm_rename)
        layout.addWidget(self._name_input)

        btn_row = QHBoxLayout()
        self._rename_btn = QPushButton("Rename")
        for btn, action in [(QPushButton("Overwrite"), "overwrite"), (self._rename_btn, "rename"), (QPushButton("Skip"), "skip")]:
            btn.setStyleSheet("QPushButton { background-color: #1a3d2e; color: #00ff99; border: none; padding: 6px 14px; } QPushButton:hover { background-color: #00ff99; color: #0d0d0d; }")
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

        # Wire buttons individually
        layout.itemAt(2).layout().itemAt(0).widget().clicked.connect(lambda: self._on_action("overwrite"))
        self._rename_btn.clicked.connect(self._toggle_rename)
        layout.itemAt(2).layout().itemAt(2).widget().clicked.connect(lambda: self._on_action("skip"))

    def _toggle_rename(self):
        if self._name_input.isHidden():
            self._name_input.show()
            self._name_input.setFocus()
            self._name_input.selectAll()
            self._rename_btn.setText("Confirm Rename")
        else:
            self._confirm_rename()

    def _confirm_rename(self):
        new_name = self._name_input.text().strip()
        if not new_name:
            return
        self.action = "rename"
        self.new_name = new_name
        self.accept()

    def _on_action(self, action):
        self.action = action
        self.accept()


class Terminal(QMainWindow):
    def __init__(self, screen_session=None, ssh_info=None):
        super().__init__()
        self.cwd = os.path.expanduser("~")
        self.history = []
        self.i = 0
        self.thread = None
        self.ssh_thread = None
        self.ssh_info = None
        self._pending_save = None
        self._collecting_tab = False
        self._ssh_tab_results = []
        self._ssh_tab_partial = ""
        self._ssh_tab_text = ""
        self._collecting_pwd = False
        self._pwd_result = ""
        self._ls_mode = False
        self._ssh_bg = "#080d1f"
        self._detecting_distro = False
        self._distro_lines = []
        self._password_mode = False
        self._screen_session = screen_session
        self._spawn_ssh_info = ssh_info
        self._tab_just_fired = False

        if screen_session:
            self.setWindowTitle(f"screen: {screen_session}")
        else:
            self.setWindowTitle("Pulse")
        self.setMinimumSize(400, 300)
        self.setAcceptDrops(True)
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
        self.overlay = PulseOverlay(self.central, self._do_clear)
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
        if not screen_session:
            cursor.setCharFormat(bold_format)
            cursor.insertText("Pulse Terminal v1.0 ⚡\n")
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

        # Auto-connect and run screen command if spawned as a screen window
        if self._spawn_ssh_info and self._screen_session:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(300, self._auto_connect_screen)

    def run_command(self):
        cmd = self.input.text().strip()

        if not cmd:
            # If pending save and user pressed enter to skip
            if self.ssh_info and hasattr(self, '_pending_save') and self._pending_save:
                self._pending_save = None
                self.input.clear()
            return

        if self.thread and self.thread.isRunning():
            text = self.input.text()
            if not self._password_mode:
                self.output.setTextColor(QColor(CMD_COLOR))
                self.output.append(f"> {text}")
                self.output.setTextColor(QColor("#e0e0e0"))
            else:
                # Show masked password as *** 
                self.output.setTextColor(QColor(STATUS_COLOR))
                self.output.append(f"> {'*' * len(text)}")
                self.output.setTextColor(QColor("#e0e0e0"))
                self._password_mode = False
                self.input.setEchoMode(QLineEdit.EchoMode.Normal)
                self.input.setPlaceholderText("input...")
            self.thread.send_input(text)
            self.input.clear()
            return

        # If connected via SSH, handle save prompt or route to remote
        if self.ssh_info:
            if hasattr(self, '_pending_save') and self._pending_save:
                if cmd.startswith("save "):
                    nickname = cmd.split(maxsplit=1)[1].strip()
                    save_connection(nickname, self._pending_save["host"], self._pending_save["user"])
                    self._pending_save = None
                    self.output.setTextColor(QColor(STATUS_COLOR))
                    self.output.append(f"Saved as '{nickname}'")
                    self.output.setTextColor(QColor("#e0e0e0"))
                    self.output.append("")
                    self.input.clear()
                    if self.ssh_thread:
                        self.ssh_thread._paused = False  # resume output
                    return
                else:
                    self._pending_save = None
                    if self.ssh_thread:
                        self.ssh_thread._paused = False  # resume output

            self.output.setTextColor(QColor("#00ff99"))
            self.output.append(f"\n> {cmd}")
            self.output.setTextColor(QColor("#e0e0e0"))
            self.history.append(cmd)
            self.i = len(self.history)
            self.input.clear()
            self.handle_ssh_command(cmd)
            return

        self.output.setTextColor(QColor("#00ff99"))
        self.output.append(f"\n> {cmd}")
        self.output.setTextColor(QColor("#e0e0e0"))
        self.history.append(cmd)
        self.i = len(self.history)
        # Handle ssh-list
        if cmd == "ssh-list":
            from ssh_manager import load_connections
            connections = load_connections()
            if not connections:
                self.output.append("No saved connections.")
            else:
                self.output.setTextColor(QColor(STATUS_COLOR))
                self.output.append("Saved connections:")
                self.output.setTextColor(QColor("#e0e0e0"))
                for nickname, info in connections.items():
                    port = info.get("port", 22)
                    port_str = f":{port}" if port != 22 else ""
                    self.output.append(f"  {nickname}  →  {info['user']}@{info['host']}{port_str}")
            self.output.append("")
            self.input.clear()
            return

        # Handle ssh-delete
        if cmd.startswith("ssh-delete "):
            nickname = cmd.split(maxsplit=1)[1].strip()
            from ssh_manager import delete_connection
            delete_connection(nickname)
            self.output.append(f"Deleted connection '{nickname}'")
            self.output.append("")
            self.input.clear()
            return

        # Handle ssh
        if cmd.startswith("ssh ") or cmd == "ssh":
            self.input.clear()
            if not self.try_parse_ssh(cmd):
                self.handle_output("Usage: ssh user@host [-p port] or ssh <nickname>", True)
            return

        # Handle cd separately
        if cmd.startswith("cd"):
            parts = cmd.split(maxsplit=1)
            path = parts[1] if len(parts) > 1 else os.path.expanduser("~")
            path = os.path.expanduser(path.strip())

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
            self.overlay.play()
            self.input.clear()
            return

        self.thread = CommandThread(cmd, self.cwd)
        self.thread.output_ready.connect(self.handle_output)
        self.thread.password_prompt.connect(self._on_password_prompt)
        self.thread.finished.connect(self.on_command_finished)
        self.thread.start()
        self.input.clear()
        self.input.setPlaceholderText("input...")

    def handle_ssh_command(self, cmd):
        if cmd == "exit":
            self.ssh_thread.disconnect()
            self.ssh_thread = None
            self.ssh_info = None
            self.input.setPlaceholderText(f"{self.cwd} $")
            self.output.append("Disconnected.")
            self.output.append("")
            return

        # Handle screen commands
        if cmd.startswith("screen"):
            self._handle_screen_command(cmd)
            return

        if cmd == "upload":
            self._open_file_picker()
            return

        if cmd == "upload-dir":
            self._open_dir_picker()
            return

        if cmd.startswith("download "):
            target = cmd.split(maxsplit=1)[1].strip().strip('"\'')
            # Build full remote path
            if not target.startswith("/"):
                remote_cwd = getattr(self, '_remote_cwd', '/root')
                target = remote_cwd.rstrip("/") + "/" + target
            self._start_download(target)
            return

        # Intercept ls to use ls -p for dir detection
        if cmd == "ls" or cmd.startswith("ls "):
            cmd = cmd.replace("ls", "ls -1p", 1)
            self._ls_mode = True
        else:
            self._ls_mode = False

        # Quote the path for cd so spaces don't break it on the server
        if cmd.startswith("cd "):
            path = cmd[3:].strip()
            # Escape any single quotes in the path, then wrap in single quotes
            safe_path = path.replace("'", "'\\''")
            self.ssh_thread.send_command(f"cd '{safe_path}'")
        else:
            self.ssh_thread.send_command(cmd)

        # After cd, send pwd to update the placeholder
        if cmd.startswith("cd"):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, self._update_ssh_prompt)

    def _update_ssh_prompt(self):
        if not self.ssh_thread or not self.ssh_info:
            return
        self._collecting_pwd = True
        self._pwd_result = ""
        self.ssh_thread.output_ready.disconnect(self.handle_output)
        self.ssh_thread.output_ready.connect(self._collect_pwd)
        self.ssh_thread.send_command("pwd")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, self._finish_pwd)

    def _collect_pwd(self, text, is_error):
        if self._collecting_pwd and text.strip().startswith("/"):
            self._pwd_result = text.strip()

    def _finish_pwd(self):
        self._collecting_pwd = False
        self.ssh_thread.output_ready.disconnect(self._collect_pwd)
        self.ssh_thread.output_ready.connect(self.handle_output)
        if self._pwd_result:
            self._remote_cwd = self._pwd_result  # track for file uploads
            user = self.ssh_info["user"]
            host = self.ssh_info["host"]
            # Trim to user/lastfolder or root/lastfolder
            parts = self._pwd_result.split("/")
            short_path = "/".join(parts[-2:]) if len(parts) > 2 else self._pwd_result
            self.input.setPlaceholderText(f"{short_path} $")

    def connect_ssh(self, host, user, port=22, key_path=None, password=None, from_saved=False):
        self.ssh_thread = SSHThread(host, user, port, key_path, password, screen_mode=bool(self._screen_session))
        self.ssh_thread.output_ready.connect(self.handle_output)
        self.ssh_thread.error.connect(lambda e: self.handle_output(f"SSH error: {e}", True))
        self.ssh_thread.connected.connect(lambda fs=from_saved: self.on_ssh_connected(host, user, fs))
        self.ssh_thread.finished.connect(self.on_ssh_disconnected)
        self.ssh_thread.start()

    def _auto_connect_screen(self):
        """Called after window opens — auto SSH and launch/attach screen session."""
        info = self._spawn_ssh_info
        self.connect_ssh(
            info["host"], info["user"],
            info.get("port", 22),
            info.get("key_path"),
            info.get("password"),
            from_saved=True
        )

    def _distro_color(self, distro_id):
        # Each color = distro's native terminal color, darkened + faint Pulse green tint
        colors = {
            "ubuntu":   "#130a10",  # dark purple + green tint
            "debian":   "#0a0a12",  # dark indigo + green tint
            "centos":   "#0f0a0a",  # dark red + green tint
            "rhel":     "#0f0a0a",
            "fedora":   "#090a10",  # dark navy + green tint
            "alpine":   "#090f0d",  # dark green tint (closest to Pulse)
            "arch":     "#090a10",  # dark slate + green tint
        }
        return colors.get(distro_id.lower().strip('"'), "#090d0f")  # fallback: near-black green tint

    def _apply_local_bg(self):
        self._ssh_bg = "#0d0d0d"
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
        self.input.setStyleSheet(
            "QLineEdit { background-color: #0d0d0d; color: #00ff99; border: none; border-top: 1px solid #222; padding: 8px; }"
        )
        self.central.setStyleSheet("background-color: #0d0d0d;")

    def _apply_ssh_bg(self, color):
        self._ssh_bg = color
        self.output.setStyleSheet(f"""
            QTextEdit {{
                background-color: {color};
                color: #e0e0e0;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {color};
                width: 6px;
                border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: #1a3d2e;
                border-radius: 3px;
                min-height: 10px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #00ff99;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        self.input.setStyleSheet(
            f"QLineEdit {{ background-color: {color}; color: #00ff99; border: none; border-top: 1px solid #222; padding: 8px; }}"
        )
        self.central.setStyleSheet(f"background-color: {color};")

    def on_ssh_connected(self, host, user, from_saved=False):
        self.ssh_info = {"host": host, "user": user, "port": self.ssh_thread.port if hasattr(self.ssh_thread, 'port') else 22}
        self._ssh_bg = "#0d0d0d"
        self._remote_cwd = f"/home/{user}" if user != "root" else "/root"
        self.input.setPlaceholderText(f"{user}@{host} $")
        # Unpause first so cat /etc/os-release output can flow
        if self.ssh_thread:
            self.ssh_thread._paused = False
        # Detect distro
        self._detecting_distro = True
        self._distro_lines = []
        self.ssh_thread.output_ready.disconnect(self.handle_output)
        self.ssh_thread.output_ready.connect(self._collect_distro)
        self.ssh_thread.send_command("cat /etc/os-release 2>/dev/null")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1000, lambda: self._finish_distro_detect(host, user, from_saved))

    def _collect_distro(self, text, is_error):
        if self._detecting_distro:
            self._distro_lines.append(text)

    def _finish_distro_detect(self, host, user, from_saved):
        self._detecting_distro = False
        self.ssh_thread.output_ready.disconnect(self._collect_distro)
        self.ssh_thread.output_ready.connect(self.handle_output)

        # Parse ID= from os-release
        distro_id = "unknown"
        for line in self._distro_lines:
            for part in line.splitlines():
                part = part.strip()
                if part.startswith("ID="):
                    distro_id = part.split("=", 1)[1].strip().strip('"')
                    break

        self._apply_ssh_bg(self._distro_color(distro_id))

        self.output.setTextColor(QColor(SUCCESS_COLOR))
        if not self._screen_session:
            self.output.append(f"Connected to {user}@{host} ({distro_id})")
        self.output.setTextColor(QColor("#e0e0e0"))

        if not from_saved and not self._screen_session:
            self.output.append("Save this connection? Type 'save <nickname>' or press Enter to skip.")
            self.output.append("")
            self._pending_save = {"host": host, "user": user}
        else:
            self._pending_save = None

        # If this window was spawned for a screen session, send the screen command now
        if hasattr(self, '_screen_cmd_pending') and self._screen_cmd_pending:
            session = self._screen_cmd_pending
            self._screen_cmd_pending = None
            from PySide6.QtCore import QTimer
            QTimer.singleShot(500, lambda: self._send_screen_attach(session))

    def _handle_screen_command(self, cmd):
        parts = cmd.split(None, 2)
        # screen list → just run screen -ls in current window
        if len(parts) == 1 or parts[1] in ("list", "-ls", "ls"):
            self.ssh_thread.send_command("screen -ls")
            return

        action = parts[1]   # start | attach | detach | kill
        name = parts[2].strip() if len(parts) > 2 else "main"

        if action in ("start", "new"):
            self._spawn_screen_window(name, "start")
        elif action in ("attach", "resume", "-r"):
            self._spawn_screen_window(name, "attach")
        elif action in ("detach", "-d"):
            self.ssh_thread.send_command(f"screen -d {name}")
        elif action in ("kill", "quit"):
            self.ssh_thread.send_command(f"screen -X -S {name} quit")
        else:
            # Unknown — pass through as-is
            self.ssh_thread.send_command(cmd)

    def _spawn_screen_window(self, name, action):
        """Open a new Pulse window for the given screen session."""
        from PySide6.QtWidgets import QApplication
        info = self.ssh_info.copy()
        session_spec = f"{action} {name}"
        win = Terminal(screen_session=f"{name}", ssh_info=info)
        win._screen_cmd_pending = session_spec
        win.resize(self.size())
        win.show()
        # Keep reference so it doesn't get garbage collected
        if not hasattr(QApplication.instance(), '_pulse_windows'):
            QApplication.instance()._pulse_windows = []
        QApplication.instance()._pulse_windows.append(win)

    def _send_screen_attach(self, session_spec):
        """session_spec is 'start <name>' or 'attach <name>'"""
        parts = session_spec.split(None, 1)
        action = parts[0]  # 'start' or 'attach'
        name = parts[1] if len(parts) > 1 else "main"
        if action == "start":
            cmd = f"screen -S {name}"
        else:
            cmd = f"screen -d -r {name}"  # force detach existing, then reattach
        self.output.setTextColor(QColor(CMD_COLOR))
        if not self._screen_session:
            self.output.append(f"\n> {cmd}")
        self.output.setTextColor(QColor("#e0e0e0"))
        self.ssh_thread.send_command(cmd)

    def on_ssh_disconnected(self):
        if self.ssh_info:
            self.output.append("")
            self.output.append("Connection closed.")
            self.output.append("")
        if self.ssh_thread:
            self.ssh_thread.disconnect()
            self.ssh_thread = None
        self.ssh_info = None
        self._pending_save = None
        self._ls_mode = False
        self._remote_cwd = None
        self._apply_local_bg()
        self.input.setPlaceholderText(f"{self.cwd} $")

    def _show_connecting(self, host, user):
        if self._screen_session:
            return
        self.output.setTextColor(QColor(STATUS_COLOR))
        self.output.append(f"\nConnecting to {user}@{host}...")
        self.output.setTextColor(QColor("#e0e0e0"))

    def try_parse_ssh(self, cmd):
        # ssh user@host or ssh user@host -p port
        parts = cmd.split()
        if parts[0] != "ssh":
            return False

        target = parts[1] if len(parts) > 1 else None
        if not target:
            return False

        # Check if it's a saved nickname
        conn = get_connection(target)
        if conn:
            self._show_connecting(conn["host"], conn["user"])
            self.connect_ssh(conn["host"], conn["user"], conn.get("port", 22),
                           conn.get("key_path"), conn.get("password"), from_saved=True)
            return True

        # Parse user@host
        if "@" in target:
            user, host = target.split("@", 1)
            port = 22
            for i, p in enumerate(parts):
                if p == "-p" and i + 1 < len(parts):
                    port = int(parts[i + 1])
            self._show_connecting(host, user)
            self.connect_ssh(host, user, port)
            return True

        return False

    def handle_output(self, text, is_error):
        scrollbar = self.output.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 10

        if is_error:
            self.output.setTextColor(QColor("#ff4444"))
            self.output.append(text)
            self.output.setTextColor(QColor("#e0e0e0"))
        elif self._ls_mode and self.ssh_info:
            self._append_ls_line(text)
        else:
            self.output.append(text)

        # Trim old lines if document gets too large (keep last 5000 lines)
        doc = self.output.document()
        max_lines = 5000
        if doc.blockCount() > max_lines + 500:
            cursor = self.output.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, 500)
            cursor.removeSelectedText()

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _append_ls_line(self, text):
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        raw = text.strip()
        if not raw:
            return
        # Linux ls wraps names with spaces like: 'Another folder'/
        # Strip leading/trailing quote chars, then check/strip trailing slash
        item = raw
        if (item.startswith("'") and "'" in item[1:]):
            item = item[1:item.rindex("'")]  # extract between first and last single quote
        elif item.startswith('"') and '"' in item[1:]:
            item = item[1:item.rindex('"')]
        is_dir = raw.endswith("/") or raw.endswith("/'") or raw.endswith('/\"')
        item = item.rstrip("/")
        if not item:
            return
        display = item + "/" if is_dir else item
        fmt = self.output.currentCharFormat()
        cursor.insertText("\n")
        if is_dir:
            fmt.setForeground(QColor("#e0e0e0"))
            fmt.setFontWeight(700)
        else:
            fmt.setForeground(QColor("#e0e0e0"))
            fmt.setFontWeight(400)
        cursor.setCharFormat(fmt)
        cursor.insertText(display)
        # Reset weight so it doesn't bleed into next output
        fmt.setFontWeight(400)
        fmt.setForeground(QColor("#e0e0e0"))
        cursor.setCharFormat(fmt)
        self.output.setTextCursor(cursor)

    def on_command_finished(self):
        self.thread = None
        self._password_mode = False
        self.input.setEchoMode(QLineEdit.EchoMode.Normal)
        self.input.setPlaceholderText(f"{self.cwd} $")

    def _on_password_prompt(self):
        self._password_mode = True
        self.input.setEchoMode(QLineEdit.EchoMode.Password)
        self.input.setPlaceholderText("password...")

    def eventFilter(self, source, event):
        if source == self.input and event.type() == QEvent.Type.KeyPress:
            self._last_key = event.key()
            if event.key() == Qt.Key.Key_C and (event.modifiers() & Qt.KeyboardModifier.MetaModifier or event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                if self.ssh_thread and self.ssh_thread.is_connected():
                    self.ssh_thread.send_interrupt()
                    return True
                if self.thread and self.thread.isRunning():
                    self.thread.kill()
                    self.output.append("^C")
                    return True

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
                if self.dropdown.isVisible():
                    if self.dropdown.currentRow() >= 0:
                        self.select_completion()
                    else:
                        self.dropdown.hide()
                    return True  # always block enter when dropdown is open
                # Handle empty enter to skip pending save
                if not self.input.text().strip() and self.ssh_info and hasattr(self,
                                                                               '_pending_save') and self._pending_save:
                    self._pending_save = None
                    if self.ssh_thread:
                        self.ssh_thread._paused = False
                    self.output.append("Skipped.")
                    self.output.append("")
                    return True
                return False

            elif event.key() == Qt.Key.Key_Backspace:
                self._last_key_backspace = True
                self.dropdown.hide()
                return False  # let Qt handle the actual deletion
            elif event.key() == Qt.Key.Key_Tab:
                if self.dropdown.isVisible() and self.dropdown.currentRow() >= 0:
                    self.select_completion()
                    return True
                self.handle_tab()
                return True
            elif event.key() == Qt.Key.Key_Escape:
                if self.dropdown.isVisible():
                    self.dropdown.hide()
                    return True
        return super().eventFilter(source, event)

    # ── Autocomplete ──────────────────────────────────────────────────────────

    def handle_tab(self):
        if self.ssh_info and self.ssh_thread:
            self._tab_ssh()
        else:
            self._tab_local()

    def _local_matches(self, last_word):
        """Return sorted list of (display_name, full_path) for last_word."""
        if os.path.isabs(last_word) or last_word.startswith("~"):
            search_base = os.path.expanduser(last_word)
        else:
            search_base = os.path.join(self.cwd, last_word)
        if search_base.endswith("/"):
            pattern = search_base + "*"
        else:
            parent_dir = os.path.dirname(search_base)
            base = os.path.basename(search_base)
            pattern = os.path.join(parent_dir, glob.escape(base)) + "*"
        matches = sorted(glob.glob(pattern))
        results = []
        for m in matches:
            name = os.path.basename(m)  # no trailing slash — user types / themselves
            results.append((name, m))
        return results

    def _apply_local(self, input_text, chosen_name):
        """Apply chosen_name to input_text. No escaping — spaces kept as-is."""
        parts = input_text.split(None, 1)
        cmd = parts[0]
        last_word = parts[1] if len(parts) > 1 else ""
        typed_parent = last_word[:last_word.rfind("/") + 1] if "/" in last_word else ""
        completed = typed_parent + chosen_name
        return f"{cmd} {completed}"

    def _tab_local(self):
        text = self.input.text()
        if not text.strip():
            return
        parts = text.split(None, 1)
        last_word = parts[1] if len(parts) > 1 else ""
        matches = self._local_matches(last_word)
        if not matches:
            return
        if len(matches) == 1:
            self.input.setText(self._apply_local(text, matches[0][0]))
            self.dropdown.hide()
        else:
            self.show_dropdown([m[0] for m in matches])

    def _tab_ssh(self, from_typing=False):
        text = self.input.text()
        if not text.strip():
            return
        parts = text.split(maxsplit=1)
        last_word = parts[1] if len(parts) > 1 else ""
        if last_word.endswith("/"):
            ls_path = last_word
        elif "/" in last_word:
            ls_path = "/".join(last_word.split("/")[:-1]) + "/"
        else:
            ls_path = "./"
        self._ssh_tab_partial = last_word
        self._ssh_tab_text = text
        self._ssh_tab_results = []
        self._collecting_tab = True
        self._ssh_tab_from_typing = from_typing
        self.ssh_thread.output_ready.disconnect(self.handle_output)
        self.ssh_thread.output_ready.connect(self._collect_ssh_tab)
        safe_ls_path = ls_path.replace("'", "'\\''")
        self.ssh_thread.send_command(f"ls -1p '{safe_ls_path}' 2>/dev/null")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, lambda: self._finish_ssh_tab(from_typing=self._ssh_tab_from_typing))

    def _collect_ssh_tab(self, text, is_error):
        if not self._collecting_tab:
            return
        for line in text.splitlines():
            raw = line.strip().rstrip("/")
            if not raw:
                continue
            # Skip the echoed command line
            if "ls -1p" in raw or "2>/dev/null" in raw:
                continue
            # Linux ls wraps names with spaces in single quotes — strip them
            if raw.startswith("'") and raw.endswith("'"):
                raw = raw[1:-1]
            if raw:
                self._ssh_tab_results.append(raw)

    def _finish_ssh_tab(self, from_typing=False):
        self._collecting_tab = False
        self.ssh_thread.output_ready.disconnect(self._collect_ssh_tab)
        self.ssh_thread.output_ready.connect(self.handle_output)
        last_word = self._ssh_tab_partial
        last_component = last_word.split("/")[-1]
        typed_parent = last_word[:last_word.rfind("/") + 1] if "/" in last_word else ""
        names = [n for n in self._ssh_tab_results if n.startswith(last_component)]
        if not names:
            if self._ssh_tab_partial.endswith("/"):
                self.show_dropdown(["(empty)"])
            return
        if len(names) == 1 and not from_typing:
            cmd = self._ssh_tab_text.split()[0]
            self.input.setText(f"{cmd} {typed_parent}{names[0]}")
        elif len(names) >= 2 or from_typing:
            self.show_dropdown(names)

    def update_dropdown(self, text):
        """Live dropdown as user types. NEVER auto-completes — only Tab does that."""
        if getattr(self, '_tab_just_fired', False):
            return

        # If last key was backspace and text ends with '/', don't trigger — user is erasing
        if getattr(self, '_last_key_backspace', False):
            self._last_key_backspace = False
            if text.endswith("/"):
                self.dropdown.hide()
                return
            # Any other character after backspace — fall through normally
        
        if self.ssh_info and self.ssh_thread:
            parts = text.split(None, 1)
            if len(parts) < 2:
                self.dropdown.hide()
                return
            last_word = parts[1]
            prev = getattr(self, '_prev_input', '')
            self._prev_input = text
            # Only fire SSH lookup when user actively typed a '/' (text grew)
            if last_word.endswith("/") and len(text) > len(prev):
                self._tab_ssh(from_typing=True)
            else:
                self.dropdown.hide()
            return
        self._prev_input = text
        # Local — show dropdown for 2+ matches only, never auto-complete
        parts = text.split(None, 1)
        if len(parts) < 2:
            self.dropdown.hide()
            return
        last_word = parts[1]
        matches = self._local_matches(last_word)
        if not matches:
            if last_word.endswith("/"):
                self.show_dropdown(["(empty)"])
            else:
                self.dropdown.hide()
            return
        self.show_dropdown([m[0] for m in matches])

    def show_dropdown(self, names):
        self.dropdown.clear()
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
        if not item or item.text() == "(empty)":
            self.dropdown.hide()
            return
        self.input.setText(self._apply_local(self.input.text(), item.text()))
        self.dropdown.hide()

    # ── End Autocomplete ──────────────────────────────────────────────────────


    def _do_clear(self):
        self.output.clear()
        self.output.setTextColor(QColor("#e0e0e0"))

    def dragEnterEvent(self, event):
        if self.ssh_info and self.ssh_thread and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not self.ssh_info or not self.ssh_thread:
            return
        urls = event.mimeData().urls()
        for url in urls:
            local_path = url.toLocalFile()
            if not local_path:
                continue
            remote_dir = getattr(self, '_remote_cwd', '~')
            self._start_upload(local_path, remote_dir)

    def _open_file_picker(self):
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select Files to Upload", os.path.expanduser("~"), "All Files (*)"
        )
        if not paths:
            return
        remote_dir = getattr(self, '_remote_cwd', '/root')
        for path in paths:
            self._start_upload(path, remote_dir)

    def _open_dir_picker(self):
        from PySide6.QtWidgets import QFileDialog
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Upload", os.path.expanduser("~")
        )
        if not folder:
            return
        remote_dir = getattr(self, '_remote_cwd', '/root')
        self._start_upload(folder, remote_dir)

    def _start_upload(self, local_path, remote_dir):
        name = os.path.basename(local_path)
        kind = "folder" if os.path.isdir(local_path) else "file"
        self._upload_local_name = name
        self._upload_kind = kind
        self._upload_remote_dir = remote_dir
        self.output.setTextColor(QColor(STATUS_COLOR))
        self.output.append(f"\nPreparing to upload {kind} '{name}'...")
        self.output.append("Uploading... 0%")
        self.output.setTextColor(QColor("#e0e0e0"))

        self._upload_thread = UploadThread(self.ssh_thread, local_path, remote_dir)
        self._upload_thread.progress.connect(self._update_upload_progress)
        self._upload_thread.done.connect(self._on_upload_done)
        self._upload_thread.error.connect(self._on_upload_error)
        self._upload_thread.conflict.connect(self._on_upload_conflict)
        self._upload_thread.start()

    def _update_upload_progress(self, msg):
        # Replace the last line in output with the updated progress
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.LineUnderCursor)
        cursor.insertText(msg)
        self.output.setTextCursor(cursor)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def _on_upload_conflict(self, filename):
        dialog = ConflictDialog(filename, self)
        dialog.exec()
        action = dialog.action or "skip"
        self._upload_thread.resolve(action, dialog.new_name)

    def _on_upload_done(self, final_name):
        if not final_name:
            self.output.setTextColor(QColor("#e0e0e0"))
            self.output.append("Upload skipped")
            self.output.setTextColor(QColor("#e0e0e0"))
        else:
            # Replace the progress line with success
            cursor = self.output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.select(cursor.SelectionType.LineUnderCursor)
            fmt = self.output.currentCharFormat()
            fmt.setForeground(QColor(SUCCESS_COLOR))
            cursor.setCharFormat(fmt)
            cursor.insertText(f"✓ '{final_name}' uploaded successfully")
            fmt.setForeground(QColor("#e0e0e0"))
            cursor.setCharFormat(fmt)
            self.output.setTextCursor(cursor)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        self.output.append("")

    def _on_upload_error(self, err):
        self.output.setTextColor(QColor("#ff4444"))
        self.output.append(f"Upload failed: {err}")
        self.output.setTextColor(QColor("#e0e0e0"))
        self.output.append("")

    def _start_download(self, remote_path):
        name = remote_path.rstrip("/").split("/")[-1]
        local_dir = os.path.expanduser("~/Downloads")
        self.output.setTextColor(QColor(STATUS_COLOR))
        self.output.append(f"\nDownloading '{name}' → {local_dir}")
        self.output.append("Downloading... 0%")
        self.output.setTextColor(QColor("#e0e0e0"))

        self._download_thread = DownloadThread(self.ssh_thread, remote_path, local_dir)
        self._download_thread.progress.connect(self._update_download_progress)
        self._download_thread.done.connect(self._on_download_done)
        self._download_thread.error.connect(self._on_download_error)
        self._download_thread.conflict.connect(self._on_download_conflict)
        self._download_thread.start()

    def _update_download_progress(self, msg):
        cursor = self.output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.LineUnderCursor)
        cursor.insertText(msg)
        self.output.setTextCursor(cursor)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def _on_download_conflict(self, filename):
        dialog = ConflictDialog(filename, self)
        dialog.exec()
        action = dialog.action or "skip"
        self._download_thread.resolve(action, dialog.new_name)

    def _on_download_done(self, final_name):
        if not final_name:
            self.output.setTextColor(QColor("#e0e0e0"))
            self.output.append("Download skipped")
            self.output.setTextColor(QColor("#e0e0e0"))
        else:
            cursor = self.output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.select(cursor.SelectionType.LineUnderCursor)
            fmt = self.output.currentCharFormat()
            fmt.setForeground(QColor(SUCCESS_COLOR))
            cursor.setCharFormat(fmt)
            cursor.insertText(f"✓ '{final_name}' downloaded to ~/Downloads")
            fmt.setForeground(QColor("#e0e0e0"))
            cursor.setCharFormat(fmt)
            self.output.setTextCursor(cursor)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
        self.output.append("")

    def _on_download_error(self, err):
        self.output.setTextColor(QColor("#ff4444"))
        self.output.append(f"Download failed: {err}")
        self.output.setTextColor(QColor("#e0e0e0"))
        self.output.append("")



