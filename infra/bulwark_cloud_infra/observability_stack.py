"""CloudWatch dashboards and alarms for bulwark-cloud."""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from constructs import Construct

NAMESPACE = "BulwarkCloud"


class ObservabilityStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        events_topic: sns.Topic,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        sns_action = cw_actions.SnsAction(events_topic)

        # ── Alarms ─────────────────────────────────────────────────────────
        alarms = [
            cw.Alarm(
                self,
                "AuditDurationAlarm",
                alarm_name="bulwark-cloud-audit-duration-p99",
                alarm_description="Audit p99 duration exceeded 90 minutes",
                metric=cw.Metric(
                    namespace=NAMESPACE,
                    metric_name="audit.duration",
                    statistic="p99",
                    period=cdk.Duration.hours(1),
                ),
                threshold=5400,  # 90 minutes
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
            cw.Alarm(
                self,
                "AnthropicSpendAlarm",
                alarm_name="bulwark-cloud-anthropic-daily-spend",
                alarm_description="Anthropic token spend exceeds $50 today",
                metric=cw.Metric(
                    namespace=NAMESPACE,
                    metric_name="anthropic.cost_usd",
                    statistic="Sum",
                    period=cdk.Duration.hours(24),
                ),
                threshold=50,
                evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
            ),
        ]

        for alarm in alarms:
            alarm.add_alarm_action(sns_action)

        # ── Operational dashboard ──────────────────────────────────────────
        cw.Dashboard(
            self,
            "OperationalDashboard",
            dashboard_name="BulwarkCloud-Operational",
            widgets=[
                [
                    cw.GraphWidget(
                        title="Audits completed / failed (24h)",
                        left=[
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="audit.completed",
                                dimensions_map={"status": "COMPLETED"},
                                statistic="Sum",
                                period=cdk.Duration.hours(1),
                                color=cw.Color.GREEN,
                                label="Completed",
                            ),
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="audit.completed",
                                dimensions_map={"status": "FAILED"},
                                statistic="Sum",
                                period=cdk.Duration.hours(1),
                                color=cw.Color.RED,
                                label="Failed",
                            ),
                        ],
                        width=12,
                    ),
                    cw.GraphWidget(
                        title="Audit duration p50/p95 (7d)",
                        left=[
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="audit.duration",
                                statistic="p50",
                                period=cdk.Duration.hours(1),
                                label="p50",
                            ),
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="audit.duration",
                                statistic="p95",
                                period=cdk.Duration.hours(1),
                                label="p95",
                            ),
                        ],
                        width=12,
                    ),
                ],
                [
                    cw.GraphWidget(
                        title="Pass durations by pass number",
                        left=[
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="pass.duration",
                                dimensions_map={"pass_number": str(n)},
                                statistic="p50",
                                period=cdk.Duration.hours(1),
                                label=f"Pass {n} p50",
                            )
                            for n in range(1, 7)
                        ],
                        width=24,
                    ),
                ],
            ],
        )

        # ── Cost dashboard ─────────────────────────────────────────────────
        cw.Dashboard(
            self,
            "CostDashboard",
            dashboard_name="BulwarkCloud-Cost",
            widgets=[
                [
                    cw.GraphWidget(
                        title="Anthropic token spend (30d)",
                        left=[
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="anthropic.cost_usd",
                                statistic="Sum",
                                period=cdk.Duration.hours(24),
                                label="Daily cost USD",
                                color=cw.Color.ORANGE,
                            )
                        ],
                        width=24,
                    ),
                ],
                [
                    cw.GraphWidget(
                        title="Anthropic tokens (input vs output)",
                        left=[
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="anthropic.tokens",
                                dimensions_map={"direction": "input"},
                                statistic="Sum",
                                period=cdk.Duration.hours(1),
                                label="Input tokens",
                            ),
                            cw.Metric(
                                namespace=NAMESPACE,
                                metric_name="anthropic.tokens",
                                dimensions_map={"direction": "output"},
                                statistic="Sum",
                                period=cdk.Duration.hours(1),
                                label="Output tokens",
                            ),
                        ],
                        width=24,
                    ),
                ],
            ],
        )
