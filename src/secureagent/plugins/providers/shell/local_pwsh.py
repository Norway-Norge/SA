"""Unconfined local PowerShell executor; no filesystem sandbox is implied."""

import os
from pathlib import Path
import shutil
import subprocess
import threading

MAX_OUTPUT = 64000
ENCODING_PREAMBLE = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                     "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); ")


def resolve_powershell():
    """Prefer a trusted explicit path, then PowerShell 7 and Windows PowerShell 5.1."""
    if os.name != "nt":
        return None
    configured = os.environ.get("SA_PWSH_PATH", "").strip()
    if configured:
        if not Path(configured).is_absolute() or not Path(configured).is_file():
            raise ValueError("SA_PWSH_PATH 必须指向存在的 PowerShell 可执行文件绝对路径")
        return configured
    standard = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell" / "7" / "pwsh.exe"
    if standard.is_file():
        return str(standard)
    found = shutil.which("pwsh")
    if found:
        return found
    fallback = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(fallback) if fallback.is_file() else None


class LocalPowerShell:
    def __init__(self, executable, workspace, on_change=lambda: None):
        self.executable = executable
        self.workspace = workspace
        self.on_change = on_change

    def execute(self, command, timeout):
        try:
            process = subprocess.Popen([self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ENCODING_PREAMBLE + command],
                                       cwd=self.workspace, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            return {"error": "无法启动 PowerShell", "status": "FAILED"}
        # Shell can mutate files outside the file tools; prior observations and approvals are stale.
        self.on_change()
        buffers = [bytearray(), bytearray()]
        truncated = [False, False]

        def drain(stream, index):
            try:
                while chunk := stream.read(8192):
                    room = MAX_OUTPUT - len(buffers[index])
                    buffers[index].extend(chunk[:max(room, 0)])
                    truncated[index] |= len(chunk) > room
            except (OSError, ValueError):
                truncated[index] = True
            finally:
                stream.close()

        threads = [threading.Thread(target=drain, args=(stream, i), daemon=True)
                   for i, stream in enumerate((process.stdout, process.stderr))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
        for thread in threads:
            thread.join(timeout=1)
        incomplete = any(thread.is_alive() for thread in threads)
        self.on_change()
        return {"status": "UNKNOWN" if timed_out or incomplete else "COMPLETED",
                "executable": Path(self.executable).name,
                "exit_code": process.returncode, "timed_out": timed_out,
                "stdout": bytes(buffers[0]).decode("utf-8", "replace"),
                "stderr": bytes(buffers[1]).decode("utf-8", "replace"),
                "truncated": any(truncated) or incomplete,
                **({"notice": "命令可能已产生副作用或子进程仍在运行，请检查后再重试。"} if timed_out or incomplete else {})}


def installer(executable, workspace, on_change=lambda: None):
    def install(scope, ctx):
        provider = LocalPowerShell(executable, workspace, on_change)
        provider.execute = scope.guard(provider.execute)
        scope.register_cleanup(ctx.shell.register_provider(provider))
    return install
