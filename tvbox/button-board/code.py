# The button board (tvbox/button-board/README.md): the listen button, the power button and the mute switch,
# sent to the TV box over USB. buttons.py says what each one sends; this file reads the pins.
import time

import board
import digitalio
import keypad
import supervisor
import usb_hid

import buttons

# Each control sits on two pins side by side: the first is read, the second is held at 0 V for it, so the
# leads plug straight on and no ground wire has to be shared. In the order buttons.LISTEN, POWER, MUTE.
READ = (board.GP0, board.GP2, board.GP4)
ZERO = (board.GP1, board.GP3, board.GP5)

zeros = []
for pin in ZERO:
    zero = digitalio.DigitalInOut(pin)
    zero.switch_to_output(value=False)
    zeros.append(zero)
time.sleep(0.02)
keys = keypad.Keys(READ, value_when_pressed=False, pull=True)   # debounced; a switch already on is reported
device = None
for candidate in usb_hid.devices:
    if candidate.usage_page == buttons.USAGE_PAGE and candidate.usage == buttons.USAGE:
        device = candidate


def make_light():
    """The board's own light: the RP2040-Zero's colour LED, or a plain LED (a Raspberry Pi Pico's), or none."""
    try:
        import neopixel_write
        pin = digitalio.DigitalInOut(board.NEOPIXEL)
        pin.direction = digitalio.Direction.OUTPUT
        return lambda rgb: neopixel_write.neopixel_write(pin, bytearray((rgb[1], rgb[0], rgb[2])))
    except (ImportError, AttributeError, ValueError):
        pass
    try:
        led = digitalio.DigitalInOut(board.LED)
        led.direction = digitalio.Direction.OUTPUT

        def plain(rgb):
            led.value = any(rgb)
        return plain
    except (AttributeError, ValueError):
        return lambda rgb: None


light = make_light()
state = buttons.Buttons()
shown = None
while True:
    now = time.monotonic()
    event = keys.events.get()
    while event:
        state.event(event.key_number, event.pressed, now)
        event = keys.events.get()
    report = state.report(now)
    if report is not None and device is not None and supervisor.runtime.usb_connected:
        try:
            device.send_report(bytes((report,)))
            state.sent(report, now)
        except OSError:
            pass            # the box is starting or switched off: sent again shortly
    colour = state.colour()
    if colour != shown:
        light(colour)
        shown = colour
    time.sleep(0.01)
