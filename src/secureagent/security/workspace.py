"""Resolve workspace paths and reject symlinks and traversal."""

from pathlib import Path, PureWindowsPath
import stat

SENSITIVE_DIRECTORIES = {
    ".aws", ".azure", ".config", ".direnv", ".docker", ".git", ".gnupg", ".kube", ".secureagent", ".ssh",
}
SENSITIVE_FILENAMES = {
    ".envrc", ".git-credentials", ".gitconfig", ".netrc", ".npmrc", ".pypirc", ".secrets",
    ".vault-token",
    "access_token", "auth.json", "credentials", "id_dsa", "id_ecdsa", "id_ed25519",
    "id_rsa", "pip.conf", "private.key", "private_key", "refresh_token", "secret",
    "secrets", "token", "credentials.json", "service-account.json", "service_account.json",
}
SENSITIVE_SUFFIXES = {
    ".env", ".jks", ".keystore", ".key", ".kdbx", ".pem", ".p12", ".pfx",
    ".tfstate", ".tfstate.backup",
}


def is_sensitive_component(value: str) -> bool:
    name = value.casefold()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if name in SENSITIVE_DIRECTORIES or name in SENSITIVE_FILENAMES or stem in SENSITIVE_FILENAMES:
        return True
    if name == ".env" or name.startswith((".env.", ".env-", ".env_")):
        return True
    if stem.endswith(("_credentials", "-credentials", ".credentials", "_secret", "-secret",
                      ".secret", "_token", "-token", ".token")):
        return True
    return any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceError("工作区必须是目录")

    def resolve(self, value: str, *, directory: bool = False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        candidate = Path(value)
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            raise WorkspaceError("只允许工作区内的相对路径")
        if any(is_sensitive_component(part) for part in candidate.parts):
            raise WorkspaceError("不允许访问敏感文件或目录")
        current = self.root
        try:
            for part in candidate.parts:
                current = current / part
                if is_link_or_reparse(current):
                    raise WorkspaceError("不允许访问符号链接或连接点")
            resolved = current.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径不存在或不在工作区内") from exc
        if directory and not resolved.is_dir():
            raise WorkspaceError("目标不是目录")
        if not directory and not resolved.is_file():
            raise WorkspaceError("目标不是文件")
        return resolved

    def resolve_new_file(self, value: str, *, allow_missing_parents: bool = False) -> Path:
        """Validate a new path without following links, optionally allowing missing parents."""
        import os

        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        candidate = Path(value)
        if (candidate.is_absolute() or candidate.drive or candidate.root
                or ".." in candidate.parts or not candidate.name
                or any(is_sensitive_component(part) for part in candidate.parts)
                or ":" in value or any(PureWindowsPath(part).is_reserved() or part.endswith((".", " "))
                                       for part in candidate.parts)):
            raise WorkspaceError("只允许工作区内的非敏感相对路径")
        if allow_missing_parents:
            parent = self.root
            for part in candidate.parent.parts:
                parent = parent / part
                if os.path.lexists(parent):
                    if is_link_or_reparse(parent) or not parent.is_dir():
                        raise WorkspaceError("父目录无效或包含符号链接")
                    try:
                        parent = parent.resolve(strict=True)
                        parent.relative_to(self.root)
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise WorkspaceError("父目录不在工作区内") from exc
        else:
            parent = self.resolve(str(candidate.parent), directory=True)
        target = parent / candidate.name
        if os.path.lexists(target):
            raise WorkspaceError("目标已存在")
        return target
