export type DocumentItem = {
  id: string;
  title: string;
  locator: string;
  source_kind: string;
  source_locator: string;
};
export type Version = {
  id: string;
  version: number;
  sha256: string;
  size_bytes: number;
  mime_type: string;
  is_current: boolean;
  created_at: string;
  attributes: Record<string, unknown>;
};
export type DocumentDetail = { document: DocumentItem; versions: Version[] };
export type Chunk = {
  id: string;
  ordinal: number;
  content: string;
  page: number | null;
  section: string | null;
  parent_id: string | null;
  attributes: Record<string, unknown>;
};
export type VersionDetail = {
  version: Version;
  extracted_text: string;
  chunks: Chunk[];
};
export type Job = {
  id: string;
  locator: string;
  kind: string;
  strategy: string;
  attempts: number;
  status: string;
  error: string | null;
};
export type Proposal = {
  id: string;
  status: string;
  created_at: string;
  published_version_id: string | null;
  proposed_change: { replace_text?: string; new_text?: string };
  attributes: Record<string, unknown>;
};
export type Gap = { question: string; failures: number };
export type Dataset = {
  id: string;
  key: string;
  version: number;
  description: string;
};
export type DatasetDetail = { dataset: Dataset; cases: unknown[] };
export type Experiment = {
  id: string;
  name: string;
  dataset_id: string;
  status: string;
  created_at: string;
  attributes: Record<string, unknown>;
  settings: Record<string, unknown>;
};
export type ExperimentRow = {
  id: string;
  run_id: string | null;
  metrics: Record<string, string | number | null>;
  attributes: { variant: string; status: string; answer: string };
};
export type ExperimentDetail = {
  experiment: Experiment;
  results: ExperimentRow[];
};
export type Workflow = {
  id: string;
  configuration: string;
  strategy: string;
  status: string;
  role: string;
  steps: string[];
  created_at: string;
};
