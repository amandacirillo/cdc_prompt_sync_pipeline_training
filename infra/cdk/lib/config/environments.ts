// Fictional environment configuration. All VPC names, subnet IDs, and
// hostnames below are made up for this training repo -- they do not
// correspond to any real infrastructure.

import { PipelineConfig } from "./types";

const TABLE_MAPPING_RULES = [
  {
    "rule-type": "selection",
    "rule-id": "1",
    "rule-name": "support-guideline-schema",
    "object-locator": { "schema-name": "support_guidelines", "table-name": "%" },
    "rule-action": "include",
  },
];

export const ENV_CONFIGS: Record<string, PipelineConfig> = {
  sandbox: {
    environment: "sandbox",
    vpcName: "training-sandbox-vpc",
    kinesis: { retentionPeriodHours: 24 },
    dynamodb: { ttlMinutes: 5, recordsPerSqsMessage: 25, timeoutMinutes: 2 },
    lambda: {
      timeoutMinutes: 2,
      memorySize: 512,
      batchSize: 10,
      maxBatchingWindowSeconds: 5,
      retryAttempts: 2,
    },
    sqs: { retentionPeriodDays: 4, visibilityTimeoutMinutes: 5 },
    dms: {
      subnetIds: ["subnet-training0001", "subnet-training0002"],
      replicationInstance: {
        instanceClass: "dms.t3.micro",
        allocatedStorageGB: 20,
        multiAz: false,
        publiclyAccessible: false,
      },
      source: {
        serverName: "training-sandbox-mariadb.example.internal",
        port: 3306,
        databaseName: "support_guidelines",
        username: "cdc_reader",
      },
      target: {
        messageFormat: "json",
        includeTableAlterOperations: false,
        includeTransactionDetails: false,
        includePartitionValue: true,
        partitionIncludeSchemaTable: true,
      },
      replicationTask: {
        migrationType: "cdc",
        tableMappings: { rules: TABLE_MAPPING_RULES },
      },
    },
  },
  nonprod: {
    environment: "nonprod",
    vpcName: "training-nonprod-vpc",
    kinesis: { retentionPeriodHours: 48 },
    dynamodb: { ttlMinutes: 5, recordsPerSqsMessage: 25, timeoutMinutes: 3 },
    lambda: {
      timeoutMinutes: 3,
      memorySize: 768,
      batchSize: 25,
      maxBatchingWindowSeconds: 10,
      retryAttempts: 3,
    },
    sqs: { retentionPeriodDays: 7, visibilityTimeoutMinutes: 10 },
    dms: {
      subnetIds: ["subnet-training1001", "subnet-training1002"],
      replicationInstance: {
        instanceClass: "dms.t3.medium",
        allocatedStorageGB: 50,
        multiAz: false,
        publiclyAccessible: false,
      },
      source: {
        serverName: "training-nonprod-mariadb.example.internal",
        port: 3306,
        databaseName: "support_guidelines",
        username: "cdc_reader",
      },
      target: {
        messageFormat: "json",
        includeTableAlterOperations: false,
        includeTransactionDetails: false,
        includePartitionValue: true,
        partitionIncludeSchemaTable: true,
      },
      replicationTask: {
        migrationType: "cdc",
        tableMappings: { rules: TABLE_MAPPING_RULES },
      },
    },
  },
  prod: {
    environment: "prod",
    vpcName: "training-prod-vpc",
    kinesis: { retentionPeriodHours: 168 },
    dynamodb: { ttlMinutes: 5, recordsPerSqsMessage: 50, timeoutMinutes: 5 },
    lambda: {
      timeoutMinutes: 5,
      memorySize: 1024,
      batchSize: 50,
      maxBatchingWindowSeconds: 15,
      retryAttempts: 3,
      sqsProcessorReservedConcurrency: 20,
    },
    sqs: { retentionPeriodDays: 14, visibilityTimeoutMinutes: 15 },
    dms: {
      subnetIds: ["subnet-training2001", "subnet-training2002"],
      replicationInstance: {
        instanceClass: "dms.t3.large",
        allocatedStorageGB: 100,
        multiAz: true,
        publiclyAccessible: false,
      },
      source: {
        serverName: "training-prod-mariadb.example.internal",
        port: 3306,
        databaseName: "support_guidelines",
        username: "cdc_reader",
      },
      target: {
        messageFormat: "json",
        includeTableAlterOperations: false,
        includeTransactionDetails: true,
        includePartitionValue: true,
        partitionIncludeSchemaTable: true,
      },
      replicationTask: {
        migrationType: "cdc",
        tableMappings: { rules: TABLE_MAPPING_RULES },
      },
    },
  },
};
