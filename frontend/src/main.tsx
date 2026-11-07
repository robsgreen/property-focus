/** The web app's entry point: session, store and router, mounted on `#root`. */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Provider } from "react-redux";
import { RouterProvider } from "react-router";

import { createRouter, SIGN_IN_CALLBACK_PATH } from "./app/router";
import { createStore } from "./app/store";
import { createSession } from "./auth/session";
import "./styles.css";

// Development falls back to the local Keycloak realm and its web client; a production build
// must be given both.
const LOCAL_ISSUER = "http://localhost:8080/realms/property-focus";
const LOCAL_CLIENT_ID = "property-focus-web";

function setting(name: string, developmentDefault: string): string {
  const value = import.meta.env[name] as string | undefined;
  if (value) {
    return value;
  }
  if (import.meta.env.DEV) {
    return developmentDefault;
  }
  throw new Error(`${name} must be set when the web app is built.`);
}

// Only for an issuer whose discovery document lists no end-session endpoint.
const endSessionEndpoint = import.meta.env["PF_WEB_OIDC_END_SESSION_URL"] as string | undefined;

const origin = window.location.origin;
const session = createSession({
  issuer: setting("PF_WEB_OIDC_ISSUER", LOCAL_ISSUER),
  clientId: setting("PF_WEB_OIDC_CLIENT_ID", LOCAL_CLIENT_ID),
  redirectUri: origin + SIGN_IN_CALLBACK_PATH,
  postSignOutRedirectUri: `${origin}/`,
  ...(endSessionEndpoint ? { endSessionEndpoint } : {}),
});

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html has no #root element.");
}
createRoot(root).render(
  <StrictMode>
    <Provider store={createStore(session)}>
      <RouterProvider router={createRouter(session)} />
    </Provider>
  </StrictMode>,
);
