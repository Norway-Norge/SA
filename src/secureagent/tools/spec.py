"""Tool descriptions supplied to an OpenAI-compatible model."""

def spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


PATH = {"type": "string", "description": "工作区内的相对路径；目录可用 ."}
READ = spec("read_file", "按行读取授权工作区中的 UTF-8 文本文件；使用 offset 和 limit 分页。",
            {"path": PATH,
             "offset": {"type": "integer", "minimum": 1, "description": "起始行号，从 1 开始，默认 1。"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 2000,
                       "description": "最多返回行数，默认 2000。"}}, ["path"])
SEARCH = spec("search_files", "在授权目录内按文字搜索 UTF-8 文件。",
              {"path": PATH, "query": {"type": "string", "minLength": 1, "maxLength": 200}},
              ["path", "query"])
WEB_SEARCH = spec("web_search", "搜索互联网，返回外部来源链接与摘要，不读取网页全文。",
                  {"query": {"type": "string", "description": "非空搜索词，最多 500 字符。"}}, ["query"])
WEB_FETCH = spec("web_fetch", "通过 DeepSeek Fetch API 读取公开网页；只返回请求网址，不提供目标网站状态或最终跳转地址。",
                 {"url": {"type": "string", "description": "公开网页的 HTTP(S) 网址，不得包含登录凭据。"}}, ["url"])
SKILL = spec("skill", "按名称读取已发现的本地 Skill 正文；不会执行脚本或授予工具权限。",
             {"name": {"type": "string", "description": "已发现且名称唯一的 Skill 名称。"}}, ["name"])
READ_SPILL = spec("read_spill", "分页读取当前会话保留的完整工具结果，offset 从 0 开始。",
                  {"spill_id": {"type": "string", "description": "工具结果返回的 spill_id。"},
                   "offset": {"type": "integer", "minimum": 0, "description": "起始字符偏移，默认 0。"},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 6000, "description": "返回字符数，默认 6000；转义后的输出过大时会进一步缩小。"}}, ["spill_id"])
GLOB = spec("glob", "列出工作区内匹配相对 glob 的普通文件绝对路径，按修改时间从新到旧；最多直接显示 100 项，更多匹配可用 read_spill 查看。扫描可能不完整。",
            {"pattern": {"type": "string", "description": "相对 glob，例如 **/*、*.py；不支持花括号。"},
             "path": {"type": "string", "description": "工作区内起始目录，默认 ."}}, ["pattern"])
TESTS = spec("run_tests", "在会话专属隔离容器中运行 Python 测试；使用首次运行时的工作区快照，之后宿主文件更改不会同步。",
             {"path": PATH, "runner": {"type": "string", "enum": ["unittest", "pytest"]}},
             ["path", "runner"])
INLINE_TESTS = spec("run_inline_tests", "在一次性 Docker 容器中测试临时 Python 代码，不访问或写入宿主工作区。code 是 solution.py 的完整代码；tests 是 unittest 测试代码，可使用 from solution import ...。容器结束后删除临时文件。必须包含 unittest.TestCase 测试；按结果如实报告。",
                    {"code": {"type": "string", "description": "要测试的完整 Python 源代码，最多 64 KiB。"},
                     "tests": {"type": "string", "description": "导入 solution 的 unittest 测试源代码，最多 64 KiB。"}},
                    ["code", "tests"])
CREATE = spec("create_file", "在工作区内创建新的 UTF-8 文本文件；不覆盖现有文件。缺失的父目录会自动创建。",
              {"path": PATH, "content": {"type": "string", "description": "新文件完整文本。"}},
              ["path", "content"])
WRITE = spec("write_file", "写入工作区内已有的空 UTF-8 文本文件。必须在当前会话先完整读取；非空文件待审批功能实现后再开放。",
             {"path": PATH, "content": {"type": "string", "description": "替换后的完整 UTF-8 文本。"}},
             ["path", "content"])
EDIT = spec("edit_file", "在已完整读取的文件中精确替换文字。提交后必须等待用户审批。默认要求原文只匹配一次。",
            {"path": PATH, "old_string": {"type": "string"}, "new_string": {"type": "string"},
             "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"])
