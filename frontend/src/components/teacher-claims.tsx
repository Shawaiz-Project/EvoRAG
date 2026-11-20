import type { TeacherReview } from "../api/rag";

export function TeacherClaims({ report }: { report: TeacherReview["report"] }) {
  if (!report.claims?.length) return null;
  return (
    <section aria-label="Claim verification">
      <h4>Claim verification</h4>
      {report.claim_review_complete === false && (
        <p className="muted-text">
          The review reached the maximum number of claims.
        </p>
      )}
      <ol className="source-list">
        {report.claims.map((claim) => (
          <li key={claim.claim_id}>
            <strong className="capitalize">{claim.status}</strong>
            <p>{claim.text}</p>
            {claim.evidence.map((item) => (
              <blockquote key={item.chunk_id + item.quote}>
                {item.title}
                {item.page === null ? "" : ` · page ${item.page}`}
                <p>{item.quote}</p>
              </blockquote>
            ))}
          </li>
        ))}
      </ol>
    </section>
  );
}
