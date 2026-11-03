/**
 * The app's routes. Every route except the sign-in callback sits behind the sign-in
 * boundary, which sends a signed-out visitor to the identity provider and brings them back
 * to the page they asked for, and stays out of the way of a sign-out.
 */
import { useEffect, useState, useSyncExternalStore } from "react";
import { createBrowserRouter, Outlet, useLocation, useNavigate } from "react-router";

import type { Session, SessionState } from "../auth/session";

/** The callback path registered with the identity provider's web client. */
export const SIGN_IN_CALLBACK_PATH = "/auth/callback";

export function createRouter(session: Session) {
  return createBrowserRouter([
    { path: SIGN_IN_CALLBACK_PATH, element: <SignInCallback session={session} /> },
    {
      element: <SignInBoundary session={session} />,
      children: [{ index: true, element: <Home session={session} /> }],
    },
  ]);
}

interface SessionProps {
  session: Session;
}

function useSessionState(session: Session): SessionState {
  return useSyncExternalStore(session.subscribe, session.state);
}

/**
 * Renders its child routes while signed in. Signed out, it starts a sign-in for the current
 * page; while signing out, or after a sign-out the issuer could not complete, it starts none.
 */
function SignInBoundary({ session }: SessionProps) {
  const state = useSessionState(session);
  const { pathname, search, hash } = useLocation();
  const [failure, setFailure] = useState<string | null>(null);
  const signedOut = state.kind === "signedOut";
  useEffect(() => {
    if (signedOut) {
      session.signIn(pathname + search + hash).catch((error: unknown) => {
        setFailure(messageOf(error));
      });
    }
  }, [signedOut, session, pathname, search, hash]);
  switch (state.kind) {
    case "signedIn":
      return <Outlet />;
    case "signingOut":
      return <p role="status">Signing out…</p>;
    case "signOutFailed":
      return <SignOutFailure session={session} reason={state.reason} />;
    case "signedOut":
      return failure === null ? (
        <p role="status">Redirecting to sign-in…</p>
      ) : (
        <SignInFailure session={session} message={failure} />
      );
  }
}

/** Completes the sign-in the identity provider redirected back from. */
function SignInCallback({ session }: SessionProps) {
  const navigate = useNavigate();
  const [failure, setFailure] = useState<string | null>(null);
  useEffect(() => {
    session.completeSignIn(window.location.href).then(
      (returnTo) => {
        void navigate(returnTo, { replace: true });
      },
      (error: unknown) => {
        setFailure(messageOf(error));
      },
    );
  }, [session, navigate]);
  return failure === null ? (
    <p role="status">Signing in…</p>
  ) : (
    <SignInFailure session={session} message={failure} />
  );
}

function SignInFailure({ session, message }: SessionProps & { message: string }) {
  return (
    <main className="page">
      <h1>Sign-in did not complete</h1>
      <p role="alert">{message}</p>
      <button type="button" onClick={() => void session.signIn()}>
        Sign in again
      </button>
    </main>
  );
}

// The tokens are gone, but the issuer may still sign this browser in without a prompt.
// A sign-in starts only when the person asks for one; if it cannot start, says why.
function SignOutFailure({ session, reason }: SessionProps & { reason: string }) {
  const [signInFailure, setSignInFailure] = useState<string | null>(null);
  if (signInFailure !== null) {
    return <SignInFailure session={session} message={signInFailure} />;
  }
  const signIn = () => {
    session.signIn().catch((error: unknown) => {
      setSignInFailure(messageOf(error));
    });
  };
  return (
    <main className="page">
      <h1>Signed out of Property Focus</h1>
      <p role="alert">
        Your sign-in with the identity provider could not be ended ({reason}). Close the browser
        to finish signing out on a shared computer.
      </p>
      <button type="button" onClick={signIn}>
        Sign in again
      </button>
    </main>
  );
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Sign-in failed.";
}

function Home({ session }: SessionProps) {
  return (
    <main className="page">
      <h1>Property Focus</h1>
      <button type="button" onClick={() => void session.signOut()}>
        Sign out
      </button>
    </main>
  );
}
