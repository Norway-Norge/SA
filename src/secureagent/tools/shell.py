"""Optional, narrowly scoped test execution in a pre-existing Docker image."""

import subprocess
import shutil
import tempfile
import uuid
import atexit
import logging
from pathlib import Path

from secureagent.security.workspace import Workspace, is_link_or_reparse, is_sensitive_component


def sandbox_available(image: str | None) -> bool:
    if not image or image.startswith("-") or "," in image:
        return False
    try:
        for args in (["docker", "info"], ["docker", "image", "inspect", image]):
            result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=10, check=False)
            if result.returncode:
                return False
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_tests(workspace: Workspace, path: str, runner: str, image: str) -> dict:
    target = workspace.resolve(path, directory=True)
    if "," in str(workspace.root):
        return {"error": "工作区路径包含 Docker 挂载选项不支持的字符"}
    relative = target.relative_to(workspace.root).as_posix()
    workdir = "/workspace" if relative == "." else "/workspace/" + relative
    if runner == "unittest":
        start_dir = "tests" if (target / "tests").is_dir() else "."
        command = ["python", "-m", "unittest", "discover", "-s", start_dir]
    else:
        command = ["python", "-m", "pytest"]
    name = "sa-test-" + uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(prefix="sa-workspace-") as temporary:
            staged_workspace = Path(temporary) / "workspace"
            shutil.copytree(workspace.root, staged_workspace, symlinks=True,
                            ignore=_ignore_staged_entries)
            if "," in str(staged_workspace):
                return {"error": "隔离工作区路径包含 Docker 挂载选项不支持的字符"}
            args = ["docker", "run", "--rm", "--name", name, "--pull=never", "--network=none",
                    "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                    "--pids-limit=128", "--memory=512m", "--cpus=1", "--user=65534:65534",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m", "--env", "PYTHONDONTWRITEBYTECODE=1",
                    "--mount", f"type=bind,src={staged_workspace},dst=/workspace,readonly",
                    "--workdir", workdir, image, *command]
            with tempfile.TemporaryFile() as output:
                try:
                    result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT,
                                            timeout=60, check=False)
                except subprocess.TimeoutExpired:
                    try:
                        cleanup = subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL, timeout=10, check=False)
                        if cleanup.returncode:
                            return {"error": "测试超时；容器清理未确认"}
                    except (OSError, subprocess.TimeoutExpired):
                        return {"error": "测试超时；容器清理未确认"}
                    return {"error": "测试超时，已请求清理容器"}
                except OSError:
                    return {"error": "无法启动隔离容器"}
                output.seek(0)
                raw = output.read(64 * 1024 + 1)
    except OSError:
        return {"error": "无法创建不含敏感文件的隔离工作区副本"}
    return {"exit_code": result.returncode, "output": raw[:64 * 1024].decode("utf-8", "replace"),
            "truncated": len(raw) > 64 * 1024}


def _ignore_staged_entries(directory: str, names: list[str]) -> list[str]:
    ignored = {".venv", "__pycache__", "node_modules"}
    base = Path(directory)
    return [name for name in names
            if name in ignored or is_sensitive_component(name) or is_link_or_reparse(base / name)]


class StatefulTestSandboxes:
    """Process-local test containers with disposable per-session snapshots."""

    def __init__(self, workspace: Workspace, image: str, *, cache_dir: Path | None = None):
        from threading import RLock
        self.workspace = workspace
        self.image = image
        self.cache_dir = cache_dir
        self._lock = RLock()
        self._sessions = {}
        self._invalid = set()
        self._closed = False
        atexit.register(self.close)

    def run(self, session: str, path: str, runner: str) -> dict:
        with self._lock:
            if self._closed:
                return {"error": "沙箱管理器已关闭"}
            if session in self._invalid:
                return {"status": "UNKNOWN", "error": "上次执行结果或清理未知；禁止继续使用该沙箱"}
            target = self.workspace.resolve(path, directory=True)
            relative = target.relative_to(self.workspace.root).as_posix()
            if session not in self._sessions:
                if len(self._sessions) >= 8:
                    return {"error": "已达到沙箱会话数量上限"}
                error = self._start(session)
                if error:
                    return error
            name, temporary = self._sessions[session]
            workdir = "/workspace" if relative == "." else "/workspace/" + relative
            staged_target = Path(temporary.name) / "workspace" / relative
            start_dir = "tests" if (staged_target / "tests").is_dir() else "."
            command = (["python", "-m", "unittest", "discover", "-s", start_dir]
                       if runner == "unittest" else ["python", "-m", "pytest"])
            args = ["docker", "exec", "--workdir", workdir, name, *command]
            with tempfile.TemporaryFile() as output:
                try:
                    result = subprocess.run(args, stdout=output, stderr=subprocess.STDOUT,
                                            timeout=60, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    cleaned = self._remove(session)
                    self._invalid.add(session)
                    return {"status": "UNKNOWN", "error": "测试结果未知；沙箱已销毁" if cleaned
                            else "测试结果未知；沙箱清理未确认"}
                output.seek(0)
                raw = output.read(64 * 1024 + 1)
            if result.returncode == 125:
                cleaned = self._remove(session)
                self._invalid.add(session)
                return {"status": "UNKNOWN", "error": "容器执行结果未知"}
            return {"exit_code": result.returncode, "output": raw[:64 * 1024].decode("utf-8", "replace"),
                    "sandbox_id": name,
                    "workspace_snapshot": "会话首次测试时创建；后续宿主机修改不会自动同步",
                    "truncated": len(raw) > 64 * 1024}

    def reset(self, session: str) -> dict:
        """Discard only this session's sandbox; a later test creates a new snapshot."""
        with self._lock:
            if self._closed:
                return {"error": "沙箱管理器已关闭"}
            if session in self._sessions and not self._remove(session):
                self._invalid.add(session)
                return {"status": "UNKNOWN", "error": "沙箱清理未确认，无法重建"}
            self._invalid.discard(session)
            return {"status": "RESET", "message": "已清除测试沙箱；下次测试将重新复制工作区"}

    def _start(self, session: str) -> dict | None:
        if self.cache_dir is not None:
            from secureagent.workspaces import workspace_storage
            try:
                cache = workspace_storage(self.workspace.root, "cache")
                if cache != self.cache_dir:
                    return {"error": "工作区缓存目录已变化"}
            except (OSError, ValueError):
                return {"error": "工作区缓存目录不可用"}
        temporary = tempfile.TemporaryDirectory(prefix="sa-stateful-", dir=self.cache_dir)
        name = "sa-session-" + uuid.uuid4().hex
        try:
            staged = Path(temporary.name) / "workspace"
            shutil.copytree(self.workspace.root, staged, symlinks=True, ignore=_ignore_staged_entries)
            if "," in str(staged):
                temporary.cleanup()
                return {"error": "隔离工作区路径包含 Docker 挂载选项不支持的字符"}
            args = ["docker", "run", "-d", "--rm", "--name", name, "--pull=never",
                    "--network=none", "--read-only", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=512m",
                    "--cpus=1", "--user=65534:65534",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,uid=65534,gid=65534",
                    "--tmpfs", "/workspace:rw,nosuid,nodev,size=256m,uid=65534,gid=65534",
                    "--env", "PYTHONDONTWRITEBYTECODE=1",
                    "--mount", f"type=bind,src={staged},dst=/source,readonly",
                    "--entrypoint", "sh", self.image, "-c",
                    "cp -R /source/. /workspace/ && touch /tmp/sa-ready && exec sleep 2147483647"]
            self._sessions[session] = (name, temporary)
            result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=30, check=False)
            if result.returncode:
                cleaned = self._remove(session)
                if not cleaned:
                    self._invalid.add(session)
                return {"error": "无法启动隔离容器" if cleaned else "容器启动失败，清理未确认"}
            ready = subprocess.run(["docker", "exec", name, "sh", "-c",
                                    "i=0; while [ ! -f /tmp/sa-ready ]; do i=$((i+1)); "
                                    "[ $i -lt 30 ] || exit 1; sleep 1; done"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=35, check=False)
            if ready.returncode:
                cleaned = self._remove(session)
                if not cleaned:
                    self._invalid.add(session)
                return {"error": "沙箱初始化失败" if cleaned else "沙箱初始化失败，清理未确认"}
            return None
        except (OSError, subprocess.TimeoutExpired):
            if session in self._sessions:
                cleaned = self._remove(session)
                if not cleaned:
                    self._invalid.add(session)
            else:
                temporary.cleanup()
                cleaned = True
            return {"error": "沙箱启动失败" if cleaned else "沙箱启动结果未知，清理未确认"}

    def _remove(self, session: str) -> bool:
        name, temporary = self._sessions[session]
        try:
            result = subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode:
                check = subprocess.run(["docker", "container", "inspect", name],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       timeout=10, check=False)
                if check.returncode == 0 or b"No such container" not in check.stderr:
                    return False
            temporary.cleanup()
            del self._sessions[session]
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def close(self) -> bool:
        with self._lock:
            self._closed = True
            success = all([self._remove(session) for session in list(self._sessions)])
            if not success:
                logging.getLogger("secureagent.audit").warning("沙箱清理未确认，请检查 sa-session 容器")
            return success
