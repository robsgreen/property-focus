// Foundation ready gate, sub-task 0.12.4: sign a synthetic user in through the web app on
// `make dev` (http://localhost:5173) and the local Keycloak realm, in headless Chromium.
//
// The app redirects a signed-out visitor to Keycloak (authorization code with PKCE S256);
// the script fills in the login form, waits for the app to finish the callback and show its
// signed-in page, and takes the access token from the app's own token-endpoint response.
// It prints the steps and the token's non-secret claims, and writes the token to the file
// named by PF_SMOKE_TOKEN_FILE (mode 0600). The token is never printed.
//
// It then waits until pkce-smoke.py has used the token (a line on stdin), clicks "Sign out"
// and checks that the app sends the browser to Keycloak's end-session endpoint with the ID
// token as a hint, that Keycloak ends its session and returns to the app, and that the app
// then shows Keycloak's sign-in form instead of signing the user back in, also when the app
// is loaded again. A token request after the sign-out fails the run.
//
// pkce-smoke.py runs this script with PF_SMOKE_USER_EMAIL, PF_SMOKE_USER_PASSWORD and
// PF_SMOKE_TOKEN_FILE set; run that, not this file.
// Needs Playwright (require('playwright')) and Chromium at PF_SMOKE_CHROMIUM
// (default /opt/pw-browsers/chromium).
import { writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createInterface } from "node:readline";

const { chromium } = createRequire(import.meta.url)("playwright");

const WEB_ORIGIN = "http://localhost:5173";
const TOKEN_ENDPOINT = "/realms/property-focus/protocol/openid-connect/token";
const END_SESSION_ENDPOINT = "/realms/property-focus/protocol/openid-connect/logout";
const AUTHORIZE_URL = /\/realms\/property-focus\/protocol\/openid-connect\/auth/;
const { PF_SMOKE_USER_EMAIL: email, PF_SMOKE_USER_PASSWORD: password } = process.env;
const tokenFile = process.env.PF_SMOKE_TOKEN_FILE;

const browser = await chromium.launch({
  executablePath: process.env.PF_SMOKE_CHROMIUM ?? "/opt/pw-browsers/chromium",
});
try {
  const page = await browser.newPage();
  const authorizeRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/protocol/openid-connect/auth?")) {
      authorizeRequests.push(new URL(request.url()));
    }
  });
  const tokenResponse = page.waitForResponse(
    (response) => response.url().endsWith(TOKEN_ENDPOINT) && response.request().method() === "POST",
  );

  await page.goto(`${WEB_ORIGIN}/`);
  await page.waitForURL(AUTHORIZE_URL);
  const authorize = authorizeRequests[0];
  console.log(`web app redirected to Keycloak: ${authorize.origin}${authorize.pathname}`);
  for (const name of ["client_id", "redirect_uri", "response_type", "code_challenge_method"]) {
    console.log(`  ${name}=${authorize.searchParams.get(name)}`);
  }
  console.log(`  code_challenge present: ${authorize.searchParams.has("code_challenge")}`);

  await page.fill("#username", email);
  await page.fill("#password", password);
  await page.click("#kc-login");

  const response = await tokenResponse;
  const form = new URLSearchParams(response.request().postData() ?? "");
  console.log(
    `web app exchanged the code: POST ${new URL(response.url()).pathname} -> ${response.status()}` +
      ` (grant_type=${form.get("grant_type")}, code_verifier present: ${form.has("code_verifier")})`,
  );
  const tokens = await response.json();

  await page.getByRole("heading", { name: "Property Focus" }).waitFor();
  console.log(`web app signed in: ${page.url()} shows "Property Focus" and a Sign out button:`,
    await page.getByRole("button", { name: "Sign out" }).isVisible());

  const claims = JSON.parse(Buffer.from(tokens.access_token.split(".")[1], "base64url"));
  const shown = ["iss", "aud", "azp", "typ", "scope", "email_verified"];
  console.log(`access token claims: ${JSON.stringify(Object.fromEntries(shown.map((k) => [k, claims[k]])))}`);
  writeFileSync(tokenFile, tokens.access_token, { mode: 0o600 });
  console.log("TOKEN-READY");

  // pkce-smoke.py writes a line once it has finished with the token.
  for await (const _ of createInterface({ input: process.stdin })) break;

  const afterSignOut = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const appNavigation = url.origin === WEB_ORIGIN && request.isNavigationRequest();
    if ([END_SESSION_ENDPOINT, TOKEN_ENDPOINT].includes(url.pathname) || AUTHORIZE_URL.test(url.pathname) || appNavigation) {
      afterSignOut.push(request);
    }
  });
  const endSession = page.waitForRequest((request) => new URL(request.url()).pathname === END_SESSION_ENDPOINT);
  await page.getByRole("button", { name: "Sign out" }).click();
  const endSessionRequest = await endSession;
  const endSessionUrl = new URL(endSessionRequest.url());
  console.log(`web app sent the browser to Keycloak's end-session endpoint: ${endSessionUrl.origin}${endSessionUrl.pathname}`);
  console.log(`  id_token_hint present: ${endSessionUrl.searchParams.has("id_token_hint")}`);
  console.log(`  post_logout_redirect_uri=${endSessionUrl.searchParams.get("post_logout_redirect_uri")}`);
  const endSessionResponse = await endSessionRequest.response();
  console.log(`  Keycloak answered ${endSessionResponse.status()}` +
    (endSessionResponse.headers().location ? ` -> ${endSessionResponse.headers().location}` : ""));

  await page.locator("#username").waitFor({ timeout: 15000 });
  if (!AUTHORIZE_URL.test(page.url())) throw new Error(`after sign-out the browser is at ${page.url()}`);
  console.log("after sign-out the browser went:");
  for (const request of afterSignOut) {
    const url = new URL(request.url());
    // React's development StrictMode starts the sign-in twice; the first navigation is cut short.
    const status = (await request.response())?.status() ?? "no response (superseded by the next navigation)";
    console.log(`  ${request.method()} ${url.origin}${url.pathname} -> ${status}`);
  }
  const tokenRequests = afterSignOut.filter((r) => new URL(r.url()).pathname === TOKEN_ENDPOINT);
  if (tokenRequests.length) throw new Error("the web app exchanged a code after sign-out");
  const signedInHeading = await page.getByRole("heading", { name: "Property Focus" }).count();
  const signOutButton = await page.getByRole("button", { name: "Sign out" }).count();
  if (signedInHeading || signOutButton) throw new Error("the signed-in page is shown after sign-out");
  console.log(`after sign-out: ${new URL(page.url()).origin}${new URL(page.url()).pathname} shows Keycloak's ` +
    `sign-in form (username field visible: ${await page.locator("#username").isVisible()}); ` +
    "no token request, no signed-in page");
  const cookies = await page.context().cookies("http://localhost:8080/realms/property-focus/");
  const ssoCookies = cookies.filter((c) => ["KEYCLOAK_IDENTITY", "KEYCLOAK_SESSION"].includes(c.name) && c.value);
  console.log(`Keycloak session cookies left in the browser: ${ssoCookies.map((c) => c.name).join(", ") || "none"}`);
  if (ssoCookies.length) throw new Error("Keycloak session cookies survive the sign-out");

  afterSignOut.length = 0;
  await page.goto(`${WEB_ORIGIN}/`);
  await page.waitForURL(AUTHORIZE_URL);
  await page.locator("#username").waitFor();
  if (afterSignOut.some((r) => new URL(r.url()).pathname === TOKEN_ENDPOINT)) {
    throw new Error("loading the app again after sign-out signed the user in");
  }
  if (await page.getByRole("heading", { name: "Property Focus" }).count()) {
    throw new Error("loading the app again shows the signed-in page");
  }
  console.log(`loading ${WEB_ORIGIN}/ again: redirected to Keycloak, which shows its sign-in form; ` +
    "no token request, not signed in");
  console.log("SIGNED-OUT");
} finally {
  await browser.close();
}
