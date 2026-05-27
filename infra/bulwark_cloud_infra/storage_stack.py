"""S3 bucket, DynamoDB table, SNS topic, and Secrets Manager for bulwark-cloud."""
import aws_cdk as cdk
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sns as sns
from constructs import Construct


class StorageStack(cdk.Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── S3 artefacts bucket ────────────────────────────────────────────
        self.bucket = s3.Bucket(
            self,
            "ArtefactsBucket",
            bucket_name=f"bulwark-cloud-artefacts-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ArchiveWorkspaceArtefacts",
                    # Only intermediate workspace/* artefacts expire; report/* is kept
                    prefix="*/workspace/",
                    expiration=cdk.Duration.days(90),
                ),
                s3.LifecycleRule(
                    id="TierToIntelligentTiering",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INTELLIGENT_TIERING,
                            transition_after=cdk.Duration.days(30),
                        ),
                    ],
                ),
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # ── DynamoDB single-table ──────────────────────────────────────────
        self.table = dynamodb.Table(
            self,
            "StateTable",
            table_name="bulwark-cloud-state",
            partition_key=dynamodb.Attribute(name="PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="SK", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        # GSI1: severity-indexed findings — query all CRITICAL findings across jobs
        self.table.add_global_secondary_index(
            index_name="GSI1",
            partition_key=dynamodb.Attribute(name="GSI1PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI1SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # GSI2: recent jobs by status — list all RUNNING or COMPLETED jobs sorted by time
        self.table.add_global_secondary_index(
            index_name="GSI2",
            partition_key=dynamodb.Attribute(name="GSI2PK", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="GSI2SK", type=dynamodb.AttributeType.STRING),
            projection_type=dynamodb.ProjectionType.INCLUDE,
            non_key_attributes=["job_id", "status", "repo", "created_at", "completed_at"],
        )

        # ── Secrets Manager ────────────────────────────────────────────────
        self.secrets = {
            "anthropic": secretsmanager.Secret(
                self,
                "AnthropicSecret",
                secret_name="bulwark-cloud/anthropic",
                description="Anthropic API key for Bulwark Claude calls — set manually after deploy",
            ),
            "github": secretsmanager.Secret(
                self,
                "GithubSecret",
                secret_name="bulwark-cloud/github-token",
                description="GitHub PAT for private repo clones (optional)",
            ),
        }

        # ── SNS events topic ───────────────────────────────────────────────
        self.events_topic = sns.Topic(
            self,
            "EventsTopic",
            topic_name="bulwark-cloud-events",
            display_name="Bulwark Cloud audit events",
        )

        # ── Outputs ────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        cdk.CfnOutput(self, "TableName", value=self.table.table_name)
        cdk.CfnOutput(self, "EventsTopicArn", value=self.events_topic.topic_arn)
        cdk.CfnOutput(
            self,
            "AnthropicSecretArn",
            value=self.secrets["anthropic"].secret_arn,
            description="Store your Anthropic API key here: aws secretsmanager put-secret-value ...",
        )
