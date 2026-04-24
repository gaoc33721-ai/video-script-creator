import os
import sys


def fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def ok(message: str) -> None:
    print(f"[OK] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def main() -> int:
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError
    except ModuleNotFoundError:
        return fail("boto3/botocore is not installed. Run `pip install -r requirements.txt`.")

    region = os.getenv("BEDROCK_AWS_REGION") or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    model_id = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-5-20250929-v1:0")
    bucket = os.getenv("S3_BUCKET") or os.getenv("APP_S3_BUCKET")

    try:
        sts = boto3.client("sts", region_name=region)
        identity = sts.get_caller_identity()
        ok(f"AWS credentials found: {identity.get('Arn')} in account {identity.get('Account')}")
    except NoCredentialsError:
        return fail("No AWS credentials found. Use AWS SSO, environment variables, or an ECS task role.")
    except (BotoCoreError, ClientError) as exc:
        return fail(f"Unable to validate AWS identity: {exc}")

    if bucket:
        try:
            s3 = boto3.client("s3", region_name=region)
            s3.head_bucket(Bucket=bucket)
            ok(f"S3 bucket is reachable: {bucket}")
        except (BotoCoreError, ClientError) as exc:
            warn(f"S3 bucket check failed for {bucket}: {exc}")
    else:
        warn("S3_BUCKET is not set; S3 storage check skipped.")

    try:
        bedrock = boto3.client("bedrock", region_name=region)
        models = bedrock.list_foundation_models()
        matching = [
            m.get("modelId")
            for m in models.get("modelSummaries", [])
            if m.get("modelId") == model_id or model_id in (m.get("modelArn") or "")
        ]
        if matching:
            ok(f"Bedrock model is listed in {region}: {model_id}")
        else:
            warn(f"Bedrock model was not found in list_foundation_models for {region}: {model_id}")
    except (BotoCoreError, ClientError) as exc:
        warn(f"Unable to list Bedrock foundation models in {region}: {exc}")

    try:
        runtime = boto3.client("bedrock-runtime", region_name=region)
        response = runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with only: ok"}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )
        text = "".join(
            block.get("text", "")
            for block in response.get("output", {}).get("message", {}).get("content", [])
        ).strip()
        ok(f"Bedrock Converse invocation succeeded. Response: {text[:80]}")
    except (BotoCoreError, ClientError) as exc:
        return fail(
            "Bedrock Converse invocation failed. Check model access, region, IAM policy, "
            f"and BEDROCK_MODEL_ID. Details: {exc}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
