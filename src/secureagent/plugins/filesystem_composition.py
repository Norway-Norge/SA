"""Trusted host configuration for the single filesystem provider plugin."""

import os
from threading import RLock


class FileSystemComposition:
    def __init__(self, plugins, filesystem):
        self._plugins = plugins
        self._filesystem = filesystem
        self._lock = RLock()
        self._factories = {}
        self._current = None

    def register_factory(self, plugin_id, installer):
        if not isinstance(plugin_id, str) or not plugin_id.strip() or not callable(installer):
            raise ValueError("文件系统插件配置无效")
        with self._lock:
            if plugin_id in self._factories:
                raise ValueError("文件系统插件配置 ID 已存在")
            self._factories[plugin_id] = installer

    def reload(self, plugin_id):
        """Apply a trusted plugin ID; do not select a backend at tool-call time."""
        with self._lock:
            installer = self._factories.get(plugin_id)
            if installer is None:
                raise ValueError("文件系统插件配置 ID 未注册")
            if plugin_id != self._current and plugin_id in self._plugins.names():
                raise ValueError("插件配置 ID 已被其他插件使用")
            self._filesystem.assert_transition_allowed()
            # 先卸载旧插件，再安装配置选择的新 Provider。
            if self._current is not None:
                self._plugins.unload(self._current)
                self._current = None
            self._plugins.register_tool(plugin_id, installer, dependencies=("fs",))
            self._current = plugin_id

    def reload_from_environment(self):
        self.reload(os.environ.get("SA_FS_PLUGIN", "fs-provider-local"))
