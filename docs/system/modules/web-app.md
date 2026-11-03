# Web App

`frontend/` is the browser app, pnpm package `@property-focus/web`: React 19, React Router 7, Redux Toolkit with RTK Query, built by Vite. Phase 0 delivers the shell only: OIDC sign-in with PKCE against the configured issuer, an in-memory session, a generated typed API client, and one signed-in page with a sign-out button. Product pages (`/homebuyer`, `/investor`) arrive in later phases.

## Key files

| File | Role |
|---|---|
| `frontend/src/main.tsx` | Entry point: builds the session from build-time settings, the store and the router, mounts on `#root` |
| `frontend/src/auth/session.ts` | `createSession()`, `Session`, `SessionState`, `SessionConfig`, `SessionEnvironment`, `SignInError`, `REFRESH_MARGIN_SECONDS` |
| `frontend/src/app/router.tsx` | `createRouter()`, `SIGN_IN_CALLBACK_PATH` (`/auth/callback`), sign-in boundary, callback and home components |
| `frontend/src/app/store.ts` | `createStore(session)`: RTK Query reducer and middleware, session as thunk extra argument |
| `frontend/src/api/baseApi.ts` | `baseApi`, `ApiContext`, `ApiError`: bearer header, problem parsing, `401` handling |
| `frontend/src/api/openapi.json` | Committed copy of the REST API's OpenAPI document (generated) |
| `frontend/src/api/generated.ts` | Generated endpoints and hooks (never edit by hand) |
| `frontend/src/api/codegen.ts` | `pnpm generate:api`: runs `@rtk-query/codegen-openapi` |
| `frontend/vite.config.ts` | Dev server on 5173 (strict), proxy for `/v1` and `/health` to `localhost:8000`, `PF_WEB_` env prefix, Vitest with jsdom |
| `frontend/tests/session.test.ts` | Session behavior with a fake issuer |
| `frontend/tests/sign-out.test.tsx` | Router and real session together: sign-out leaves for the issuer and starts no sign-in |
| `frontend/tests/router.test.tsx` | Sign-in boundary and callback route with a fake session (`tests/support/fakeSession.ts`) |
| `frontend/tests/baseApi.test.ts` | Bearer header, error statuses and problem bodies, `401` handling |

## Configuration

Only `PF_WEB_*` variables reach browser code.

| Variable | Development default | Notes |
|---|---|---|
| `PF_WEB_OIDC_ISSUER` | `http://localhost:8080/realms/property-focus` | Must equal the tokens' `iss`; required for a production build |
| `PF_WEB_OIDC_CLIENT_ID` | `property-focus-web` | Public client registered with the issuer; required for a production build |
| `PF_WEB_OIDC_END_SESSION_URL` | unset | Optional; the issuer's end-session endpoint, for an issuer whose discovery document lists none |

A production build without the issuer or the client ID throws at start-up. The redirect URI is `<origin>/auth/callback` and the post-sign-out URI `<origin>/`; both must be registered on the client (Keycloak derives them from `PF_WEB_ORIGIN`). The dogfood deploy workflow builds the app with the dogfood issuer.

## Session

`createSession(config, environment?)` wraps `oidc-client-ts`'s `OidcClient` with `response_type=code`, `scope=openid`, and the issuer's discovery document for endpoints; `config.endSessionEndpoint`, when given, supplies the end-session endpoint the discovery document lacks. Tokens live only in memory. The PKCE verifier and `state` of a sign-in in progress live in `sessionStorage` and are removed when the callback completes. A page reload therefore starts a new sign-in, which the issuer's own session usually completes silently.

| Member | Behavior |
|---|---|
| `state()` / `subscribe(listener)` | `SessionState` for `useSyncExternalStore`: `signedIn`, `signedOut`, `signingOut` or `signOutFailed` (with a `reason`) |
| `isSignedIn()` | True while the session holds tokens |
| `signIn(returnTo?)` | Redirects to the issuer; a `returnTo` that is not a same-origin path becomes `/` |
| `completeSignIn(callbackUrl)` | Checks `state`, exchanges the code with the PKCE verifier, keeps the tokens, resolves to `returnTo`; idempotent for the same URL; rejects with `SignInError` |
| `accessToken()` | Current token, refreshed first when it expires within 30 seconds; concurrent callers share one refresh; a failed refresh ends the session and returns `null` |
| `end()` | Forgets tokens locally (after an API `401`); does nothing unless signed in, so it cannot interrupt a sign-out |
| `signOut()` | Forgets tokens, moves to `signingOut` and redirects to the issuer's end-session endpoint with `id_token_hint` and `post_logout_redirect_uri`; never rejects: with no end-session endpoint, or when the request cannot be built, the state becomes `signOutFailed` |

A refresh that completes after the session ended or signed out is discarded.

## Routing

`createRouter(session)` defines `/auth/callback` (runs `completeSignIn`, then navigates to the returned path) and a `SignInBoundary` around every other route. The boundary renders children while signed in. Signed out, it calls `signIn` with the current path, query and hash; a failure renders the message with a retry. While `signingOut` it shows "Signing out…" and starts no sign-in, so nothing replaces the issuer's sign-out page. After `signOutFailed` it says the person is signed out of Property Focus but the identity provider's session could not be ended, and offers "Sign in again" instead of signing in silently; if that sign-in cannot start, it shows the failure with a retry. The only child route today is the index page (`Home`).

## API client

`baseApi` uses `fetchBaseQuery` with base URL `/`: the API shares the app's origin (Vite proxy locally, Caddy on dogfood). `prepareHeaders` adds `Authorization: Bearer <token>` from `session.accessToken()`. Errors resolve to `ApiError { status, problem }`. `status` is the HTTP status, also for a body that is not JSON (RTK Query's `PARSING_ERROR`, from its `originalStatus`), and `null` only when no response arrived (`FETCH_ERROR`, `TIMEOUT_ERROR`). `problem` is the parsed `application/problem+json` body or `null`. A `401`, whatever its body, calls `session.end()`, so the boundary starts a new sign-in.

`generated.ts` injects endpoints into `baseApi` and exports hooks such as `useGetWorkflowRunQuery`, `useGetLivenessQuery` and `useGetReadinessQuery`; operation tags (`health`, `workflow-runs`) become cache tags. To change it, change the backend and run `make generate-api`; `make check-generated` fails on drift ([REST API](./rest-api.md)).

## Build and checks

`make fe` runs `lint` (ESLint with `typescript-eslint` strict type-checked and stylistic rules plus React Hooks, zero warnings; `generated.ts` ignored), `typecheck` (`tsc --noEmit` with `tsconfig.base.json`: `strict`, `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess` and more), `test --run` (Vitest) and `build`. `pnpm --filter @property-focus/web dev` serves on `http://localhost:5173`.

## Design decisions

- Provider-neutral sign-in: endpoints come from discovery, so changing issuer is a build-settings change. Sign-out is the exception: an issuer whose discovery document lists no `end_session_endpoint` (Cognito's does not) needs `PF_WEB_OIDC_END_SESSION_URL`. Cognito's `/logout` endpoint also expects `client_id` and `logout_uri` rather than `id_token_hint` and `post_logout_redirect_uri`, so moving to the Cognito stack (phase 9) still needs a sign-out change; until then sign-out there ends in `signOutFailed`, never a silent re-sign-in.
- No tokens in persistent storage, to limit what a script injection could steal.
- The client is generated from the committed OpenAPI document, so the frontend never hand-writes request types.

## Related docs

- [Identity and authentication](./identity-and-auth.md) for the `property-focus-web` client
- [REST API](./rest-api.md)
- [Infrastructure and delivery](./infrastructure-and-delivery.md) for how dogfood serves the build
