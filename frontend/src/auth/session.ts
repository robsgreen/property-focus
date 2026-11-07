/**
 * The signed-in user's session with the OpenID Connect issuer.
 *
 * Sign-in is the authorization code flow with PKCE (`S256`) against whichever issuer the
 * configuration names; its endpoints come from the issuer's discovery document at
 * `<issuer>/.well-known/openid-configuration`, except an end-session endpoint the
 * configuration names for an issuer whose discovery document lacks one. `oidc-client-ts`
 * builds the requests and validates the responses.
 *
 * Tokens live only in memory. The PKCE verifier and the `state` value of a sign-in in
 * progress live in session storage, the only thing that survives the round trip through the
 * issuer, and are removed when the callback is processed. Reloading the page therefore
 * starts a new sign-in, which the issuer's own session usually completes without a prompt.
 */
import { OidcClient, WebStorageStateStore } from "oidc-client-ts";
import type { RefreshState, SigninResponse, UserProfile } from "oidc-client-ts";

export interface SessionConfig {
  /** The issuer URL, exactly as it appears in the tokens' `iss` claim. */
  issuer: string;
  /** The public client registered with the issuer for this web app. */
  clientId: string;
  /** Absolute URL of the sign-in callback route; must be registered with the client. */
  redirectUri: string;
  /** Absolute URL the issuer returns to after sign-out; must be registered with the client. */
  postSignOutRedirectUri: string;
  /**
   * The issuer's end-session (sign-out) endpoint, for an issuer whose discovery document
   * does not list one. Omitted, the discovery document's `end_session_endpoint` is used.
   */
  endSessionEndpoint?: string;
}

/** Browser facilities the session uses; tests replace them. */
export interface SessionEnvironment {
  /** Holds the state of a sign-in in progress across the redirect to the issuer. */
  storage: Storage;
  /** Leaves the app for `url` (the issuer's sign-in or sign-out page). */
  navigate: (url: string) => void;
}

/**
 * Where the session stands. `signingOut` means the browser has been sent (or is about to be
 * sent) to the issuer's sign-out page, so nothing may start a sign-in. `signOutFailed` means
 * the tokens are forgotten but the issuer's own session could not be ended, so a new sign-in
 * may complete without a prompt; `reason` says why, for the person reading it.
 */
export type SessionState =
  | { readonly kind: "signedIn" }
  | { readonly kind: "signedOut" }
  | { readonly kind: "signingOut" }
  | { readonly kind: "signOutFailed"; readonly reason: string };

/** A sign-in callback that cannot complete: unknown `state`, an issuer error, or a failed exchange. */
export class SignInError extends Error {
  override name = "SignInError";
}

/**
 * One browser tab's sign-in state and tokens. Members are plain functions that need no
 * `this`, so they can be passed around (to `useSyncExternalStore`, for example).
 */
export interface Session {
  /** True while the session holds tokens. */
  isSignedIn: () => boolean;
  /** The current state; the same object until the state changes. */
  state: () => SessionState;
  /** Calls `listener` whenever `state()` changes; returns the unsubscribe function. */
  subscribe: (listener: () => void) => () => void;
  /**
   * Sends the browser to the issuer's sign-in page. `returnTo`, an app path such as
   * `/searches?id=1`, is where `completeSignIn` sends the user back to; anything that is not
   * a same-origin path is replaced by `/`.
   */
  signIn: (returnTo?: string) => Promise<void>;
  /**
   * Completes the sign-in the issuer redirected back from, given the callback URL: checks
   * its `state` against the sign-in this browser started, exchanges the code with the PKCE
   * verifier and keeps the tokens. Resolves to the `returnTo` path given to `signIn`.
   * Calling it again with the same URL returns the same result. Rejects with `SignInError`.
   */
  completeSignIn: (callbackUrl: string) => Promise<string>;
  /**
   * A current access token, or null when signed out. A token that expires within
   * `REFRESH_MARGIN_SECONDS` is first replaced using the refresh token; when that fails the
   * session ends and the result is null. Concurrent callers share one refresh.
   */
  accessToken: () => Promise<string | null>;
  /**
   * Forgets the tokens without contacting the issuer, for a token the API has rejected.
   * Does nothing unless signed in, so a late rejection cannot interrupt a sign-out.
   */
  end: () => void;
  /**
   * Forgets the tokens and sends the browser to the issuer's end-session endpoint with the
   * ID token as a hint. The state is `signingOut` from the start, so no sign-in replaces the
   * issuer's page. Never rejects: when the issuer has no end-session endpoint or the request
   * cannot be built, the state becomes `signOutFailed`.
   */
  signOut: () => Promise<void>;
}

/** An access token is refreshed when it has fewer seconds than this left. */
export const REFRESH_MARGIN_SECONDS = 30;

const SIGNED_IN: SessionState = { kind: "signedIn" };
const SIGNED_OUT: SessionState = { kind: "signedOut" };
const SIGNING_OUT: SessionState = { kind: "signingOut" };

// What a successful token response leaves behind.
interface Tokens {
  accessToken: string;
  // Seconds since the epoch; undefined when the issuer did not say.
  expiresAt: number | undefined;
  refreshToken: string | undefined;
  idToken: string | undefined;
  profile: UserProfile;
  sessionState: string | null;
  scope: string | undefined;
}

function browserEnvironment(): SessionEnvironment {
  return {
    storage: window.sessionStorage,
    navigate: (url) => {
      window.location.assign(url);
    },
  };
}

/**
 * A signed-out session for the client `config` describes. `environment` defaults to the
 * browser's session storage and `window.location`.
 */
export function createSession(
  config: SessionConfig,
  environment: SessionEnvironment = browserEnvironment(),
): Session {
  const client = new OidcClient({
    authority: config.issuer,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    post_logout_redirect_uri: config.postSignOutRedirectUri,
    response_type: "code",
    scope: "openid",
    stateStore: new WebStorageStateStore({ store: environment.storage }),
    ...(config.endSessionEndpoint === undefined
      ? {}
      : { metadataSeed: { end_session_endpoint: config.endSessionEndpoint } }),
  });
  const listeners = new Set<() => void>();
  let state = SIGNED_OUT;
  let tokens: Tokens | null = null;
  let refreshing: Promise<string | null> | null = null;
  let completing: { url: string; result: Promise<string> } | null = null;

  // Keeps `tokens` and `state` in step: tokens mean signed in, and forgetting them moves to
  // `nextState`.
  function setTokens(next: Tokens | null, nextState: SessionState = SIGNED_OUT): void {
    tokens = next;
    setState(next === null ? nextState : SIGNED_IN);
  }

  function setState(next: SessionState): void {
    if (next !== state) {
      state = next;
      listeners.forEach((listener) => {
        listener();
      });
    }
  }

  async function exchange(callbackUrl: string): Promise<string> {
    let response: SigninResponse;
    try {
      response = await client.processSigninResponse(callbackUrl);
    } catch (error) {
      throw new SignInError(error instanceof Error ? error.message : "Sign-in failed.", {
        cause: error,
      });
    }
    setTokens(tokensFrom(response, null));
    return safeReturnPath(response.userState);
  }

  async function refresh(current: Tokens): Promise<string | null> {
    if (current.refreshToken === undefined) {
      setTokens(null);
      return null;
    }
    try {
      // oidc-client-ts declares RefreshState as a class but does not export it at runtime;
      // the client reads only these fields.
      const refreshState: RefreshState = {
        refresh_token: current.refreshToken,
        session_state: current.sessionState,
        profile: current.profile,
        ...(current.idToken === undefined ? {} : { id_token: current.idToken }),
        ...(current.scope === undefined ? {} : { scope: current.scope }),
      };
      const response = await client.useRefreshToken({ state: refreshState });
      if (tokens !== current) {
        // The session ended or signed out while the refresh was in flight.
        return null;
      }
      const next = tokensFrom(response, current);
      setTokens(next);
      return next.accessToken;
    } catch {
      // A refused or failed refresh ends the session; the sign-in boundary starts a new one.
      if (tokens === current) {
        setTokens(null);
      }
      return null;
    }
  }

  return {
    isSignedIn: () => tokens !== null,

    state: () => state,

    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    async signIn(returnTo = "/") {
      await client.clearStaleState();
      const request = await client.createSigninRequest({ state: safeReturnPath(returnTo) });
      environment.navigate(request.url);
    },

    completeSignIn(callbackUrl) {
      if (completing?.url !== callbackUrl) {
        completing = { url: callbackUrl, result: exchange(callbackUrl) };
      }
      return completing.result;
    },

    accessToken() {
      if (tokens === null) {
        return Promise.resolve(null);
      }
      const secondsLeft =
        tokens.expiresAt === undefined ? Infinity : tokens.expiresAt - Date.now() / 1000;
      if (secondsLeft >= REFRESH_MARGIN_SECONDS) {
        return Promise.resolve(tokens.accessToken);
      }
      refreshing ??= refresh(tokens).finally(() => {
        refreshing = null;
      });
      return refreshing;
    },

    end() {
      if (tokens !== null) {
        setTokens(null);
      }
    },

    async signOut() {
      const idToken = tokens?.idToken;
      setTokens(null, SIGNING_OUT);
      try {
        const request = await client.createSignoutRequest(
          idToken === undefined ? {} : { id_token_hint: idToken },
        );
        environment.navigate(request.url);
      } catch (error) {
        setState({
          kind: "signOutFailed",
          reason: error instanceof Error ? error.message : "Sign-out failed.",
        });
      }
    },
  };
}

/** The tokens from `response`, keeping what a refresh response leaves out from `previous`. */
function tokensFrom(response: SigninResponse, previous: Tokens | null): Tokens {
  return {
    accessToken: response.access_token,
    expiresAt: response.expires_at,
    refreshToken: response.refresh_token ?? previous?.refreshToken,
    idToken: response.id_token ?? previous?.idToken,
    profile: response.profile,
    sessionState: response.session_state ?? previous?.sessionState ?? null,
    scope: response.scope ?? previous?.scope,
  };
}

/**
 * `value` when it is a same-origin app path, otherwise `/`, so a sign-in never redirects
 * off-site. Browsers read both `//host` and `/\host` as another origin.
 */
function safeReturnPath(value: unknown): string {
  return typeof value === "string" && /^\/(?![/\\])/.test(value) ? value : "/";
}
