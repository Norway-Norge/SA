"""Docker-backed provider for isolated tests."""

from secureagent.security.observations import current_session
from secureagent.tools.inline_tests import run_inline_tests
from secureagent.tools.shell import run_tests


def installer(workspace, image, sandboxes):
    def install(scope, ctx):
        class DockerTests:
            def run(self, path, runner):
                session = current_session()
                return (sandboxes.run(session, path, runner) if session is not None
                        else run_tests(workspace, path, runner, image))

            def run_inline(self, code, tests):
                return run_inline_tests(code, tests, image)

        provider = DockerTests()
        provider.run = scope.guard(provider.run)
        provider.run_inline = scope.guard(provider.run_inline)
        def close():
            if not sandboxes.close():
                raise RuntimeError("测试容器清理未确认")
        scope.register_cleanup(close)
        # 注册能力提供方，并在插件卸载时撤销注册。
        scope.register_cleanup(ctx.testRunner.register_provider("docker", provider))
    return install
