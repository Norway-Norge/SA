"""Reviewed, recoverable file operations and their tool descriptions."""

import json
import hashlib
import os
import secrets
import stat
import sys
import threading
import time
import uuid
import tempfile
from pathlib import Path

from secureagent.security.observations import current_session
from secureagent.security.workspace import WorkspaceError, is_link_or_reparse
from secureagent.security.recycle_access import resolve_delete_target, valid_recycle_id
from secureagent.tools.spec import PATH, spec
from secureagent.capabilities.filesystem.file_backend import file_version as _version, write_lock as _write_lock
from secureagent.capabilities.filesystem.create_file import MAX_CREATE_BYTES
from secureagent.capabilities.filesystem.edit_file import _observed_bytes
from secureagent.capabilities.filesystem.file_service import local_observation_parts


DELETE = spec("safe_delete", "申请将单个文件或目录移入回收区，必须等待用户审批。文件需先完整读取；目录会检查内容并展示清单。",
              {"path": PATH}, ["path"])
LIST = spec("list_recycle_bin", "列出当前会话移入回收区的文件、目录及其状态。", {}, [])
RESTORE = spec("restore_file", "申请恢复当前会话回收区中的文件或目录到原位置；必须等待用户审批，不覆盖已有目标。",
               {"recycle_id": {"type": "string", "description": "回收记录标识"}}, ["recycle_id"])
CONVERT = spec("convert_file", "将已完整读取的文件转换到新的路径，保留转换后完整文本并将原文件移入回收区。需要用户审批，不执行文件。目标不得已存在，父目录必须存在。",
               {"source_path": PATH, "target_path": PATH,
                "content": {"type": "string", "description": "转换后文件的完整 UTF-8 内容。"}},
               ["source_path", "target_path", "content"])


class SafeDeleteOperations:
    def __init__(self, policy, observations, *, authorize=None):
        self.policy = policy
        self.authorize = authorize or policy.authorize
        self.observations = observations
        self._lock = threading.RLock()
        self._pending = {}
        self._results = {}
        self._decision_handlers = {}

    def discard_pending(self):
        with self._lock:
            self._pending.clear()

    def bind_decisions(self, actions, handler):
        token = object()
        with self._lock:
            if any(action in self._decision_handlers for action in actions):
                raise ValueError("审批处理器已注册")
            for action in actions:
                self._decision_handlers[action] = (token, handler)

        def dispose():
            with self._lock:
                removed = set()
                for action in actions:
                    if self._decision_handlers.get(action, (None,))[0] is token:
                        del self._decision_handlers[action]
                        removed.add(action)
                self._pending = {key: value for key, value in self._pending.items()
                                 if value["action"] not in removed}
        return dispose

    def _resolve_target(self, path):
        return resolve_delete_target(self.policy.workspace, path)

    def _scan_directory(self, root):
        """Bind an approval to a bounded, link-free, same-device directory tree."""
        entries = []
        total = 0
        root_stat = root.stat()
        def walk(directory, relative):
            nonlocal total
            before = directory.stat()
            if is_link_or_reparse(directory) or before.st_dev != root_stat.st_dev:
                raise ValueError("目录包含链接或跨文件系统内容")
            with os.scandir(directory) as iterator:
                children = []
                for entry in iterator:
                    children.append(entry)
                    if len(children) + len(entries) > 500:
                        raise ValueError("目录内容超过 500 项限制")
                children.sort(key=lambda entry: entry.name)
            for entry in children:
                if self.policy.workspace.exclude_entry(entry.name):
                    raise ValueError("目录包含受保护的敏感路径")
                child = Path(entry.path)
                rel = (relative / entry.name).as_posix()
                if is_link_or_reparse(child):
                    raise ValueError("目录包含符号链接或连接点")
                info = child.stat()
                if info.st_dev != root_stat.st_dev:
                    raise ValueError("目录包含跨文件系统内容")
                if len(entries) >= 500:
                    raise ValueError("目录内容超过 500 项限制")
                if stat.S_ISDIR(info.st_mode):
                    entries.append({"path": rel, "type": "directory", "device": info.st_dev,
                                    "inode": info.st_ino, "mtime": info.st_mtime_ns})
                    walk(child, relative / entry.name)
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > 64 * 1024 * 1024:
                        raise ValueError("目录文件总大小超过 64 MiB")
                    digest = hashlib.sha256()
                    with child.open("rb") as stream:
                        first = os.fstat(stream.fileno())
                        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns):
                            raise ValueError("目录文件在检查时发生变化")
                        for chunk in iter(lambda: stream.read(65536), b""):
                            digest.update(chunk)
                            if stream.tell() > first.st_size:
                                raise ValueError("目录文件在检查时发生变化")
                        last = os.fstat(stream.fileno())
                    if (first.st_dev, first.st_ino, first.st_size, first.st_mtime_ns, first.st_ctime_ns) != (last.st_dev, last.st_ino, last.st_size, last.st_mtime_ns, last.st_ctime_ns):
                        raise ValueError("目录文件在检查时发生变化")
                    entries.append({"path": rel, "type": "file", "size": info.st_size,
                                    "device": info.st_dev, "inode": info.st_ino,
                                    "mtime": info.st_mtime_ns, "sha256": digest.hexdigest()})
                else:
                    raise ValueError("目录包含不支持的特殊文件")
            after = directory.stat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
                raise ValueError("目录在检查时发生变化")
        walk(root, Path())
        data = {"root_device": root_stat.st_dev, "root_inode": root_stat.st_ino,
                "root_mtime": root_stat.st_mtime_ns, "entries": entries}
        version = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        return version, [{k: e[k] for k in ("path", "type", "size") if k in e} for e in entries], total

    @staticmethod
    def _rename_directory_noreplace(source, target):
        if os.name == "nt":
            os.rename(source, target)  # Windows does not replace an existing destination.
            return
        if sys.platform.startswith("linux"):
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            rename = getattr(libc, "renameat2", None)
            if rename is None:
                raise OSError("当前平台不支持安全恢复目录")
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            return
        raise OSError("当前平台不支持安全恢复目录")

    @staticmethod
    def _valid_id(value):
        return valid_recycle_id(value)

    def _trash(self, create=False):
        current = self.policy.workspace.root
        for part in (".secureagent", "recycle"):
            current = current / part
            if create:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            if not os.path.lexists(current):
                return None
            if is_link_or_reparse(current) or not current.is_dir():
                raise WorkspaceError("回收区路径无效")
        return current

    def _record(self, recycle_id):
        if not self._valid_id(recycle_id):
            raise ValueError("回收记录标识无效")
        root = self._trash()
        folder = root / recycle_id if root else None
        if folder is None or not os.path.lexists(folder) or is_link_or_reparse(folder) or not folder.is_dir():
            return None
        meta = folder / "meta.json"
        if is_link_or_reparse(meta) or not meta.is_file() or meta.stat().st_size > 8192:
            raise ValueError("回收记录无效")
        with meta.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
        if (not isinstance(record, dict) or record.get("id") != recycle_id
                or not isinstance(record.get("session"), str) or not isinstance(record.get("path"), str)
                or record.get("state") not in {"PREPARED", "PUBLISHING", "DESTINATION_PUBLISHED", "CONVERTED", "QUARANTINED", "RESTORING", "RESTORED"}
                or not isinstance(record.get("version"), str)):
            raise ValueError("回收记录无效")
        return record

    @staticmethod
    def _save(folder, record):
        temp = folder / (".meta-" + uuid.uuid4().hex)
        try:
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, folder / "meta.json")
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _propose(self, action, path, version, identity, recycle_id=None, preview=None, total_bytes=None, extra=None):
        session = current_session()
        if not session:
            return {"error": "缺少会话，无法申请审批"}
        with self._lock:
            now = time.monotonic()
            self._pending = {k: v for k, v in self._pending.items() if v["expires"] > now}
            self._results = {k: v for k, v in self._results.items() if v[2] > now}
            if len(self._pending) >= 256:
                return {"error": "待审批请求过多"}
            key = secrets.token_urlsafe(24)
            recycle_id = uuid.uuid4().hex if action in {"delete", "delete_directory", "convert"} else recycle_id
            self._pending[key] = {"action": action, "path": path, "version": version, "identity": identity,
                                  "session": session, "recycle_id": recycle_id, "expires": now + 600}
            if extra:
                self._pending[key].update(extra)
        response = {"status": "REVIEW", "approval_id": key, "action": action, "path": path,
                    "recycle_id": recycle_id, "message": "操作尚未执行，等待用户审批"}
        if preview is not None:
            response.update({"entries": preview, "entry_count": len(preview), "total_bytes": total_bytes})
        return response

    def propose_convert(self, source_path, target_path, content):
        if len(source_path) > 1024 or len(target_path) > 1024 or len(content.encode("utf-8")) > MAX_CREATE_BYTES:
            return {"error": "路径或转换内容超过大小限制"}
        allowed, reason = self.authorize("convert_file", {"source_path": source_path, "target_path": target_path})
        if not allowed:
            return {"code": "DENIED", "error": reason}
        source = self.policy.workspace.resolve(source_path)
        observation = self.observations.get(str(source))
        if observation is None or not observation.complete:
            return {"code": "NOT_OBSERVED", "error": "请先在当前会话完整读取源文件"}
        local_version = local_observation_parts(observation)
        before = _observed_bytes(source, observation)
        if before is None or local_version is None or _version(source) != local_version:
            return {"code": "STALE_VERSION", "error": "源文件已变化，请重新读取"}
        try:
            original = before.decode("utf-8")
        except UnicodeDecodeError:
            return {"error": "源文件不是 UTF-8 文本"}
        result = self._propose("convert", source_path, local_version[0], local_version[1],
                               extra={"target_path": target_path, "content": content})
        if result.get("status") == "REVIEW":
            result.update({"source_path": source_path, "target_path": target_path,
                           "original_content": original, "new_content": content,
                           "message": "转换尚未执行；批准后创建目标并将源文件移入回收区"})
        return result

    def propose_delete(self, path):
        allowed, reason = self.authorize("safe_delete", {"path": path})
        if not allowed:
            return {"code": "DENIED", "error": reason}
        target = self._resolve_target(path)
        if target.is_dir():
            version, preview, total = self._scan_directory(target)
            return self._propose("delete_directory", path, version, None, preview=preview, total_bytes=total)
        observation = self.observations.get(str(target))
        if observation is None or not observation.complete:
            return {"code": "NOT_OBSERVED", "error": "请先在当前会话完整读取文件"}
        local_version = local_observation_parts(observation)
        if local_version is None or _version(target) != local_version:
            return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
        return self._propose("delete", path, local_version[0], local_version[1])

    def list_items(self):
        session = current_session()
        if not session:
            return {"error": "缺少会话"}
        root = self._trash()
        if root is None:
            return {"items": []}
        items = []
        for folder in root.iterdir():
            if self._valid_id(folder.name):
                record = self._record(folder.name)
                if record and record["session"] == session:
                    entry = {"recycle_id": record["id"], "path": record["path"],
                             "state": record["state"], "kind": record.get("kind", "file")}
                    if isinstance(record.get("target_path"), str):
                        entry["target_path"] = record["target_path"]
                    items.append(entry)
                    if len(items) > 200:
                        return {"items": items[:200], "truncated": True}
        return {"items": items, "truncated": False}

    def propose_restore(self, recycle_id):
        record = self._record(recycle_id)
        if not record or record["session"] != current_session() or record["state"] not in {"QUARANTINED", "CONVERTED"}:
            return {"error": "回收记录不存在、不属于当前会话或状态不可恢复"}
        root = self._trash()
        payload = root / recycle_id / "payload"
        if record.get("kind") == "directory":
            if is_link_or_reparse(payload) or not payload.is_dir():
                return {"error": "回收目录不可用"}
            version, preview, total = self._scan_directory(payload)
            if version != record["version"]:
                return {"error": "回收目录已变化，无法恢复"}
            try:
                self.policy.workspace.resolve_new_file(record["path"])
            except WorkspaceError:
                return {"error": "原路径不可用或已存在，无法恢复"}
            return self._propose("restore_directory", record["path"], version, None,
                                 recycle_id, preview=preview, total_bytes=total)
        if is_link_or_reparse(payload) or not payload.is_file():
            return {"error": "回收文件不可用"}
        version = _version(payload)
        if not version or version[0] != record["version"]:
            return {"error": "回收文件已变化，无法恢复"}
        try:
            self.policy.workspace.resolve_new_file(record["path"])
        except WorkspaceError:
            return {"error": "原路径不可用或已存在，无法恢复"}
        return self._propose("restore", record["path"], version[0], version[1], recycle_id)

    def decide(self, approval_id, approve):
        with self._lock:
            item = self._pending.get(approval_id)
            entry = self._decision_handlers.get(item["action"]) if item else None
        if entry is None:
            return {"code": "INVALID_APPROVAL", "error": "审批不存在或对应工具已卸载"}
        return entry[1](approval_id, approve)

    def _decide(self, approval_id, approve):
        session = current_session()
        with self._lock:
            item = self._pending.get(approval_id)
            if not item or item["session"] != session:
                return {"code": "INVALID_APPROVAL", "error": "审批不存在或不属于当前会话"}
            del self._pending[approval_id]
            if item["expires"] <= time.monotonic():
                return {"code": "INVALID_APPROVAL", "error": "审批已过期"}
            self._results[approval_id] = (session, {"status": "PROCESSING"}, time.monotonic() + 600)
            if not approve:
                result = {"status": "DENIED", "message": "用户拒绝操作"}
            else:
                try:
                    with _write_lock:
                        result = self._commit(item)
                except Exception:
                    result = {"status": "UNKNOWN", "recycle_id": item["recycle_id"],
                              "error": "执行结果未知，请检查原文件和回收区，不要自动重试"}
                    if item["action"] == "convert":
                        result.update({"source_path": item["path"], "target_path": item["target_path"],
                                       "error": "转换结果未知，请检查源文件、目标文件和回收区，不要自动重试"})
            self._results[approval_id] = (session, result, time.monotonic() + 600)
            return result

    def status(self, approval_id):
        with self._lock:
            item = self._pending.get(approval_id)
            if item and item["session"] == current_session() and item["expires"] > time.monotonic():
                return {"status": "REVIEW"}
            result = self._results.get(approval_id)
            if result and result[0] == current_session() and result[2] > time.monotonic():
                return dict(result[1])
            return {"status": "UNKNOWN", "message": "审批状态不可用，请检查文件和回收区"}

    def _commit(self, item):
        action, path = item["action"], item["path"]
        if action == "convert":
            return self._commit_convert(item)
        deleting = action in {"delete", "delete_directory"}
        allowed, reason = self.authorize("safe_delete" if deleting else "restore_file",
                                                {"path": path} if deleting else {"recycle_id": item["recycle_id"]})
        if not allowed:
            return {"code": "DENIED", "error": reason}
        if action == "delete_directory":
            target = self.policy.workspace.resolve(path, directory=True)
            if target == self.policy.workspace.root or self._scan_directory(target)[0] != item["version"]:
                return {"code": "STALE_VERSION", "error": "目录已变化，请重新申请审批"}
            root = self._trash(create=True)
            recycle_id = item["recycle_id"]
            folder = root / recycle_id
            folder.mkdir(mode=0o700)
            record = {"id": recycle_id, "session": item["session"], "path": path,
                      "version": item["version"], "kind": "directory", "state": "PREPARED"}
            self._save(folder, record)
            allowed, reason = self.authorize("safe_delete", {"path": path})
            if not allowed:
                return {"code": "DENIED", "error": reason}
            if self.policy.workspace.resolve(path, directory=True) != target or self._scan_directory(target)[0] != item["version"]:
                return {"code": "STALE_VERSION", "error": "目录已变化，请重新申请审批"}
            os.rename(target, folder / "payload")
            record["state"] = "QUARANTINED"
            self._save(folder, record)
            return {"status": "QUARANTINED", "path": path, "recycle_id": recycle_id, "kind": "directory"}
        if action == "delete":
            target = self.policy.workspace.resolve(path)
            if _version(target) != (item["version"], item["identity"]):
                return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取并申请审批"}
            root = self._trash(create=True)
            recycle_id = item["recycle_id"]
            folder = root / recycle_id
            folder.mkdir(mode=0o700)
            record = {"id": recycle_id, "session": item["session"], "path": path,
                      "version": item["version"], "state": "PREPARED"}
            self._save(folder, record)
            # Recheck both policy and observed identity immediately before the move.
            allowed, reason = self.authorize("safe_delete", {"path": path})
            if not allowed:
                return {"code": "DENIED", "error": reason}
            if self.policy.workspace.resolve(path) != target or _version(target) != (item["version"], item["identity"]):
                return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
            os.rename(target, folder / "payload")
            self.observations.invalidate(str(target))
            record["state"] = "QUARANTINED"
            self._save(folder, record)
            return {"status": "QUARANTINED", "path": path, "recycle_id": recycle_id}
        recycle_id = item["recycle_id"]
        record = self._record(recycle_id)
        if not record or record["session"] != item["session"] or record["state"] not in {"QUARANTINED", "CONVERTED"} or record["path"] != path:
            return {"error": "回收记录已变化"}
        folder = self._trash() / recycle_id
        payload = folder / "payload"
        if action == "restore_directory":
            if record.get("kind") != "directory" or is_link_or_reparse(payload) or not payload.is_dir():
                return {"error": "回收目录不可用"}
            if self._scan_directory(payload)[0] != item["version"]:
                return {"error": "回收目录已变化"}
            target = self.policy.workspace.resolve_new_file(path)
            record["state"] = "RESTORING"
            self._save(folder, record)
            allowed, reason = self.authorize("restore_file", {"recycle_id": recycle_id})
            if not allowed:
                return {"code": "DENIED", "error": reason}
            if self.policy.workspace.resolve_new_file(path) != target or self._scan_directory(payload)[0] != item["version"]:
                return {"error": "恢复目标或回收目录已变化"}
            self._rename_directory_noreplace(payload, target)
            record["state"] = "RESTORED"
            self._save(folder, record)
            return {"status": "RESTORED", "path": path, "recycle_id": recycle_id, "kind": "directory"}
        if is_link_or_reparse(payload) or _version(payload) != (item["version"], item["identity"]):
            return {"error": "回收文件已变化"}
        target = self.policy.workspace.resolve_new_file(path)
        record["state"] = "RESTORING"
        self._save(folder, record)
        allowed, reason = self.authorize("restore_file", {"recycle_id": recycle_id})
        if not allowed:
            return {"code": "DENIED", "error": reason}
        if self.policy.workspace.resolve_new_file(path) != target or _version(payload) != (item["version"], item["identity"]):
            return {"error": "恢复目标或回收文件已变化"}
        os.link(payload, target)
        os.unlink(payload)
        record["state"] = "RESTORED"
        self._save(folder, record)
        return {"status": "RESTORED", "path": path, "recycle_id": recycle_id}

    def _commit_convert(self, item):
        source_path, target_path = item["path"], item["target_path"]
        args = {"source_path": source_path, "target_path": target_path}
        allowed, reason = self.authorize("convert_file", args)
        if not allowed:
            return {"code": "DENIED", "error": reason}
        allowed, reason = self.authorize("safe_delete", {"path": source_path})
        if not allowed:
            return {"code": "DENIED", "error": reason}
        source = self.policy.workspace.resolve(source_path)
        target = self.policy.workspace.resolve_new_file(target_path)
        expected = (item["version"], item["identity"])
        if _version(source) != expected:
            return {"code": "STALE_VERSION", "error": "源文件已变化，请重新读取并申请审批"}
        root = self._trash(create=True)
        folder = root / item["recycle_id"]
        folder.mkdir(mode=0o700)
        record = {"id": item["recycle_id"], "session": item["session"], "path": source_path,
                  "target_path": target_path, "version": item["version"], "state": "PREPARED"}
        self._save(folder, record)
        descriptor, staged = tempfile.mkstemp(prefix=".sa-convert-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(item["content"].encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            staged_version = _version(Path(staged))
            allowed, reason = self.authorize("convert_file", args)
            if not allowed:
                return {"code": "DENIED", "error": reason}
            if (self.policy.workspace.resolve(source_path) != source or _version(source) != expected
                    or self.policy.workspace.resolve_new_file(target_path) != target):
                return {"code": "STALE_VERSION", "error": "源文件或目标路径已变化"}
            record["state"] = "PUBLISHING"
            self._save(folder, record)
            os.link(staged, target)
            record["state"] = "DESTINATION_PUBLISHED"
            self._save(folder, record)
            published_version = _version(target)
            allowed, reason = self.authorize("safe_delete", {"path": source_path})
            if (not allowed or self.policy.workspace.resolve(source_path) != source or _version(source) != expected
                    or self.policy.workspace.resolve(target_path) != target or not published_version
                    or not staged_version or published_version[0] != staged_version[0]
                    or _version(target) != published_version):
                return {"status": "PARTIAL", "recycle_id": item["recycle_id"], "target_path": target_path,
                        "error": "目标已创建，但源文件未移走；请检查两个文件后再操作"}
            os.rename(source, folder / "payload")
            self.observations.invalidate(str(source))
            record["state"] = "CONVERTED"
            self._save(folder, record)
            return {"status": "CONVERTED", "source_path": source_path, "target_path": target_path,
                    "recycle_id": item["recycle_id"]}
        finally:
            try:
                os.unlink(staged)
            except OSError:
                pass
