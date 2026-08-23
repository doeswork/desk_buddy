from __future__ import annotations

import argparse
import logging
import signal
import threading

from desk_buddy_vision_protocol import TopicLayout
from desk_buddy_vision_protocol.envelopes import VISION_SCHEMA, utc_now

from .config import load_config
from .coordinator import VisionCoordinator
from .mqtt import PahoTransport


def main() -> None:
    parser = argparse.ArgumentParser(description="Desk Buddy Vision MQTT coordinator")
    parser.add_argument("--config", required=True, help="Path to coordinator TOML configuration")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    transport = PahoTransport(config.mqtt)
    coordinator = VisionCoordinator(config=config, transport=transport)
    transport.set_message_handler(coordinator.handle_message)
    transport.add_connect_handler(lambda: coordinator.publish_status(online=True))
    transport.set_last_will(
        TopicLayout(config.mqtt.topic_root).coordinator_status(),
        {
            "schema": VISION_SCHEMA,
            "sender": "vision_coordinator",
            "service_id": config.coordinator.service_id,
            "status": "offline",
            "updated_at": utc_now(),
        },
    )
    transport.connect()
    coordinator.start()
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    stopped.wait()
    coordinator.stop()
    transport.close()


if __name__ == "__main__":
    main()
