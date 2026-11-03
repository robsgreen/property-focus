import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSession, SignInError } from "../src/auth/session";
import type { Session, SessionConfig } from "../src/auth/session";

const ISSUER = "https://issuer.test/realms/synthetic";
const CONFIG: SessionConfig = {
  issuer: ISSUER,
  clientId: "synthetic-web",
  redirectUri: "https://app.test/auth/callback",
  postSignOutRedirectUri: "https://app.test/",
};
const DISCOVERY = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/authorize`,
  token_endpoint: `${ISSUER}/token`,
  end_session_endpoint: `${ISSUER}/logout`,
  jwks_uri: `${ISSUER}/keys`,
};

/** A fake issuer: answers discovery and the token endpoint, and records token requests. */
class FakeIssuer {
  discovery: Partial<typeof DISCOVERY> = { ...DISCOVERY };
  tokenRequests: URLSearchParams[] = [];
  refreshRefused = false;
  private issued = 0;

  fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = input instanceof Request ? input.url : String(input);
    if (url === `${ISSUER}/.well-known/openid-configuration`) {
      return json(this.discovery);
    }
    if (url === DISCOVERY.token_endpoint) {
      const body = new URLSearchParams(await new Response(init?.body).text());
      this.tokenRequests.push(body);
      if (body.get("grant_type") === "refresh_token" && this.refreshRefused) {
        return json({ error: "invalid_grant" }, 400);
      }
      return json(this.tokens());
    }
    return new Response("not found", { status: 404 });
  };

  private tokens() {
    this.issued += 1;
    return {
      access_token: `access-${String(this.issued)}`,
      refresh_token: `refresh-${String(this.issued)}`,
      id_token: unsignedIdToken(),
      token_type: "Bearer",
      expires_in: 300,
      scope: "openid",
    };
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// The client reads the ID token's claims but never its signature (the API verifies tokens).
function unsignedIdToken(): string {
  const now = Math.floor(Date.now() / 1000);
  const encode = (value: object) => base64Url(new TextEncoder().encode(JSON.stringify(value)));
  const claims = {
    iss: ISSUER,
    aud: CONFIG.clientId,
    sub: "synthetic-user",
    iat: now,
    exp: now + 300,
  };
  return `${encode({ alg: "none" })}.${encode(claims)}.`;
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function s256(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

let issuer: FakeIssuer;
let visited: string[];
let session: Session;

beforeEach(() => {
  issuer = new FakeIssuer();
  visited = [];
  sessionStorage.clear();
  vi.stubGlobal("fetch", issuer.fetch);
  session = createSession(CONFIG, {
    storage: sessionStorage,
    navigate: (url) => visited.push(url),
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Starts a sign-in and returns the authorization request the browser was sent to. */
async function startSignIn(returnTo?: string): Promise<URL> {
  await session.signIn(returnTo);
  const url = visited.at(-1);
  if (url === undefined) {
    throw new Error("signIn did not navigate");
  }
  return new URL(url);
}

/** The callback URL the issuer redirects to for `request`. */
function callbackFor(request: URL, state = request.searchParams.get("state") ?? ""): string {
  return `${CONFIG.redirectUri}?code=synthetic-code&state=${encodeURIComponent(state)}`;
}

describe("sign-in", () => {
  it("sends an S256 challenge of the verifier it later exchanges the code with", async () => {
    const request = await startSignIn("/searches?id=1");

    expect(request.origin + request.pathname).toBe(DISCOVERY.authorization_endpoint);
    expect(request.searchParams.get("response_type")).toBe("code");
    expect(request.searchParams.get("client_id")).toBe(CONFIG.clientId);
    expect(request.searchParams.get("code_challenge_method")).toBe("S256");

    await expect(session.completeSignIn(callbackFor(request))).resolves.toBe("/searches?id=1");

    const exchange = issuer.tokenRequests[0];
    const verifier = exchange?.get("code_verifier") ?? "";
    expect(exchange?.get("grant_type")).toBe("authorization_code");
    expect(verifier).toMatch(/^[A-Za-z0-9._~-]{43,128}$/);
    expect(request.searchParams.get("code_challenge")).toBe(await s256(verifier));
    expect(session.isSignedIn()).toBe(true);
    await expect(session.accessToken()).resolves.toBe("access-1");
  });

  it("uses a new verifier for every sign-in", async () => {
    const first = await startSignIn();
    const second = await startSignIn();

    expect(first.searchParams.get("code_challenge")).not.toBe(
      second.searchParams.get("code_challenge"),
    );
  });

  it("rejects a callback whose state this browser did not issue", async () => {
    const request = await startSignIn();

    await expect(session.completeSignIn(callbackFor(request, "forged-state"))).rejects.toThrow(
      SignInError,
    );
    expect(issuer.tokenRequests).toHaveLength(0);
    expect(session.isSignedIn()).toBe(false);
  });

  it("accepts each state once, so a replayed callback cannot sign in again", async () => {
    const request = await startSignIn();
    await session.completeSignIn(callbackFor(request));
    session.end();
    const replay = `${callbackFor(request)}&replayed=1`;

    await expect(session.completeSignIn(replay)).rejects.toThrow(SignInError);
    expect(session.isSignedIn()).toBe(false);
  });

  it("processes a repeated call with the same callback URL once", async () => {
    const callback = callbackFor(await startSignIn());

    const results = await Promise.all([
      session.completeSignIn(callback),
      session.completeSignIn(callback),
    ]);

    expect(results).toEqual(["/", "/"]);
    expect(issuer.tokenRequests).toHaveLength(1);
  });

  it.each(["https://elsewhere.test/", "//elsewhere.test/", "/\\elsewhere.test/"])(
    "returns to / instead of the off-site path %s",
    async (returnTo) => {
      const request = await startSignIn(returnTo);

      await expect(session.completeSignIn(callbackFor(request))).resolves.toBe("/");
    },
  );
});

describe("token lifecycle", () => {
  async function signIn(): Promise<void> {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    await session.completeSignIn(callbackFor(await startSignIn()));
  }

  it("keeps using the access token while it has time left", async () => {
    await signIn();
    vi.setSystemTime(new Date("2026-01-01T00:04:00Z"));

    await expect(session.accessToken()).resolves.toBe("access-1");
    expect(issuer.tokenRequests).toHaveLength(1);
  });

  it("refreshes an access token about to expire, once for concurrent callers", async () => {
    await signIn();
    vi.setSystemTime(new Date("2026-01-01T00:04:45Z"));

    const tokens = await Promise.all([session.accessToken(), session.accessToken()]);

    expect(tokens).toEqual(["access-2", "access-2"]);
    const refreshes = issuer.tokenRequests.filter(
      (request) => request.get("grant_type") === "refresh_token",
    );
    expect(refreshes).toHaveLength(1);
    expect(refreshes[0]?.get("refresh_token")).toBe("refresh-1");
    expect(session.isSignedIn()).toBe(true);
  });

  it("ends the session when the issuer refuses the refresh", async () => {
    await signIn();
    const changes = vi.fn();
    session.subscribe(changes);
    issuer.refreshRefused = true;
    vi.setSystemTime(new Date("2026-01-01T00:06:00Z"));

    await expect(session.accessToken()).resolves.toBeNull();
    expect(session.isSignedIn()).toBe(false);
    expect(changes).toHaveBeenCalledTimes(1);
  });

  it("signs out at the issuer with the ID token as a hint", async () => {
    await signIn();

    await session.signOut();

    const logout = new URL(visited.at(-1) ?? "");
    expect(logout.origin + logout.pathname).toBe(DISCOVERY.end_session_endpoint);
    expect(logout.searchParams.get("id_token_hint")).toBeTruthy();
    expect(logout.searchParams.get("post_logout_redirect_uri")).toBe(CONFIG.postSignOutRedirectUri);
    expect(session.isSignedIn()).toBe(false);
    expect(session.state().kind).toBe("signingOut");
    await expect(session.accessToken()).resolves.toBeNull();
  });

  it("stays signing out when a rejected token ends the session meanwhile", async () => {
    await signIn();

    await session.signOut();
    session.end();

    expect(session.state().kind).toBe("signingOut");
  });

  it("signs out at the configured end-session endpoint when discovery lists none", async () => {
    const endSessionEndpoint = "https://issuer.test/logout";
    session = createSession(
      { ...CONFIG, endSessionEndpoint },
      { storage: sessionStorage, navigate: (url) => visited.push(url) },
    );
    delete issuer.discovery.end_session_endpoint;
    await signIn();

    await session.signOut();

    const logout = new URL(visited.at(-1) ?? "");
    expect(logout.origin + logout.pathname).toBe(endSessionEndpoint);
    expect(logout.searchParams.get("id_token_hint")).toBeTruthy();
  });

  it("forgets the tokens and reports the failure when the issuer cannot sign out", async () => {
    delete issuer.discovery.end_session_endpoint;
    await signIn();
    visited = [];

    await session.signOut();

    expect(visited).toEqual([]);
    expect(session.isSignedIn()).toBe(false);
    const state = session.state();
    expect(state.kind).toBe("signOutFailed");
    expect(state.kind === "signOutFailed" && state.reason).toBeTruthy();
    await expect(session.accessToken()).resolves.toBeNull();
  });
});
