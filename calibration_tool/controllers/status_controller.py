from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict

try:
    from ..models.status_state import build_calibration_status_rows
    from ..views.status_view import StatusView
except ImportError:  # pragma: no cover - direct script execution
    from models.status_state import build_calibration_status_rows
    from views.status_view import StatusView

STATE_LABELS = {
    "SAVED": "✓ Saved",
    "DEFAULT": "○ Default",
    "MISSING": "! Missing",
    "OPTIONAL": "— Optional",
}
STATE_TAGS = {
    "SAVED": "saved",
    "DEFAULT": "default",
    "MISSING": "missing",
    "OPTIONAL": "optional",
}


class StatusController:
    """Wires the Status view to the app's shared MQTT state.

    Takes its collaborators as constructor arguments instead of reaching up
    into the main window: connect/save-summary/refresh are shared app-level
    actions (Reconnect lives on the Connect view too), so they are passed in
    rather than owned here.
    """

    def __init__(
        self,
        view: StatusView,
        *,
        connect_mqtt: Callable[[], None],
        save_session_summary: Callable[[], None],
        refresh_status: Callable[[], None],
        heartbeat_age: Callable[[], Any],
    ) -> None:
        self.view = view
        self._heartbeat_age = heartbeat_age

        view.on_reconnect = connect_mqtt
        view.on_save_summary = save_session_summary
        view.on_refresh_status = refresh_status

    def render(
        self,
        values: Dict[str, Any],
        heartbeat: Dict[str, Any],
        ready: Dict[str, Any],
        last_error: str,
    ) -> None:
        self.view.status_summary_vars["overall"].set("Ready" if values.get("initial_calibration_ready") else "Needs work")
        self.view.status_summary_vars["base"].set("Ready" if values.get("base_rotation_ready") else "Not calibrated")
        self.view.status_summary_vars["perch"].set("Saved" if values.get("perch_configured") else "Using defaults")
        if values.get("ik_hover_calibrated"):
            ik_status = "Table + upper ready" if values.get("ik_z50_calibrated") else "Table ready"
        else:
            ik_status = "Not calibrated"
        self.view.status_summary_vars["ik"].set(ik_status)
        self.view.status_summary_vars["stencil"].set("Ready" if values.get("stencil_calibrated") else "Not calibrated")

        firmware_version = heartbeat.get("firmware_version") or ready.get("firmware_version")
        ota_state = heartbeat.get("ota_state") or ready.get("ota_state")
        firmware_text = str(firmware_version or "—")
        if ota_state:
            firmware_text += f" · {ota_state}"
        self.view.system_status_vars["firmware"].set(firmware_text)
        age = self._heartbeat_age()
        self.view.system_status_vars["heartbeat"].set("—" if age is None else f"{age:.1f} seconds ago")
        self.view.system_status_vars["reset"].set(str(ready.get("last_reset_reason") or "—"))
        self.view.system_status_vars["error"].set(last_error or "None")

        tree = self.view.status_tree
        for item_id in tree.get_children():
            tree.delete(item_id)

        groups: Dict[str, str] = {}
        for row in build_calibration_status_rows(values):
            group_name = row["group"]
            if group_name not in groups:
                groups[group_name] = tree.insert("", tk.END, text=group_name, values=("", "", "", ""), tags=("group",), open=True)
            state = row["state"]
            tree.insert(
                groups[group_name],
                tk.END,
                text=row["label"],
                values=(STATE_LABELS[state], row["key"], row["value"], row["source"]),
                tags=(STATE_TAGS[state],),
            )
