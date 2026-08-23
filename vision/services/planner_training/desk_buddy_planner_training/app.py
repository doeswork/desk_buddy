from __future__ import annotations

import argparse
import logging
import signal
import threading

from desk_buddy_vision_protocol import MQTTWorker, TopicLayout, load_service_settings
from desk_buddy_vision_protocol.paho_transport import PahoServiceTransport

from .handler import ResidualPlannerHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Desk Buddy residual planner/training MQTT worker")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_service_settings(args.config)
    handlers = {
        model_id: ResidualPlannerHandler(
            model_id=model_id,
            configured_version=str(values.get("version", "unloaded")),
        )
        for model_id, values in settings.models.items()
    }
    broker = settings.broker
    transport = PahoServiceTransport(
        host=broker.host,
        port=broker.port,
        client_id=settings.client_id,
        keepalive=broker.keepalive,
        username=broker.username,
        password=broker.password,
        tls=broker.tls,
        ca_file=broker.ca_file,
        cert_file=broker.cert_file,
        key_file=broker.key_file,
    )
    worker = MQTTWorker(
        service_id=settings.service_id,
        service_kind=settings.service_kind,
        transport=transport,
        handlers=handlers,
        topics=TopicLayout(broker.topic_root),
        compute_device=settings.compute_device,
        artifact_timeout_seconds=settings.artifact_timeout_seconds,
        chunk_size=settings.artifact_chunk_size,
    )
    transport.set_message_handler(worker.handle_message)
    transport.add_connect_handler(worker.publish_status)
    will_topic, will_payload = worker.last_will()
    transport.set_last_will(will_topic, will_payload)
    transport.connect()
    worker.start()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    stopped.wait()
    worker.stop()
    transport.close()


if __name__ == "__main__":
    main()
