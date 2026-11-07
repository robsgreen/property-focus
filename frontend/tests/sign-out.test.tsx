/**
 * Signing out from the app: the router and the real session, wired together against a fake
 * issuer. The sign-in boundary re-renders as soon as the tokens are forgotten, so these check
 * that it does not start a new sign-in that would replace the issuer's sign-out page, and
 * that a sign-out the issuer cannot complete is reported instead of silently signing back in.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { RouterProvider } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createRouter } from "../src/app/router";
import { createSession } from "../src/auth/session";
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

// What the fake issuer's discovery document lists; a test may remove the end-session endpoint.
let discovery: Partial<typeof DISCOVERY>;

async function fakeIssuerFetch(input: RequestInfo | URL): Promise<Response> {
  const url = input instanceof Request ? input.url : String(input);
  if (url === `${ISSUER}/.well-known/openid-configuration`) {
    return json(discovery);
  }
  if (url === DISCOVERY.token_endpoint) {
    return json({
      access_token: "access-1",
      refresh_token: "refresh-1",
      id_token: unsignedIdToken(),
      token_type: "Bearer",
      expires_in: 300,
      scope: "openid",
    });
  }
  return Promise.resolve(new Response("not found", { status: 404 }));
}

function json(body: unknown): Promise<Response> {
  return Promise.resolve(
    new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } }),
  );
}

// The client reads the ID token's claims but never its signature.
function unsignedIdToken(): string {
  const now = Math.floor(Date.now() / 1000);
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "none" })}.${encode({
    iss: ISSUER,
    aud: CONFIG.clientId,
    sub: "synthetic-user",
    iat: now,
    exp: now + 300,
  })}.`;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 300));

let visited: string[];
let session: Session;
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  visited = [];
  discovery = { ...DISCOVERY };
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", fakeIssuerFetch);
  session = createSession(CONFIG, {
    storage: sessionStorage,
    navigate: (url) => visited.push(url),
  });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
  vi.unstubAllGlobals();
});

/** Renders the app, completes the sign-in it starts and returns the "Sign out" button. */
async function signInThroughTheApp(): Promise<HTMLButtonElement> {
  act(() => {
    root.render(<RouterProvider router={createRouter(session)} />);
  });
  await vi.waitFor(() => {
    expect(visited).toHaveLength(1);
  });
  const authorize = new URL(visited.at(-1) ?? "");
  expect(authorize.origin + authorize.pathname).toBe(DISCOVERY.authorization_endpoint);
  await session.completeSignIn(
    `${CONFIG.redirectUri}?code=synthetic-code&state=${authorize.searchParams.get("state") ?? ""}`,
  );
  await act(settle);
  const signOut = [...container.querySelectorAll("button")].find(
    (button) => button.textContent === "Sign out",
  );
  if (signOut === undefined) {
    throw new Error("the signed-in page has no Sign out button");
  }
  visited = [];
  return signOut;
}

it("leaves the app for the issuer's sign-out page and starts no new sign-in", async () => {
  const signOut = await signInThroughTheApp();

  act(() => {
    signOut.click();
  });
  await vi.waitFor(() => {
    expect(visited.length).toBeGreaterThan(0);
  });
  await act(settle);

  expect(visited.map((url) => new URL(url).pathname)).toEqual(["/realms/synthetic/logout"]);
  const logout = new URL(visited.at(-1) ?? "");
  expect(logout.origin + logout.pathname).toBe(DISCOVERY.end_session_endpoint);
  expect(logout.searchParams.get("id_token_hint")).toBeTruthy();
  expect(logout.searchParams.get("post_logout_redirect_uri")).toBe(CONFIG.postSignOutRedirectUri);
  expect(session.isSignedIn()).toBe(false);
  await expect(session.accessToken()).resolves.toBeNull();
});

it("reports a sign-out the issuer has no endpoint for and starts no new sign-in", async () => {
  delete discovery.end_session_endpoint;
  const signOut = await signInThroughTheApp();

  act(() => {
    signOut.click();
  });
  await act(settle);

  expect(visited).toEqual([]);
  expect(session.isSignedIn()).toBe(false);
  expect(session.state().kind).toBe("signOutFailed");
  expect(container.querySelector("h1")?.textContent).toBe("Signed out of Property Focus");
  expect(container.querySelector('[role="alert"]')?.textContent).toContain(
    "could not be ended",
  );
});
