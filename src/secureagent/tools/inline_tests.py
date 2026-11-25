"""Run generated Python and unittest code in a disposable, isolated container."""

from secureagent.tools.spec import spec

INLINE_TESTS = spec("run_inline_tests", "在一次性 Docker 容器中测试临时 Python 代码，不访问或写入宿主工作区。code 是 solution.py 的完整代码；tests 是 unittest 测试代码，可使用 from solution import ...。容器结束后删除临时文件。必须包含 unittest.TestCase 测试；按结果如实报告。",
                    {"code": {"type": "string", "description": "要测试的完整 Python 源代码，最多 64 KiB。"},
                     "tests": {"type": "string", "description": "导入 solution 的 unittest 测试源代码，最多 64 KiB。"}},
                    ["code", "tests"])


import json
import subprocess
import threading
import uuid


MAX_CODE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024

# This fixed bootstrap receives code only through stdin; it is never shell-expanded.
_BOOTSTRAP = '''
import json, os, pathlib, sys, tempfile, unittest
payload = json.load(sys.stdin)
with tempfile.TemporaryDirectory(prefix="sa-inline-", dir="/tmp") as folder:
    root = pathlib.Path(folder)
    (root / "solution.py").write_text(payload["code"], encoding="utf-8")
    (root / "test_solution.py").write_text(payload["tests"], encoding="utf-8")
    os.chdir(folder)
    sys.path.insert(0, folder)
    suite = unittest.defaultTestLoader.loadTestsFromName("test_solution")
    if not suite.countTestCases():
        print("No unittest test cases found", file=sys.stderr)
        sys.exit(5)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
'''


def _cleanup(name: str) -> bool:
    try:
        removed = subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, timeout=10, check=False)
        if removed.returncode == 0:
            return True
        inspected = subprocess.run(["docker", "container", "inspect", name], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, timeout=10, check=False)
        return inspected.returncode != 0 and b"No such container" in inspected.stderr
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_inline_tests(code: str, tests: str, image: str) -> dict:
    if not code.strip() or not tests.strip():
        return {"error": "程序和测试代码不能为空"}
    if len(code.encode("utf-8")) > MAX_CODE_BYTES or len(tests.encode("utf-8")) > MAX_CODE_BYTES:
        return {"error": "程序或测试代码超过 64 KiB"}
    name = "sa-inline-" + uuid.uuid4().hex
    args = ["docker", "run", "-i", "--rm", "--name", name, "--pull=never", "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=128", "--memory=512m", "--cpus=1", "--user=65534:65534",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,uid=65534,gid=65534",
            "--workdir", "/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint", "python", image, "-c", _BOOTSTRAP]
    payload = json.dumps({"code": code, "tests": tests}, ensure_ascii=True).encode("utf-8")
    try:
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        cleaned = _cleanup(name)
        return {"status": "UNKNOWN", "error": "测试启动失败，结果未知",
                "cleanup": "confirmed" if cleaned else "unknown"}
    raw = bytearray()

    def read_output():
        try:
            while True:
                chunk = process.stdout.read(8192)
                if not chunk:
                    break
                remaining = MAX_OUTPUT_BYTES + 1 - len(raw)
                if remaining > 0:
                    raw.extend(chunk[:remaining])
        except (OSError, ValueError):
            pass
        finally:
            process.stdout.close()

    def write_input():
        try:
            process.stdin.write(payload)
        except (OSError, ValueError):
            pass
        finally:
            process.stdin.close()

    reader = threading.Thread(target=read_output, daemon=True)
    writer = threading.Thread(target=write_input, daemon=True)
    reader.start()
    writer.start()
    try:
        exit_code = process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        cleaned = _cleanup(name)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        reader.join(timeout=2)
        writer.join(timeout=2)
        return {"status": "UNKNOWN", "error": "测试超时，结果未知",
                "cleanup": "confirmed" if cleaned else "unknown"}
    reader.join(timeout=2)
    writer.join(timeout=2)
    if reader.is_alive() or writer.is_alive():
        cleaned = _cleanup(name)
        return {"status": "UNKNOWN", "error": "测试输出或输入未结束",
                "cleanup": "confirmed" if cleaned else "unknown"}
    if exit_code == 125:
        cleaned = _cleanup(name)
        return {"status": "UNKNOWN", "error": "容器执行结果未知",
                "cleanup": "confirmed" if cleaned else "unknown"}
    cleaned = _cleanup(name)
    response = {"exit_code": exit_code, "output": raw[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"),
                "truncated": len(raw) > MAX_OUTPUT_BYTES, "cleanup": "confirmed" if cleaned else "unknown"}
    if not cleaned:
        response["warning"] = "容器清理未确认，请检查 sa-inline 容器"
    return response
