"""Bounded pattern-based output redaction for common credential formats."""
"""输出脱敏"""

import re

REDACTED = "[REDACTED]"
KEY = re.compile(r"(?i)^(?:.*[_-])?(?:password|passwd|secret|token|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|authorization)$")
ASSIGNMENT = re.compile(
    r"(?i)(\b(?:[a-z0-9_-]*[_-])?(?:password|passwd|secret|token|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|authorization)\b[\"']?\s*[:=]\s*)(\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s\"',;&<>]+)"
)
BEARER = re.compile(r"(?i)(\bbearer\s+)([a-z0-9._~+/=-]+)")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|\Z)")
BEGIN_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
END_KEY = re.compile(r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")


def redact(value):
    if isinstance(value, dict):
        return {key: REDACTED if KEY.fullmatch(key) and item is not None else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = PRIVATE_KEY.sub(REDACTED, value)
        value = BEARER.sub(lambda match: match.group(1) + REDACTED, value)
        def replace(match):
            secret = match.group(2)
            quote = secret[0] if secret.startswith(("'", '"')) else ""
            return match.group(1) + quote + REDACTED + quote
        return ASSIGNMENT.sub(replace, value)
    return value


def install(scope, ctx):
    def sanitize(call, result):
        cleaned = redact(result)
        if call.name == "read_file" and isinstance(cleaned.get("lines"), list):
            # read_file also exposes one string per line; preserve PEM state
            # across these strings so key material is not leaked via that view.
            in_key = False
            for line, original_line in zip(cleaned["lines"], result["lines"]):
                if (not isinstance(line, dict) or not isinstance(original_line, dict)
                        or not isinstance(original_line.get("text"), str)):
                    continue
                text = original_line["text"]
                if in_key or BEGIN_KEY.search(text):
                    in_key = not bool(END_KEY.search(text))
                    line["text"] = REDACTED
        # Never present a modified approval payload as the exact operation.
        if result.get("status") == "REVIEW" and cleaned != result:
            return {"status": "DENIED", "error": "审批内容包含敏感信息，无法安全展示；操作未获批准。"}
        if cleaned != result:
            cleaned["output_redacted"] = True
        return cleaned
    scope.register_cleanup(ctx.tools.register_output_filter(scope.guard(sanitize)))
