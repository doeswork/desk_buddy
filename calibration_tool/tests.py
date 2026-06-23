from __future__ import annotations

import os
import tkinter as tk
import unittest

try:
    from .app import CalibrationWizard, format_topic_log_event
    from .mqtt_robot import base_angle_payload, base_degrees_payload, base_profile_payload, base_steps_payload, calibrationvalues_payload, gripper_payload, ik_payload, photo_payload, resolve_cert_path, save_hover_payload, save_perch_payload, servo_payload, stencil_payload
    from .photo_decode import decode_photo_message
except ImportError:  # pragma: no cover - direct execution from calibration_tool/
    from app import CalibrationWizard, format_topic_log_event
    from mqtt_robot import base_angle_payload, base_degrees_payload, base_profile_payload, base_steps_payload, calibrationvalues_payload, gripper_payload, ik_payload, photo_payload, resolve_cert_path, save_hover_payload, save_perch_payload, servo_payload, stencil_payload
    from photo_decode import decode_photo_message


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
            self.assertEqual(app.observed_angles["ELBOW"].get(), "101")

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

    def test_stencil_tab_exists(self) -> None:
        if not os.environ.get("DISPLAY"):
            self.skipTest("Tk display is not available")
        try:
            app = CalibrationWizard()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is not available: {exc}")

        try:
            tab_texts = [app.notebook.tab(tab_id, "text") for tab_id in app.notebook.tabs()]
            self.assertIn("5. Stencil", tab_texts)
            self.assertIn("6. Final Check", tab_texts)
            self.assertTrue(hasattr(app, "stencil_status_box"))
            self.assertTrue(hasattr(app, "stencil_points_box"))
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
