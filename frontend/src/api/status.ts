export type Readiness = {
  status: "ready" | "not_ready";
  checks: { postgres: boolean; redis: boolean };
};

export async function getReadiness(signal: AbortSignal): Promise<Readiness> {
  const response = await fetch(apiUrl("/ready"), { signal });
  if (!response.ok && response.status !== 503) {
    throw new Error(`The API returned HTTP ${response.status}.`);
  }
  const data: Partial<Readiness> | null = await response.json();
  const expectedStatus = response.ok ? "ready" : "not_ready";
  if (
    data?.status !== expectedStatus ||
    typeof data.checks?.postgres !== "boolean" ||
    typeof data.checks.redis !== "boolean"
  ) {
    throw new Error("The API returned an invalid readiness response.");
  }
  return { status: data.status, checks: data.checks };
}
import { apiUrl } from "./client";
