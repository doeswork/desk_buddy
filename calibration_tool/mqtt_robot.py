from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import ssl
import threading
import time
from typing import Any, Callable, Dict, Optional

try:
    from .config import WizardConfig
    from .photo_decode import DecodedPhoto, decode_photo_message, save_photo
except ImportError:  # pragma: no cover - direct script fallback
    from config import WizardConfig
    from photo_decode import DecodedPhoto, decode_photo_message, save_photo


Payload = Dict[str, Any]
EventCallback = Callable[[str, Any], None]
TOOL_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TOOL_DIR.parent


def action_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def base_payload(action: str, prefix: str, sender: str = "calibration_wizard") -> Payload:
    return {"sender": sender, "action_id": action_id(prefix), "action": action}


def calibrationvalues_payload(sender: str) -> Payload:
    return base_payload("calibrationvalues", "check_cal", sender)


def photo_payload(sender: str, label: str = "photo") -> Payload:
    return base_payload("photo", label, sender)


def visual_calibration_payload(sender: str, magnet_position: int = 1) -> Payload:
    payload = base_payload("calibrate_depth", "visual_calibration", sender)
    payload["MagnetPosition"] = int(magnet_position)
    return payload


def base_profile_payload(sender: str, neutral: Optional[int] = None) -> Payload:
    payload = base_payload("baseRotate", "base_profile", sender)
    payload["controlType"] = "CALIBRATE_PROFILE"
    if neutral is not None:
        payload["neutralServoAngle"] = int(neutral)
    return payload


def base_status_payload(sender: str) -> Payload:
    payload = base_payload("baseRotate", "base_status", sender)
    payload["controlType"] = "STATUS"
    return payload


def base_angle_payload(sender: str, angle: float, speed: str = "slow") -> Payload:
    payload = base_payload("baseRotate", "base_angle", sender)
    payload.update({"controlType": "ANGLE", "value": float(angle), "speed": speed})
    return payload


def base_degrees_payload(sender: str, direction: str, degrees: float, speed: str = "slow") -> Payload:
    payload = base_payload("baseRotate", "base_degrees", sender)
    payload.update({"controlType": "DEGREES", "direction": direction.upper(), "value": float(degrees), "speed": speed})
    return payload


def base_steps_payload(sender: str, direction: str, steps: int, speed: str = "slow") -> Payload:
    payload = base_payload("baseRotate", "base_steps", sender)
    payload.update({"controlType": "STEPS", "direction": direction.upper(), "steps": int(steps), "speed": speed})
    return payload


def servo_payload(sender: str, servo_name: str, position: int, live: bool = False) -> Payload:
    payload = base_payload("servo", "live" if live else "servo", sender)
    if live:
        payload["action_id"] = "live"
    payload.update({"servoName": servo_name.upper(), "position": int(position)})
    return payload


def gripper_payload(sender: str, command: str) -> Payload:
    payload = base_payload("gripper", "gripper", sender)
    payload["command"] = command.upper()
    return payload


def perch_payload(sender: str) -> Payload:
    return base_payload("perch", "perch", sender)


def save_perch_payload(sender: str, kind: str, value: float) -> Payload:
    calibration_type = f"perch_{kind}_angle" if kind in {"elbow", "wrist", "twist"} else f"perch_{kind}"
    payload = base_payload("calibrate", f"save_{calibration_type}", sender)
    payload.update({"calibration_type": calibration_type, "value": float(value)})
    return payload


def save_hover_payload(
    sender: str,
    plane: str,
    kind: str,
    distance: float,
    elbow: int,
    wrist: int,
    twist: int,
) -> Payload:
    if plane == "z0":
        calibration_type = f"hover_over_{kind}"
    elif plane == "z50":
        calibration_type = f"hover_{kind}_120"
    else:
        raise ValueError("plane must be z0 or z50")

    payload = base_payload("calibrate", f"save_{plane}_{kind}", sender)
    payload.update(
        {
            "calibration_type": calibration_type,
            "distance": float(distance),
            "ELBOW": int(elbow),
            "WRIST": int(wrist),
            "TWIST": int(twist),
        }
    )
    return payload


def ik_payload(sender: str, distance: float, z_height: float) -> Payload:
    payload = base_payload("controlik", "ik_test", sender)
    payload.update({"distance": float(distance), "z_height": float(z_height)})
    return payload


def stencil_payload(sender: str, command: str, rotation: Optional[float] = None, distance: Optional[float] = None) -> Payload:
    normalized = command.upper()
    payload = base_payload("stencilCalibrate", f"stencil_{normalized.lower()}", sender)
    payload["command"] = normalized
    if rotation is not None:
        payload["rotationNudgeDegrees"] = float(rotation)
    if distance is not None:
        payload["distanceNudgeMm"] = float(distance)
    return payload


def resolve_cert_path(path_text: str) -> Optional[Path]:
    if not path_text:
        return None

    raw = Path(path_text).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend([TOOL_DIR / raw, PROJECT_DIR / raw])

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return raw.resolve() if raw.is_absolute() else None


@dataclass
class VisualCalibrationCapture:
    photo_path: Path
    response: Dict[str, Any]


@dataclass
class RobotState:
    broker_connected: bool = False
    robot_online: bool = False
    last_error: str = ""
    last_ready: Dict[str, Any] = field(default_factory=dict)
    last_heartbeat: Dict[str, Any] = field(default_factory=dict)
    last_heartbeat_at: float = 0.0
    calibrationvalues: Dict[str, Any] = field(default_factory=dict)
    last_response: Dict[str, Any] = field(default_factory=dict)
    last_visual_calibration: Dict[str, Any] = field(default_factory=dict)
    last_photo_path: str = ""

    def heartbeat_age(self) -> Optional[float]:
        if not self.last_heartbeat_at:
            return None
        return time.time() - self.last_heartbeat_at


class MqttRobot:
    def __init__(self, on_event: Optional[EventCallback] = None):
        self.on_event = on_event
        self.config: Optional[WizardConfig] = None
        self.client: Any = None
        self.state = RobotState()
        self._lock = threading.Lock()
        self._pending: Dict[str, Dict[str, Any]] = {}

    def _emit(self, kind: str, payload: Any) -> None:
        if self.on_event:
            self.on_event(kind, payload)

    def _load_mqtt(self) -> Any:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("Install MQTT support first: python3 -m pip install paho-mqtt") from exc
        return mqtt

    def connect(self, config: WizardConfig) -> None:
        self.disconnect()
        self.config = config
        mqtt = self._load_mqtt()
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=config.client_id)
        except AttributeError:
            client = mqtt.Client(client_id=config.client_id)

        if config.admin_user or config.admin_password:
            client.username_pw_set(config.admin_user, config.admin_password)

        if config.tls:
            ca_path = resolve_cert_path(config.ca_cert_path)
            if config.ca_cert_path and not ca_path and not config.tls_insecure:
                checked = ", ".join(
                    str(p)
                    for p in (
                        Path(config.ca_cert_path).expanduser(),
                        TOOL_DIR / Path(config.ca_cert_path).expanduser(),
                        PROJECT_DIR / Path(config.ca_cert_path).expanduser(),
                    )
                )
                raise RuntimeError(
                    "MQTT CA cert was not found. "
                    f"Set the CA Cert Path to the broker CA file or enable TLS insecure mode. Checked: {checked}"
                )
            ca_certs = str(ca_path) if ca_path else None
            client.tls_set(
                ca_certs=ca_certs,
                cert_reqs=ssl.CERT_NONE if config.tls_insecure else ssl.CERT_REQUIRED,
            )
            client.tls_insecure_set(config.tls_insecure)

        connected = threading.Event()

        def on_connect(client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
            rc_value = int(getattr(rc, "value", rc))
            if rc_value == 0:
                self.state.broker_connected = True
                self.state.last_error = ""
                client.subscribe(config.command_topic)
                client.subscribe(config.heartbeat_topic)
                self._emit("status", "Connected to MQTT broker")
            else:
                self.state.broker_connected = False
                self.state.last_error = f"MQTT connect failed rc={rc_value}"
                self._emit("error", self.state.last_error)
            connected.set()
            self._emit("state", self.state)

        def on_disconnect(client: Any, userdata: Any, flags: Any = None, rc: Any = None, properties: Any = None) -> None:
            self.state.broker_connected = False
            self._emit("status", "Disconnected from MQTT broker")
            self._emit("state", self.state)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = self._on_message
        self.client = client

        try:
            client.connect(config.broker, config.port, keepalive=30)
        except (OSError, ValueError) as exc:
            self.state.last_error = f"MQTT connection failed: {exc}"
            self._emit("error", self.state.last_error)
            raise RuntimeError(self.state.last_error) from exc

        client.loop_start()
        if not connected.wait(timeout=10):
            raise TimeoutError("Timed out waiting for MQTT broker connection")
        if not self.state.broker_connected:
            raise RuntimeError(self.state.last_error or "MQTT broker connection failed")

    def disconnect(self) -> None:
        if self.client:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.state.broker_connected = False

    def publish(self, payload: Payload) -> None:
        if not self.client or not self.config or not self.state.broker_connected:
            raise RuntimeError("MQTT is not connected")
        encoded = json.dumps(payload, separators=(",", ":"))
        info = self.client.publish(self.config.command_topic, encoded)
        if getattr(info, "rc", 0) not in (0, None):
            raise RuntimeError(f"MQTT publish failed rc={info.rc}")
        self._emit("sent", payload)

    def request(self, payload: Payload, timeout: Optional[float] = None) -> Dict[str, Any]:
        target = str(payload.get("action_id", ""))
        if not target:
            raise ValueError("payload must include action_id")
        wait_timeout = timeout if timeout is not None else (self.config.timeout if self.config else 240.0)
        event = threading.Event()
        pending = {"event": event, "result": None, "want": "json"}
        with self._lock:
            self._pending[target] = pending
        try:
            self.publish(payload)
            if not event.wait(wait_timeout):
                raise TimeoutError(f"Timed out waiting for action_id={target}")
            result = pending["result"]
            if not isinstance(result, dict):
                raise RuntimeError(f"No JSON result for action_id={target}")
            return result
        finally:
            with self._lock:
                self._pending.pop(target, None)

    def capture_photo(self, label: str, output_dir: Path, timeout: Optional[float] = None) -> Path:
        if not self.config:
            raise RuntimeError("MQTT is not configured")
        payload = photo_payload(self.config.sender, label)
        target = str(payload["action_id"])
        wait_timeout = timeout if timeout is not None else self.config.timeout
        event = threading.Event()
        pending = {"event": event, "result": None, "want": "photo"}
        with self._lock:
            self._pending[target] = pending
        try:
            self.publish(payload)
            if not event.wait(wait_timeout):
                raise TimeoutError(f"Timed out waiting for photo action_id={target}")
            photo = pending["result"]
            if not isinstance(photo, DecodedPhoto):
                raise RuntimeError(f"No photo result for action_id={target}")
            path = save_photo(photo, output_dir, label)
            self.state.last_photo_path = str(path)
            self._emit("photo_saved", str(path))
            self._emit("state", self.state)
            return path
        finally:
            with self._lock:
                self._pending.pop(target, None)

    def capture_visual_calibration(
        self,
        output_dir: Path,
        magnet_position: int = 1,
        timeout: Optional[float] = None,
    ) -> VisualCalibrationCapture:
        """Capture a firmware photo and wait for the Visual AI calibration result."""
        if not self.config:
            raise RuntimeError("MQTT is not configured")

        payload = visual_calibration_payload(self.config.sender, magnet_position)
        target = str(payload["action_id"])
        wait_timeout = timeout if timeout is not None else self.config.timeout
        deadline = time.monotonic() + wait_timeout
        photo_event = threading.Event()
        result_event = threading.Event()
        pending = {
            "want": "visual_calibration",
            "photo": None,
            "response": None,
            "photo_event": photo_event,
            "result_event": result_event,
        }
        with self._lock:
            self._pending[target] = pending

        try:
            self.publish(payload)
            if not photo_event.wait(max(0.0, deadline - time.monotonic())):
                raise TimeoutError(
                    f"Timed out waiting for visual calibration photo action_id={target}"
                )

            photo = pending["photo"]
            if not isinstance(photo, DecodedPhoto):
                raise RuntimeError(f"No visual calibration photo for action_id={target}")

            path = save_photo(photo, output_dir, "visual_calibration")
            self.state.last_photo_path = str(path)
            self._emit("photo_saved", str(path))
            self._emit("visual_calibration_photo_saved", str(path))
            self._emit("state", self.state)

            if not result_event.wait(max(0.0, deadline - time.monotonic())):
                raise TimeoutError(
                    f"Timed out waiting for Visual AI calibration result action_id={target}"
                )

            response = pending["response"]
            if not isinstance(response, dict):
                raise RuntimeError(
                    f"No Visual AI calibration result for action_id={target}"
                )
            return VisualCalibrationCapture(photo_path=path, response=response)
        finally:
            with self._lock:
                self._pending.pop(target, None)

    def refresh_calibrationvalues(self) -> Dict[str, Any]:
        if not self.config:
            raise RuntimeError("MQTT is not configured")
        response = self.request(calibrationvalues_payload(self.config.sender))
        values = response.get("calibrationvalues")
        if isinstance(values, dict):
            self.state.calibrationvalues = values
            self._emit("state", self.state)
            return values
        return {}

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        raw = bytes(msg.payload)
        decoded_photo = decode_photo_message(raw)
        if decoded_photo:
            self._handle_photo(decoded_photo)
            return

        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return

        self._emit("message", data)
        if self._handle_visual_calibration_result(data):
            return
        if data.get("sender") != "firmware":
            return

        if data.get("log") == "heartbeat":
            self.state.last_heartbeat = data
            self.state.last_heartbeat_at = time.time()
            self.state.robot_online = True
            self._emit("state", self.state)
            return

        if data.get("status") == "ready":
            self.state.last_ready = data
            self.state.robot_online = True
            self._emit("state", self.state)
            return

        action = str(data.get("action_id", ""))
        if data.get("status") in ("completed", "failed"):
            self.state.last_response = data
            if data.get("status") == "failed":
                self.state.last_error = json.dumps(data, separators=(",", ":"))
            if isinstance(data.get("calibrationvalues"), dict):
                self.state.calibrationvalues = data["calibrationvalues"]
            self._emit("state", self.state)
            with self._lock:
                pending = self._pending.get(action)
            if pending and pending.get("want") == "json":
                pending["result"] = data
                pending["event"].set()

    def _handle_visual_calibration_result(self, data: Dict[str, Any]) -> bool:
        message_type = str(data.get("type") or data.get("action") or "").lower()
        status = str(data.get("status") or "").lower()
        if (
            data.get("sender") != "visual_ai"
            or message_type != "calibrate_depth"
            or status not in ("completed", "failed")
        ):
            return False

        self.state.last_visual_calibration = data
        self._emit("visual_calibration_result", data)
        self._emit("state", self.state)

        target = str(data.get("action_id", ""))
        with self._lock:
            pending = self._pending.get(target)
        if pending and pending.get("want") == "visual_calibration":
            pending["response"] = data
            pending["result_event"].set()
        return True

    def _handle_photo(self, photo: DecodedPhoto) -> None:
        self._emit("photo", photo)
        with self._lock:
            pending = self._pending.get(photo.action_id)
        if pending and pending.get("want") == "photo":
            pending["result"] = photo
            pending["event"].set()
        elif pending and pending.get("want") == "visual_calibration":
            pending["photo"] = photo
            pending["photo_event"].set()
