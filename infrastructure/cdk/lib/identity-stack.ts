import { CfnOutput, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import {
  AccountRecovery,
  OAuthScope,
  UserPool,
  UserPoolClientIdentityProvider,
  type UserPoolClient,
} from 'aws-cdk-lib/aws-cognito';
import type { Construct } from 'constructs';

import type { EnvironmentSettings } from './deployment-environment';

/** Stack props plus the identity settings of the target environment. */
export type IdentityStackProps = StackProps &
  Pick<
    EnvironmentSettings,
    'hostedUiDomainPrefix' | 'webCallbackUrls' | 'webLogoutUrls' | 'mcpCallbackUrls'
  >;

/**
 * The Cognito identity stack: a user pool where people sign themselves up with a
 * verified email address, its hosted UI domain, and two public app clients that both
 * use only the authorization code flow with PKCE:
 *
 * - the web app client, used by the browser app;
 * - one MCP app client shared by every MCP client. Any client implementing the MCP
 *   authorization specification uses this client ID; nothing here is specific to a
 *   particular MCP client except the callback-URL allowlist Cognito requires.
 *
 * The stack depends on no other stack, so it deploys alone. The user pool is retained
 * and deletion-protected because it holds every person's sign-in identity.
 */
export class IdentityStack extends Stack {
  readonly userPool: UserPool;
  readonly webClient: UserPoolClient;
  readonly mcpClient: UserPoolClient;

  constructor(scope: Construct, id: string, props: IdentityStackProps) {
    super(scope, id, props);

    this.userPool = new UserPool(this, 'UserPool', {
      selfSignUpEnabled: true,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      accountRecovery: AccountRecovery.EMAIL_ONLY,
      deletionProtection: true,
      removalPolicy: RemovalPolicy.RETAIN,
    });

    const domain = this.userPool.addDomain('HostedUiDomain', {
      cognitoDomain: { domainPrefix: props.hostedUiDomainPrefix },
    });

    this.webClient = this.addPublicPkceClient(
      'WebClient',
      props.webCallbackUrls,
      props.webLogoutUrls,
    );
    this.mcpClient = this.addPublicPkceClient('McpClient', props.mcpCallbackUrls, []);

    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'IssuerUrl', { value: this.userPool.userPoolProviderUrl });
    new CfnOutput(this, 'HostedUiBaseUrl', { value: domain.baseUrl() });
    new CfnOutput(this, 'WebClientId', { value: this.webClient.userPoolClientId });
    new CfnOutput(this, 'McpClientId', { value: this.mcpClient.userPoolClientId });
  }

  /**
   * Adds an app client without a secret that can obtain tokens only through the hosted
   * UI's authorization code flow (with PKCE, as public clients require) and refresh them.
   */
  private addPublicPkceClient(
    id: string,
    callbackUrls: string[],
    logoutUrls: string[],
  ): UserPoolClient {
    return this.userPool.addClient(id, {
      generateSecret: false,
      // Listing every direct sign-in flow as disabled leaves only refresh-token use;
      // omitting authFlows would let Cognito enable SRP and custom auth by default.
      authFlows: {
        user: false,
        userPassword: false,
        adminUserPassword: false,
        userSrp: false,
        custom: false,
      },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [OAuthScope.OPENID, OAuthScope.EMAIL, OAuthScope.PROFILE],
        callbackUrls,
        logoutUrls,
      },
      supportedIdentityProviders: [UserPoolClientIdentityProvider.COGNITO],
      preventUserExistenceErrors: true,
    });
  }
}
