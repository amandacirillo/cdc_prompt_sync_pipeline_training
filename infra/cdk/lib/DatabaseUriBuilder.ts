// Builds the MariaDB connection URI passed to the DynamoDB handler Lambda at
// synth time. Reading these from the CI/CD environment (rather than baking a
// literal password into the CDK app or the synthesized CloudFormation
// template) keeps the credential out of source control and out of
// `cdk synth` output history.

export class DatabaseUriBuilder {
  static buildDatabaseUri(): string {
    const server = process.env.CDK_MARIADB_SERVER || "";
    const port = process.env.CDK_MARIADB_PORT || "";
    const databaseName = process.env.CDK_MARIADB_DATABASE || "";
    const username = process.env.CDK_MARIADB_USERNAME || "";
    const password = process.env.CDK_MARIADB_PASSWORD || "";

    if (!server || !port || !databaseName || !username || !password) {
      throw new Error("Missing required MariaDB environment variables");
    }

    const encodedPassword = encodeURIComponent(password);
    return `mariadb://${username}:${encodedPassword}@${server}:${port}/${databaseName}`;
  }
}
