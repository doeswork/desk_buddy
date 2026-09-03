"""MQTT topic layout and explicit model-to-worker routing."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ContractError, WorkerStatus


@dataclass(frozen=True)
class VisionTopics:
    root: str = "desk_buddy"

    def _base(self, worker_id: str) -> str:
        return f"{self.root.strip().strip('/')}/vision/worker/{worker_id}"

    def request(self, worker_id: str) -> str:
        return f"{self._base(worker_id)}/request"

    def artifact_in(self, worker_id: str) -> str:
        return f"{self._base(worker_id)}/artifact/in"

    def event(self, worker_id: str) -> str:
        return f"{self._base(worker_id)}/event"

    def artifact_out(self, worker_id: str) -> str:
        return f"{self._base(worker_id)}/artifact/out"

    def status(self, worker_id: str) -> str:
        return f"{self._base(worker_id)}/status"

    def all_status(self) -> str:
        return f"{self.root.strip().strip('/')}/vision/worker/+/status"

    def all_events(self) -> str:
        return f"{self.root.strip().strip('/')}/vision/worker/+/event"

    def all_artifacts_out(self) -> str:
        return f"{self.root.strip().strip('/')}/vision/worker/+/artifact/out"


@dataclass(frozen=True)
class ModelRoute:
    model_id: str
    worker_id: str
    kind: str


class WorkerRegistry:
    """Configured routes plus retained capability state; never load-balances."""

    def __init__(self, routes: tuple[ModelRoute, ...] = ()) -> None:
        self._routes = {(route.model_id, route.kind): route for route in routes}
        self._statuses: dict[str, WorkerStatus] = {}

    def set_route(self, route: ModelRoute) -> None:
        self._routes[(route.model_id, route.kind)] = route

    def update(self, status: WorkerStatus) -> None:
        self._statuses[status.worker_id] = status

    def status(self, worker_id: str) -> WorkerStatus | None:
        return self._statuses.get(worker_id)

    def statuses(self) -> tuple[WorkerStatus, ...]:
        return tuple(sorted(self._statuses.values(), key=lambda item: item.worker_id))

    def route(self, model_id: str, kind: str) -> ModelRoute:
        route = self._routes.get((model_id, kind))
        if route is None:
            raise ContractError("model_not_routed", f"no worker is configured for {model_id}")
        status = self._statuses.get(route.worker_id)
        if status is None or not status.supports(model_id, kind):
            raise ContractError("worker_unavailable", f"worker {route.worker_id} is not ready for {model_id}")
        return route
