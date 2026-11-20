import importlib
import json
from math import isfinite
from typing import Any

import aiohttp
from pydantic import BaseModel

from app.core.config import Settings
from app.core.ollama import model_digests
from app.rag.engine import Answer
from app.rag.providers import OllamaGenerator


class RagasOllama:
    def __init__(self, generator: OllamaGenerator) -> None:
        self.generator = generator

    async def agenerate[Output: BaseModel](self, prompt: str, schema: type[Output]) -> Output:
        result = await self.generator.generate(
            "Apply the evaluation rubric and return the requested structured score data.",
            prompt,
            schema,
            1000,
        )
        return result.output


async def ragas_faithfulness(settings: Settings, question: str, answer: Answer) -> float | None:
    if not settings.ragas_ollama_model or answer.status != "completed":
        return None
    metrics = importlib.import_module("ragas.metrics.collections")
    name = settings.ragas_ollama_model
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=10), trust_env=False
    ) as client:
        digests = await model_digests(client, settings.ollama_url)
    if name not in digests:
        raise ValueError("Pull the configured Ollama evaluation model before running RAGAS")
    llm = RagasOllama(OllamaGenerator(name, digests[name], settings.ollama_url))
    scorer = metrics.Faithfulness(llm=llm)
    result = await scorer.ascore(
        user_input=question,
        response=answer.answer,
        retrieved_contexts=[source.context or source.content for source in answer.sources],
    )
    score = float(result.value)
    return score if isfinite(score) else None


def track(
    settings: Settings, name: str, snapshot: dict[str, Any], rows: list[dict[str, Any]]
) -> str | None:
    if not settings.mlflow_tracking_uri:
        return None
    mlflow = importlib.import_module("mlflow")
    client = mlflow.MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    experiment = client.get_experiment_by_name("EvoRAG")
    experiment_id = experiment.experiment_id if experiment else client.create_experiment("EvoRAG")
    run = client.create_run(experiment_id, run_name=name)
    run_id = run.info.run_id
    try:
        client.log_param(run_id, "dataset_version", snapshot["dataset_version"])
        client.log_dict(run_id, json.loads(json.dumps(snapshot)), "configuration.json")
        client.log_dict(run_id, {"results": rows}, "results.json")
        for row in rows:
            for key, value in row["metrics"].items():
                if isinstance(value, (float, int)) and not isinstance(value, bool):
                    client.log_metric(run_id, f"{row['case']}.{row['variant']}.{key}", value)
    except Exception:
        client.set_terminated(run_id, status="FAILED")
        raise
    client.set_terminated(run_id)
    return run_id
