# The button board's USB set-up (tvbox/button-board/README.md). CircuitPython runs this when the board is
# plugged in, before code.py.
#
# To the TV box the board is a button box and nothing else: no USB drive and no serial port (which the box's
# software could mistake for a modem). Hold the listen button while plugging the board into a computer to have
# its CIRCUITPY drive back, to change these files.
import time

import board
import digitalio
import storage
import supervisor
import usb_cdc
import usb_hid

import buttons

# The listen button joins GP0 to GP1, and GP1 is held at 0 V (code.py does the same for every control).
zero = digitalio.DigitalInOut(board.GP1)
zero.switch_to_output(value=False)
listen = digitalio.DigitalInOut(board.GP0)
listen.switch_to_input(pull=digitalio.Pull.UP)
time.sleep(0.02)
changing_files = not listen.value
listen.deinit()
zero.deinit()

try:
    # The box's input devices are then called "Companion Button board ..." (the support report lists them).
    supervisor.set_usb_identification(manufacturer="Companion", product="Button board")
except (AttributeError, NotImplementedError, RuntimeError):
    pass

if not changing_files:
    storage.disable_usb_drive()
    usb_cdc.disable()

usb_hid.enable((usb_hid.Device(report_descriptor=buttons.REPORT_DESCRIPTOR, usage_page=buttons.USAGE_PAGE,
                               usage=buttons.USAGE, report_ids=(buttons.REPORT_ID,), in_report_lengths=(1,),
                               out_report_lengths=(0,)),))
