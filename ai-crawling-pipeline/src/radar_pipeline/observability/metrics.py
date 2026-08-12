"""In-memory counters and summary reporting."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PipelineRun:
    stage: str
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    success: bool = True
    details: dict = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        end = self.ended_at or time.time()
        return end - self.started_at

    def finish(self, success: bool = True, **details) -> None:
        self.ended_at = time.time()
        self.success = success
        self.details.update(details)


class MetricsCollector:
    def __init__(self) -> None:
        self.runs: list[PipelineRun] = []

    def start(self, stage: str) -> PipelineRun:
        run = PipelineRun(stage=stage)
        self.runs.append(run)
        return run

    def summary(self) -> str:
        lines = ["\n=== Pipeline Summary ==="]
        for r in self.runs:
            status = "OK" if r.success else "FAIL"
            dur = f"{r.duration_seconds:.1f}s"
            lines.append(f"  [{status}] {r.stage} ({dur})")
            for k, v in r.details.items():
                lines.append(f"         {k}: {v}")
        return "\n".join(lines)
