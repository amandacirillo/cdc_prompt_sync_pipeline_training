const { DynamoDBClient } = require('@aws-sdk/client-dynamodb');
const { DynamoDBDocumentClient, ScanCommand, DeleteCommand } = require('@aws-sdk/lib-dynamodb');

// Most local DynamoDB emulators don't enforce TTL expiry (only real AWS
// DynamoDB does), so this Lambda stands in for that behavior on a schedule
// (see template.yaml's `Schedule` event). In AWS, native TTL expiry deletes
// items for free and this function isn't needed there -- see infra/cdk,
// which relies on TimeToLiveSpecification instead.

const dynamoClient = DynamoDBDocumentClient.from(new DynamoDBClient({
  region: process.env.AWS_REGION || 'us-east-1',
  endpoint: process.env.DYNAMODB_ENDPOINT || undefined,
}));

const TABLE_NAME = process.env.DYNAMODB_TABLE;

exports.handler = async () => {
  try {
    let scanParams = { TableName: TABLE_NAME };
    let itemsToDelete = [];
    let lastEvaluatedKey;

    do {
      const data = await dynamoClient.send(new ScanCommand(scanParams));
      const now = Math.floor(Date.now() / 1000);

      const expiredItems = data.Items.filter((item) => item.ttl && item.ttl < now);
      itemsToDelete.push(...expiredItems);

      lastEvaluatedKey = data.LastEvaluatedKey;
      scanParams.ExclusiveStartKey = lastEvaluatedKey;
    } while (lastEvaluatedKey);

    console.log(`Found ${itemsToDelete.length} expired item(s) to delete`);

    for (const item of itemsToDelete) {
      await dynamoClient.send(new DeleteCommand({
        TableName: TABLE_NAME,
        Key: { tableName: item.tableName, id: item.id },
      }));
      console.log(`Deleted expired item ${item.tableName} / ${item.id}`);
    }

    console.log(`Deleted ${itemsToDelete.length} expired item(s) from ${TABLE_NAME}`);
  } catch (error) {
    console.error('Error deleting expired items:', error);
    throw error;
  }
};
