#!/usr/bin/env node
import { App, type IStackSynthesizer, LegacyStackSynthesizer } from 'aws-cdk-lib';

import { addRecordedAccountRule, resolveDeploymentEnvironment } from '../lib/deployment-environment';
import { IdentityStack } from '../lib/identity-stack';

// Synthesizes the stacks of the one environment named by the `environment` context value
// (cdk.json sets the default; override with `--context environment=<name>`).
const app = new App();
const target = resolveDeploymentEnvironment(
  app.node.tryGetContext('environment'),
  app.node.tryGetContext('propertyFocus:environments'),
  process.env['CDK_DEFAULT_ACCOUNT'],
);

const { settings } = target;
const identity = new IdentityStack(app, `PropertyFocus-Identity-${target.name}`, {
  env: target.env,
  // The stack has no assets, so it deploys as an inline template with the caller's
  // credentials and needs no `cdk bootstrap` resources in the account. The cast only
  // bridges aws-cdk-lib's own typings, which conflict under `exactOptionalPropertyTypes`.
  synthesizer: new LegacyStackSynthesizer() as IStackSynthesizer,
  hostedUiDomainPrefix: settings.hostedUiDomainPrefix,
  webCallbackUrls: settings.webCallbackUrls,
  webLogoutUrls: settings.webLogoutUrls,
  mcpCallbackUrls: settings.mcpCallbackUrls,
});
addRecordedAccountRule(identity, settings.account);
