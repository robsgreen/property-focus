/**
 * The RTK Query API that `generated.ts` injects the REST endpoints into.
 *
 * Every request carries the session's access token. A failed request resolves to an
 * `ApiError` holding the HTTP status and, when the body is one, the API's
 * `application/problem+json` document; a `401`, whatever its body, also ends the local
 * session, so the sign-in boundary starts a new sign-in. Cache tags are the operations'
 * OpenAPI tags, which the generator registers.
 */
import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";
import type { BaseQueryFn, FetchArgs, FetchBaseQueryError } from "@reduxjs/toolkit/query/react";

import type { Session } from "../auth/session";
import type { Problem } from "./generated";

/** What the store hands to every request (its thunk extra argument). */
export interface ApiContext {
  session: Session;
}

/** A failed request. */
export interface ApiError {
  /**
   * The HTTP status of the response, including one whose body was not JSON; null only when
   * no response arrived (a network failure or a timeout).
   */
  status: number | null;
  /**
   * The API's problem document, or null when the response carried none (a proxy's HTML
   * error page, for example).
   */
  problem: Problem | null;
}

// The API shares the app's origin (see vite.config.ts), so requests use root-relative paths.
const fetchFromApi = fetchBaseQuery({
  baseUrl: "/",
  prepareHeaders: async (headers, { extra }) => {
    const token = await (extra as ApiContext).session.accessToken();
    if (token !== null) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    return headers;
  },
});

const baseQuery: BaseQueryFn<string | FetchArgs, unknown, ApiError> = async (
  args,
  api,
  extraOptions,
) => {
  const result = await fetchFromApi(args, api, extraOptions);
  if (result.error === undefined) {
    return { data: result.data, ...(result.meta === undefined ? {} : { meta: result.meta }) };
  }
  const { error } = result;
  const status = httpStatusOf(error);
  if (status === 401) {
    (api.extra as ApiContext).session.end();
  }
  const problem = "data" in error && isProblem(error.data) ? error.data : null;
  return { error: { status, problem } };
};

export const baseApi = createApi({
  reducerPath: "api",
  baseQuery,
  endpoints: () => ({}),
});

function httpStatusOf(error: FetchBaseQueryError): number | null {
  if (typeof error.status === "number") {
    return error.status;
  }
  // A response whose body is not JSON still has a status.
  return error.status === "PARSING_ERROR" ? error.originalStatus : null;
}

function isProblem(value: unknown): value is Problem {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate["code"] === "string" &&
    typeof candidate["status"] === "number" &&
    typeof candidate["title"] === "string"
  );
}
