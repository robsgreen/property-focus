/**
 * The API client's request and error handling, through the real store against a stubbed
 * `fetch`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { baseApi } from "../src/api/baseApi";
import type { ApiError } from "../src/api/baseApi";
import { createStore } from "../src/app/store";
import { fakeSession } from "./support/fakeSession";
import type { FakeSession } from "./support/fakeSession";

const probeApi = baseApi.injectEndpoints({
  endpoints: (build) => ({
    probe: build.query<unknown, string>({ query: (path) => path }),
  }),
});

const PROBLEM = {
  type: "about:blank",
  title: "Not found",
  status: 404,
  code: "not_found",
  detail: "No such run.",
};

// Node's Request rejects root-relative URLs, which a browser resolves against the page.
class PageRequest extends Request {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(typeof input === "string" ? new URL(input, window.location.origin) : input, init);
  }
}

let session: FakeSession;
let requests: Request[];
let respond: () => Response | Promise<Response>;

beforeEach(() => {
  session = fakeSession();
  requests = [];
  respond = () => Response.json({ ok: true });
  vi.stubGlobal("Request", PageRequest);
  vi.stubGlobal("fetch", (request: Request) => {
    requests.push(request);
    return respond();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Runs the probe query for `path` in a fresh store and returns its data or error. */
async function probe(path = "v1/probe"): Promise<{ data: unknown; error: ApiError | undefined }> {
  const store = createStore(session);
  const result = await store.dispatch(probeApi.endpoints.probe.initiate(path));
  return { data: result.data, error: result.error as ApiError | undefined };
}

describe("baseApi", () => {
  it("sends the session's access token as a bearer token", async () => {
    vi.mocked(session.accessToken).mockResolvedValue("access-1");

    await expect(probe()).resolves.toEqual({ data: { ok: true }, error: undefined });

    expect(requests[0]?.url).toBe(`${window.location.origin}/v1/probe`);
    expect(requests[0]?.headers.get("Authorization")).toBe("Bearer access-1");
  });

  it("sends no Authorization header while signed out", async () => {
    await probe();

    expect(requests[0]?.headers.has("Authorization")).toBe(false);
  });

  it("returns the status and the API's problem document", async () => {
    respond = () =>
      Response.json(PROBLEM, {
        status: 404,
        headers: { "Content-Type": "application/problem+json" },
      });

    const { error } = await probe();

    expect(error).toEqual({ status: 404, problem: PROBLEM });
    expect(session.end).not.toHaveBeenCalled();
  });

  it("returns the status without a problem for a JSON body that is not one", async () => {
    respond = () => Response.json({ message: "Bad gateway" }, { status: 502 });

    await expect(probe()).resolves.toMatchObject({ error: { status: 502, problem: null } });
  });

  it("returns the status of a response whose body is not JSON", async () => {
    respond = () =>
      new Response("<html>Bad gateway</html>", {
        status: 502,
        headers: { "Content-Type": "application/json" },
      });

    await expect(probe()).resolves.toMatchObject({ error: { status: 502, problem: null } });
  });

  it("returns no status when no response arrives", async () => {
    respond = () => Promise.reject(new TypeError("Failed to fetch"));

    await expect(probe()).resolves.toMatchObject({ error: { status: null, problem: null } });
  });

  it.each([
    ["a problem document", () => Response.json({ ...PROBLEM, status: 401 }, { status: 401 })],
    [
      "a body that is not JSON",
      () =>
        new Response("Unauthorized", {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
    ],
  ])("ends the session on a 401 with %s", async (_body, response) => {
    respond = response;

    const { error } = await probe();

    expect(error?.status).toBe(401);
    expect(session.end).toHaveBeenCalledOnce();
  });
});
