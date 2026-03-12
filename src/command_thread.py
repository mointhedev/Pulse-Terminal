import os
import pty
import signal
import select
import re
import termios
from PySide6.QtCore import QThread, Signal

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][AB012]|\x1b[=>]|\r')
PASSWORD_PROMPT = re.compile(r'(?i)(password\s*:|passphrase\s*:|enter password)')


def strip_ansi(text):
    return ANSI_ESCAPE.sub('', text)


class CommandThread(QThread):
    output_ready = Signal(str, bool)
    password_prompt = Signal()
    finished = Signal()

    def __init__(self, cmd, cwd):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self._master_fd = None
        self._pid = None
        self._running = True
        self._last_input = None  # track last sent input to suppress echo

    def run(self):
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd

        self._pid = os.fork()
        if self._pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            import fcntl
            import termios as _termios
            fcntl.ioctl(slave_fd, _termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(slave_fd)
            if self.cwd:
                try:
                    os.chdir(self.cwd)
                except Exception:
                    pass
            os.execvp('/bin/sh', ['/bin/sh', '-c', self.cmd])
            os._exit(1)

        # Parent process
        os.close(slave_fd)

        # Disable echo on the master PTY so input isn't echoed back
        attrs = termios.tcgetattr(master_fd)
        attrs[3] &= ~termios.ECHO   # lflags: turn off ECHO
        termios.tcsetattr(master_fd, termios.TCSANOW, attrs)
        buf = b''
        while self._running:
            try:
                r, _, _ = select.select([master_fd], [], [], 0.1)
            except (ValueError, OSError):
                break
            if r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                # Emit complete lines; hold partial line in buf
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    text = strip_ansi(line.decode('utf-8', errors='replace'))
                    if not text:
                        continue
                    # Suppress echo: drop lines that are just a prompt + what we typed
                    if self._last_input is not None:
                        stripped = text.lstrip('> ').lstrip('$ ').lstrip('% ')
                        # Strip common REPL prompts: >>>, ..., $, >, %
                        for prompt in ('>>> ', '... ', '$ ', '> ', '% '):
                            if text.startswith(prompt):
                                stripped = text[len(prompt):]
                                break
                        if stripped == self._last_input:
                            self._last_input = None
                            continue
                    if PASSWORD_PROMPT.search(text):
                        self.output_ready.emit(text, False)
                        self.password_prompt.emit()
                    else:
                        self.output_ready.emit(text, False)
                # Also flush partial buffer if it looks like a prompt (no newline yet)
                if buf:
                    partial = strip_ansi(buf.decode('utf-8', errors='replace'))
                    if PASSWORD_PROMPT.search(partial):
                        self.output_ready.emit(partial.strip(), False)
                        self.password_prompt.emit()
                        buf = b''
            else:
                # Check if child exited
                result = os.waitpid(self._pid, os.WNOHANG)
                if result[0] != 0:
                    break

        # Flush remaining buffer
        if buf:
            text = strip_ansi(buf.decode('utf-8', errors='replace'))
            if text.strip():
                self.output_ready.emit(text, False)

        try:
            os.close(master_fd)
        except OSError:
            pass

        # Reap child
        try:
            os.waitpid(self._pid, 0)
        except ChildProcessError:
            pass

        self.finished.emit()

    def send_input(self, text):
        """Send text to the running process (for interactive input)."""
        if self._master_fd is not None:
            try:
                self._last_input = text
                os.write(self._master_fd, (text + '\n').encode())
            except OSError:
                pass

    def kill(self):
        self._running = False
        if self._pid:
            try:
                os.killpg(os.getpgid(self._pid), signal.SIGINT)
            except (ProcessLookupError, OSError):
                try:
                    os.kill(self._pid, signal.SIGKILL)
                except OSError:
                    pass
