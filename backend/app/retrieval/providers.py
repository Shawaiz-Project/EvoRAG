import asyncio
import importlib
from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal, Protocol

import aiohttp

from app.core.config import Settings
from app.core.ollama import model_digests
from app.db.identity import ModelArtifact
from app.db.knowledge import EmbeddingSpace

Purpose = Literal["query", "document"]


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    provider: str
    name: str
    version: str
    dimensions: int
    recipe: str


class EmbeddingProvider(Protocol):
    identity: EmbeddingIdentity

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]: ...


class Reranker(Protocol):
    async def score(self, question: str, passages: Sequence[str]) -> list[float]: ...


def validate_vectors(vectors: list[list[float]], count: int, dimensions: int) -> list[list[float]]:
    if len(vectors) != count or any(
        len(vector) != dimensions or not all(isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise ModelUnavailable("Provider returned embeddings with an unexpected shape or values")
    return vectors


def sentence_transformers() -> Any:
    try:
        return importlib.import_module("sentence_transformers")
    except ImportError as error:
        raise ModelUnavailable("Install the local-models extra to load local models") from error


class SentenceTransformerProvider:
    def __init__(self, identity: EmbeddingIdentity) -> None:
        self.identity = identity
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _inputs(self, texts: Sequence[str], purpose: Purpose) -> list[str]:
        provider = self.identity.provider
        if provider == "e5":
            prefix = "query: " if purpose == "query" else "passage: "
        elif provider == "bge" and purpose == "query":
            prefix = "Represent this sentence for searching relevant passages: "
        else:
            prefix = ""
        return [prefix + text for text in texts]

    def _encode(self, inputs: list[str]) -> list[list[float]]:
        if self._model is None:
            self._model = sentence_transformers().SentenceTransformer(
                self.identity.name, revision=self.identity.version, trust_remote_code=False
            )
        return self._model.encode(
            inputs, normalize_embeddings=True, show_progress_bar=False
        ).tolist()

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if not texts:
            return []
        async with self._lock:
            try:
                vectors = await asyncio.to_thread(self._encode, self._inputs(texts, purpose))
            except Exception as error:
                raise ModelUnavailable(
                    "Could not encode with the configured local model"
                ) from error
        return validate_vectors(vectors, len(texts), self.identity.dimensions)


class GeminiEmbeddingProvider:
    def __init__(self, identity: EmbeddingIdentity, api_key: str) -> None:
        self.identity = identity
        self.api_key = api_key

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if not texts:
            return []
        model = self.identity.name.removeprefix("models/")
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
        )
        requests = []
        for text in texts:
            request = {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.identity.dimensions,
                "taskType": "RETRIEVAL_QUERY" if purpose == "query" else "RETRIEVAL_DOCUMENT",
            }
            requests.append(request)
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=45),
                    trust_env=False,
                    headers={"x-goog-api-key": self.api_key},
                ) as client,
                client.post(
                    endpoint, json={"requests": requests}, allow_redirects=False
                ) as response,
            ):
                if response.status != 200:
                    raise ModelUnavailable(f"Gemini embedding returned HTTP {response.status}")
                payload = await response.json()
                vectors = [item["values"] for item in payload["embeddings"]]
        except (TimeoutError, aiohttp.ClientError, KeyError, ValueError, TypeError) as error:
            raise ModelUnavailable("Gemini embedding request failed") from error
        return validate_vectors(vectors, len(texts), self.identity.dimensions)


class OllamaEmbeddingProvider:
    def __init__(self, identity: EmbeddingIdentity, base_url: str) -> None:
        self.identity = identity
        self.base_url = base_url
        self.endpoint = f"{base_url.rstrip('/')}/api/embed"

    async def embed(self, texts: Sequence[str], purpose: Purpose) -> list[list[float]]:
        if not texts:
            return []
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=45), trust_env=False
            ) as client:
                tags = await model_digests(client, self.base_url)
                if tags.get(self.identity.name) != self.identity.version:
                    raise ModelUnavailable("Ollama embedding artifact changed or is missing")
                async with client.post(
                    self.endpoint,
                    json={"model": self.identity.name, "input": list(texts)},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise ModelUnavailable(f"Ollama embedding returned HTTP {response.status}")
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as error:
            raise ModelUnavailable("Ollama embedding request failed") from error
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list):
            raise ModelUnavailable("Ollama did not return embeddings")
        return validate_vectors(vectors, len(texts), self.identity.dimensions)


class CrossEncoderReranker:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self._model: Any = None
        self._lock = asyncio.Lock()

    def _predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        if self._model is None:
            self._model = sentence_transformers().CrossEncoder(
                self.name, revision=self.version, trust_remote_code=False
            )
        return self._model.predict(pairs, show_progress_bar=False).tolist()

    async def score(self, question: str, passages: Sequence[str]) -> list[float]:
        async with self._lock:
            try:
                scores = await asyncio.to_thread(
                    self._predict, [(question, text) for text in passages]
                )
            except Exception as error:
                raise ModelUnavailable("Cross-encoder inference failed") from error
        if len(scores) != len(passages) or not all(isfinite(score) for score in scores):
            raise ModelUnavailable("Cross-encoder returned invalid scores")
        return scores


def embedding_identity(space: EmbeddingSpace, model: ModelArtifact) -> EmbeddingIdentity:
    if (
        space.model_id != model.id
        or model.kind != "embedding"
        or model.dimensions != space.dimensions
    ):
        raise ModelUnavailable("Model is incompatible with the configured embedding space")
    return EmbeddingIdentity(
        model.provider, model.name, model.version, space.dimensions, space.recipe
    )


def embedding_provider(identity: EmbeddingIdentity, settings: Settings) -> EmbeddingProvider:
    recipes = {
        "sentence-transformers": "normalize-v1",
        "bge": "bge-retrieval-v1",
        "e5": "e5-retrieval-v1",
        "gemini": "gemini-task-v1",
        "ollama": "ollama-v1",
    }
    if recipes.get(identity.provider) != identity.recipe:
        raise ModelUnavailable("Unsupported provider or embedding recipe")
    if identity.provider == "gemini":
        key = settings.gemini_api_key
        if key is None or not key.get_secret_value():
            raise ModelUnavailable("Set GEMINI_API_KEY to enable Gemini embeddings")
        if identity.name.removeprefix("models/") != "gemini-embedding-001":
            raise ModelUnavailable("The Gemini task recipe requires gemini-embedding-001")
        return GeminiEmbeddingProvider(identity, key.get_secret_value())
    if identity.provider == "ollama":
        return OllamaEmbeddingProvider(identity, settings.ollama_url)
    return SentenceTransformerProvider(identity)
