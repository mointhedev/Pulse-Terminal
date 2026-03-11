import os
import json
import time
import paramiko
from PySide6.QtCore import QThread, Signal

CONNECTIONS_FILE = os.path.expanduser("~/.pulse/connections.json")


def load_connections():
    if not os.path.exists(CONNECTIONS_FILE):
        return {}
    with open(CONNECTIONS_FILE, "r") as f:
        return json.load(f)


def save_connection(nickname, host, user, port=22, key_path=None, password=None):
    os.makedirs(os.path.dirname(CONNECTIONS_FILE), exist_ok=True)
    connections = load_connections()
    connections[nickname] = {
        "host": host,
        "user": user,
        "port": port,
        "key_path": key_path,
        "password": password
    }
    with open(CONNECTIONS_FILE, "w") as f:
        json.dump(connections, f, indent=2)


def delete_connection(nickname):
    connections = load_connections()
    if nickname in connections:
        del connections[nickname]
        with open(CONNECTIONS_FILE, "w") as f:
            json.dump(connections, f, indent=2)


def get_connection(nickname):
    connections = load_connections()
    return connections.get(nickname, None)


class SSHThread(QThread):
    output_ready = Signal(str, bool)  # text, is_error
    connected = Signal()
    finished = Signal()
    error = Signal(str)

    def __init__(self, host, user, port=22, key_path=None, password=None):
        super().__init__()
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.client = None
        self.channel = None
        self._connected = False
        self._running = False
        self._paused = False

    def run(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": self.host,
                "username": self.user,
                "port": self.port,
            }
            if self.key_path:
                connect_kwargs["key_filename"] = os.path.expanduser(self.key_path)
            elif self.password:
                connect_kwargs["password"] = self.password

            self.client.connect(**connect_kwargs)

            # Open a persistent shell
            self.channel = self.client.invoke_shell(term="xterm", width=220, height=50)
            self.channel.settimeout(0.1)
            # Enable keepalive every 30 seconds
            self.client.get_transport().set_keepalive(30)
            self._connected = True
            self._running = True
            self._paused = True  # pause until save prompt is handled
            self.connected.emit()

            # Wait until unpaused before reading output
            while self._paused:
                time.sleep(0.05)

            # Read output continuously
            self._read_loop()

        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()

    def _read_loop(self):
        buffer = ""
        while self._running:
            try:
                chunk = self.channel.recv(4096).decode("utf-8", errors="replace")
                if chunk:
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = self._strip_ansi(line)
                        if line.strip() and line.strip() != getattr(self, '_last_cmd', None):
                            self.output_ready.emit(line, False)
                else:
                    # Empty chunk means server closed the connection
                    self._running = False
                    self._connected = False
                    self.finished.emit()
                    return
            except Exception:
                time.sleep(0.05)

        # Emit any remaining buffer
        if buffer.strip():
            self.output_ready.emit(self._strip_ansi(buffer), False)
        self.finished.emit()

    def _strip_ansi(self, text):
        import re
        # Strip all ANSI/VT100 escape sequences including bracketed paste mode
        ansi_escape = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\r')
        text = ansi_escape.sub('', text)
        # Strip shell prompt (e.g. root@ubuntu-server:~#)
        text = re.sub(r'\S+@\S+:[^\$#]*[\$#]\s*', '', text)
        return text

    def send_command(self, cmd):
        if self.channel and self._connected:
            self._last_cmd = cmd
            self.channel.send(cmd + "\n")

    def send_interrupt(self):
        if self.channel and self._connected:
            self.channel.send("\x03")  # Ctrl+C

    def disconnect(self):
        self._running = False
        self._connected = False
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()

    def is_connected(self):
        return self._connected
