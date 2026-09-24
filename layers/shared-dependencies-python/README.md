# Shared Python dependency layer

This directory is packaged as a Lambda layer and mounted by both Python
Lambdas (`dynamodb_handler`, `sqs_processor`). It carries only third-party
dependencies (boto3, pymysql, langfuse) -- never domain code -- so the layer
can be rebuilt/redeployed independently of either Lambda's business logic.

In this training repo the directory is a placeholder (no real vendored
packages are committed) since each Lambda's own `requirements.txt` already
declares what it needs for local `pip install` / SAM-based local dev. In the
original system this directory is populated by a build step that runs
`pip install -r shared-requirements.txt -t python/` before `cdk deploy`.
