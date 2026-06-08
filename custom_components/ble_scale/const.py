"""Constants for the BLE Scale integration."""

DOMAIN = "ble_scale"

WRITE_CHAR_UUID = "0000ffb1-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR_UUID = "0000ffb2-0000-1000-8000-00805f9b34fb"
SERVICE_UUID = "0000ffb0-0000-1000-8000-00805f9b34fb"

WAKE_PAYLOAD = b"\x01"

# How long to hold the connection open after the last weight notification before
# voluntarily disconnecting and releasing the BLE proxy connection slot. Kept
# short: the scale powers off within seconds of you stepping off, and on a shared
# ESP32 proxy every held slot is one the other devices (e.g. showers) can't use.
IDLE_DISCONNECT_SECONDS = 15
