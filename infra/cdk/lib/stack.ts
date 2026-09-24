import * as cdk from "aws-cdk-lib";
import * as kinesis from "aws-cdk-lib/aws-kinesis";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import * as dms from "aws-cdk-lib/aws-dms";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambdaEventSources from "aws-cdk-lib/aws-lambda-event-sources";
import * as iam from "aws-cdk-lib/aws-iam";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Construct } from "constructs";
import * as path from "path";
import { PipelineConfig } from "./config/types";
import { DatabaseUriBuilder } from "./DatabaseUriBuilder";

export interface CdcPromptSyncStackProps extends cdk.StackProps {
  config: PipelineConfig;
}

export class CdcPromptSyncPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: CdcPromptSyncStackProps) {
    super(scope, id, props);
    const { config } = props;

    // Training repo note: the real stack uses ec2.Vpc.fromLookup against a
    // real account/VPC. We use fromVpcAttributes with fictional IDs instead
    // so `cdk synth` runs deterministically in CI without AWS credentials.
    const vpc = ec2.Vpc.fromVpcAttributes(this, "Vpc", {
      vpcId: "vpc-training0000",
      availabilityZones: ["us-east-1a", "us-east-1b"],
      privateSubnetIds: config.dms.subnetIds,
    });

    // --- DynamoDB trigger-bus table -------------------------------------
    // Nothing ever reads these items back; the table exists purely so that
    // its TTL-expiry delete fires a DynamoDB Streams REMOVE event carrying
    // the changed row's payload as OldImage. See dynamodb_handler's module
    // docstring for the full rationale.
    const trackingTable = new dynamodb.Table(this, "GuidelineTrackingTable", {
      partitionKey: { name: "id", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "expiresAt",
      stream: dynamodb.StreamViewType.OLD_IMAGE,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Kinesis stream (DMS CDC target) ---------------------------------
    const kinesisStream = new kinesis.Stream(this, "CdcKinesisStream", {
      retentionPeriod: cdk.Duration.hours(config.kinesis.retentionPeriodHours),
      streamMode: kinesis.StreamMode.ON_DEMAND,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // --- Shared Python dependency layer -----------------------------------
    // Mirrors the real system's split: third-party deps (boto3, pymysql,
    // langfuse) live in a shared layer; domain code stays in each Lambda's
    // own package so the two can be deployed independently.
    const sharedPythonDependencyLayer = new lambda.LayerVersion(this, "SharedPythonDependencyLayer", {
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../layers/shared-dependencies-python")),
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: "Shared third-party dependencies for Python Lambda functions",
    });

    // --- Queues -------------------------------------------------------
    const kinesisHandlerDlq = new sqs.Queue(this, "KinesisHandlerDLQ", {
      retentionPeriod: cdk.Duration.days(config.sqs.retentionPeriodDays),
      visibilityTimeout: cdk.Duration.minutes(config.sqs.visibilityTimeoutMinutes),
    });

    // Standard (not FIFO) queue: SYNC_COMBINATIONS messages rely on
    // per-message DelaySeconds to land after their SYNC_GUIDELINES
    // counterpart, and FIFO queues do not support per-message delays. This
    // is a deliberate correction from the original system's real stack,
    // which used FIFO queues -- see the README's "intentional
    // improvements" section.
    const processingDlq = new sqs.Queue(this, "ProcessingDLQ", {
      queueName: `ProcessingDLQ-${config.environment}`,
      retentionPeriod: cdk.Duration.days(config.sqs.retentionPeriodDays),
      visibilityTimeout: cdk.Duration.minutes(config.sqs.visibilityTimeoutMinutes),
    });

    const processingQueue = new sqs.Queue(this, "ProcessingQueue", {
      queueName: `ProcessingQueue-${config.environment}`,
      retentionPeriod: cdk.Duration.days(14),
      visibilityTimeout: cdk.Duration.minutes(config.sqs.visibilityTimeoutMinutes),
      deadLetterQueue: { queue: processingDlq, maxReceiveCount: 3 },
    });

    // --- DynamoDB Streams handler Lambda (Python) -------------------------
    const dynamodbHandler = new lambda.Function(this, "DynamodbHandler", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "dynamodb_processor.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../dynamodb_handler"), {
        exclude: ["requirements.txt"],
      }),
      layers: [sharedPythonDependencyLayer],
      environment: {
        SQS_QUEUE_URL: processingQueue.queueUrl,
        DATABASE_URI: DatabaseUriBuilder.buildDatabaseUri(),
        RECORDS_PER_SQS: `${config.dynamodb.recordsPerSqsMessage}`,
      },
      timeout: cdk.Duration.minutes(config.dynamodb.timeoutMinutes),
      memorySize: config.lambda.memorySize,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });
    processingQueue.grantSendMessages(dynamodbHandler);

    // Native DynamoDB Streams trigger, filtered to REMOVE only -- this is
    // the local/lower-environment path (see template.yaml for the SAM
    // equivalent used in local dev).
    dynamodbHandler.addEventSource(
      new lambdaEventSources.DynamoEventSource(trackingTable, {
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        filters: [lambda.FilterCriteria.filter({ eventName: lambda.FilterRule.isEqual("REMOVE") })],
        retryAttempts: config.lambda.retryAttempts,
      }),
    );

    // --- SQS processor Lambda (Python) ------------------------------------
    const sqsProcessor = new lambda.Function(this, "SqsProcessor", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "sqs_processor.lambda_handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../sqs_processor"), {
        exclude: ["requirements.txt"],
      }),
      layers: [sharedPythonDependencyLayer],
      environment: {
        LANGFUSE_SECRET_KEY: process.env.LANGFUSE_SECRET_KEY ?? "",
        LANGFUSE_PUBLIC_KEY: process.env.LANGFUSE_PUBLIC_KEY ?? "",
        LANGFUSE_HOST: process.env.LANGFUSE_HOST ?? "",
      },
      timeout: cdk.Duration.minutes(config.lambda.timeoutMinutes),
      memorySize: config.lambda.memorySize,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      reservedConcurrentExecutions: config.lambda.sqsProcessorReservedConcurrency,
    });

    sqsProcessor.addEventSource(
      new lambdaEventSources.SqsEventSource(processingQueue, {
        batchSize: 1,
        maxConcurrency: config.lambda.sqsProcessorReservedConcurrency ?? 100,
        reportBatchItemFailures: true,
      }),
    );

    // --- Kinesis bridge Lambda (Node.js) ----------------------------------
    // Decodes DMS-via-Kinesis CDC records and invokes dynamodbHandler
    // directly with a synthetic DynamoDB-Streams-shaped REMOVE event -- this
    // is the production CDC path (DMS never touches the DynamoDB table).
    const kinesisHandler = new lambda.Function(this, "KinesisHandler", {
      runtime: lambda.Runtime.NODEJS_20_X,
      handler: "index.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "../../../kinesis_handler")),
      environment: {
        TTL_MINUTES: config.dynamodb.ttlMinutes.toString(),
        TARGET_LAMBDA_NAME: dynamodbHandler.functionName,
      },
      timeout: cdk.Duration.minutes(config.lambda.timeoutMinutes),
      memorySize: config.lambda.memorySize,
    });
    dynamodbHandler.grantInvoke(kinesisHandler);

    kinesisHandler.addEventSource(
      new lambdaEventSources.KinesisEventSource(kinesisStream, {
        batchSize: config.lambda.batchSize,
        startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        maxBatchingWindow: cdk.Duration.seconds(config.lambda.maxBatchingWindowSeconds),
        retryAttempts: config.lambda.retryAttempts,
        reportBatchItemFailures: true,
        onFailure: new lambdaEventSources.SqsDlq(kinesisHandlerDlq),
      }),
    );

    // --- DMS: MariaDB -> Kinesis CDC pipeline -----------------------------
    const dmsSubnetGroup = new dms.CfnReplicationSubnetGroup(this, "DmsSubnetGroup", {
      replicationSubnetGroupIdentifier: `${config.environment}-cdc-dms-subnet-group`,
      replicationSubnetGroupDescription: `DMS subnet group for ${config.environment}`,
      subnetIds: config.dms.subnetIds,
    });

    const dmsSecurityGroup = new ec2.SecurityGroup(this, "DmsSecurityGroup", {
      vpc,
      description: `DMS security group for ${config.environment}`,
      allowAllOutbound: false,
    });
    dmsSecurityGroup.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(3306), "Allow MariaDB");
    dmsSecurityGroup.addEgressRule(ec2.Peer.anyIpv4(), ec2.Port.tcp(443), "Allow HTTPS");

    const replicationInstance = new dms.CfnReplicationInstance(this, "ReplicationInstance", {
      replicationInstanceClass: config.dms.replicationInstance.instanceClass,
      allocatedStorage: config.dms.replicationInstance.allocatedStorageGB,
      multiAz: config.dms.replicationInstance.multiAz,
      publiclyAccessible: config.dms.replicationInstance.publiclyAccessible,
      replicationSubnetGroupIdentifier: dmsSubnetGroup.ref,
      vpcSecurityGroupIds: [dmsSecurityGroup.securityGroupId],
    });
    replicationInstance.addDependency(dmsSubnetGroup);

    const sourceEndpoint = new dms.CfnEndpoint(this, "SourceEndpoint", {
      endpointType: "source",
      engineName: "mariadb",
      serverName: config.dms.source.serverName,
      port: config.dms.source.port,
      databaseName: config.dms.source.databaseName,
      username: config.dms.source.username,
      password: process.env.CDK_MARIADB_PASSWORD ?? "",
      extraConnectionAttributes: config.dms.source.extraConnectionAttributes,
    });

    const targetEndpoint = new dms.CfnEndpoint(this, "TargetEndpoint", {
      endpointType: "target",
      engineName: "kinesis",
      kinesisSettings: {
        streamArn: kinesisStream.streamArn,
        messageFormat: config.dms.target.messageFormat,
        serviceAccessRoleArn: this.createDmsKinesisRole(config.environment).roleArn,
        includeTableAlterOperations: config.dms.target.includeTableAlterOperations,
        includeTransactionDetails: config.dms.target.includeTransactionDetails,
        includePartitionValue: config.dms.target.includePartitionValue,
        partitionIncludeSchemaTable: config.dms.target.partitionIncludeSchemaTable,
      },
    });

    new dms.CfnReplicationTask(this, "ReplicationTask", {
      sourceEndpointArn: sourceEndpoint.ref,
      targetEndpointArn: targetEndpoint.ref,
      replicationInstanceArn: replicationInstance.ref,
      migrationType: config.dms.replicationTask.migrationType,
      tableMappings: JSON.stringify({ rules: config.dms.replicationTask.tableMappings.rules }),
      replicationTaskSettings: JSON.stringify({
        // Before-images let the downstream bridge distinguish an update
        // from a delete even though DMS-via-Kinesis only ever produces
        // synthetic REMOVE events for this pipeline.
        BeforeImageSettings: { EnableBeforeImage: true, FieldName: "before-data", ColumnFilter: "all" },
      }),
    });

    // --- Outputs -----------------------------------------------------
    new cdk.CfnOutput(this, "KinesisHandlerName", { value: kinesisHandler.functionName });
    new cdk.CfnOutput(this, "DynamodbHandlerName", { value: dynamodbHandler.functionName });
    new cdk.CfnOutput(this, "SqsProcessorName", { value: sqsProcessor.functionName });
    new cdk.CfnOutput(this, "KinesisStreamName", { value: kinesisStream.streamName });
    new cdk.CfnOutput(this, "ProcessingQueueName", { value: processingQueue.queueName });
    new cdk.CfnOutput(this, "TrackingTableName", { value: trackingTable.tableName });
  }

  private createDmsKinesisRole(environment: string): iam.Role {
    return new iam.Role(this, "DmsKinesisRole", {
      assumedBy: new iam.ServicePrincipal("dms.amazonaws.com"),
      inlinePolicies: {
        KinesisAccess: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ["kinesis:PutRecord", "kinesis:PutRecords", "kinesis:DescribeStream"],
              resources: ["*"],
            }),
          ],
        }),
      },
    });
  }
}
