import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiJson, postJson } from "../api/client";
import type { Principal, TeacherReview } from "../api/rag";
import { errorText, useResource } from "../api/resource";
import { Data, Empty, Json, Page, Table } from "../components/data";
import { TeacherClaims } from "../components/teacher-claims";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import type { Gap, Proposal } from "../types";

const PROPOSALS = "/api/v1/learning/proposals";

export function TeacherChart({ counts }: { counts: Record<string, number> }) {
  const graph = Object.entries(counts).map(([outcome, total]) => ({
    outcome,
    total,
  }));
  if (!graph.some((point) => point.total > 0))
    return <Empty>No teacher reviews yet.</Empty>;
  return (
    <div className="chart-frame" aria-label="Teacher verdict counts">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={graph}>
          <CartesianGrid vertical={false} stroke="var(--line)" />
          <XAxis
            dataKey="outcome"
            stroke="var(--muted-text)"
            tickLine={false}
          />
          <YAxis
            allowDecimals={false}
            stroke="var(--muted-text)"
            width={35}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: "var(--card)",
              borderColor: "var(--line)",
              color: "var(--foreground)",
            }}
          />
          <Bar
            dataKey="total"
            name="Reviews"
            fill="var(--accent)"
            radius={[5, 5, 0, 0]}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TeacherCenter() {
  const reviews = useResource<TeacherReview[]>("/api/v1/teacher/reviews");
  const analytics = useResource<Record<string, number>>(
    "/api/v1/teacher/analytics",
  );
  return (
    <Page
      title="Teacher Center"
      description="Inspect independently retrieved evidence and saved verdicts."
    >
      <Card title="Review outcomes">
        <Data query={analytics}>
          {(counts) => <TeacherChart counts={counts} />}
        </Data>
      </Card>
      <Card title="Review history">
        <Data query={reviews}>
          {(rows) => (
            <Table
              headers={["Verdict", "Independent chunks", "Report"]}
              rows={rows.map((item) => [
                item.verdict,
                item.report.evidence_chunk_ids.length,
                <details key={item.id}>
                  <summary>{item.report.reason}</summary>
                  <TeacherClaims report={item.report} />
                  <Json value={item.report} />
                </details>,
              ])}
            />
          )}
        </Data>
      </Card>
    </Page>
  );
}

export function LearningCenter({ user }: { user: Principal }) {
  const admin = user.roles.includes("admin");
  const proposals = useResource<Proposal[]>(PROPOSALS, admin);
  const gaps = useResource<Gap[]>("/api/v1/learning/gaps", admin);
  const cache = useQueryClient();
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [working, setWorking] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  async function act(
    proposal: Proposal,
    operation: string,
    decision?: "approve" | "reject",
  ) {
    setWorking(proposal.id);
    setNotice("");
    setError("");
    try {
      const updated = await apiJson<Proposal>(
        `${PROPOSALS}/${proposal.id}/${operation}`,
        decision
          ? postJson({ decision, reason: reasons[proposal.id] ?? "" })
          : { method: "POST" },
      );
      setNotice(`Proposal updated to ${updated.status.replace("_", " ")}.`);
      await cache.invalidateQueries({ queryKey: [PROPOSALS] });
      await cache.invalidateQueries({ queryKey: ["/api/v1/documents"] });
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setWorking(null);
    }
  }

  return (
    <Page
      title="Learning Center"
      description="Review versioned corrections, verify evidence, and control publication."
    >
      {!admin ? (
        <Card>
          <Empty>Administrator access is required.</Empty>
        </Card>
      ) : (
        <>
          {notice && <p role="status">{notice}</p>}
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <Data query={proposals}>
            {(rows) =>
              rows.length ? (
                rows.map((proposal) => (
                  <Card
                    key={proposal.id}
                    title={proposal.status.replace("_", " ")}
                  >
                    <div className="section-grid">
                      <div>
                        <h3 className="field-title">Current passage</h3>
                        <p>{proposal.proposed_change.replace_text || "—"}</p>
                      </div>
                      <div>
                        <h3 className="field-title">Proposed replacement</h3>
                        <p>{proposal.proposed_change.new_text || "—"}</p>
                      </div>
                    </div>
                    {proposal.attributes.verification != null && (
                      <details>
                        <summary>Verification report</summary>
                        <Json value={proposal.attributes.verification} />
                      </details>
                    )}
                    {["pending", "under_review"].includes(proposal.status) && (
                      <div className="form-stack">
                        <label>
                          Review reason
                          <textarea
                            rows={2}
                            value={reasons[proposal.id] ?? ""}
                            onChange={(event) =>
                              setReasons({
                                ...reasons,
                                [proposal.id]: event.target.value,
                              })
                            }
                          />
                        </label>
                        <div className="actions">
                          <Button
                            variant="outline"
                            disabled={working === proposal.id}
                            onClick={() => void act(proposal, "verify")}
                          >
                            Verify
                          </Button>
                          <Button
                            disabled={
                              working === proposal.id ||
                              proposal.status !== "under_review" ||
                              (reasons[proposal.id]?.trim().length ?? 0) < 3
                            }
                            onClick={() =>
                              void act(proposal, "decision", "approve")
                            }
                          >
                            Approve
                          </Button>
                          <Button
                            variant="danger"
                            disabled={
                              working === proposal.id ||
                              (reasons[proposal.id]?.trim().length ?? 0) < 3
                            }
                            onClick={() =>
                              void act(proposal, "decision", "reject")
                            }
                          >
                            Reject
                          </Button>
                        </div>
                      </div>
                    )}
                    {proposal.status === "approved" && (
                      <Button
                        className="mt-4"
                        disabled={working === proposal.id}
                        onClick={() => void act(proposal, "publish")}
                      >
                        Publish and index
                      </Button>
                    )}
                    {proposal.status === "published" && (
                      <Button
                        className="mt-4"
                        variant="outline"
                        disabled={working === proposal.id}
                        onClick={() => void act(proposal, "rollback")}
                      >
                        Rollback
                      </Button>
                    )}
                  </Card>
                ))
              ) : (
                <Card>
                  <Empty>There are no learning proposals.</Empty>
                </Card>
              )
            }
          </Data>
          <Card title="Repeated failures">
            <Data query={gaps}>
              {(rows) => (
                <Table
                  headers={["Question", "Failed runs"]}
                  rows={rows.map((gap) => [gap.question, gap.failures])}
                />
              )}
            </Data>
          </Card>
        </>
      )}
    </Page>
  );
}
