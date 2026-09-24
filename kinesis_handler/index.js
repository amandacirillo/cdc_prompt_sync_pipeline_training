const { LambdaClient, InvokeCommand } = require('@aws-sdk/client-lambda');

// This Lambda is a *protocol adapter*: it lets a DMS-via-Kinesis CDC feed
// drive the exact same downstream processor Lambda that DynamoDB Streams
// drives locally, by re-shaping each Kinesis record into a synthetic
// DynamoDB Streams REMOVE event (the one shape that processor already
// handles). No business logic lives here -- just event translation.

const lambdaClient = new LambdaClient({ region: process.env.AWS_REGION || 'us-east-1' });

function buildSyntheticRemoveEvent(cdcRecord) {
  const data = cdcRecord.data || {};
  const beforeData = cdcRecord['before-data'] || {};
  const metadata = cdcRecord.metadata || {};

  return {
    Records: [
      {
        eventID: `${Date.now()}`,
        eventName: 'REMOVE',
        eventVersion: '1.1',
        eventSource: 'aws:dynamodb',
        awsRegion: process.env.AWS_REGION,
        dynamodb: {
          Keys: { tableName: metadata['table-name'], id: data.id },
          OldImage: {
            data: {
              ...data,
              tableName: metadata['table-name'],
              operation: metadata['operation'],
            },
            'before-data': beforeData,
          },
          ApproximateCreationDateTime: Math.floor(Date.now() / 1000),
          StreamViewType: 'OLD_IMAGE',
          SequenceNumber: `${Date.now()}`,
        },
      },
    ],
  };
}

exports.handler = async (event) => {
  console.log(`Processing ${event.Records.length} Kinesis CDC record(s)`);
  const results = [];

  for (const record of event.Records) {
    try {
      const payload = JSON.parse(Buffer.from(record.kinesis.data, 'base64').toString('utf-8'));

      if (payload.metadata?.['record-type'] !== 'data') {
        console.log('Skipping non-data CDC record');
        continue;
      }

      const syntheticEvent = buildSyntheticRemoveEvent(payload);
      const targetLambda = process.env.TARGET_LAMBDA_NAME;

      await lambdaClient.send(new InvokeCommand({
        FunctionName: targetLambda,
        InvocationType: 'Event', // async, fire-and-forget
        Payload: Buffer.from(JSON.stringify(syntheticEvent)),
      }));

      results.push({ success: true, id: payload.data?.id, tableName: payload.metadata?.['table-name'] });
    } catch (err) {
      console.error('Error processing CDC record:', err);
      results.push({ success: false, error: err.message });
    }
  }

  console.log(`Finished processing ${results.length} record(s)`);
  return results;
};
