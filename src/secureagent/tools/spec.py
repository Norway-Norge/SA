"""Tool descriptions supplied to an OpenAI-compatible model."""

def spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


PATH = {"type": "string", "description": "工作区内的相对路径；目录可用 ."}
