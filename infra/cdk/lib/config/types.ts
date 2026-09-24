// Typed per-environment configuration for the CDC prompt-sync stack.
//
// Mirrors the real system's environment-config pattern: every tunable knob
// (queue retention, Lambda memory/timeout, DMS instance sizing, VPC/subnet
// selection) lives in one typed object per environment rather than scattered
// magic numbers in the stack, so promoting a change from sandbox -> nonprod
// -> prod is a config diff, not a code diff.

export interface PipelineConfig {
  environment: string;

  vpcName: string;

  kinesis: {
    retentionPeriodHours: number;
  };

  dynamodb: {
    ttlMinutes: number;
    recordsPerSqsMessage: number;
    timeoutMinutes: number;
  };

  lambda: {
    timeoutMinutes: number;
    memorySize: number;
    batchSize: number;
    maxBatchingWindowSeconds: number;
    retryAttempts: number;
    sqsProcessorReservedConcurrency?: number;
  };

  sqs: {
    retentionPeriodDays: number;
    visibilityTimeoutMinutes: number;
  };

  dms: {
    subnetIds: string[];
    replicationInstance: {
      instanceClass: string;
      allocatedStorageGB: number;
      multiAz: boolean;
      publiclyAccessible: boolean;
    };
    source: {
      serverName: string;
      port: number;
      databaseName: string;
      username: string;
      // Real password is never stored in config; it's read from an
      // environment variable at synth time (see DatabaseUriBuilder) so it
      // never lands in checked-in code or CloudFormation templates as a
      // literal.
      extraConnectionAttributes?: string;
    };
    target: {
      messageFormat: "json";
      includeTableAlterOperations: boolean;
      includeTransactionDetails: boolean;
      includePartitionValue: boolean;
      partitionIncludeSchemaTable: boolean;
    };
    replicationTask: {
      migrationType: "full-load" | "cdc" | "full-load-and-cdc";
      tableMappings: {
        rules: Array<{
          "rule-type": string;
          "rule-id": string;
          "rule-name": string;
          "object-locator": {
            "schema-name": string;
            "table-name": string;
          };
          "rule-action": string;
        }>;
      };
    };
  };
}
