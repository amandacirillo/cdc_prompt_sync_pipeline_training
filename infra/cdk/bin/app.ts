#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { CdcPromptSyncPipelineStack } from "../lib/stack";
import { ENV_CONFIGS } from "../lib/config/environments";

const app = new cdk.App();

const envName = app.node.tryGetContext("envName") ?? "sandbox";
const config = ENV_CONFIGS[envName];
if (!config) {
  throw new Error(`Unknown envName "${envName}". Expected one of: ${Object.keys(ENV_CONFIGS).join(", ")}`);
}

new CdcPromptSyncPipelineStack(app, `CdcPromptSyncPipelineTrainingStack-${config.environment}`, {
  config,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? "us-east-1",
  },
});
