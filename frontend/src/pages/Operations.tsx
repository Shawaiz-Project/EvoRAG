import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Database,
  FlaskConical,
  Layers,
  TerminalSquare,
} from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { getReadiness } from "../api/status";
import { useResource } from "../api/resource";
import { Data, Empty, Json, Page, Table } from "../components/data";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  DocumentItem,
  Experiment,
  ExperimentDetail,
  Job,
  Workflow,
} from "../types";
import { TeacherChart } from "./Administration";
import { ExperimentResults } from "./Research";

type Conversation = { id: string; title: string | null; created_at: string };
type Message = {
  id: string;
  role: string;
  content: string;
  created_at: string;
};
type Model = {
  id: string;
  provider: string;
  name: string;
  version: string;
  dimensions: number | null;
  kind: string;
};
type RuntimeSettings = {
  max_upload_bytes: number;
  max_remote_bytes: number;
  teacher_max_refinements: number;
  mlflow_enabled: boolean;
  ragas_enabled: boolean;
  secure_session_cookie: boolean;
};
type Trace = {
  status: string;
  attributes: Record<string, unknown>;
  retrieval: Array<{
    chunk_id: string;
    stage: string;
    rank: number;
    score: number | null;
  }>;
};

export function Dashboard() {
  const health = useQuery({
    queryKey: ["readiness"],
    queryFn: ({ signal }) => getReadiness(signal),
    refetchInterval: 20000,
  });
  const documents = useResource<DocumentItem[]>("/api/v1/documents");
  const jobs = useResource<Job[]>("/api/v1/ingestion/jobs");
  const experiments = useResource<Experiment[]>("/api/v1/experiments");
  const workflows = useResource<Workflow[]>("/api/v1/workflows");
  const analytics = useResource<Record<string, number>>(
    "/api/v1/teacher/analytics",
  );
  const metrics = [
    {
      title: "Recent documents",
      value: documents.data?.length,
      icon: Database,
      path: "/knowledge",
    },
    {
      title: "Recent ingestion jobs",
      value: jobs.data?.length,
      icon: Layers,
      path: "/knowledge",
    },
    {
      title: "Recent runs",
      value: workflows.data?.length,
      icon: TerminalSquare,
      path: "/workflows",
    },
    {
      title: "Recent experiments",
      value: experiments.data?.length,
      icon: FlaskConical,
      path: "/experiments",
    },
  ];
  return (
    <Page
      title="Dashboard"
      description="Monitor the corpus, model workflows, and research activity."
    >
      <div className="metric-grid">
        {metrics.map(({ title, value, icon: Icon, path }) => (
          <Link className="metric-card" to={path} key={title}>
            <span className="metric-icon">
              <Icon size={19} aria-hidden />
            </span>
            <span className="metric-number">{value ?? "—"}</span>
            <span className="muted-text">{title}</span>
          </Link>
        ))}
      </div>
      <div className="section-grid main-grid">
        <Card title="Teacher review outcomes">
          <Data query={analytics}>
            {(counts) => <TeacherChart counts={counts} />}
          </Data>
        </Card>
        <Card
          title="Service readiness"
          actions={
            <Button
              variant="ghost"
              aria-label="Refresh readiness"
              onClick={() => void health.refetch()}
            >
              <Activity size={18} />
            </Button>
          }
        >
          <Data query={health}>
            {(status) => (
              <dl className="key-list">
                <div>
                  <dt>FastAPI</dt>
                  <dd>
                    <span className="service-dot ready" />
                    Responding
                  </dd>
                </div>
                <div>
                  <dt>PostgreSQL + pgvector</dt>
                  <dd>
                    <span
                      className={`service-dot ${status.checks.postgres ? "ready" : "fault"}`}
                    />
                    {status.checks.postgres ? "Connected" : "Unavailable"}
                  </dd>
                </div>
                <div>
                  <dt>Redis</dt>
                  <dd>
                    <span
                      className={`service-dot ${status.checks.redis ? "ready" : "fault"}`}
                    />
                    {status.checks.redis ? "Connected" : "Unavailable"}
                  </dd>
                </div>
              </dl>
            )}
          </Data>
        </Card>
      </div>
      <Card
        title="Recent workflows"
        actions={
          <Button variant="outline" asChild>
            <Link to="/workflows">View workflows</Link>
          </Button>
        }
      >
        <Data query={workflows}>
          {(rows) => (
            <Table
              headers={["Configuration", "Role", "Status", "Created"]}
              rows={rows.slice(0, 5).map((row) => [
                <Link key={row.id} to={`/workflows?run=${row.id}`}>
                  {row.configuration}
                </Link>,
                row.role,
                row.status,
                new Date(row.created_at).toLocaleString(),
              ])}
            />
          )}
        </Data>
      </Card>
    </Page>
  );
}

export function Analytics() {
  const reviews = useResource<Record<string, number>>(
    "/api/v1/teacher/analytics",
  );
  const experiments = useResource<Experiment[]>("/api/v1/experiments");
  const [params, setParams] = useSearchParams();
  const selected =
    params.get("experiment") ??
    experiments.data?.find((item) => item.status === "completed")?.id ??
    "";
  const details = useResource<ExperimentDetail>(
    `/api/v1/experiments/${selected}`,
    Boolean(selected),
  );
  return (
    <Page
      title="Analytics"
      description="Inspect verdict distributions and measured benchmark outcomes."
    >
      <Card title="Teacher verdicts">
        <Data query={reviews}>
          {(counts) => <TeacherChart counts={counts} />}
        </Data>
      </Card>
      <Card title="Experiment metrics">
        <Data query={experiments}>
          {(items) =>
            items.length ? (
              <label>
                Experiment
                <select
                  value={selected}
                  onChange={(event) =>
                    setParams({ experiment: event.target.value })
                  }
                >
                  <option value="">Choose an experiment</option>
                  {items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <Empty>No experiments have been run.</Empty>
            )
          }
        </Data>
        {selected && (
          <Data query={details}>
            {(item) => <ExperimentResults rows={item.results} />}
          </Data>
        )}
      </Card>
    </Page>
  );
}

export function MemoryCenter() {
  const conversations = useResource<Conversation[]>("/api/v1/conversations");
  const [params, setParams] = useSearchParams();
  const identifier = params.get("conversation") ?? "";
  const messages = useResource<Message[]>(
    `/api/v1/conversations/${identifier}/messages`,
    Boolean(identifier),
  );
  return (
    <Page
      title="Memory Center"
      description="Read the history of your saved conversations."
    >
      <Card title="Conversations">
        <Data query={conversations}>
          {(rows) => (
            <Table
              headers={["Title", "Created"]}
              rows={rows.map((row) => [
                <Button
                  key={row.id}
                  variant="ghost"
                  onClick={() => setParams({ conversation: row.id })}
                >
                  {row.title || "Untitled conversation"}
                </Button>,
                new Date(row.created_at).toLocaleString(),
              ])}
            />
          )}
        </Data>
      </Card>
      {identifier && (
        <Card title="Conversation history">
          <Data query={messages}>
            {(rows) =>
              rows.length ? (
                rows.map((message) => (
                  <article className="message-row" key={message.id}>
                    <h3>{message.role}</h3>
                    <p className="answer-text">{message.content}</p>
                  </article>
                ))
              ) : (
                <Empty>No messages in this conversation.</Empty>
              )
            }
          </Data>
        </Card>
      )}
    </Page>
  );
}

export function ModelRegistry() {
  const records = useResource<Model[]>("/api/v1/models");
  return (
    <Page
      title="Model Registry"
      description="Inspect versioned model artifacts used across embeddings and generation."
    >
      <Card title="Registered models">
        <Data query={records}>
          {(rows) => (
            <Table
              headers={["Model", "Provider", "Kind", "Dimensions", "Version"]}
              rows={rows.map((row) => [
                row.name,
                row.provider,
                row.kind,
                row.dimensions ?? "—",
                <code key={row.id} title={row.version}>
                  {row.version.length > 24
                    ? row.version.slice(0, 24) + "…"
                    : row.version}
                </code>,
              ])}
            />
          )}
        </Data>
      </Card>
    </Page>
  );
}

export function WorkflowManager() {
  const workflows = useResource<Workflow[]>("/api/v1/workflows");
  const [params, setParams] = useSearchParams();
  const id = params.get("run") ?? "";
  const trace = useResource<Trace>(`/api/v1/rag/runs/${id}`, Boolean(id));
  return (
    <Page
      title="Workflow Manager"
      description="Inspect execution status, retrieval ranks, and checkpoint traces."
    >
      <Card title="Execution history">
        <Data query={workflows}>
          {(rows) => (
            <Table
              headers={["Configuration", "Role", "Status", "Workflow"]}
              rows={rows.map((row) => [
                <Button
                  key={row.id}
                  variant="ghost"
                  onClick={() => setParams({ run: row.id })}
                >
                  {row.configuration}
                </Button>,
                row.role,
                row.status,
                row.steps.join(" / ") || row.strategy,
              ])}
            />
          )}
        </Data>
      </Card>
      {id && (
        <Data query={trace}>
          {(item) => (
            <Card title={`Run trace · ${item.status}`}>
              <Table
                headers={["Stage", "Rank", "Chunk", "Score"]}
                rows={item.retrieval.map((row) => [
                  row.stage,
                  row.rank,
                  row.chunk_id,
                  row.score?.toFixed(3) ?? "—",
                ])}
              />
              <details>
                <summary>Execution metadata</summary>
                <Json value={item.attributes} />
              </details>
            </Card>
          )}
        </Data>
      )}
    </Page>
  );
}

export function SettingsPage({
  theme,
  setTheme,
}: {
  theme: "dark" | "light";
  setTheme: (theme: "dark" | "light") => void;
}) {
  const runtime = useResource<RuntimeSettings>("/api/v1/settings");
  return (
    <Page
      title="Settings"
      description="Set your dashboard appearance and inspect local runtime limits."
    >
      <Card title="Appearance">
        <div className="form-stack">
          <label htmlFor="dashboard-theme">Theme</label>
          <select
            id="dashboard-theme"
            value={theme}
            onChange={(event) =>
              setTheme(event.target.value === "light" ? "light" : "dark")
            }
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
          </select>
        </div>
      </Card>
      <Card title="Runtime configuration">
        <Data query={runtime}>
          {(settings) => (
            <dl className="key-list">
              <div>
                <dt>Maximum file upload</dt>
                <dd>{Math.round(settings.max_upload_bytes / 1048576)} MiB</dd>
              </div>
              <div>
                <dt>Maximum remote content</dt>
                <dd>{Math.round(settings.max_remote_bytes / 1048576)} MiB</dd>
              </div>
              <div>
                <dt>Teacher refinements</dt>
                <dd>{settings.teacher_max_refinements}</dd>
              </div>
              <div>
                <dt>MLflow tracking</dt>
                <dd>{settings.mlflow_enabled ? "Enabled" : "Disabled"}</dd>
              </div>
              <div>
                <dt>RAGAS evaluator</dt>
                <dd>
                  {settings.ragas_enabled ? "Configured" : "Unconfigured"}
                </dd>
              </div>
              <div>
                <dt>HTTPS-only session cookie</dt>
                <dd>
                  {settings.secure_session_cookie ? "Enabled" : "Disabled"}
                </dd>
              </div>
            </dl>
          )}
        </Data>
      </Card>
    </Page>
  );
}
