import json
import os
import time
from typing import Any


METRIC_NAMESPACE = "Wilvor/Pipeline"


def now_ms() -> int:
    return int(time.time() * 1000)


def get_environment() -> str:
    return os.environ.get("ENVIRONMENT", "dev")


def emit_metric(
    *,
    pipeline: str,
    component: str,
    stage: str,
    metrics: dict[str, int | float],
    properties: dict[str, Any] | None = None,
) -> None:
    metric_definitions = [
        {
            "Name": metric_name,
            "Unit": "Count",
        }
        for metric_name in metrics.keys()
    ]

    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": now_ms(),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRIC_NAMESPACE,
                    "Dimensions": [
                        [
                            "Environment",
                            "Pipeline",
                            "Component",
                            "Stage",
                        ]
                    ],
                    "Metrics": metric_definitions,
                }
            ],
        },
        "Environment": get_environment(),
        "Pipeline": pipeline,
        "Component": component,
        "Stage": stage,
        **metrics,
    }

    if properties:
        payload.update(properties)

    print(json.dumps(payload))