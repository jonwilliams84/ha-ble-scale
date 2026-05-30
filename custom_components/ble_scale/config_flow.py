"""Config flow for the BLE Scale integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN, SERVICE_UUID


def _is_scale(info: BluetoothServiceInfoBleak) -> bool:
    """Best-effort check that a discovered device looks like our scale."""
    return SERVICE_UUID in (info.service_uuids or [])


def _title_for(info: BluetoothServiceInfoBleak) -> str:
    """Build a human-readable title for a discovered device."""
    if info.name:
        return f"{info.name} ({info.address})"
    return f"BLE Scale ({info.address})"


class BLEScaleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLE Scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._discovery_info: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a discovery from the bluetooth integration."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": _title_for(discovery_info)}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup of a single discovered device."""
        assert self._discovery_info is not None
        info = self._discovery_info
        if user_input is not None:
            return self.async_create_entry(
                title=_title_for(info),
                data={CONF_ADDRESS: info.address},
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": _title_for(info)},
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user-initiated flow."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered_devices[address]
            return self.async_create_entry(
                title=_title_for(info),
                data={CONF_ADDRESS: address},
            )

        current_addresses = self._async_current_ids()
        self._discovered_devices = {}
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current_addresses:
                continue
            if not _is_scale(info):
                continue
            self._discovered_devices[info.address] = info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: _title_for(info)
                            for address, info in self._discovered_devices.items()
                        }
                    )
                }
            ),
        )
