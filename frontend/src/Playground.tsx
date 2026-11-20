import { useEffect, useRef, useState, type FormEvent } from "react";
import { apiJson, postJson } from "./api/client";
import { errorText, useResource } from "./api/resource";
import {
  streamAnswer,
  type Answer,
  type Configuration,
  type TeacherReview,
} from "./api/rag";
import { Data, Page } from "./components/data";
import { TeacherClaims } from "./components/teacher-claims";
import { Button } from "./components/ui/button";
import { Card } from "./components/ui/card";

type Turn = { question: string; result: Answer };

export default function Playground() {
  const configs = useResource<Configuration[]>("/api/v1/rag/configurations");
  const [configuration, setConfiguration] = useState("advanced");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [streamText, setStreamText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const active = useRef<AbortController | null>(null);

  useEffect(() => () => active.current?.abort(), []);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!question.trim()) return;
    const text = question.trim();
    const controller = new AbortController();
    active.current = controller;
    setError("");
    setStreamText("");
    setPending(true);
    try {
      const result = await streamAnswer(
        text,
        configuration,
        conversationId,
        (delta) => setStreamText((previous) => previous + delta),
        controller.signal,
      );
      setConversationId(result.conversation_id);
      setTurns((previous) => [...previous, { question: text, result }]);
      setQuestion("");
    } catch (error) {
      if (!controller.signal.aborted) setError(errorText(error));
    } finally {
      active.current = null;
      setStreamText("");
      setPending(false);
    }
  }

  return (
    <Page
      title="RAG Playground"
      description="Ask questions against the indexed corpus and inspect retrieved evidence."
    >
      <Card>
        <Data query={configs}>
          {(available) => (
            <form className="form-stack" onSubmit={ask}>
              <label>
                Configuration
                <select
                  value={configuration}
                  onChange={(event) => setConfiguration(event.target.value)}
                  disabled={pending}
                >
                  {available.map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.key} · v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Question
                <textarea
                  required
                  maxLength={4000}
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  rows={4}
                  disabled={pending}
                  placeholder="Ask a question about your knowledge base"
                />
              </label>
              <div className="actions">
                <Button
                  disabled={pending || !available.length || !question.trim()}
                >
                  {pending ? "Searching…" : "Ask"}
                </Button>
                {conversationId && (
                  <Button
                    variant="outline"
                    type="button"
                    disabled={pending}
                    onClick={() => {
                      setTurns([]);
                      setConversationId(null);
                    }}
                  >
                    New conversation
                  </Button>
                )}
              </div>
            </form>
          )}
        </Data>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </Card>
      {pending && (
        <Card>
          <p className="answer-text" role="status">
            {streamText || "Retrieving evidence…"}
          </p>
        </Card>
      )}
      {turns.map((turn) => (
        <TurnView key={turn.result.run_id} turn={turn} />
      ))}
    </Page>
  );
}
function TurnView({ turn }: { turn: Turn }) {
  const answer = turn.result;
  const [review, setReview] = useState<TeacherReview | null>(null);
  const [correction, setCorrection] = useState<Answer | null>(null);
  const [working, setWorking] = useState(false);
  const [failure, setFailure] = useState("");
  const [savedFeedback, setSavedFeedback] = useState("");

  const reviewedAnswer = correction ?? answer;
  const firstCitation = reviewedAnswer.citations[0];

  async function sendFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const replacement = String(values.get("replacement") ?? "").trim();
    setWorking(true);
    setFailure("");
    setSavedFeedback("");
    try {
      const saved = await apiJson<{ proposal_id: string | null }>(
        `/api/v1/rag/runs/${reviewedAnswer.run_id}/feedback`,
        postJson({
          rating: Number(values.get("rating")) || null,
          comment: String(values.get("comment") ?? "").trim() || null,
          correction:
            replacement && firstCitation
              ? {
                  version_id: firstCitation.document_version_id,
                  replace_text: firstCitation.quote,
                  new_text: replacement,
                }
              : null,
        }),
      );
      setSavedFeedback(
        saved.proposal_id
          ? "Correction submitted for review."
          : "Feedback saved.",
      );
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setWorking(false);
    }
  }

  async function checkAnswer() {
    setWorking(true);
    setFailure("");
    try {
      if (review) {
        setCorrection(
          await apiJson<Answer>(`/api/v1/teacher/reviews/${review.id}/refine`, {
            method: "POST",
          }),
        );
      } else {
        setReview(
          await apiJson<TeacherReview>(
            `/api/v1/teacher/reviews/${answer.run_id}`,
            { method: "POST" },
          ),
        );
      }
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setWorking(false);
    }
  }
  return (
    <article className="panel rag-turn">
      <h3>{turn.question}</h3>
      <p className="answer-text">{answer.answer || answer.error}</p>
      <p className="run-meta">
        {answer.strategy} · {answer.configuration} v
        {answer.configuration_version} · {answer.status}
        {answer.citations.length > 0 &&
          (answer.citations_verified
            ? " · citations verified"
            : " · citations invalid")}
      </p>
      {answer.citations.length > 0 && (
        <ul className="source-list">
          {answer.citations.map((item) => (
            <li key={item.source + item.quote}>
              <strong>
                {item.source} · {item.title}
                {item.page ? ` · page ${item.page}` : ""}
              </strong>
              <blockquote>{item.quote}</blockquote>
            </li>
          ))}
        </ul>
      )}
      {answer.sources.length > 0 && (
        <details>
          <summary>Retrieved chunks ({answer.sources.length})</summary>
          <ul className="source-list">
            {answer.sources.map((source) => (
              <li key={source.chunk_id}>
                <strong>
                  {source.title} · score {source.score.toFixed(3)}
                </strong>
                <p>{source.content}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      <section className="teacher-review" aria-label="Teacher evaluation">
        {review && (
          <>
            <h4>Teacher verdict · {review.verdict}</h4>
            <p>{review.report.reason}</p>
            <p>
              Independent evidence: {review.report.evidence_chunk_ids.length}{" "}
              chunks
            </p>
            {review.report.integrity_problems.map((problem, index) => (
              <p key={index}>{problem}</p>
            ))}
            {review.report.model_critique?.verdict &&
              review.report.model_critique.verdict !== review.verdict && (
                <p className="muted-text">
                  Model assessment · {review.report.model_critique.verdict}
                </p>
              )}
            {review.report.model_critique?.unsupported && (
              <p>{review.report.model_critique.unsupported}</p>
            )}
            {review.report.model_critique?.missing && (
              <p>{review.report.model_critique.missing}</p>
            )}
            <TeacherClaims report={review.report} />
          </>
        )}
        {(!review || (review.verdict === "revise" && !correction)) && (
          <button type="button" onClick={checkAnswer} disabled={working}>
            {working
              ? "Working…"
              : review
                ? "Refine answer"
                : "Review with teacher"}
          </button>
        )}
        {correction && (
          <>
            <h4>Refinement · {correction.status}</h4>
            <p className="answer-text">
              {correction.answer || correction.error}
            </p>
            {correction.citations.map((item) => (
              <blockquote key={item.source + item.quote}>
                {item.title}: {item.quote}
              </blockquote>
            ))}
          </>
        )}
      </section>
      <details>
        <summary>Give feedback</summary>
        <form className="playground-form" onSubmit={sendFeedback}>
          <label>
            Rating
            <select name="rating" defaultValue="">
              <option value="">Choose a rating</option>
              {[1, 2, 3, 4, 5].map((rating) => (
                <option key={rating} value={rating}>
                  {rating}
                </option>
              ))}
            </select>
          </label>
          <label>
            Comment
            <textarea name="comment" maxLength={4000} rows={2} />
          </label>
          {firstCitation && (
            <>
              <blockquote>{firstCitation.quote}</blockquote>
              <label>
                Proposed replacement
                <textarea name="replacement" maxLength={4000} rows={2} />
              </label>
            </>
          )}
          <button disabled={working}>Save feedback</button>
        </form>
        {savedFeedback && <p role="status">{savedFeedback}</p>}
      </details>
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </article>
  );
}
