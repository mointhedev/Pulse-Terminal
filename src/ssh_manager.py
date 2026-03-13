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

    def __init__(self, host, user, port=22, key_path=None, password=None, screen_mode=False):
        super().__init__()
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.password = password
        self.screen_mode = screen_mode  # skip stty -echo, use larger scrollback
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

            # Open a persistent shell with echo disabled
            self.channel = self.client.invoke_shell(term="xterm", width=220, height=50)
            self.channel.settimeout(0.1)
            import time
            time.sleep(0.2)  # let shell settle
            if not self.screen_mode:
                # Disable remote echo so input isn't echoed back
                self.channel.send("stty -echo\n")
                time.sleep(0.1)
                # Flush the stty command echo from buffer
                while self.channel.recv_ready():
                    self.channel.recv(1024)
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
        recv_size = 65536 if self.screen_mode else 4096
        while self._running:
            try:
                chunk = self.channel.recv(recv_size).decode("utf-8", errors="replace")
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
        # Strip all ANSI/VT100/screen escape sequences
        ansi_escape = re.compile(
            r'\x1b\[[0-9;?]*[a-zA-Z]'   # CSI sequences: ESC [ ... letter
            r'|\x1b\][^\x07\x1b]*[\x07\x1b]'  # OSC sequences: ESC ] ... BEL/ESC
            r'|\x1b[()][AB012]'          # Character set sequences
            r'|\x1b[=>]'                 # Keypad mode
            r'|\x1b[a-zA-Z]'            # Single char sequences
            r'|\x07'                     # BEL
            r'|\x08'                     # Backspace
            r'|\r'                       # Carriage return
        )
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

    def upload_file(self, local_path, remote_dir, progress_callback=None):
        sftp = self.client.open_sftp()
        try:
            if os.path.isdir(local_path):
                self._upload_dir(sftp, local_path, remote_dir, progress_callback)
            else:
                filename = os.path.basename(local_path)
                remote_path = remote_dir.rstrip("/") + "/" + filename
                def _progress(transferred, total):
                    if progress_callback:
                        progress_callback(transferred, total)
                sftp.put(local_path, remote_path, callback=_progress)
        finally:
            sftp.close()

    def _sftp_mkdir_p(self, sftp, remote_dir):
        dirs = []
        path = remote_dir
        while path not in ("", "/"):
            dirs.append(path)
            path = "/".join(path.rstrip("/").split("/")[:-1]) or "/"
        for d in reversed(dirs):
            try:
                sftp.mkdir(d)
            except Exception:
                pass  # already exists

    def _upload_dir(self, sftp, local_dir, remote_base, progress_callback=None):
        folder_name = os.path.basename(local_dir.rstrip("/"))
        remote_root = remote_base.rstrip("/") + "/" + folder_name
        self._sftp_mkdir_p(sftp, remote_root)
        for root, dirs, files in os.walk(local_dir):
            rel = os.path.relpath(root, local_dir)
            remote_dir = remote_root if rel == "." else remote_root + "/" + rel.replace(os.sep, "/")
            self._sftp_mkdir_p(sftp, remote_dir)
            for filename in files:
                local_file = os.path.join(root, filename)
                remote_file = remote_dir + "/" + filename
                def _progress(transferred, total, fn=filename):
                    if progress_callback:
                        progress_callback(transferred, total, fn)
                sftp.put(local_file, remote_file, callback=_progress)

    def disconnect(self):
        self._running = False
        self._connected = False
        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self.wait(2000)  # wait up to 2s for thread to finish

    def is_connected(self):
        return self._connected


class DownloadThread(QThread):
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)
    conflict = Signal(str)

    def __init__(self, ssh_thread, remote_path, local_dir):
        super().__init__()
        self.ssh_thread = ssh_thread
        self.remote_path = remote_path
        self.local_dir = local_dir
        self._conflict_action = None
        self._conflict_new_name = None

    def resolve(self, action, new_name=None):
        self._conflict_action = action
        self._conflict_new_name = new_name

    def _check_conflict(self, local_path, name):
        if not os.path.exists(local_path):
            return local_path, False
        self._conflict_action = None
        self._conflict_new_name = None
        self.conflict.emit(name)
        while self._conflict_action is None:
            self.msleep(50)
        if self._conflict_action == "skip":
            return local_path, True
        elif self._conflict_action == "rename" and self._conflict_new_name:
            dir_part = os.path.dirname(local_path)
            return os.path.join(dir_part, self._conflict_new_name), False
        else:  # overwrite
            return local_path, False

    def run(self):
        try:
            sftp = self.ssh_thread.client.open_sftp()
            name = self.remote_path.rstrip("/").split("/")[-1]
            try:
                import stat as stat_mod
                st = sftp.stat(self.remote_path)
                is_dir = stat_mod.S_ISDIR(st.st_mode)
            except Exception:
                self.error.emit(f"'{name}' not found on server")
                sftp.close()
                return

            local_path = os.path.join(self.local_dir, name)
            local_path, skip = self._check_conflict(local_path, name)
            if skip:
                sftp.close()
                self.done.emit("")
                return

            if is_dir:
                self._download_dir(sftp, self.remote_path, os.path.dirname(local_path), os.path.basename(local_path))
            else:
                self._download_file(sftp, self.remote_path, local_path)
            sftp.close()
            self.done.emit(os.path.basename(local_path))
        except Exception as e:
            self.error.emit(str(e))

    def _download_file(self, sftp, remote_path, local_path):
        last_pct = [-1]
        def on_progress(transferred, total):
            pct = int(transferred / total * 100)
            if pct != last_pct[0] and pct % 5 == 0:
                last_pct[0] = pct
                self.progress.emit(f"Downloading... {pct}%")
        sftp.get(remote_path, local_path, callback=on_progress)
        # Update timestamps so Finder shows it as most recently modified
        import time
        now = time.time()
        os.utime(local_path, (now, now))

    def _download_dir(self, sftp, remote_dir, local_base, folder_name):
        import stat as stat_mod
        local_root = os.path.join(local_base, folder_name)
        os.makedirs(local_root, exist_ok=True)

        def _walk(remote, local):
            items = sftp.listdir_attr(remote)
            files = [i for i in items if not stat_mod.S_ISDIR(i.st_mode)]
            dirs = [i for i in items if stat_mod.S_ISDIR(i.st_mode)]
            total = len(files)
            done = [0]
            for attr in files:
                sftp.get(remote + "/" + attr.filename, os.path.join(local, attr.filename))
                done[0] += 1
                if total:
                    self.progress.emit(f"Downloading... {done[0]}/{total} files")
            for attr in dirs:
                sub_local = os.path.join(local, attr.filename)
                os.makedirs(sub_local, exist_ok=True)
                _walk(remote + "/" + attr.filename, sub_local)

        _walk(remote_dir, local_root)


class UploadThread(QThread):
    progress = Signal(str)
    done = Signal(str)
    error = Signal(str)
    conflict = Signal(str)  # emits filename, thread pauses waiting for resolve()

    def __init__(self, ssh_thread, local_path, remote_dir):
        super().__init__()
        self.ssh_thread = ssh_thread
        self.local_path = local_path
        self.remote_dir = remote_dir
        self._conflict_action = None  # set by resolve()
        self._conflict_new_name = None

    def resolve(self, action, new_name=None):
        """Called from UI thread: action = 'overwrite' | 'rename' | 'skip'"""
        self._conflict_action = action
        self._conflict_new_name = new_name

    def _check_conflict(self, sftp, remote_path, filename):
        """Returns (final_remote_path, skip) after resolving conflict if needed."""
        exists = False
        try:
            sftp.stat(remote_path)
            exists = True
        except Exception:
            exists = False

        if not exists:
            return remote_path, False

        # File exists — emit conflict and wait for UI response
        self._conflict_action = None
        self._conflict_new_name = None
        self.conflict.emit(filename)
        while self._conflict_action is None:
            self.msleep(50)
        if self._conflict_action == "skip":
            return remote_path, True
        elif self._conflict_action == "rename" and self._conflict_new_name:
            dir_part = "/".join(remote_path.rstrip("/").split("/")[:-1])
            return dir_part + "/" + self._conflict_new_name, False
        else:  # overwrite
            return remote_path, False

    def run(self):
        try:
            if os.path.isdir(self.local_path):
                self._upload_dir()
            else:
                self._upload_file(self.local_path, self.remote_dir)
        except Exception as e:
            self.error.emit(str(e))

    def _upload_dir(self):
        sftp = self.ssh_thread.client.open_sftp()
        local_path = self.local_path.rstrip("/")
        local_base = os.path.dirname(local_path)
        folder_name = os.path.basename(local_path)

        # Check conflict on root folder
        remote_root = self.remote_dir.rstrip("/") + "/" + folder_name
        remote_root, skip = self._check_conflict(sftp, remote_root, folder_name)
        if skip:
            sftp.close()
            self.done.emit("")
            return

        all_files = []
        for root, dirs, files in os.walk(local_path):
            rel = os.path.relpath(root, local_base)
            # If renamed, replace original folder name with new root
            rel_parts = rel.replace(os.sep, "/").split("/")
            rel_parts[0] = os.path.basename(remote_root)
            remote_dir = self.remote_dir.rstrip("/") + "/" + "/".join(rel_parts)
            self._mkdir_p(sftp, remote_dir)
            for f in files:
                all_files.append((os.path.join(root, f), remote_dir + "/" + f))

        total_files = len(all_files)
        if total_files == 0:
            self.progress.emit("No files to upload (empty folder structure created)")
        for idx, (local_file, remote_file) in enumerate(all_files, 1):
            sftp.put(local_file, remote_file)
            pct = int(idx / total_files * 100)
            self.progress.emit(f"Uploading... {idx}/{total_files} files ({pct}%)")

        sftp.close()
        self.done.emit(os.path.basename(remote_root))

    def _mkdir_p(self, sftp, remote_path):
        parts = remote_path.strip("/").split("/")
        current = ""
        for part in parts:
            current += "/" + part
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def _upload_file(self, local_path, remote_dir):
        sftp = self.ssh_thread.client.open_sftp()
        filename = os.path.basename(local_path)
        remote_path = remote_dir.rstrip("/") + "/" + filename
        remote_path, skip = self._check_conflict(sftp, remote_path, filename)
        if not skip:
            last_pct = [-1]
            def on_progress(transferred, total):
                pct = int(transferred / total * 100)
                if pct != last_pct[0] and pct % 5 == 0:
                    last_pct[0] = pct
                    self.progress.emit(f"Uploading... {pct}%")
            sftp.put(local_path, remote_path, callback=on_progress)
        sftp.close()
        if skip:
            self.done.emit("")
        else:
            self.done.emit(os.path.basename(remote_path))
