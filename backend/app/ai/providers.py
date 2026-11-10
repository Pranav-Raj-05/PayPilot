"""LLM provider adapters with native tool/function calling. Provider is chosen by AI_PROVIDER."""
import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.errors import NotConfigured, ProviderError

log = logging.getLogger("paypilot.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict | None = None  # provider-native assistant message, echoed back on the next turn


class LLMProvider(Protocol):
    def generate(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResult: ...


def clean_schema(schema: dict) -> dict:
    """Inline $refs and strip keys that Gemini/OpenAI function schemas reject."""
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        node = {k: v for k, v in node.items() if k not in ("title", "$defs", "default", "additionalProperties", "examples")}
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            merged = {**walk(options[0]), **rest} if options else rest
            return merged
        return {k: walk(v) for k, v in node.items()}

    return walk(schema)


class GeminiProvider:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.gemini_api_key:
            raise NotConfigured("GEMINI_API_KEY is not configured")
        self.key, self.model = settings.gemini_api_key, settings.gemini_model
        self.http = http or httpx.Client(timeout=60)

    def generate(self, system, messages, tools) -> LLMResult:
        contents: list[dict] = []
        for m in messages:
            if m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                if m.get("raw"):
                    contents.append(m["raw"])
                else:
                    contents.append({"role": "model", "parts": [{"text": m["content"] or " "}]})
            elif m["role"] == "tool":
                part = {"functionResponse": {"name": m["name"], "response": {"result": json.loads(m["content"])}}}
                if contents and contents[-1]["role"] == "user" and contents[-1]["parts"] and "functionResponse" in contents[-1]["parts"][0]:
                    contents[-1]["parts"].append(part)  # parallel results must share one turn
                else:
                    contents.append({"role": "user", "parts": [part]})
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                "tools": [{"functionDeclarations": [{"name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in tools]}]}
        try:
            r = self.http.post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                               headers={"x-goog-api-key": self.key}, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach the AI provider") from exc
        if r.status_code != 200:
            log.error("gemini error %s %s", r.status_code, r.text[:300])
            raise ProviderError("The AI provider returned an error", details={"status": r.status_code})
        cands = r.json().get("candidates") or []
        if not cands or "content" not in cands[0]:
            return LLMResult(text="I couldn't produce a response for that. Could you rephrase?")
        content = cands[0]["content"]
        text, calls = "", []
        for i, p in enumerate(content.get("parts", [])):
            if "text" in p and not p.get("thought"):
                text += p["text"]
            if "functionCall" in p:
                calls.append(ToolCall(id=f"call_{i}", name=p["functionCall"]["name"], args=p["functionCall"].get("args") or {}))
        return LLMResult(text=text.strip(), tool_calls=calls, raw={"role": "model", "parts": content.get("parts", [])})


class OpenAIProvider:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not settings.openai_api_key:
            raise NotConfigured("OPENAI_API_KEY is not configured")
        self.key, self.model, self.base = settings.openai_api_key, settings.openai_model, settings.openai_base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=60)

    def generate(self, system, messages, tools) -> LLMResult:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                msgs.append({"role": "assistant", "content": m["content"] or None, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.args)}} for c in m["tool_calls"]]})
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                msgs.append({"role": m["role"], "content": m["content"]})
        body = {"model": self.model, "messages": msgs,
                "tools": [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in tools]}
        try:
            r = self.http.post(f"{self.base}/chat/completions", headers={"Authorization": f"Bearer {self.key}"}, json=body)
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach the AI provider") from exc
        if r.status_code != 200:
            log.error("openai error %s %s", r.status_code, r.text[:300])
            raise ProviderError("The AI provider returned an error", details={"status": r.status_code})
        msg = r.json()["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=c["id"], name=c["function"]["name"], args=args))
        return LLMResult(text=(msg.get("content") or "").strip(), tool_calls=calls)


def get_provider(settings: Settings) -> LLMProvider:
    if settings.ai_provider == "openai":
        return OpenAIProvider(settings)
    if settings.ai_provider == "gemini":
        return GeminiProvider(settings)
    raise NotConfigured(f"Unknown AI_PROVIDER '{settings.ai_provider}'")
