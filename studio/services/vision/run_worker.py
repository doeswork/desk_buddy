"""Shared process lifecycle for MQTT-only workers."""

from __future__ import annotations

import logging
import signal
import threading
from typing import Mapping

from .config import WorkerSettings
from .topics import VisionTopics
from .transport import PahoTransport
from .worker import JobHandler, MQTTWorker


def run_worker(settings: WorkerSettings, handlers: Mapping[str, JobHandler]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    transport = PahoTransport(
        host=settings.broker.host,
        port=settings.broker.port,
        client_id=settings.client_id,
        username=settings.broker.username,
        password=settings.broker.password,
        keepalive=settings.broker.keepalive,
        tls=settings.broker.tls,
        ca_file=settings.broker.ca_file,
    )
    worker = MQTTWorker(
        worker_id=settings.worker_id,
        worker_kind=settings.worker_kind,
        transport=transport,
        handlers=handlers,
        topics=VisionTopics(settings.broker.topic_root),
        device=settings.device,
        artifact_timeout_seconds=settings.artifact_timeout_seconds,
        max_frame_bytes=settings.max_frame_bytes,
    )
    for handler in {id(value): value for value in handlers.values()}.values():
        setter = getattr(handler, "set_status_callback", None)
        if callable(setter):
            setter(worker.publish_status)
    will_topic, will_payload = worker.last_will()
    transport.set_last_will(will_topic, will_payload)
    transport.set_handler(worker.handle_message)
    transport.add_connect_handler(worker.publish_status)
    transport.connect()
    worker.start()

    stopping = threading.Event()

    def stop(signum=None, frame=None) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping.wait(1.0):
            pass
    finally:
        worker.stop()
        transport.close()
    return 0
