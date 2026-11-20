import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { apiJson, postJson } from "../api/client";
import { errorText, useResource } from "../api/resource";
import { Data, Empty, Json, Page, Table } from "../components/data";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type {
  DocumentDetail,
  DocumentItem,
  Job,
  VersionDetail,
} from "../types";

const DOCUMENTS = "/api/v1/documents";
const JOBS = "/api/v1/ingestion/jobs";

export function KnowledgeBase() {
  const cache = useQueryClient();
  const documents = useResource<DocumentItem[]>(DOCUMENTS);
  const jobs = useResource<Job[]>(JOBS);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const processing =
    jobs.data?.some((job) => ["queued", "processing"].includes(job.status)) ??
    false;

  useEffect(() => {
    if (!processing) return;
    const timer = setInterval(() => {
      void cache.invalidateQueries({ queryKey: [JOBS] });
      void cache.invalidateQueries({ queryKey: [DOCUMENTS] });
    }, 2500);
    return () => clearInterval(timer);
  }, [cache, processing]);

  async function submit(event: FormEvent<HTMLFormElement>, remote = false) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setNotice("");
    setFailure("");
    try {
      if (remote) {
        await apiJson<Job>(
          "/api/v1/ingestion/remote",
          postJson({
            kind: form.get("kind"),
            url: form.get("url"),
            strategy: form.get("strategy"),
          }),
        );
      } else {
        const file = form.get("file");
        if (!(file instanceof File) || !file.size)
          throw new Error("Choose a file.");
        const params = new URLSearchParams({
          filename: file.name,
          strategy: String(form.get("strategy")),
        });
        await apiJson<Job>(`/api/v1/ingestion/files?${params}`, {
          method: "POST",
          headers: { "Content-Type": "application/octet-stream" },
          body: file,
        });
      }
      setNotice("Ingestion queued.");
      await cache.invalidateQueries({ queryKey: [JOBS] });
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setPending(false);
    }
  }

  async function retry(job: Job) {
    setFailure("");
    try {
      await apiJson(`${JOBS}/${job.id}/retry`, { method: "POST" });
      await cache.invalidateQueries({ queryKey: [JOBS] });
    } catch (error) {
      setFailure(errorText(error));
    }
  }

  return (
    <Page
      title="Knowledge Base"
      description="Ingest source material and track the documents available for retrieval."
    >
      <div className="section-grid">
        <Card title="Upload a file">
          <form className="form-stack" onSubmit={submit}>
            <label>
              File
              <input
                required
                name="file"
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown,.csv,.xlsx"
              />
            </label>
            <StrategySelect />
            <Button disabled={pending}>Queue upload</Button>
          </form>
        </Card>
        <Card title="Import a remote source">
          <form
            className="form-stack"
            onSubmit={(event) => void submit(event, true)}
          >
            <label>
              Source
              <select name="kind">
                <option value="website">Website</option>
                <option value="github">GitHub repository</option>
              </select>
            </label>
            <label>
              URL
              <input
                required
                name="url"
                type="url"
                placeholder="https://example.com"
              />
            </label>
            <StrategySelect />
            <Button disabled={pending}>Queue import</Button>
          </form>
        </Card>
      </div>
      {notice && <p role="status">{notice}</p>}
      {failure && (
        <p role="alert" className="form-error">
          {failure}
        </p>
      )}
      <Card title="Documents">
        <Data query={documents}>
          {(rows) => (
            <Table
              headers={["Title", "Source", "Location"]}
              rows={rows.map((row) => [
                <Link key={row.id} to={`/inspector?document=${row.id}`}>
                  {row.title}
                </Link>,
                row.source_kind,
                row.locator,
              ])}
            />
          )}
        </Data>
      </Card>
      <Card
        title="Ingestion jobs"
        actions={
          <Button variant="outline" onClick={() => void jobs.refetch()}>
            Refresh
          </Button>
        }
      >
        <Data query={jobs}>
          {(rows) => (
            <Table
              headers={["Source", "Status", "Attempts", "Details"]}
              rows={rows.map((row) => [
                row.locator,
                row.status,
                row.attempts,
                row.status === "failed" ? (
                  <div key={row.id} className="actions">
                    <span>{row.error}</span>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => void retry(row)}
                    >
                      Retry
                    </Button>
                  </div>
                ) : (
                  "—"
                ),
              ])}
            />
          )}
        </Data>
      </Card>
    </Page>
  );
}

function StrategySelect() {
  return (
    <label>
      Chunking
      <select name="strategy">
        <option value="recursive">Recursive</option>
        <option value="sentence">Sentence</option>
        <option value="semantic">Semantic</option>
      </select>
    </label>
  );
}

export function DocumentInspector() {
  const [parameters, setParameters] = useSearchParams();
  const selected = parameters.get("document") ?? "";
  const versionId = parameters.get("version") ?? "";
  const offset = Number(parameters.get("offset") || 0);
  const documents = useResource<DocumentItem[]>(DOCUMENTS);
  const detail = useResource<DocumentDetail>(
    `${DOCUMENTS}/${selected}`,
    Boolean(selected),
  );
  const versions = detail.data?.versions ?? [];
  const current =
    versionId ||
    versions.find((item) => item.is_current)?.id ||
    versions[0]?.id;
  const version = useResource<VersionDetail>(
    `${DOCUMENTS}/${selected}/versions/${current}?offset=${offset}&limit=50`,
    Boolean(current),
  );
  return (
    <Page
      title="Document Inspector"
      description="Inspect extracted text, provenance, and versioned chunks."
    >
      <Card>
        <Data query={documents}>
          {(rows) => (
            <label>
              Document
              <select
                value={selected}
                onChange={(event) =>
                  setParameters({ document: event.target.value })
                }
              >
                <option value="">Choose a document</option>
                {rows.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.title}
                  </option>
                ))}
              </select>
            </label>
          )}
        </Data>
        {selected && (
          <Data query={detail}>
            {(row) => (
              <label className="stack-label">
                Version
                <select
                  value={current}
                  onChange={(event) =>
                    setParameters({
                      document: row.document.id,
                      version: event.target.value,
                    })
                  }
                >
                  {row.versions.map((item) => (
                    <option key={item.id} value={item.id}>
                      Version {item.version}
                      {item.is_current ? " · current" : ""}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </Data>
        )}
      </Card>
      {current ? (
        <Data query={version}>
          {(data) => (
            <>
              <Card title="Extracted text">
                <pre className="document-text">{data.extracted_text}</pre>
              </Card>
              <Card title="Provenance">
                <Json
                  value={{
                    sha256: data.version.sha256,
                    ...data.version.attributes,
                  }}
                />
              </Card>
              <Card
                title="Chunks"
                actions={
                  <div className="actions">
                    <Button
                      variant="outline"
                      disabled={offset <= 0}
                      onClick={() =>
                        setParameters({
                          document: selected,
                          version: current,
                          offset: String(offset - 50),
                        })
                      }
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      disabled={data.chunks.length < 50}
                      onClick={() =>
                        setParameters({
                          document: selected,
                          version: current,
                          offset: String(offset + 50),
                        })
                      }
                    >
                      Next
                    </Button>
                  </div>
                }
              >
                <Table
                  headers={["Ordinal", "Page / section", "Content"]}
                  rows={data.chunks.map((item) => [
                    item.ordinal,
                    [item.page, item.section].filter(Boolean).join(" · ") ||
                      "—",
                    <details key={item.id}>
                      <summary>{item.content.slice(0, 140)}</summary>
                      <p>{item.content}</p>
                      <Json
                        value={{
                          id: item.id,
                          parent_id: item.parent_id,
                          ...item.attributes,
                        }}
                      />
                    </details>,
                  ])}
                />
              </Card>
            </>
          )}
        </Data>
      ) : (
        <Empty>Choose a document to inspect its versions.</Empty>
      )}
    </Page>
  );
}
