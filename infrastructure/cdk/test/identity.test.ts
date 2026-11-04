import { App, Stack } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { describe, expect, it } from 'vitest';

import { addRecordedAccountRule, resolveDeploymentEnvironment } from '../lib/deployment-environment';
import { IdentityStack } from '../lib/identity-stack';

const MCP_CALLBACK = 'http://localhost:8976/callback';

function synthesizeIdentityTemplate(): Template {
  const stack = new IdentityStack(new App(), 'PropertyFocus-Identity-test', {
    env: { account: '123456789012', region: 'us-east-1' },
    hostedUiDomainPrefix: 'property-focus-test',
    webCallbackUrls: ['https://app.example.test/auth/callback'],
    webLogoutUrls: ['https://app.example.test/'],
    mcpCallbackUrls: [MCP_CALLBACK],
  });
  return Template.fromStack(stack);
}

describe('IdentityStack', () => {
  const template = synthesizeIdentityTemplate();
  const clientProperties = Object.values(
    template.findResources('AWS::Cognito::UserPoolClient'),
  ).map((resource) => resource['Properties']);

  it('lets people sign themselves up and verifies their email address', () => {
    template.hasResourceProperties('AWS::Cognito::UserPool', {
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: false },
      UsernameAttributes: ['email'],
      AutoVerifiedAttributes: ['email'],
    });
  });

  it('retains the user pool so sign-in identities survive a stack deletion', () => {
    template.hasResource('AWS::Cognito::UserPool', {
      DeletionPolicy: 'Retain',
      Properties: Match.objectLike({ DeletionProtection: 'ACTIVE' }),
    });
  });

  it('serves the hosted UI on the configured domain prefix', () => {
    template.hasResourceProperties('AWS::Cognito::UserPoolDomain', {
      Domain: 'property-focus-test',
    });
  });

  it('defines exactly two app clients: the web app and one shared MCP client', () => {
    template.resourceCountIs('AWS::Cognito::UserPoolClient', 2);
    const callbackLists = clientProperties.map((client) => client.CallbackURLs);
    expect(callbackLists).toEqual(
      expect.arrayContaining([['https://app.example.test/auth/callback'], [MCP_CALLBACK]]),
    );
  });

  it('gives no app client a secret', () => {
    for (const client of clientProperties) {
      expect(client.GenerateSecret).toBe(false);
    }
  });

  it('allows only the authorization code flow from the hosted UI, with no direct sign-in', () => {
    for (const client of clientProperties) {
      expect(client).toMatchObject({
        AllowedOAuthFlowsUserPoolClient: true,
        AllowedOAuthFlows: ['code'],
        ExplicitAuthFlows: ['ALLOW_REFRESH_TOKEN_AUTH'],
        SupportedIdentityProviders: ['COGNITO'],
      });
    }
  });

  it('publishes the issuer and both client IDs for the backend and sign-in smoke', () => {
    const outputs = ['UserPoolId', 'IssuerUrl', 'HostedUiBaseUrl', 'WebClientId', 'McpClientId'];
    for (const output of outputs) {
      template.hasOutput(output, {});
    }
  });
});

describe('resolveDeploymentEnvironment', () => {
  const environments = {
    dogfood: {
      account: '123456789012',
      region: 'us-east-1',
      hostedUiDomainPrefix: 'property-focus-dogfood',
      webCallbackUrls: ['http://localhost:5173/auth/callback'],
      webLogoutUrls: ['http://localhost:5173/'],
      mcpCallbackUrls: [MCP_CALLBACK],
    },
    beta: {
      account: null,
      region: 'us-west-2',
      hostedUiDomainPrefix: 'property-focus-beta',
      webCallbackUrls: ['http://localhost:5173/auth/callback'],
      webLogoutUrls: ['http://localhost:5173/'],
      mcpCallbackUrls: [MCP_CALLBACK],
    },
  };

  it('pins the stack to the allowlisted account when the credentials match it', () => {
    const target = resolveDeploymentEnvironment('dogfood', environments, '123456789012');
    expect(target.name).toBe('dogfood');
    expect(target.env).toEqual({ account: '123456789012', region: 'us-east-1' });
  });

  it('synthesizes without AWS credentials', () => {
    expect(resolveDeploymentEnvironment('dogfood', environments, undefined).env).toEqual({
      account: '123456789012',
      region: 'us-east-1',
    });
    expect(resolveDeploymentEnvironment('beta', environments, undefined).env).toEqual({
      region: 'us-west-2',
    });
  });

  it('refuses credentials for an account outside the allowlist', () => {
    expect(() => resolveDeploymentEnvironment('dogfood', environments, '999999999999')).toThrow(
      /may only use account 123456789012/,
    );
  });

  it('refuses any credentials while the environment has no recorded account', () => {
    expect(() => resolveDeploymentEnvironment('beta', environments, '123456789012')).toThrow(
      /no allowlisted AWS account/,
    );
  });

  it('refuses an unknown environment name', () => {
    expect(() => resolveDeploymentEnvironment('production', environments, undefined)).toThrow(
      /Unknown environment "production"; expected one of: dogfood, beta/,
    );
  });

  it('refuses malformed settings', () => {
    const malformed = {
      dogfood: { ...environments.dogfood, account: '1234' },
    };
    expect(() => resolveDeploymentEnvironment('dogfood', malformed, undefined)).toThrow(
      /"account" must be a 12-digit AWS account ID or null/,
    );
    const noCallbacks = { dogfood: { ...environments.dogfood, mcpCallbackUrls: [] } };
    expect(() => resolveDeploymentEnvironment('dogfood', noCallbacks, undefined)).toThrow(
      /"mcpCallbackUrls" must be a non-empty list of absolute URLs/,
    );
  });
});

describe('addRecordedAccountRule', () => {
  function accountRule(account: string | null): unknown {
    const stack = new Stack(new App(), 'RuleTest');
    addRecordedAccountRule(stack, account);
    return Template.fromStack(stack).toJSON()['Rules']['RecordedAccountOnly'];
  }

  it('lets the stack deploy only to the recorded account', () => {
    expect(accountRule('123456789012')).toEqual({
      Assertions: [
        {
          Assert: { 'Fn::Equals': [{ Ref: 'AWS::AccountId' }, '123456789012'] },
          AssertDescription: 'This stack may only be deployed to AWS account 123456789012.',
        },
      ],
    });
  });

  it('refuses every account while none is recorded', () => {
    expect(accountRule(null)).toEqual({
      Assertions: [
        {
          Assert: { 'Fn::Equals': [{ Ref: 'AWS::AccountId' }, ''] },
          AssertDescription:
            'This environment has no recorded AWS account; record it in cdk.json first.',
        },
      ],
    });
  });
});
