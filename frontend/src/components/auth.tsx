import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { Orbit } from "lucide-react";
import {
  ApiError,
  apiFetch,
  apiJson,
  postJson,
  SESSION_RECHECK_EVENT,
  setSessionToken,
} from "../api/client";
import type { Principal } from "../api/rag";
import { errorText } from "../api/resource";
import { Button } from "./ui/button";
import { Card } from "./ui/card";

const SESSION = "/api/v1/auth/me";
const sessionQuery = queryOptions({
  queryKey: [SESSION],
  staleTime: 0,
  queryFn: async ({ signal }) => {
    try {
      return await apiJson<Principal>(SESSION, { signal, cache: "no-store" });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return null;
      throw error;
    }
  },
});

export function AuthGate({
  children,
}: {
  children: (user: Principal, signOut: () => Promise<void>) => ReactNode;
}) {
  const cache = useQueryClient();
  const session = useQuery(sessionQuery);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");

  useEffect(() => {
    function recheckSession() {
      void cache.invalidateQueries(
        { queryKey: sessionQuery.queryKey, exact: true },
        { cancelRefetch: false },
      );
    }
    window.addEventListener(SESSION_RECHECK_EVENT, recheckSession);
    return () =>
      window.removeEventListener(SESSION_RECHECK_EVENT, recheckSession);
  }, [cache]);

  useEffect(() => {
    if (session.data === null) {
      cache.removeQueries({
        predicate: (query) => query.queryKey[0] !== SESSION,
      });
    }
  }, [cache, session.data]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setFailure("");
    try {
      const request = postJson({
        email: form.get("email"),
        password: form.get("password"),
      });
      await apiFetch("/api/v1/auth/login", request);
      await cache.cancelQueries({
        queryKey: sessionQuery.queryKey,
        exact: true,
      });
      let account = await cache.fetchQuery(sessionQuery);
      if (!account) {
        const { access_token } = await apiJson<{ access_token: string }>(
          "/api/v1/auth/token",
          request,
        );
        setSessionToken(access_token);
        account = await cache.fetchQuery(sessionQuery);
        if (!account)
          setFailure("Could not start your session. Please try again.");
      }
    } catch (error) {
      setFailure(errorText(error));
    } finally {
      setPending(false);
    }
  }

  async function signOut() {
    await apiFetch("/api/v1/auth/logout", { method: "POST" });
    await cache.cancelQueries({ queryKey: sessionQuery.queryKey, exact: true });
    setSessionToken(null);
    cache.setQueryData(sessionQuery.queryKey, null);
  }

  if (session.data) return children(session.data, signOut);
  const unavailable = session.error;
  return (
    <main className="auth-surface">
      <div className="auth-frame">
        <div className="auth-brand">
          <Orbit size={26} aria-hidden /> EvoRAG
        </div>
        <Card title="Sign in">
          {session.isPending ? (
            <p role="status">Checking session…</p>
          ) : (
            <form className="form-stack" onSubmit={signIn}>
              <label>
                Email
                <input
                  name="email"
                  type="email"
                  autoComplete="username"
                  required
                />
              </label>
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <Button disabled={pending}>
                {pending ? "Signing in…" : "Sign in"}
              </Button>
              {(failure || unavailable) && (
                <p role="alert" className="form-error">
                  {failure || session.error?.message}
                </p>
              )}
            </form>
          )}
        </Card>
      </div>
    </main>
  );
}
