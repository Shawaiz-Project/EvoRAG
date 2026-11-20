import { apiFetch, postJson } from "./client";

export type Principal = {
  id: string;
  email: string;
  display_name: string;
  roles: string[];
};
export type Configuration = { key: string; strategy: string; version: number };
export type Citation = {
  source: string;
  chunk_id: string;
  document_id: string;
  document_version_id: string;
  title: string;
  locator: string;
  page: number | null;
  section: string | null;
  quote: string;
};
export type SourceHit = {
  chunk_id: string;
  title: string;
  content: string;
  page: number | null;
  section: string | null;
  score: number;
};
export type Answer = {
  run_id: string;
  conversation_id: string;
  status: "completed" | "abstained" | "failed";
  answer: string;
  strategy: string;
  configuration: string;
  configuration_version: number;
  citations: Citation[];
  citations_verified: boolean;
  sources: SourceHit[];
  queries: string[];
  execution: Record<string, unknown>;
  error: string | null;
};

export type TeacherClaim = {
  claim_id: number;
  text: string;
  status:
    "supported" | "conditional" | "unsupported" | "contradicted" | "unverified";
  evidence: {
    identifier: string;
    quote: string;
    chunk_id: string;
    title: string;
    page: number | null;
  }[];
};

export type TeacherReview = {
  id: string;
  verdict: "pass" | "revise" | "reject";
  report: {
    reason: string;
    integrity_problems: string[];
    evidence_chunk_ids: string[];
    model_critique: {
      verdict?: "pass" | "revise" | "reject";
      unsupported: string;
      missing: string;
    } | null;
    claims?: TeacherClaim[];
    claim_review_complete?: boolean;
  };
};

export async function streamAnswer(
  question: string,
  configuration: string,
  conversationId: string | null,
  onDelta: (text: string) => void,
  signal: AbortSignal,
): Promise<Answer> {
  const response = await apiFetch(
    "/api/v1/rag/query/stream",
    postJson(
      { question, configuration, conversation_id: conversationId },
      signal,
    ),
  );
  if (!response.body)
    throw new Error("The browser received an empty response.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) throw new Error("The stream ended before the run finished.");
      buffer += decoder.decode(value, { stream: true });
      let separator = buffer.indexOf("\n\n");
      while (separator >= 0) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const [type, data] = frame.split("\n");
        if (data?.startsWith("data: ")) {
          const payload = JSON.parse(data.slice(6));
          if (type === "event: delta") onDelta(payload.text as string);
          if (type === "event: error")
            throw new Error(payload.message as string);
          if (type === "event: done") return payload as Answer;
        }
        separator = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel();
  }
}
