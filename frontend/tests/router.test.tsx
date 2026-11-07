/**
 * The sign-in boundary and the sign-in callback route, rendered with a fake session.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";
import { RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createRouter, SIGN_IN_CALLBACK_PATH } from "../src/app/router";
import { SignInError } from "../src/auth/session";
import { fakeSession } from "./support/fakeSession";
import type { FakeSession } from "./support/fakeSession";

let session: FakeSession;
let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  session = fakeSession();
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => {
    root.unmount();
  });
  container.remove();
});

/** Opens the app at `url` (a path with query and hash) and returns its router. */
async function open(url: string): Promise<ReturnType<typeof createRouter>> {
  window.history.replaceState(null, "", url);
  const router = createRouter(session);
  await act(async () => {
    root.render(<RouterProvider router={router} />);
    await Promise.resolve();
  });
  return router;
}

function button(name: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent === name,
  );
  if (found === undefined) {
    throw new Error(`no "${name}" button`);
  }
  return found;
}

describe("sign-in boundary", () => {
  it("starts a sign-in that returns to the requested path, query and hash", async () => {
    await open("/?id=1#top");

    expect(session.signIn).toHaveBeenCalledExactlyOnceWith("/?id=1#top");
    expect(container.querySelector('[role="status"]')?.textContent).toBe(
      "Redirecting to sign-in…",
    );
  });

  it("shows the page while signed in", async () => {
    session.setState({ kind: "signedIn" });

    await open("/");

    expect(session.signIn).not.toHaveBeenCalled();
    expect(container.querySelector("h1")?.textContent).toBe("Property Focus");
  });

  it("shows why a sign-in could not start and offers another", async () => {
    vi.mocked(session.signIn).mockRejectedValueOnce(new Error("Issuer unreachable."));

    await open("/");

    expect(container.querySelector('[role="alert"]')?.textContent).toBe("Issuer unreachable.");
    act(() => {
      button("Sign in again").click();
    });
    expect(session.signIn).toHaveBeenCalledTimes(2);
  });

  it("starts no sign-in while signing out", async () => {
    session.setState({ kind: "signingOut" });

    await open("/");

    expect(session.signIn).not.toHaveBeenCalled();
    expect(container.querySelector('[role="status"]')?.textContent).toBe("Signing out…");
  });

  it("reports a sign-out the issuer could not complete and starts no sign-in", async () => {
    session.setState({ kind: "signOutFailed", reason: "No end session endpoint" });

    await open("/");

    expect(session.signIn).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "No end session endpoint",
    );
    act(() => {
      button("Sign in again").click();
    });
    expect(session.signIn).toHaveBeenCalledOnce();
  });

  it("shows why a sign-in after a failed sign-out could not start", async () => {
    session.setState({ kind: "signOutFailed", reason: "No end session endpoint" });
    vi.mocked(session.signIn).mockRejectedValueOnce(new Error("Issuer unreachable."));
    await open("/");

    await act(async () => {
      button("Sign in again").click();
      await Promise.resolve();
    });

    expect(session.signIn).toHaveBeenCalledOnce();
    expect(container.querySelector("h1")?.textContent).toBe("Sign-in did not complete");
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("Issuer unreachable.");
  });
});

describe("sign-in callback", () => {
  const callback = `${SIGN_IN_CALLBACK_PATH}?code=synthetic-code&state=synthetic-state`;

  it("completes the sign-in and replaces the callback with the returned path", async () => {
    vi.mocked(session.completeSignIn).mockImplementationOnce(() => {
      session.setState({ kind: "signedIn" });
      return Promise.resolve("/?id=1");
    });

    const router = await open(callback);

    expect(session.completeSignIn).toHaveBeenCalledWith(window.location.origin + callback);
    await vi.waitFor(() => {
      expect(router.state.location.pathname + router.state.location.search).toBe("/?id=1");
    });
    expect(router.state.historyAction).toBe("REPLACE");
    expect(session.signIn).not.toHaveBeenCalled();
  });

  it("shows why the sign-in failed and offers another", async () => {
    vi.mocked(session.completeSignIn).mockRejectedValueOnce(new SignInError("State unknown."));

    const router = await open(callback);

    expect(container.querySelector('[role="alert"]')?.textContent).toBe("State unknown.");
    expect(router.state.location.pathname).toBe(SIGN_IN_CALLBACK_PATH);
    act(() => {
      button("Sign in again").click();
    });
    expect(session.signIn).toHaveBeenCalledOnce();
  });
});
