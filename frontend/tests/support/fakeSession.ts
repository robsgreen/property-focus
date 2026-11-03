import { vi } from "vitest";

import type { Session, SessionState } from "../../src/auth/session";

/**
 * A `Session` whose state a test sets directly. Every member is a `vi.fn`, so a test can
 * replace what one returns and inspect its calls. Signed out to begin with.
 */
export interface FakeSession extends Session {
  /** Moves to `next` and notifies subscribers, as the real session does. */
  setState: (next: SessionState) => void;
}

export function fakeSession(): FakeSession {
  const listeners = new Set<() => void>();
  let state: SessionState = { kind: "signedOut" };
  return {
    isSignedIn: vi.fn(() => state.kind === "signedIn"),
    state: vi.fn(() => state),
    subscribe: vi.fn((listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    }),
    signIn: vi.fn(() => Promise.resolve()),
    completeSignIn: vi.fn(() => Promise.resolve("/")),
    accessToken: vi.fn(() => Promise.resolve<string | null>(null)),
    end: vi.fn(),
    signOut: vi.fn(() => Promise.resolve()),
    setState(next) {
      state = next;
      listeners.forEach((listener) => {
        listener();
      });
    },
  };
}
