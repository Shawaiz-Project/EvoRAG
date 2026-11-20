import { useQuery } from "@tanstack/react-query";
import { apiJson } from "./client";

export function useResource<T>(path: string, enabled = true) {
  return useQuery({
    queryKey: [path],
    queryFn: ({ signal }) => apiJson<T>(path, { signal }),
    enabled,
  });
}

export function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed.";
}
