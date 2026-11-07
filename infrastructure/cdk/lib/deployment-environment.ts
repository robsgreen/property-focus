import { Aws, CfnRule, Fn, type Environment, type Stack } from 'aws-cdk-lib';

/**
 * One deployment environment as recorded in `cdk.json` under the
 * `propertyFocus:environments` context key.
 */
export interface EnvironmentSettings {
  /**
   * The only AWS account (12 digits) this environment may deploy to, or `null` until an
   * operator records it. With `null`, the app still synthesizes but refuses to run under
   * any AWS credentials.
   */
  readonly account: string | null;
  readonly region: string;
  /** Prefix of the Cognito hosted UI domain; unique per region across all AWS accounts. */
  readonly hostedUiDomainPrefix: string;
  /** Redirect URLs the web app may receive authorization codes on. */
  readonly webCallbackUrls: string[];
  readonly webLogoutUrls: string[];
  /**
   * Redirect URLs allowlisted on the one shared MCP app client. Cognito accepts only
   * listed URLs, so an MCP client with a new redirect URI needs an entry here.
   */
  readonly mcpCallbackUrls: string[];
}

/** The environment an app run targets: its name, CDK environment and settings. */
export interface DeploymentEnvironment {
  readonly name: string;
  readonly env: Environment;
  readonly settings: EnvironmentSettings;
}

/**
 * Selects the deployment environment named by `environmentName` from `environments`
 * (both raw CDK context values) and enforces the account allowlist.
 *
 * `credentialsAccount` is the account of the AWS credentials the CDK CLI resolved
 * (`CDK_DEFAULT_ACCOUNT`), or `undefined` when there are none, as in offline synth and
 * tests. When credentials are present they must belong to the environment's recorded
 * account; this stops a deploy or diff from running against any other account.
 *
 * Throws an `Error` naming the problem when the environment is unknown, its settings are
 * malformed, or the credentials belong to a non-allowlisted account.
 */
export function resolveDeploymentEnvironment(
  environmentName: unknown,
  environments: unknown,
  credentialsAccount: string | undefined,
): DeploymentEnvironment {
  if (typeof environmentName !== 'string' || !isRecord(environments)) {
    throw new Error(
      'Set the "environment" and "propertyFocus:environments" context values (see cdk.json).',
    );
  }
  const raw = environments[environmentName];
  if (!isRecord(raw)) {
    const known = Object.keys(environments).join(', ');
    throw new Error(`Unknown environment "${environmentName}"; expected one of: ${known}.`);
  }
  const settings = parseSettings(environmentName, raw);

  if (credentialsAccount !== undefined) {
    if (settings.account === null) {
      throw new Error(
        `Environment "${environmentName}" has no allowlisted AWS account; ` +
          'record it in cdk.json before using AWS credentials.',
      );
    }
    if (credentialsAccount !== settings.account) {
      throw new Error(
        `AWS credentials are for account ${credentialsAccount}, but environment ` +
          `"${environmentName}" may only use account ${settings.account}.`,
      );
    }
  }

  const env: Environment =
    settings.account === null
      ? { region: settings.region }
      : { account: settings.account, region: settings.region };
  return { name: environmentName, env, settings };
}

/**
 * Adds a CloudFormation rule to `stack` that refuses to create or update it in any account
 * but `account`, the environment's recorded one. The app's own credential check guards
 * only `cdk` commands; the rule also guards a synthesized template deployed another way.
 * With no recorded account (`null`) the rule refuses every account.
 */
export function addRecordedAccountRule(stack: Stack, account: string | null): void {
  new CfnRule(stack, 'RecordedAccountOnly', {
    assertions: [
      {
        // AWS::AccountId is never empty, so a null account makes the assertion always fail.
        assert: Fn.conditionEquals(Aws.ACCOUNT_ID, account ?? ''),
        assertDescription:
          account === null
            ? 'This environment has no recorded AWS account; record it in cdk.json first.'
            : `This stack may only be deployed to AWS account ${account}.`,
      },
    ],
  });
}

function parseSettings(name: string, raw: Record<string, unknown>): EnvironmentSettings {
  const fail = (field: string, expected: string): never => {
    throw new Error(`Environment "${name}": "${field}" must be ${expected}.`);
  };
  const text = (field: string): string => {
    const value = raw[field];
    return typeof value === 'string' && value !== '' ? value : fail(field, 'a non-empty string');
  };
  const urls = (field: string): string[] => {
    const value = raw[field];
    return Array.isArray(value) &&
      value.length > 0 &&
      value.every((url) => typeof url === 'string' && URL.canParse(url))
      ? (value as string[])
      : fail(field, 'a non-empty list of absolute URLs');
  };

  const account = raw['account'];
  if (account !== null && !(typeof account === 'string' && /^\d{12}$/.test(account))) {
    fail('account', 'a 12-digit AWS account ID or null');
  }
  return {
    account: account as string | null,
    region: text('region'),
    hostedUiDomainPrefix: text('hostedUiDomainPrefix'),
    webCallbackUrls: urls('webCallbackUrls'),
    webLogoutUrls: urls('webLogoutUrls'),
    mcpCallbackUrls: urls('mcpCallbackUrls'),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
