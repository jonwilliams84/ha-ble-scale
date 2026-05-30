"""Sensor platform for the BLE Scale integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    IDLE_DISCONNECT_SECONDS,
    NOTIFY_CHAR_UUID,
    RECONNECT_INTERVAL_SECONDS,
    WAKE_PAYLOAD,
    WRITE_CHAR_UUID,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLE Scale sensor from a config entry."""
    address: str = config_entry.data[CONF_ADDRESS]
    async_add_entities([BLEScaleSensor(address, config_entry.title)])


class BLEScaleSensor(SensorEntity):
    """Representation of a BLE Scale weight reading."""

    _attr_has_entity_name = True
    _attr_name = "Weight"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_should_poll = False

    def __init__(self, address: str, title: str) -> None:
        """Initialize the sensor."""
        self._address = address
        self._attr_unique_id = f"{address}_weight"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, address)},
            identifiers={(DOMAIN, address)},
            name=title,
            manufacturer="BLE Scale",
        )
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._disconnect_handle: asyncio.TimerHandle | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._unavailable_cancel: Any = None
        self._available = False

    @property
    def available(self) -> bool:
        """Return whether the scale is currently available."""
        return self._available

    async def async_added_to_hass(self) -> None:
        """Register callbacks and try to connect when added."""
        self._unavailable_cancel = bluetooth.async_track_unavailable(
            self.hass, self._handle_unavailable, self._address, connectable=True
        )
        self.async_on_remove(
            bluetooth.async_register_callback(
                self.hass,
                self._handle_advertisement,
                {"address": self._address, "connectable": True},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
        )
        self.hass.async_create_task(self._async_connect())

    async def async_will_remove_from_hass(self) -> None:
        """Tear down connection state when entity is removed."""
        if self._unavailable_cancel is not None:
            self._unavailable_cancel()
            self._unavailable_cancel = None
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
            self._disconnect_handle = None
        await self._async_disconnect()

    @callback
    def _handle_unavailable(
        self, _service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Mark unavailable when HA stops seeing advertisements."""
        self._available = False
        self.async_write_ha_state()

    @callback
    def _handle_advertisement(
        self,
        _service_info: bluetooth.BluetoothServiceInfoBleak,
        _change: bluetooth.BluetoothChange,
    ) -> None:
        """Connect when the scale wakes up and starts advertising."""
        if self._client and self._client.is_connected:
            return
        self.hass.async_create_task(self._async_connect())

    def _decode_weight(self, data: bytes) -> float | None:
        """Decode a weight notification payload into a numeric value."""
        if len(data) < 8:
            _LOGGER.debug("Ignoring short notification: %s", data.hex())
            return None
        weight_raw = int.from_bytes(data[4:8], byteorder="big")
        return weight_raw / 256000

    @callback
    def _notification_handler(self, _sender: Any, data: bytearray) -> None:
        """Handle an incoming weight notification."""
        _LOGGER.debug("Notification from %s: %s", self._address, data.hex())
        weight = self._decode_weight(bytes(data))
        if weight is None:
            return
        self._attr_native_value = round(weight, 1)
        self._available = True
        self.async_write_ha_state()
        self._reset_idle_disconnect()

    @callback
    def _reset_idle_disconnect(self) -> None:
        """(Re)arm the idle disconnect timer."""
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
        self._disconnect_handle = self.hass.loop.call_later(
            IDLE_DISCONNECT_SECONDS,
            lambda: self.hass.async_create_task(self._async_disconnect()),
        )

    @callback
    def _handle_client_disconnect(self, _client: BleakClient) -> None:
        """Bleak disconnected callback — flag unavailable, drop client."""
        self._client = None
        self._available = False
        self.async_write_ha_state()

    async def _async_connect(self) -> None:
        """Connect to the scale, subscribe to notifications, and wake it."""
        async with self._connect_lock:
            if self._client and self._client.is_connected:
                return

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self._address, connectable=True
            )
            if ble_device is None:
                _LOGGER.debug("Scale %s not currently visible", self._address)
                self._schedule_retry()
                return

            try:
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self._address,
                    disconnected_callback=self._handle_client_disconnect,
                )
                await client.start_notify(NOTIFY_CHAR_UUID, self._notification_handler)
                await client.write_gatt_char(
                    WRITE_CHAR_UUID, WAKE_PAYLOAD, response=False
                )
            except (BleakError, asyncio.TimeoutError, TimeoutError) as err:
                _LOGGER.warning("Failed to connect to %s: %s", self._address, err)
                self._available = False
                self.async_write_ha_state()
                self._schedule_retry()
                return

            self._client = client
            self._available = True
            self.async_write_ha_state()
            self._reset_idle_disconnect()

    async def _async_disconnect(self) -> None:
        """Drop the BLE connection if held."""
        client = self._client
        self._client = None
        if self._disconnect_handle is not None:
            self._disconnect_handle.cancel()
            self._disconnect_handle = None
        if client is None or not client.is_connected:
            return
        try:
            await client.disconnect()
        except BleakError as err:
            _LOGGER.debug("Error disconnecting from %s: %s", self._address, err)

    @callback
    def _schedule_retry(self) -> None:
        """Schedule a connection retry after a delay."""
        if self._retry_task and not self._retry_task.done():
            return
        self._retry_task = self.hass.async_create_task(self._async_retry())

    async def _async_retry(self) -> None:
        """Sleep then try to connect again."""
        try:
            await asyncio.sleep(RECONNECT_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return
        await self._async_connect()
