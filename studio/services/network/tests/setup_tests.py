"""Setup tests use temporary files and mocked OS operations; never elevate.

    QT_QPA_PLATFORM=offscreen python -m studio.services.network.tests.setup_tests
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ..broker import (
    setup,
    setup_helper as helper,
    setup_platform as platforms,
    system,
)
from ..wsl import windows_host as windows

LINUX = platforms.Environment(True, "ubuntu", "apt-get", "systemd", False, "")
WSL = platforms.Environment(True, "ubuntu", "apt-get", "systemd", True, "Ubuntu")


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.root = root
        for key, value in dict(CONFIG_DIR=root, CONFIG=root / "mosquitto.conf", FRAGMENT=root / "conf.d/desk-buddy.conf", PASSWD=root / "passwd", ACL=root / "acl", RULE=root / "polkit/rule").items():
            self.stack.enter_context(mock.patch.object(helper, key, value))
        helper.CONFIG.write_text("# custom comment\npersistence true\n")

    def test_fresh_configuration_is_authenticated_and_included(self):
        main, fragment = helper.configuration(1883)
        self.assertIn("# custom comment", main)
        self.assertIn("include_dir", main)
        for line in ("listener 1883 0.0.0.0", "allow_anonymous false", f"password_file {helper.PASSWD}", f"acl_file {helper.ACL}"):
            self.assertIn(line, fragment)

    def test_second_configuration_does_not_duplicate_any_directive(self):
        main, fragment = helper.configuration(1883)
        helper.FRAGMENT.parent.mkdir()
        helper.CONFIG.write_text(main)
        helper.FRAGMENT.write_text(fragment)
        self.assertEqual((main, fragment), helper.configuration(1883))

    def test_compatible_existing_listener_is_preserved(self):
        helper.CONFIG.write_text(f"listener 1883\npassword_file {helper.PASSWD}\n")
        main, fragment = helper.configuration(1883)
        self.assertIn("listener 1883", main)
        self.assertNotIn("listener", fragment)
        self.assertNotIn("password_file", fragment)

    def test_custom_authentication_and_listeners_are_not_rewritten(self):
        for content in ("plugin custom.so\n", "allow_anonymous true\n", "listener 1883 127.0.0.1\n", "password_file /some/other/file\n", "listener 1883\nlistener 8883\n"):
            helper.CONFIG.write_text(content)
            with self.assertRaises(RuntimeError):
                helper.configuration(1883)
            self.assertEqual(helper.CONFIG.read_text(), content)

    def test_included_custom_config_is_checked(self):
        directory = self.root / "other"
        directory.mkdir()
        (directory / "auth.conf").write_text("plugin auth.so\n")
        helper.CONFIG.write_text(f"include_dir {directory}\n")
        with self.assertRaises(RuntimeError):
            helper.configuration(1883)

    def provision(self, *, reuse=False, fail=False, installed=True):
        commands = []
        def checked(command, **kwargs):
            commands.append(command)
            if fail and command[:2] == ["systemctl", "restart"]:
                raise RuntimeError("Could not start broker")
        request = dict(uid=os.getuid(), user="studio", password="generated-secret", port=1883, reuse=reuse, subnet="192.168.1.0/24")
        with mock.patch.object(helper, "detect", return_value=LINUX), \
             mock.patch.object(helper, "executable", side_effect=lambda name: "/usr/bin/" + name if installed else ""), \
             mock.patch.object(helper, "checked", side_effect=checked), \
             mock.patch.object(helper, "run"), \
             mock.patch.object(helper, "firewall"), \
             mock.patch.object(helper.grp, "getgrnam", return_value=SimpleNamespace(gr_gid=os.getgid())), \
             mock.patch.object(helper.os, "chown"), \
             mock.patch.object(setup, "verify_connection", return_value=""):
            result = helper.provision(request, lambda *args: None)
        return result, commands

    def test_conflicting_username_gets_unused_name_and_preserves_accounts(self):
        helper.PASSWD.write_text("studio:oldhash\nstudio-2:otherhash\nrobot:hash\n")
        helper.ACL.write_text("user robot\ntopic readwrite robot/#\n")
        result, commands = self.provision()
        self.assertEqual(result["user"], "studio-3")
        self.assertIn("user robot", helper.ACL.read_text())
        self.assertIn("user studio-3", helper.ACL.read_text())
        self.assertEqual(helper.PASSWD.read_text(), "studio:oldhash\nstudio-2:otherhash\nrobot:hash\n")
        self.assertTrue(any("studio-3" in command for command in commands))

    def test_orphan_acl_name_is_not_reused(self):
        helper.ACL.write_text("user studio\ntopic readwrite old/#\n")
        result, _commands = self.provision()
        self.assertEqual(result["user"], "studio-2")
        self.assertIn("user studio\n", helper.ACL.read_text())
        self.assertIn("user studio-2\ntopic readwrite #", helper.ACL.read_text())

    def test_working_account_is_not_reset(self):
        helper.PASSWD.write_text("studio:hash\n")
        helper.ACL.write_text("user studio\ntopic readwrite desk-buddy/setup/#\n")
        _, commands = self.provision(reuse=True)
        self.assertFalse(any("mosquitto_passwd" in command[0] for command in commands))
        self.assertIn("user studio\ntopic readwrite #", helper.ACL.read_text())
        self.assertNotIn("desk-buddy/setup/#", helper.ACL.read_text())

    def test_reused_account_replaces_its_entire_commented_acl_block(self):
        helper.PASSWD.write_text("studio:old-hash\nrobot:hash\n")
        helper.ACL.write_text(
            "user studio\n# old controller rules\ntopic read desk-buddy/status\n\n"
            "user robot\n# keep this comment\ntopic read robot/#\n"
        )
        self.provision(reuse=True)
        acl = helper.ACL.read_text()
        self.assertNotIn("old controller rules", acl)
        self.assertNotIn("desk-buddy/status", acl)
        self.assertIn("user studio\ntopic readwrite #", acl)
        self.assertIn("user robot\n# keep this comment\ntopic read robot/#", acl)

    def test_configuration_and_accounts_roll_back_on_start_failure(self):
        before = helper.CONFIG.read_text()
        helper.PASSWD.write_text("robot:hash\n")
        helper.ACL.write_text("user robot\n")
        with self.assertRaises(RuntimeError):
            self.provision(fail=True)
        self.assertEqual(helper.CONFIG.read_text(), before)
        self.assertEqual(helper.ACL.read_text(), "user robot\n")
        self.assertEqual(helper.PASSWD.read_text(), "robot:hash\n")
        self.assertFalse(helper.FRAGMENT.exists())

    def test_missing_packages_are_installed_before_configuration(self):
        _, commands = self.provision(installed=False)
        self.assertEqual(commands[0], ["apt-get", "update"])
        self.assertIn("install", commands[1])

    def test_install_failure_does_not_change_configuration(self):
        before = helper.CONFIG.read_text()
        with mock.patch.object(helper, "detect", return_value=LINUX), mock.patch.object(helper, "executable", return_value=""), mock.patch.object(helper, "checked", side_effect=RuntimeError("Package install failed")):
            with self.assertRaises(RuntimeError):
                helper.provision(dict(uid=os.getuid(), user="studio", password="secret", port=1883), lambda *a: None)
        self.assertEqual(helper.CONFIG.read_text(), before)


class PlatformTests(unittest.TestCase):
    def test_all_supported_package_managers_have_install_commands(self):
        for manager in ("apt-get", "pacman", "dnf", "zypper", "apk"):
            commands = platforms.install_commands(manager)
            self.assertTrue(all(command[0] == manager for command in commands))
            self.assertTrue(any("mosquitto" in command for command in commands))
        self.assertNotIn("-Sy", platforms.install_commands("pacman")[0])

    def test_service_manager_adapters_and_unsupported_case(self):
        for manager, command in (("systemd", "systemctl"), ("openrc", "rc-service"), ("sysv", "service")):
            self.assertTrue(any(item[0] == command for item in platforms.service_commands(manager, "start")))
        with self.assertRaises(RuntimeError):
            platforms.service_commands("", "start")

    def test_wsl_terminal_targets_current_distro_with_argument_boundaries(self):
        with mock.patch.object(setup, "executable", side_effect=lambda name: "/win/wt.exe" if name == "wt.exe" else ""):
            command = setup.terminal_command(WSL, Path("/tmp/space dir/approve.sh"))
        self.assertIn("Ubuntu", command)
        self.assertEqual(command[-1], "/tmp/space dir/approve.sh")

    def test_native_terminal_fallback(self):
        with mock.patch.object(setup, "executable", side_effect=lambda name: "/usr/bin/xterm" if name == "xterm" else ""):
            self.assertEqual(setup.terminal_command(LINUX, Path("/tmp/setup")), ["/usr/bin/xterm", "-e", "/bin/sh", "/tmp/setup"])

    def test_cancelled_polkit_does_not_open_terminal(self):
        process = mock.Mock()
        process.poll.return_value = 126
        with mock.patch.object(setup, "executable", return_value="/usr/bin/pkexec"), mock.patch.object(setup.subprocess, "Popen", return_value=process), mock.patch.object(setup, "terminal_command") as terminal:
            with self.assertRaises(setup.SetupCancelled):
                setup.authorize({"user": "studio", "password": "secret"}, LINUX, lambda *a: None)
        terminal.assert_not_called()

    def test_wsl_uses_its_root_user_without_a_second_linux_prompt(self):
        launched = []

        def start(command, **_kwargs):
            launched.append(command)
            Path(command[-1]).write_text(json.dumps({"state": "complete", "user": "studio"}))
            process = mock.Mock()
            process.poll.return_value = 0
            return process

        def find(name):
            return "/mnt/c/Windows/System32/wsl.exe" if name == "wsl.exe" else ""

        with mock.patch.object(setup, "executable", side_effect=find), \
                mock.patch.object(setup.subprocess, "Popen", side_effect=start), \
                mock.patch.object(setup, "terminal_command") as terminal:
            result = setup.authorize(
                {"uid": os.getuid(), "user": "studio", "password": "secret"},
                WSL,
                lambda *a: None,
            )

        self.assertEqual(result["user"], "studio")
        self.assertEqual(launched[0][0], "/mnt/c/Windows/System32/wsl.exe")
        self.assertIn("--user", launched[0])
        self.assertEqual(launched[0][launched[0].index("--user") + 1], "root")
        terminal.assert_not_called()

    def test_source_and_frozen_helper_entry(self):
        source = setup.helper_command(Path("/tmp/request"), Path("/tmp/result"))
        self.assertTrue(Path(source[1]).exists())
        with mock.patch.object(setup.sys, "frozen", True, create=True):
            frozen = setup.helper_command(Path("/tmp/request"), Path("/tmp/result"))
        self.assertEqual(frozen[1], "--broker-setup-helper")


class CoordinatorTests(unittest.TestCase):
    def finish(self, coordinator):
        deadline = time.monotonic() + 2
        while coordinator.running and time.monotonic() < deadline:
            coordinator.poll()
            time.sleep(0.005)
        self.assertFalse(coordinator.running)

    def test_only_one_attempt_runs_and_failure_requires_retry(self):
        release = threading.Event()
        def operation(request, emit):
            release.wait(1)
            raise setup.SetupCancelled("Cancelled")
        coordinator = setup.Coordinator(operation)
        request = {"password": "secret"}
        self.assertTrue(coordinator.start(request))
        self.assertFalse(coordinator.start(request, retry=True))
        release.set()
        self.finish(coordinator)
        self.assertEqual(coordinator.status.state, "cancelled")
        self.assertFalse(coordinator.start(request))
        self.assertTrue(coordinator.start(request, retry=True))
        self.finish(coordinator)

    def test_errors_are_redacted(self):
        def operation(*args):
            raise RuntimeError("utility failed with secret-password")
        coordinator = setup.Coordinator(operation)
        coordinator.start({"password": "secret-password"})
        self.finish(coordinator)
        self.assertNotIn("secret-password", coordinator.status.detail)

    def test_empty_secret_cannot_break_error_reporting(self):
        def operation(*args):
            raise RuntimeError("Invalid account details")
        coordinator = setup.Coordinator(operation)
        coordinator.start({"password": ""})
        self.finish(coordinator)
        self.assertEqual(coordinator.status.state, "failed")
        self.assertIn("Invalid account details", coordinator.status.detail)

    def test_account_reload_uses_direct_success_without_authorization(self):
        with (
            mock.patch.object(
                setup, "detect", return_value=LINUX
            ),
            mock.patch(
                "studio.services.network.broker.system.reload",
                return_value=system.AccountChange(changed=True, applied=True),
            ),
            mock.patch.object(setup, "authorize") as authorize,
        ):
            result = setup.apply_account_reload(
                {"user": "studio", "password": "secret", "port": 1883},
                lambda *a, **kw: None,
            )
        self.assertEqual(result.state, "ready")
        authorize.assert_not_called()

    def test_account_reload_uses_the_narrow_privileged_action(self):
        request = {
            "user": "studio", "password": "secret", "port": 1883,
            "direct_attempted": True,
        }
        with (
            mock.patch.object(setup, "detect", return_value=WSL),
            mock.patch.object(system, "reload") as direct,
            mock.patch.object(
                setup, "authorize", return_value={"state": "complete"}
            ) as authorize,
        ):
            result = setup.apply_account_reload(
                request, lambda *a, **kw: None
            )
        self.assertEqual(result.state, "ready")
        direct.assert_not_called()
        self.assertEqual(authorize.call_args.args[0]["action"], "reload")

    def test_account_reload_coordinator_serializes_and_redacts_failures(self):
        release = threading.Event()

        def operation(request, emit):
            release.wait(1)
            raise RuntimeError("reload failed with secret-password")

        coordinator = setup.AccountReloadCoordinator(operation)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertTrue(coordinator.start({"password": "secret-password"}))
            self.assertFalse(
                coordinator.start({"password": "secret-password"}, retry=True)
            )
            release.set()
            self.finish(coordinator)
        self.assertEqual(coordinator.status.state, "failed")
        self.assertNotIn("secret-password", coordinator.status.detail)
        self.assertNotIn("secret-password", output.getvalue())

    def test_existing_wsl_broker_is_ready_without_implicit_windows_networking(self):
        def verify(host, number, user, secret):
            return "" if secret == "existing-password" else "Credentials refused"

        with (
            mock.patch.object(setup, "detect", return_value=WSL),
            mock.patch.object(setup, "verify_connection", side_effect=verify),
            mock.patch.object(setup, "local_network", return_value=("172.20.0.2", "172.20.0.0/24")),
            mock.patch.object(setup, "firewall_ready", return_value=True),
            mock.patch.object(helper, "FRAGMENT", Path("/nonexistent/desk-buddy.conf")),
            mock.patch.object(setup, "authorize") as authorize,
            mock.patch.object(windows, "powershell") as powershell,
        ):
            result = setup.perform(
                dict(host="", user="studio", password="existing-password", port=1883,
                     robot_host="192.168.1.10", network_ready=True),
                lambda *a, **kw: None,
        )
        authorize.assert_not_called()
        powershell.assert_not_called()
        self.assertEqual(result.state, "ready")
        self.assertTrue(result.connected)
        self.assertFalse(result.network_ready)
        self.assertEqual(result.robot_host, "")
        self.assertIn("127.0.0.1:1883", result.message)
        self.assertEqual(result.detail, "")

    def test_new_wsl_broker_uses_configured_port_without_windows_setup(self):
        with (
            mock.patch.object(setup, "detect", return_value=WSL),
            mock.patch.object(setup, "verify_connection", side_effect=["Not answering", ""]) as verify,
            mock.patch.object(setup, "local_network", return_value=("172.20.0.2", "172.20.0.0/24")),
            mock.patch.object(setup, "authorize", return_value={"user": "studio-2", "reload_rule": True}) as authorize,
        ):
            result = setup.perform(dict(host="", user="studio", password="secret", port=1884), lambda *a, **kw: None)
        self.assertEqual(authorize.call_args.args[0]["action"], "setup")
        self.assertEqual(authorize.call_args.args[0]["port"], 1884)
        verify.assert_called_with("127.0.0.1", 1884, "studio-2", "secret")
        self.assertEqual(result.state, "ready")
        self.assertTrue(result.connected)
        self.assertTrue(result.reload_rule)
        self.assertEqual(result.user, "studio-2")
        self.assertEqual(result.robot_host, "")
        self.assertFalse(result.network_ready)
        self.assertEqual(result.detail, "")

    def test_wsl_still_reports_linux_firewall_failure(self):
        with (
            mock.patch.object(setup, "detect", return_value=WSL),
            mock.patch.object(setup, "verify_connection", side_effect=["Not answering", ""]),
            mock.patch.object(setup, "local_network", return_value=("172.20.0.2", "172.20.0.0/24")),
            mock.patch.object(setup, "authorize", return_value={"user": "studio", "network_problem": "Linux firewall failed"}),
        ):
            result = setup.perform(dict(host="", user="studio", password="secret", port=1883), lambda *a, **kw: None)
        self.assertEqual(result.state, "failed")
        self.assertTrue(result.connected)
        self.assertFalse(result.network_ready)
        self.assertEqual(result.detail, "Linux firewall failed")

    def test_existing_authenticated_broker_needs_no_setup_approval(self):
        def verify(host, number, user, secret):
            return "" if secret == "existing-password" else "Credentials refused"
        with mock.patch.object(setup, "detect", return_value=LINUX), mock.patch.object(setup, "verify_connection", side_effect=verify), mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")), mock.patch.object(setup, "firewall_ready", return_value=True), mock.patch.object(setup, "authorize") as authorize, mock.patch.object(helper, "FRAGMENT", Path("/nonexistent/desk-buddy.conf")):
            result = setup.perform(dict(host="", user="studio", password="existing-password", port=1883), lambda *a, **kw: None)
        authorize.assert_not_called()
        self.assertTrue(result.connected)
        self.assertTrue(result.network_ready)

    def test_a_verify_only_recheck_never_asks_for_a_password(self):
        """A passive revisit reports an unmet precondition; it does not prompt.

        Without this, any precondition that keeps reading as unmet turns
        every visit to the Network tab into another authorization dialog,
        because the check runs again on each visit and escalates each time.
        """
        def verify(host, number, user, secret):
            return "" if secret == "existing-password" else "Credentials refused"
        with (
            mock.patch.object(setup, "detect", return_value=LINUX),
            mock.patch.object(setup, "verify_connection", side_effect=verify),
            mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")),
            mock.patch.object(setup, "firewall_ready", return_value=False),
            mock.patch.object(helper, "FRAGMENT", Path("/nonexistent/desk-buddy.conf")),
            mock.patch.object(setup, "authorize") as authorize,
        ):
            result = setup.perform(
                dict(host="", user="studio", password="existing-password",
                     port=1883, verify_only=True),
                lambda *a, **kw: None,
            )
        authorize.assert_not_called()
        self.assertTrue(result.connected)
        self.assertEqual(result.state, "failed")

    def test_verify_only_still_reports_a_healthy_broker_as_ready(self):
        """The common case: nothing to do, so the revisit is silent."""
        def verify(host, number, user, secret):
            return "" if secret == "existing-password" else "Credentials refused"
        with (
            mock.patch.object(setup, "detect", return_value=LINUX),
            mock.patch.object(setup, "verify_connection", side_effect=verify),
            mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")),
            mock.patch.object(setup, "firewall_ready", return_value=True),
            mock.patch.object(helper, "FRAGMENT", Path("/nonexistent/desk-buddy.conf")),
            mock.patch.object(setup, "authorize") as authorize,
        ):
            result = setup.perform(
                dict(host="", user="studio", password="existing-password",
                     port=1883, verify_only=True),
                lambda *a, **kw: None,
            )
        authorize.assert_not_called()
        self.assertEqual(result.state, "ready")
        self.assertTrue(result.connected)

    def test_an_explicit_retry_may_still_authorize(self):
        """verify_only is the passive path only. Retry is the user's own
        gesture, and it must still be able to finish setup."""
        with (
            mock.patch.object(setup, "detect", return_value=LINUX),
            mock.patch.object(setup, "verify_connection", side_effect=["Not answering", ""]),
            mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")),
            mock.patch.object(setup, "authorize", return_value={"user": "studio-2"}) as authorize,
        ):
            result = setup.perform(
                dict(host="", user="studio", password="secret", port=1883),
                lambda *a, **kw: None,
            )
        authorize.assert_called_once()
        self.assertEqual(result.state, "ready")

    def test_stock_anonymous_broker_creates_a_real_account(self):
        with mock.patch.object(setup, "detect", return_value=LINUX), mock.patch.object(setup, "verify_connection", return_value=""), mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")), mock.patch.object(setup, "firewall_ready", return_value=True), mock.patch.object(setup, "authorize", return_value={"user": "studio-2"}) as authorize:
            result = setup.perform(dict(host="", user="studio", password="secret", port=1883), lambda *a, **kw: None)
        self.assertFalse(authorize.call_args.args[0]["reuse"])
        self.assertEqual(result.user, "studio-2")

    def test_stopped_broker_runs_setup_and_verifies_new_credentials(self):
        with mock.patch.object(setup, "detect", return_value=LINUX), mock.patch.object(setup, "verify_connection", side_effect=["Not answering", ""]), mock.patch.object(setup, "local_network", return_value=("192.168.1.2", "192.168.1.0/24")), mock.patch.object(setup, "authorize", return_value={"user": "studio-2"}) as authorize:
            result = setup.perform(dict(host="", user="studio", password="secret", port=1883), lambda *a, **kw: None)
        self.assertEqual(authorize.call_args.args[0]["action"], "setup")
        self.assertTrue(result.connected)
        self.assertEqual(result.user, "studio-2")


class WindowsTests(unittest.TestCase):
    def script(self, mode="nat", apply=False):
        return windows.network_script(name="DeskBuddy-abc123", mode=mode, host="192.168.1.10", target="172.20.0.2", subnet="192.168.1.0/24", port=1883, apply=apply)

    def test_windows_rules_are_scoped_and_owned(self):
        script = self.script()
        self.assertIn("-Profile $profile", script)
        self.assertIn("-RemoteAddress $subnet", script)
        self.assertIn("not owned by Studio", script)
        self.assertIn("Another Windows application", script)
        self.assertIn("$old.Target", script)
        self.assertIn("$needs -and $apply", script)
        self.assertIn("ConnectAsync($hostAddress, $port)", script)

    def test_mirrored_mode_uses_scoped_hyperv_rule(self):
        script = self.script("mirrored", True)
        self.assertIn("New-NetFirewallHyperVRule", script)
        self.assertIn("-LocalPorts $port -RemoteAddresses $subnet", script)
        self.assertNotIn("DefaultInboundAction", script)

    def test_public_profile_and_custom_port_are_scoped(self):
        script = windows.network_script(
            name="DeskBuddy-abc123", mode="nat", host="192.168.1.10",
            target="172.20.0.2", subnet="192.168.1.0/24", port=1884,
            profile="Public", apply=True,
        )
        self.assertIn("$port = 1884", script)
        self.assertIn("$profile = 'Public'", script)
        self.assertIn("-RemoteAddress $subnet -Profile $profile", script)

    def test_nat_does_not_require_hyperv_firewall_cmdlets(self):
        script = self.script("nat", True)
        self.assertIn("$hyperAvailable", script)
        self.assertIn("$old.Mode -eq 'mirrored'", script)
        self.assertIn("-Name $hyperName", script)

    def test_bad_addresses_and_modes_never_reach_powershell(self):
        with self.assertRaises(ValueError):
            windows.network_script(name="DeskBuddy-abc", mode="nat", host="'; bad", target="172.20.0.2", subnet="192.168.1.0/24", port=1883, apply=True)
        with self.assertRaises(ValueError):
            self.script("unknown")

    def test_removal_requires_ownership_and_matches_the_recorded_proxy(self):
        script = windows.remove_script(name="DeskBuddy-abc123")
        self.assertIn("and -not $old", script)
        self.assertIn("$listenAddress -eq $old.HostAddress", script)
        self.assertIn("$targetValue -eq \"$($old.Target)/$($old.Port)\"", script)
        self.assertIn("Remove-Item -Path $key", script)

    def windows_mock(self, *, needed=False, denied=False, mode="nat"):
        calls = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        def powershell(script, **kwargs):
            calls.append(script)
            if "Get-NetIPConfiguration" in script:
                data = dict(address="192.168.1.10", prefix=24, profile="Private", temp=str(root))
            elif "Start-Process -FilePath" in script:
                if denied:
                    data = {"error": "The operation was canceled by the user.", "nativeCode": 1223}
                    return subprocess.CompletedProcess([], 0, json.dumps(data), "")
                # Simulate only the authorized helper's output file.
                (next(root.iterdir()) / "result.json").write_text(json.dumps({"needed": False}))
                data = {"exitCode": 0}
            else:
                pending = needed if len(calls) == 2 else False
                data = {
                    "needed": pending,
                    "owned": pending,
                    "host": "192.168.1.10",
                    "reachable": not pending,
                }
            return subprocess.CompletedProcess([], 0, json.dumps(data), "")
        def command(argv, **kwargs):
            result = mode if argv[-1] == "--networking-mode" else argv[-1]
            return subprocess.CompletedProcess(argv, 0, result, "")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(windows, "powershell", side_effect=powershell))
        stack.enter_context(mock.patch.object(windows, "run", side_effect=command))
        stack.enter_context(mock.patch.object(windows, "executable", return_value="/usr/bin/wslinfo"))
        stack.enter_context(mock.patch.object(setup, "local_network", return_value=("172.20.0.9", "172.20.0.0/24")))
        return calls

    def test_existing_windows_network_needs_no_uac(self):
        calls = self.windows_mock()
        result = windows.enable(WSL, 1883, lambda *a, **kw: None)
        self.assertTrue(result.ready)
        self.assertEqual(result.host, "192.168.1.10")
        self.assertEqual(len(calls), 2)

    def test_changed_wsl_address_is_repaired_and_checked_again(self):
        calls = self.windows_mock(needed=True)
        progress = []
        result = windows.enable(WSL, 1883, lambda *a, **kw: progress.append(a))
        self.assertEqual(result.host, "192.168.1.10")
        self.assertIn("172.20.0.9", calls[1])
        self.assertIn("-Verb RunAs", calls[2])
        self.assertEqual(len(calls), 4)
        self.assertTrue(progress)

    def test_windows_uac_cancellation_is_reported(self):
        self.windows_mock(needed=True, denied=True)
        with self.assertRaises(windows.AuthorizationCancelled):
            windows.enable(WSL, 1883, lambda *a, **kw: None)

    def test_missing_windows_interoperability_is_explained(self):
        with mock.patch.object(windows, "executable", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "interoperability"):
                windows.powershell("Write-Output test")

    def test_disable_uses_uac_and_returns_disabled(self):
        with (
            mock.patch.object(windows, "_windows_info", return_value={"temp": "C:\\Temp"}),
            mock.patch.object(windows, "_elevated", return_value={"removed": True}) as elevated,
        ):
            result = windows.disable(WSL, 1883)
        self.assertEqual(result.state, "disabled")
        self.assertFalse(result.ready)
        self.assertIn("Remove-Item -Path $key", elevated.call_args.args[0])

    def test_elevation_uses_a_script_file_not_a_nested_encoded_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def convert(argv, **kwargs):
                value = argv[-1]
                if argv[1] == "-u":
                    result = str(root)
                else:
                    result = "C:\\Temp\\" + Path(value).parent.name + "\\" + Path(value).name
                return subprocess.CompletedProcess(argv, 0, result, "")

            def launch(script, **kwargs):
                self.assertIn("Start-Process -FilePath", script)
                self.assertNotIn("-EncodedCommand", script)
                program = next(root.rglob("network.ps1"))
                body = program.read_text(encoding="utf-8-sig")
                self.assertIn("Out-File -LiteralPath 'C:\\Temp", body)
                (program.parent / "result.json").write_text(json.dumps({"ok": True}))
                reply = json.dumps({"exitCode": 0})
                return subprocess.CompletedProcess([], 0, reply, "")

            with (
                mock.patch.object(windows, "run", side_effect=convert),
                mock.patch.object(windows, "powershell", side_effect=launch),
            ):
                result = windows._elevated("@{ ok=$true } | ConvertTo-Json", "C:\\Temp")
        self.assertTrue(result["ok"])

    def test_mirrored_network_uses_actual_mode(self):
        calls = self.windows_mock(mode="mirrored")
        windows.enable(WSL, 1883, lambda *a, **kw: None)
        self.assertIn("$mode = 'mirrored'", calls[1])

    def test_inspection_marks_owned_drift_for_repair(self):
        self.windows_mock(needed=True)
        status = windows.inspect(WSL, 1883)
        self.assertEqual(status.state, "repair")
        self.assertFalse(status.ready)

    def test_inspection_requires_a_windows_side_tcp_connection(self):
        self.windows_mock()
        def unreachable(script, **kwargs):
            if "Get-NetIPConfiguration" in script:
                data = dict(address="192.168.1.10", prefix=24, profile="Private", temp="C:\\Temp")
            else:
                data = dict(needed=False, owned=True, host="192.168.1.10", reachable=False)
            return subprocess.CompletedProcess([], 0, json.dumps(data), "")
        with mock.patch.object(windows, "powershell", side_effect=unreachable):
            status = windows.inspect(WSL, 1883)
        self.assertEqual(status.state, "failed")
        self.assertIn("did not answer", status.detail)

    def test_conflict_reason_reaches_the_user(self):
        result = subprocess.CompletedProcess([], 0, json.dumps({"error": "Another Windows application is using the broker port."}), "")
        with self.assertRaisesRegex(RuntimeError, "Another Windows application"):
            windows.decode(result)

    def test_coordinator_keeps_windows_failure_in_its_own_state(self):
        coordinator = windows.AccessCoordinator()
        with mock.patch.object(windows, "enable", side_effect=RuntimeError("Windows unavailable")):
            self.assertTrue(coordinator.start("enable", WSL, 1883))
            deadline = time.monotonic() + 2
            while coordinator.running and time.monotonic() < deadline:
                coordinator.poll()
                time.sleep(0.005)
        self.assertFalse(coordinator.status.ready)
        self.assertEqual(coordinator.status.state, "failed")
        self.assertIn("Windows unavailable", coordinator.status.detail)


@unittest.skipUnless(platforms.executable("mosquitto") and platforms.executable("mosquitto_passwd"), "Mosquitto tools are not installed")
class MqttIntegrationTests(unittest.TestCase):
    def test_live_authentication_and_publish_subscribe(self):
        """An isolated unprivileged broker; no system service/config is touched."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password_file = root / "passwd"
            command = [platforms.executable("mosquitto_passwd"), "-b", "-c", str(password_file), "studio", "test-only-password"]
            subprocess.run(command, check=True, capture_output=True)
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            acl = root / "acl"
            acl.write_text("user studio\ntopic readwrite desk-buddy/setup/#\n")
            config = root / "mosquitto.conf"
            config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous false\npassword_file {password_file}\nacl_file {acl}\n")
            broker = subprocess.Popen([platforms.executable("mosquitto"), "-c", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                from ..broker.finder import port_open
                deadline = time.monotonic() + 3
                while not port_open(port) and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(setup.verify_connection("127.0.0.1", port, "studio", "test-only-password"), "")
                self.assertIn("refused", setup.verify_connection("127.0.0.1", port, "studio", "wrong-password"))
            finally:
                broker.terminate()
                broker.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
