import subprocess
import os
import signal
from PySide6.QtCore import QThread, Signal


class CommandThread(QThread):
    output_ready = Signal(str, bool)
    finished = Signal()

    def __init__(self, cmd, cwd):
        super().__init__()
        self.cmd = cmd
        self.cwd = cwd
        self.process = None

    def run(self):
        self.process = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True  # ← add this
        )
        # Read stdout and stderr line by line
        for line in self.process.stdout:
            self.output_ready.emit(line.rstrip(), False)
        for line in self.process.stderr:
            self.output_ready.emit(line.rstrip(), True)
        self.process.wait()
        self.finished.emit()

    def kill(self):
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
            except ProcessLookupError:
                self.process.kill()