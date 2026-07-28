from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import tkinter as tk
import unittest

try:
    from .app import CalibrationWizard, build_calibration_status_rows, explain_reach_and_grab_failure, fit_image_to_width, format_topic_log_event
    from .mqtt_robot import MqttRobot, ReachAndGrabResult, VisualCalibrationCapture, base_angle_payload, base_degrees_payload, base_profile_payload, base_steps_payload, calibrationvalues_payload, gripper_payload, ik_payload, photo_payload, reach_and_grab_payload, resolve_cert_path, save_hover_payload, save_perch_payload, servo_payload, stencil_payload, visual_calibration_payload
    from .photo_decode import DecodedPhoto, decode_photo_message
except ImportError:  # pragma: no cover - direct execution from calibration_tool/
    from app import CalibrationWizard, build_calibration_status_rows, explain_reach_and_grab_failure, fit_image_to_width, format_topic_log_event
    from mqtt_robot import MqttRobot, ReachAndGrabResult, VisualCalibrationCapture, base_angle_payload, base_degrees_payload, base_profile_payload, base_steps_payload, calibrationvalues_payload, gripper_payload, ik_payload, photo_payload, reach_and_grab_payload, resolve_cert_path, save_hover_payload, save_perch_payload, servo_payload, stencil_payload, visual_calibration_payload
    from photo_decode import DecodedPhoto, decode_photo_message


class PayloadTests(unittest.TestCase):
    def test_servo_payload(self) -> None:
        payload = servo_payload("tester", "elbow", 125)
        self.assertEqual(payload["action"], "servo")
        self.assertEqual(payload["servoName"], "ELBOW")
        self.assertEqual(payload["position"], 125)

    def test_gripper_payloads(self) -> None:
        for command in ("GRAB", "SOFTHOLD", "DROP"):
            payload = gripper_payload("tester", command.lower())
            self.assertEqual(payload["action"], "gripper")
            self.assertEqual(payload["sender"], "tester")
            self.assertEqual(payload["command"], command)

    def test_base_profile_payload(self) -> None:
        payload = base_profile_payload("tester", 90)
        self.assertEqual(payload["action"], "baseRotate")
        self.assertEqual(payload["controlType"], "CALIBRATE_PROFILE")
        self.assertEqual(payload["neutralServoAngle"], 90)

    def test_base_angle_payload(self) -> None:
        payload = base_angle_payload("tester", 30)
        self.assertEqual(payload["controlType"], "ANGLE")
        self.assertEqual(payload["value"], 30.0)

    def test_base_degrees_payload(self) -> None:
        payload = base_degrees_payload("tester", "left", 12.5, "regular")
        self.assertEqual(payload["action"], "baseRotate")
        self.assertEqual(payload["controlType"], "DEGREES")
        self.assertEqual(payload["direction"], "LEFT")
        self.assertEqual(payload["value"], 12.5)
        self.assertEqual(payload["speed"], "regular")

    def test_base_steps_payload(self) -> None:
        payload = base_steps_payload("tester", "right", 3, "slow")
        self.assertEqual(payload["action"], "baseRotate")
        self.assertEqual(payload["controlType"], "STEPS")
        self.assertEqual(payload["direction"], "RIGHT")
        self.assertEqual(payload["steps"], 3)
        self.assertEqual(payload["speed"], "slow")

    def test_calibrationvalues_payload(self) -> None:
        payload = calibrationvalues_payload("tester")
        self.assertEqual(payload["action"], "calibrationvalues")
        self.assertEqual(payload["sender"], "tester")

    def test_photo_payload(self) -> None:
        payload = photo_payload("tester", "perch")
        self.assertEqual(payload["action"], "photo")
        self.assertTrue(str(payload["action_id"]).startswith("perch_"))

    def test_visual_calibration_payload(self) -> None:
        payload = visual_calibration_payload("tester", 3)
        self.assertEqual(payload["action"], "calibrate_depth")
        self.assertEqual(payload["sender"], "tester")
        self.assertEqual(payload["MagnetPosition"], 3)
        self.assertTrue(str(payload["action_id"]).startswith("visual_calibration_"))

    def test_reach_and_grab_payload(self) -> None:
        payload = reach_and_grab_payload(
            "calibration_wizard",
            "  red cup  ",
            use_model=False,
            model_name="detector-v2",
            box_threshold=0.35,
            text_threshold=0.25,
            magnet_position=1,
            workflow_id=812,
            workflow_event_id=9914,
            request_action_id="detect-unique-001",
        )
        self.assertEqual(payload["sender"], "calibration_wizard")
        self.assertEqual(payload["action_id"], "detect-unique-001")
        self.assertEqual(payload["action"], "detect_object")
        self.assertEqual(payload["phrase"], "red cup")
        self.assertIs(payload["use_model"], False)
        self.assertEqual(payload["model_name"], "detector-v2")
        self.assertEqual(payload["box_threshold"], 0.35)
        self.assertEqual(payload["text_threshold"], 0.25)
        self.assertEqual(payload["MagnetPosition"], 1)
        self.assertEqual(payload["workflow_id"], 812)
        self.assertEqual(payload["workflow_event_id"], 9914)

    def test_reach_and_grab_payload_rejects_unsafe_contract_values(self) -> None:
        with self.assertRaises(ValueError):
            reach_and_grab_payload("firmware", "red cup")
        with self.assertRaises(ValueError):
            reach_and_grab_payload("calibration_wizard", "  ")
        with self.assertRaises(ValueError):
            reach_and_grab_payload("calibration_wizard", "red cup", use_model="train")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            reach_and_grab_payload(
                "calibration_wizard",
                "red cup",
                workflow_event_id=12,
            )

    def test_resolve_root_cert_from_relative_path(self) -> None:
        resolved = resolve_cert_path("mqtt-ca.crt")
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertTrue(str(resolved).endswith("mqtt-ca.crt"))

    def test_save_perch_payload(self) -> None:
        payload = save_perch_payload("tester", "elbow", 125)
        self.assertEqual(payload["action"], "calibrate")
        self.assertEqual(payload["calibration_type"], "perch_elbow_angle")
        self.assertEqual(payload["value"], 125.0)

    def test_save_hover_z0_payload(self) -> None:
        payload = save_hover_payload("tester", "z0", "mid", 60, 132, 46, 90)
        self.assertEqual(payload["calibration_type"], "hover_over_mid")
        self.assertEqual(payload["ELBOW"], 132)
        self.assertEqual(payload["WRIST"], 46)
        self.assertEqual(payload["TWIST"], 90)

    def test_save_hover_z50_payload(self) -> None:
        payload = save_hover_payload("tester", "z50", "max", 120, 165, 160, 90)
        self.assertEqual(payload["calibration_type"], "hover_max_120")

    def test_ik_z25_payload(self) -> None:
        payload = ik_payload("tester", 60, 25)
        self.assertEqual(payload["action"], "controlik")
        self.assertEqual(payload["distance"], 60.0)
        self.assertEqual(payload["z_height"], 25.0)

    def test_stencil_payload_commands(self) -> None:
        for command in ("START", "RUN_POINT", "STATUS", "CANCEL", "CLEAR", "ADJUST_PREVIOUS"):
            payload = stencil_payload("tester", command.lower())
            self.assertEqual(payload["action"], "stencilCalibrate")
            self.assertEqual(payload["sender"], "tester")
            self.assertEqual(payload["command"], command)
            self.assertTrue(str(payload["action_id"]).startswith(f"stencil_{command.lower()}_"))

    def test_stencil_adjust_payload(self) -> None:
        payload = stencil_payload("tester", "ADJUST", rotation=2.5, distance=-3)
        self.assertEqual(payload["action"], "stencilCalibrate")
        self.assertEqual(payload["command"], "ADJUST")
        self.assertEqual(payload["rotationNudgeDegrees"], 2.5)
        self.assertEqual(payload["distanceNudgeMm"], -3.0)


class CalibrationStatusTests(unittest.TestCase):
    def test_status_rows_distinguish_saved_defaults_and_missing(self) -> None:
        rows = build_calibration_status_rows(
            {
                "base_rotation_profileCalibrated": True,
                "base_rotation_veryslowValidated": True,
                "base_rotation_calibrated": True,
                "base_rotation_leftCountsPerRev": 24000,
                "base_rotation_rightCountsPerRev": 24100,
                "base_rotation_lastValid": True,
                "PERCH_ELBOW_ANGLE": None,
                "PERCH_WRIST_ANGLE": 94.0,
                "perch_effective": {"ELBOW": 120, "WRIST": 94, "TWIST": 90, "MIN": 0, "MID": 50, "MAX": 100},
                "hover_over_min": {"DISTANCE": 0, "ELBOW": 126, "WRIST": 0, "TWIST": 90},
                "rot_off_deg": 0.0,
            }
        )
        by_key = {row["key"]: row for row in rows}
        self.assertEqual(by_key["PERCH_ELBOW_ANGLE"]["state"], "DEFAULT")
        self.assertEqual(by_key["PERCH_ELBOW_ANGLE"]["value"], "120")
        self.assertEqual(by_key["PERCH_WRIST_ANGLE"]["state"], "SAVED")
        self.assertEqual(by_key["hover_over_min"]["state"], "SAVED")
        self.assertEqual(by_key["hover_over_mid"]["state"], "MISSING")
        self.assertEqual(by_key["hover_min_120"]["state"], "OPTIONAL")
        self.assertEqual(by_key["rot_off_deg"]["state"], "SAVED")
        self.assertEqual(by_key["base_rotation_veryslowValidated"]["state"], "SAVED")


class PhotoDecodeTests(unittest.TestCase):
    def test_raw_jpeg_photo_decode(self) -> None:
        jpeg = b"\xff\xd8jpeg bytes with } inside\xff\xd9"
        raw = b'{"sender":"firmware","action_id":"photo_1","photo":"sending_photo","payload":' + jpeg + b"}"
        decoded = decode_photo_message(raw)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded.action_id, "photo_1")
        self.assertEqual(decoded.jpeg_bytes, jpeg)

    def test_non_photo_returns_none(self) -> None:
        self.assertIsNone(decode_photo_message(b'{"sender":"firmware"}'))


class VisualCalibrationMqttTests(unittest.TestCase):
    def test_capture_waits_for_photo_and_visual_ai_result(self) -> None:
        robot = MqttRobot()
        robot.config = SimpleNamespace(sender="tester", timeout=1.0)

        def fake_publish(payload: dict) -> None:
            target = str(payload["action_id"])
            robot._handle_photo(
                DecodedPhoto(
                    metadata={
                        "sender": "firmware",
                        "action_id": target,
                        "photo": "sending_photo",
                    },
                    jpeg_bytes=b"\xff\xd8visual calibration\xff\xd9",
                )
            )
            robot._handle_visual_calibration_result(
                {
                    "sender": "visual_ai",
                    "type": "calibrate_depth",
                    "action_id": target,
                    "status": "completed",
                    "image_id": 42,
                    "MagnetPosition": 3,
                    "calibration_points": {"origin": {"x": 10.0, "y": 20.0}},
                }
            )

        robot.publish = fake_publish  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as directory:
            capture = robot.capture_visual_calibration(
                Path(directory),
                magnet_position=3,
            )

            self.assertTrue(capture.photo_path.exists())
            self.assertEqual(capture.response["status"], "completed")
            self.assertEqual(capture.response["image_id"], 42)
            self.assertEqual(robot.state.last_visual_calibration["image_id"], 42)
            self.assertEqual(robot._pending, {})

    def test_visual_ai_failed_result_is_correlated(self) -> None:
        robot = MqttRobot()
        target = "visual_calibration_1"
        result_event = threading.Event()
        robot._pending[target] = {
            "want": "visual_calibration",
            "response": None,
            "result_event": result_event,
        }
        response = {
            "sender": "visual_ai",
            "type": "calibrate_depth",
            "action_id": target,
            "status": "failed",
            "error": "not enough calibration points",
        }

        robot._on_message(
            None,
            None,
            SimpleNamespace(payload=json.dumps(response).encode("utf-8")),
        )
        self.assertTrue(result_event.is_set())
        self.assertEqual(robot._pending[target]["response"], response)


class ReachAndGrabMqttTests(unittest.TestCase):
    def test_waits_for_matching_visual_ai_terminal_and_saves_photo(self) -> None:
        events: list[tuple[str, object]] = []
        robot = MqttRobot(lambda kind, payload: events.append((kind, payload)))
        robot.config = SimpleNamespace(timeout=1.0)
        payload = reach_and_grab_payload(
            "calibration_wizard",
            "red cup",
            request_action_id="detect-unique-001",
        )

        def fake_publish(outgoing: dict) -> None:
            target = str(outgoing["action_id"])
            robot._handle_reach_and_grab_message(
                {
                    "sender": "firmware",
                    "action_id": target,
                    "status": "in_progress",
                    "type": "detect_object",
                }
            )
            robot._handle_photo(
                DecodedPhoto(
                    metadata={
                        "sender": "firmware",
                        "action_id": target,
                        "photo": "sending_photo",
                    },
                    jpeg_bytes=b"\xff\xd8reach and grab\xff\xd9",
                )
            )
            robot._handle_reach_and_grab_message(
                {
                    "sender": "visual_ai",
                    "action_id": target,
                    "status": "in_progress",
                    "type": "detect_object",
                    "stage": "executing_reach_and_grab",
                    "motion_step_count": 3,
                }
            )
            robot._handle_reach_and_grab_message(
                {
                    "sender": "visual_ai",
                    "action_id": target,
                    "status": "completed",
                    "type": "detect_object",
                    "stage": "reach_and_grab_completed",
                    "grab_status": "completed",
                    "telemetry_status": "completed",
                }
            )

        robot.publish = fake_publish  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as directory:
            result = robot.reach_and_grab(payload, Path(directory))

            self.assertEqual(result.response["sender"], "visual_ai")
            self.assertEqual(result.response["stage"], "reach_and_grab_completed")
            self.assertEqual(len(result.progress), 3)
            self.assertIsNotNone(result.photo_path)
            assert result.photo_path is not None
            self.assertTrue(result.photo_path.exists())
            self.assertEqual(robot.state.active_reach_and_grab_action_id, "")
            self.assertEqual(robot._pending, {})
            self.assertTrue(any(kind == "reach_and_grab_photo_saved" for kind, _ in events))

    def test_firmware_completed_does_not_finish_overall_operation(self) -> None:
        robot = MqttRobot()
        target = "detect-unique-002"
        result_event = threading.Event()
        robot._pending[target] = {
            "want": "reach_and_grab",
            "response": None,
            "result_event": result_event,
            "progress": [],
        }
        robot._reach_and_grab_action_ids.append(target)

        handled = robot._handle_reach_and_grab_message(
            {
                "sender": "firmware",
                "action_id": target,
                "status": "completed",
                "type": "detect_object",
            }
        )

        self.assertFalse(handled)
        self.assertFalse(result_event.is_set())
        self.assertIsNone(robot._pending[target]["response"])

    def test_unrelated_visual_ai_result_is_ignored(self) -> None:
        robot = MqttRobot()
        handled = robot._handle_reach_and_grab_message(
            {
                "sender": "visual_ai",
                "action_id": "someone-elses-request",
                "status": "completed",
                "type": "detect_object",
                "stage": "reach_and_grab_completed",
                "grab_status": "completed",
            }
        )
        self.assertFalse(handled)

    def test_detection_only_is_a_terminal_visual_ai_result(self) -> None:
        robot = MqttRobot()
        target = "detect-unique-003"
        result_event = threading.Event()
        robot._pending[target] = {
            "want": "reach_and_grab",
            "response": None,
            "result_event": result_event,
            "progress": [],
        }
        robot._reach_and_grab_action_ids.append(target)
        response = {
            "sender": "visual_ai",
            "action_id": target,
            "status": "completed",
            "type": "detect_object",
            "stage": "detection_only",
            "warning": "auto_reach_grab_disabled",
        }

        self.assertTrue(robot._handle_reach_and_grab_message(response))
        self.assertTrue(result_event.is_set())
        self.assertEqual(robot._pending[target]["response"], response)

    def test_timeout_does_not_resend_and_late_terminal_is_still_observed(self) -> None:
        events: list[tuple[str, object]] = []
        publish_count = 0
        robot = MqttRobot(lambda kind, payload: events.append((kind, payload)))
        robot.config = SimpleNamespace(timeout=0.001)
        payload = reach_and_grab_payload(
            "calibration_wizard",
            "red cup",
            request_action_id="detect-late-001",
        )

        def fake_publish(outgoing: dict) -> None:
            nonlocal publish_count
            publish_count += 1

        robot.publish = fake_publish  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(TimeoutError, "Do not automatically resend"):
                robot.reach_and_grab(payload, Path(directory))

        self.assertEqual(publish_count, 1)
        self.assertEqual(robot._pending, {})
        late_response = {
            "sender": "visual_ai",
            "action_id": "detect-late-001",
            "status": "failed",
            "type": "detect_object",
            "stage": "reach_and_grab_failed",
            "error": "robot_command_timeout",
        }
        self.assertTrue(robot._handle_reach_and_grab_message(late_response))
        self.assertEqual(robot.state.last_reach_and_grab, late_response)
        self.assertTrue(
            any(
                kind == "reach_and_grab_progress" and event_payload == late_response
                for kind, event_payload in events
            )
        )


class TopicLogFormatTests(unittest.TestCase):
    def test_heartbeat_message_is_ignored(self) -> None:
        self.assertIsNone(format_topic_log_event("message", {"sender": "firmware", "log": "heartbeat"}))

    def test_outgoing_command_is_logged(self) -> None:
        line = format_topic_log_event("sent", {"action": "servo", "action_id": "servo_1", "position": 120})
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("OUT servo servo_1", line)

    def test_completed_response_is_logged(self) -> None:
        line = format_topic_log_event("message", {"sender": "firmware", "status": "completed", "action_id": "servo_1"})
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("IN completed servo_1", line)

    def test_photo_event_is_summarized(self) -> None:
        class FakePhoto:
            action_id = "photo_1"

        line = format_topic_log_event("photo", FakePhoto())
        self.assertEqual(line, "PHOTO photo_1 received raw JPEG")

    def test_failed_reach_worker_reports_terminal_result_not_success(self) -> None:
        line = format_topic_log_event("worker_success", ("reach and grab", object(), None))
        self.assertEqual(line, "APP reach and grab terminal result received")


class ReachAndGrabFailureExplanationTests(unittest.TestCase):
    def test_robot_command_timeout_identifies_firmware_response_deadline(self) -> None:
        summary, explanation = explain_reach_and_grab_failure(
            {
                "error": "robot_command_timeout",
                "failed_step": 1,
                "failed_action": "baseRotate",
            }
        )

        self.assertEqual(summary, "Failed — firmware response timeout during step 1 (baseRotate).")
        rendered = "\n".join(explanation)
        self.assertIn("per-command firmware response timeout", rendered)
        self.assertIn("exact matching firmware status=completed", rendered)
        self.assertIn("not a camera, detection, or GUI timeout", rendered)
        self.assertIn("do not automatically retry", rendered)

    def test_other_failure_preserves_server_error(self) -> None:
        summary, explanation = explain_reach_and_grab_failure({"error": "robot_busy"})
        self.assertEqual(summary, "Failed — robot_busy")
        self.assertEqual(explanation, [])


class ImageSizingTests(unittest.TestCase):
    def test_fit_image_to_width_preserves_landscape_aspect_ratio(self) -> None:
        self.assertEqual(fit_image_to_width(800, 600, 320), (320, 240))

    def test_fit_image_to_width_preserves_portrait_aspect_ratio(self) -> None:
        self.assertEqual(fit_image_to_width(600, 800, 300), (300, 400))

    def test_fit_image_to_width_does_not_enlarge_small_images(self) -> None:
        self.assertEqual(fit_image_to_width(200, 100, 400), (200, 100))

    def test_fit_image_to_width_rejects_invalid_dimensions(self) -> None:
        with self.assertRaises(ValueError):
            fit_image_to_width(0, 600, 320)


class GuiStateTests(unittest.TestCase):
    def test_heartbeat_does_not_reset_controller_until_sync(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            app.controller_angles["ELBOW"].set(11)
            app.controller_angles["WRIST"].set(22)
            app.controller_angles["TWIST"].set(33)
            app.robot.state.last_heartbeat = {
                "ELBOW_ANGLE": 101,
                "WRIST_ANGLE": 102,
                "TWIST_ANGLE": 103,
                "GRIPPER_ANGLE": 44,
            }
            app.robot.state.calibrationvalues = {
                "ELBOW_ANGLE": 151,
                "WRIST_ANGLE": 152,
                "TWIST_ANGLE": 153,
            }

            app._render_state()
            self.assertEqual(app.controller_angles["ELBOW"].get(), 11)
            self.assertEqual(app.controller_angles["WRIST"].get(), 22)
            self.assertEqual(app.controller_angles["TWIST"].get(), 33)
            self.assertEqual(app.observed_angles["ELBOW"].get(), "101 °")

            app.sync_controller_from_robot()
            self.assertEqual(app.controller_angles["ELBOW"].get(), 101)
            self.assertEqual(app.controller_angles["WRIST"].get(), 102)
            self.assertEqual(app.controller_angles["TWIST"].get(), 103)
        finally:
            app.destroy()

    def test_ik_rows_sync_from_firmware_calibrationvalues_once(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            app.robot.state.calibrationvalues = {
                "hover_over_min": {"DISTANCE": 0, "ELBOW": 126, "WRIST": 0, "TWIST": 90},
                "hover_over_mid": {"DISTANCE": 60, "ELBOW": 132, "WRIST": 46, "TWIST": 90},
                "hover_over_max": {"DISTANCE": 120, "ELBOW": 165, "WRIST": 160, "TWIST": 90},
                "hover_min_120": {"DISTANCE": 30, "ELBOW": 120, "WRIST": 20, "TWIST": 90},
                "hover_mid_120": {"DISTANCE": 75, "ELBOW": 140, "WRIST": 80, "TWIST": 90},
                "hover_max_120": {"DISTANCE": 120, "ELBOW": 170, "WRIST": 150, "TWIST": 90},
            }

            app._render_state()
            self.assertEqual(app.ik_rows["z0"]["min"]["distance"].get(), 0.0)
            self.assertEqual(app.ik_rows["z0"]["min"]["elbow"].get(), 126)
            self.assertEqual(app.ik_rows["z0"]["min"]["result"].get(), "Saved in firmware")
            self.assertEqual(app.ik_rows["z50"]["min"]["distance"].get(), 30.0)
            self.assertEqual(app.ik_rows["z50"]["mid"]["wrist"].get(), 80)

            app.ik_rows["z0"]["min"]["elbow"].set(1)
            app._render_state()
            self.assertEqual(app.ik_rows["z0"]["min"]["elbow"].get(), 1)
        finally:
            app.destroy()

    def test_simplified_tabs_and_persistent_controller_exist(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            tab_texts = [app.notebook.tab(tab_id, "text") for tab_id in app.notebook.tabs()]
            self.assertEqual(
                tab_texts,
                [
                    "Setup",
                    "Status",
                    "Base + Perch",
                    "IK",
                    "Visual Calibration",
                    "Reach and Grab",
                    "Stencil",
                ],
            )
            self.assertTrue(hasattr(app, "status_tree"))
            self.assertTrue(hasattr(app, "controller_shell"))
            self.assertNotEqual(app.controller_shell.master, app.notebook)
            self.assertTrue(hasattr(app, "base_rotation_value_entry"))
            self.assertTrue(hasattr(app, "controller_photo_label"))
            self.assertEqual(app.controller_capture_button.cget("text"), "Capture Photo")
            self.assertTrue(hasattr(app, "visual_calibration_result_box"))
            self.assertTrue(hasattr(app, "reach_and_grab_result_box"))
            self.assertTrue(hasattr(app, "reach_and_grab_button"))
            self.assertTrue(hasattr(app, "stencil_status_box"))
            self.assertTrue(hasattr(app, "stencil_points_box"))
        finally:
            app.destroy()

    def test_reach_and_grab_terminal_rendering_distinguishes_physical_success(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            app.reach_and_grab_current_action_id = "detect-unique-001"
            app.reach_and_grab_request = {"phrase": "red cup"}
            response = {
                "sender": "visual_ai",
                "action_id": "detect-unique-001",
                "status": "completed",
                "type": "detect_object",
                "stage": "reach_and_grab_completed",
                "phrase": "red cup",
                "image_id": 44,
                "raw_x": 62,
                "raw_y": 41,
                "motion_steps_completed": 3,
                "grab_status": "completed",
                "telemetry_status": "completed",
            }
            app.reach_and_grab_progress = [response]

            app._render_reach_and_grab_terminal(response)

            self.assertIn("confirmed the object was grabbed", app.reach_and_grab_status_text.get())
            rendered = app.reach_and_grab_result_box.get("1.0", tk.END)
            self.assertIn("Vision image ID: 44", rendered)
            self.assertIn("x=62% left-to-right", rendered)
            self.assertEqual(app.session["reach_and_grab"]["response"], response)
        finally:
            app.destroy()

    def test_visual_calibration_result_rendering(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            app._render_visual_calibration_result(
                VisualCalibrationCapture(
                    photo_path=Path("/tmp/visual_calibration.jpg"),
                    response={
                        "sender": "visual_ai",
                        "type": "calibrate_depth",
                        "action_id": "visual_calibration_1",
                        "status": "completed",
                        "image_id": 42,
                        "MagnetPosition": 1,
                        "calibration_points": {
                            "origin": {"x": 10.0, "y": 20.0},
                            "far": {"x": 300.0, "y": 220.0},
                        },
                    },
                )
            )
            self.assertIn("2 calibration points", app.visual_calibration_status_text.get())
            rendered = app.visual_calibration_result_box.get("1.0", tk.END)
            self.assertIn("Vision image ID: 42", rendered)
            self.assertIn("origin:", rendered)
            self.assertIn("far:", rendered)
        finally:
            app.destroy()

    def test_window_resize_zoom_and_controller_scroll(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            self.assertEqual(app.resizable(), (1, 1))
            self.assertEqual(app.minsize(), (900, 650))
            app.geometry("900x650")
            app.update_idletasks()
            app.update()
            self.assertTrue(app.controller_canvas.cget("scrollregion"))

            app._set_zoom(0.75)
            self.assertEqual(app.zoom_text.get(), "75%")
            self.assertEqual(app.ui_fonts["body"].cget("size"), 8)
            self.assertEqual(int(app.controller_shell.cget("width")), 300)

            app._set_zoom(1.25)
            self.assertEqual(app.zoom_text.get(), "125%")
            self.assertEqual(app.ui_fonts["body"].cget("size"), 12)
            self.assertEqual(int(app.controller_shell.cget("width")), 469)

            app._reset_zoom()
            self.assertEqual(app.zoom_text.get(), "100%")
        finally:
            app.destroy()

    def test_stencil_status_rendering(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            app._render_stencil_response(
                {
                    "stencil_calibration": {
                        "phase": "place_peg",
                        "active": True,
                        "pointIndex": 0,
                        "totalPointCount": 15,
                        "offsetPointCount": 9,
                        "validationPointCount": 6,
                        "homeDirection": "RIGHT",
                        "baseMoveSpeed": "veryslow",
                        "baseMoveSkipped": True,
                        "lastBaseTargetAngleDegrees": -30,
                        "pointId": "min_left",
                        "targetAngleDegrees": -30,
                        "targetDistanceMm": 0,
                        "targetZHeightMm": 0,
                        "offsetContributor": True,
                        "attempts": 0,
                        "grabbed": False,
                        "message": "Place peg at min_left",
                        "points": [
                            {
                                "id": "min_left",
                                "angleDegrees": -30,
                                "distanceMm": 0,
                                "zHeightMm": 0,
                                "offsetContributor": True,
                                "completed": False,
                                "grabbed": False,
                                "rotationNudgeDegrees": 0,
                                "distanceNudgeMm": 0,
                                "attempts": 0,
                            }
                        ],
                    }
                }
            )
            self.assertIn("place_peg", app.stencil_status_box.get("1.0", tk.END))
            self.assertIn("Total points: 15", app.stencil_status_box.get("1.0", tk.END))
            self.assertIn("Base move speed: veryslow", app.stencil_status_box.get("1.0", tk.END))
            self.assertIn("Base move skipped: yes", app.stencil_status_box.get("1.0", tk.END))
            self.assertIn("Offset contributor: yes", app.stencil_status_box.get("1.0", tk.END))
            self.assertIn("min_left", app.stencil_points_box.get("1.0", tk.END))
            self.assertIn("offset", app.stencil_points_box.get("1.0", tk.END))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
