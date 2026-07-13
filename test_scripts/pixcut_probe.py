#!/usr/bin/env python3
"""
Minimal PixCut S1 (Liene, 302c:3101) Linux probe.

Reverse-engineered from a Cynthion capture (pixcut_initialize.pcapng):
the device speaks JSON-RPC over bulk USB on the vendor interface
"WinUSB_E2" (interface 2), endpoints 0x06 OUT / 0x86 IN.

Wire framing:
    request : b"cmd json\n" + <json bytes>      -> EP 0x06 (OUT)
    response: <json bytes>                       <- EP 0x86 (IN)

Run:  python3 pixcut_probe.py
(may need sudo, or a udev rule granting access to 302c:3101)
"""
import json
import sys
import usb.core
import usb.util

VID, PID = 0x302C, 0x3101
IFACE = 2
EP_OUT = 0x06
EP_IN = 0x86
CMD_PREFIX = b"cmd json\n"


def open_device():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("PixCut S1 (302c:3101) not found. Plugged in and powered on?")
    # The vendor interface isn't claimed by a kernel driver, but detach just in case.
    if dev.is_kernel_driver_active(IFACE):
        dev.detach_kernel_driver(IFACE)
    usb.util.claim_interface(dev, IFACE)
    return dev


def rpc(dev, method, params, req_id):
    payload = json.dumps({"method": method, "params": params, "id": req_id}).encode()
    dev.write(EP_OUT, CMD_PREFIX + payload, timeout=2000)
    # Device NAKs until it has data; poll with a generous timeout.
    data = dev.read(EP_IN, 512, timeout=5000)
    return json.loads(bytes(data).decode("utf-8", "replace"))


def main():
    dev = open_device()
    try:
        print("== device-info ==")
        print(json.dumps(rpc(dev, "get-prop",
              ["device-info", "mac-address", "auto-off-interval"], 1), indent=2))
        print("== printer-state ==")
        print(json.dumps(rpc(dev, "get-prop",
              ["printer-state", "printer-sub-state", "printer-state-alerts"], 2), indent=2))
    finally:
        usb.util.release_interface(dev, IFACE)


if __name__ == "__main__":
    main()
