from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.ollama import model_digests
from app.db.identity import ModelArtifact
from app.retrieval.providers import ModelUnavailable


@dataclass(frozen=True, slots=True)
class Generation[Output: BaseModel]:
    output: Output
    model_version: str
    input_tokens: int | None
    output_tokens: int | None


class Generator(Protocol):
    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]: ...


class StructuredOutputError(ModelUnavailable):
    pass


class GeminiGenerator:
    def __init__(self, name: str, version: str, key: str) -> None:
        self.name = name.removeprefix("models/")
        self.version = version
        self.key = key

    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]:
        name = quote(self.name, safe="")
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": instructions}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema.model_json_schema(),
            },
        }
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=90),
                    trust_env=False,
                    headers={"x-goog-api-key": self.key},
                ) as client,
                client.post(endpoint, json=body, allow_redirects=False) as response,
            ):
                if response.status != 200:
                    raise ModelUnavailable(f"Gemini generation returned HTTP {response.status}")
                data = await response.json()
                content = "".join(
                    part["text"]
                    for part in data["candidates"][0]["content"]["parts"]
                    if "text" in part and not part.get("thought")
                )
                usage = data.get("usageMetadata", {})
                return Generation(
                    schema.model_validate_json(content),
                    data.get("modelVersion") or self.version,
                    usage.get("promptTokenCount"),
                    usage.get("candidatesTokenCount"),
                )
        except (aiohttp.ClientError, TimeoutError, KeyError, IndexError, TypeError) as error:
            raise ModelUnavailable("Gemini generation request failed") from error
        except ValidationError as error:
            raise StructuredOutputError("Gemini returned invalid structured output") from error


class OllamaGenerator:
    def __init__(self, name: str, version: str, base_url: str) -> None:
        self.name = name
        self.version = version
        self.base_url = base_url
        self.endpoint = f"{base_url.rstrip('/')}/api/chat"

    async def generate[Output: BaseModel](
        self, instructions: str, prompt: str, schema: type[Output], max_tokens: int
    ) -> Generation[Output]:
        body = {
            "model": self.name,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"temperature": 0, "num_predict": max_tokens},
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120), trust_env=False
            ) as client:
                tags = await model_digests(client, self.base_url)
                if tags.get(self.name) != self.version:
                    raise ModelUnavailable("Ollama generation artifact changed or is missing")
                async with client.post(self.endpoint, json=body, allow_redirects=False) as response:
                    if response.status != 200:
                        raise ModelUnavailable(f"Ollama generation returned HTTP {response.status}")
                    data = await response.json()
                    return Generation(
                        schema.model_validate_json(data["message"]["content"]),
                        self.version,
                        data.get("prompt_eval_count"),
                        data.get("eval_count"),
                    )
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError) as error:
            raise ModelUnavailable("Ollama generation request failed") from error
        except ValidationError as error:
            raise StructuredOutputError("Ollama returned invalid structured output") from error


def generator_for(model: ModelArtifact, settings: Settings) -> Generator:
    if model.kind != "llm":
        raise ModelUnavailable("The registered model does not generate answers")
    if model.provider == "gemini":
        key = settings.gemini_api_key
        if key is None or not key.get_secret_value():
            raise ModelUnavailable("Set GEMINI_API_KEY to enable Gemini generation")
        return GeminiGenerator(model.name, model.version, key.get_secret_value())
    if model.provider == "ollama":
        return OllamaGenerator(model.name, model.version, settings.ollama_url)
    raise ModelUnavailable("Unsupported generation provider")
