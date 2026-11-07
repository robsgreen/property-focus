import { baseApi as api } from "./baseApi";
export const addTagTypes = ["health", "workflow-runs"] as const;
const injectedRtkApi = api
  .enhanceEndpoints({
    addTagTypes,
  })
  .injectEndpoints({
    endpoints: (build) => ({
      getLiveness: build.query<GetLivenessApiResponse, GetLivenessApiArg>({
        query: () => ({ url: `/health/live` }),
        providesTags: ["health"],
      }),
      getReadiness: build.query<GetReadinessApiResponse, GetReadinessApiArg>({
        query: () => ({ url: `/health/ready` }),
        providesTags: ["health"],
      }),
      getWorkflowRun: build.query<
        GetWorkflowRunApiResponse,
        GetWorkflowRunApiArg
      >({
        query: (queryArg) => ({ url: `/v1/workflow-runs/${queryArg.runId}` }),
        providesTags: ["workflow-runs"],
      }),
    }),
    overrideExisting: false,
  });
export { injectedRtkApi as api };
export type GetLivenessApiResponse =
  /** status 200 Successful Response */ Health;
export type GetLivenessApiArg = void;
export type GetReadinessApiResponse =
  /** status 200 Successful Response */ Health;
export type GetReadinessApiArg = void;
export type GetWorkflowRunApiResponse =
  /** status 200 Successful Response */ WorkflowRun;
export type GetWorkflowRunApiArg = {
  runId: string;
};
export type Health = {
  status: "ok";
};
export type Problem = {
  code: string;
  correlation_id: string;
  detail: string;
  failure_class:
    | (
        | "TRANSIENT"
        | "AUTH_REQUIRED"
        | "DATA_UNAVAILABLE"
        | "INVALID_INPUT"
        | "PERMANENT"
        | "BUG_UNKNOWN"
      )
    | null;
  retry_after_seconds?: number | null;
  retryable: boolean;
  status: number;
  title: string;
  type: string;
};
export type WorkflowStep = {
  attempt_count: number;
  failure_class:
    | (
        | "TRANSIENT"
        | "AUTH_REQUIRED"
        | "DATA_UNAVAILABLE"
        | "INVALID_INPUT"
        | "PERMANENT"
        | "BUG_UNKNOWN"
      )
    | null;
  failure_code: string | null;
  name: string;
  next_attempt_at: string | null;
  result_refs: string[];
  retryable: boolean | null;
  safe_explanation: string | null;
  state:
    | "QUEUED"
    | "RUNNING"
    | "COMPLETE"
    | "PARTIAL"
    | "WAITING_FOR_USER"
    | "FAILED"
    | "CANCELLED";
};
export type WorkflowRun = {
  analysis_tier: ("BASELINE" | "DEEP") | null;
  budget_tier: ("BASELINE" | "DEEP" | "EXPERIMENT") | null;
  correlation_id: string | null;
  created_at: string;
  failure_class:
    | (
        | "TRANSIENT"
        | "AUTH_REQUIRED"
        | "DATA_UNAVAILABLE"
        | "INVALID_INPUT"
        | "PERMANENT"
        | "BUG_UNKNOWN"
      )
    | null;
  failure_code: string | null;
  finished_at: string | null;
  id: string;
  kind: string;
  result_refs: string[];
  started_at: string | null;
  state:
    | "QUEUED"
    | "RUNNING"
    | "COMPLETE"
    | "PARTIAL"
    | "WAITING_FOR_USER"
    | "FAILED"
    | "CANCELLED";
  steps: WorkflowStep[];
  updated_at: string;
};
export const {
  useGetLivenessQuery,
  useGetReadinessQuery,
  useGetWorkflowRunQuery,
} = injectedRtkApi;
