import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiJson, postJson } from "../api/client";
import type { Answer, Configuration } from "../api/rag";
import { errorText, useResource } from "../api/resource";
import { Data, Empty, Json, Page, Table } from "../components/data";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  Dataset,
  DatasetDetail,
  Experiment,
  ExperimentDetail,
  ExperimentRow,
} from "../types";

const DATASETS = "/api/v1/benchmarks/datasets";
const EXPERIMENTS = "/api/v1/experiments";
const VARIANTS = [
  "naive",
  "advanced",
  "corrective",
  "teacher-guided",
  "without-parent-context",
] as const;

export function ComparisonLab() {
  const configs = useResource<Configuration[]>("/api/v1/rag/configurations");
  const [results, setResults] = useState<Answer[] | null>(null);
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState("");

  async function compare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const strategies = form.getAll("strategy");
    if (strategies.length < 2) {
      setFailure("Select at least two configurations.");
      return;
    }
    setWorking(true);
    setFailure("");
    try {
      setResults(
        await apiJson<Answer[]>(
          "/api/v1/rag/compare",
          postJson({
            question: form.get("question"),
            configurations: strategies,
            budget_steps: Number(form.get("budget")),
          }),
        ),
      );
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setWorking(false);
    }
  }

  return (
    <Page
      title="RAG Comparison Lab"
      description="Run different pipelines on one question and compare their evidence."
    >
      <Card>
        <Data query={configs}>
          {(items) => (
            <form className="form-stack" onSubmit={compare}>
              <label>
                Question
                <textarea name="question" required maxLength={4000} rows={3} />
              </label>
              <fieldset className="check-grid">
                <legend>Configurations</legend>
                {items.map((item) => (
                  <label key={item.key} className="checkbox-label">
                    <input
                      type="checkbox"
                      name="strategy"
                      value={item.key}
                      defaultChecked={
                        item.key === "advanced" || item.key === "naive"
                      }
                    />
                    {item.key}
                  </label>
                ))}
              </fieldset>
              <label>
                Execution steps
                <input
                  name="budget"
                  type="number"
                  min={1}
                  max={4}
                  required
                  defaultValue={3}
                />
              </label>
              <Button disabled={working}>
                {working ? "Comparing…" : "Run comparison"}
              </Button>
            </form>
          )}
        </Data>
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </Card>
      {results && (
        <Card title="Comparison results">
          <Table
            headers={["Configuration", "Status", "Sources", "Answer"]}
            rows={results.map((row) => [
              row.configuration,
              row.status,
              row.sources.length,
              <details key={row.run_id}>
                <summary>{row.answer || row.error || "No answer"}</summary>
                <Json
                  value={{
                    citations: row.citations,
                    stages: row.execution.retrieval_stages,
                    workflow: row.execution.workflow,
                  }}
                />
              </details>,
            ])}
          />
        </Card>
      )}
    </Page>
  );
}

export function BenchmarkManager({ admin }: { admin: boolean }) {
  const datasets = useResource<Dataset[]>(DATASETS);
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("dataset") ?? "";
  const details = useResource<DatasetDetail>(
    `${DATASETS}/${selected}`,
    Boolean(selected),
  );
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setFailure("");
    try {
      const evidence = String(form.get("evidence") ?? "")
        .split(/[\s,]+/)
        .filter(Boolean)
        .map((chunk_id) => ({ chunk_id, relevance: 1 }));
      const result = await apiJson<Dataset>(
        DATASETS,
        postJson({
          key: form.get("key"),
          version: Number(form.get("version")),
          description: form.get("description"),
          cases: [
            {
              key: form.get("case_key"),
              question: form.get("question"),
              golden_answer: form.get("golden") || null,
              expected_verdict: form.get("verdict") || null,
              evidence,
            },
          ],
        }),
      );
      await cache.invalidateQueries({ queryKey: [DATASETS] });
      setParams({ dataset: result.id });
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <Page
      title="Benchmark Manager"
      description="Manage versioned datasets, golden answers, and graded evidence labels."
    >
      <Card title="Datasets">
        <Data query={datasets}>
          {(rows) => (
            <Table
              headers={["Dataset", "Version", "Description"]}
              rows={rows.map((row) => [
                <Link key={row.id} to={`/benchmarks?dataset=${row.id}`}>
                  {row.key}
                </Link>,
                row.version,
                row.description,
              ])}
            />
          )}
        </Data>
      </Card>
      {selected && (
        <Card title="Labeled cases">
          <Data query={details}>{(row) => <Json value={row.cases} />}</Data>
        </Card>
      )}
      {admin && (
        <Card title="Create a dataset version">
          <form className="form-stack" onSubmit={create}>
            <div className="section-grid">
              <label>
                Dataset key
                <input
                  name="key"
                  required
                  pattern="[A-Za-z0-9_-]+"
                  maxLength={60}
                />
              </label>
              <label>
                Version
                <input
                  name="version"
                  required
                  type="number"
                  min={1}
                  defaultValue={1}
                />
              </label>
            </div>
            <label>
              Description
              <input name="description" required maxLength={3000} />
            </label>
            <div className="section-grid">
              <label>
                Case key
                <input
                  name="case_key"
                  required
                  pattern="[A-Za-z0-9_-]+"
                  maxLength={60}
                />
              </label>
              <label>
                Expected teacher verdict
                <select name="verdict">
                  <option value="">Unlabeled</option>
                  <option value="pass">Pass</option>
                  <option value="revise">Revise</option>
                  <option value="reject">Reject</option>
                </select>
              </label>
            </div>
            <label>
              Question
              <textarea name="question" required maxLength={4000} rows={2} />
            </label>
            <label>
              Golden answer
              <textarea name="golden" maxLength={4000} rows={2} />
            </label>
            <label>
              Relevant chunk IDs
              <input
                name="evidence"
                placeholder="Separate chunk IDs with commas"
              />
            </label>
            <Button disabled={pending}>
              {pending ? "Saving…" : "Create version"}
            </Button>
          </form>
          {failure && (
            <p className="form-error" role="alert">
              {failure}
            </p>
          )}
        </Card>
      )}
    </Page>
  );
}

export function Experiments() {
  const datasets = useResource<Dataset[]>(DATASETS);
  const runs = useResource<Experiment[]>(EXPERIMENTS);
  const cache = useQueryClient();
  const [params, setParams] = useSearchParams();
  const id = params.get("experiment") ?? "";
  const selected = useResource<ExperimentDetail>(
    `${EXPERIMENTS}/${id}`,
    Boolean(id),
  );
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");

  async function runExperiment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const variants = form.getAll("variant");
    if (!variants.length) {
      setFailure("Select a variant.");
      return;
    }
    setPending(true);
    setFailure("");
    try {
      const experiment = await apiJson<Experiment>(
        EXPERIMENTS,
        postJson({
          name: form.get("name"),
          dataset_id: form.get("dataset"),
          variants,
          k: Number(form.get("k")),
        }),
      );
      setParams({ experiment: experiment.id });
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setPending(false);
      await cache.invalidateQueries({ queryKey: [EXPERIMENTS] });
    }
  }

  return (
    <Page
      title="Experiments"
      description="Run labeled benchmarks and inspect reproducible measurements."
    >
      <Card title="New experiment">
        <Data query={datasets}>
          {(items) =>
            items.length ? (
              <form className="form-stack" onSubmit={runExperiment}>
                <div className="section-grid">
                  <label>
                    Name
                    <input name="name" required maxLength={120} />
                  </label>
                  <label>
                    Dataset
                    <select name="dataset">
                      {items.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.key} · v{item.version}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <fieldset className="check-grid">
                  <legend>Variants</legend>
                  {VARIANTS.map((variant, index) => (
                    <label key={variant} className="checkbox-label">
                      <input
                        type="checkbox"
                        name="variant"
                        value={variant}
                        defaultChecked={index < 4}
                      />
                      {variant}
                    </label>
                  ))}
                </fieldset>
                <label>
                  Retrieval cutoff
                  <input
                    type="number"
                    name="k"
                    required
                    min={1}
                    max={20}
                    defaultValue={5}
                  />
                </label>
                <Button disabled={pending}>
                  {pending ? "Benchmark running…" : "Run experiment"}
                </Button>
              </form>
            ) : (
              <Empty>
                <Link to="/benchmarks">Create a benchmark dataset</Link> to run
                an experiment.
              </Empty>
            )
          }
        </Data>
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </Card>
      <Card title="Experiment history">
        <Data query={runs}>
          {(rows) => (
            <Table
              headers={["Name", "Status", "Created"]}
              rows={rows.map((row) => [
                <Link key={row.id} to={`/experiments?experiment=${row.id}`}>
                  {row.name}
                </Link>,
                row.status,
                new Date(row.created_at).toLocaleString(),
              ])}
            />
          )}
        </Data>
      </Card>
      {id && (
        <Data query={selected}>
          {(record) => (
            <Card
              title={record.experiment.name}
              actions={
                <Button
                  variant="outline"
                  onClick={() => exportExperiment(record)}
                >
                  Export JSON
                </Button>
              }
            >
              <ExperimentResults rows={record.results} />
              <details>
                <summary>Configuration snapshot</summary>
                <Json value={record.experiment.settings} />
              </details>
            </Card>
          )}
        </Data>
      )}
    </Page>
  );
}

export function ExperimentResults({ rows }: { rows: ExperimentRow[] }) {
  return (
    <Table
      headers={["Variant", "Status", "Recall", "MRR", "Latency (ms)", "Report"]}
      rows={rows.map((row) => [
        row.attributes.variant,
        row.attributes.status,
        formatMetric(row.metrics.recall_at_k),
        formatMetric(row.metrics.mrr_at_k),
        formatMetric(row.metrics.wall_latency_ms),
        <details key={row.id}>
          <summary>Metrics and answer</summary>
          <p>{row.attributes.answer}</p>
          <Json value={row.metrics} />
        </details>,
      ])}
    />
  );
}

function formatMetric(value: number | string | null | undefined): string {
  return typeof value === "number"
    ? Number(value.toFixed(3)).toString()
    : (value ?? "—");
}

function exportExperiment(record: ExperimentDetail) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(record, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `evorag-${record.experiment.id}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
