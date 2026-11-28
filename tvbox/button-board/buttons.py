"""The desk device's button board (tvbox/button-board/README.md): what its listen button, power button and mute
switch tell the TV box, as one byte of a USB HID report.

Nothing here touches the hardware: code.py reads the pins and sends the reports, boot.py presents the board to
the box, and the tests on a computer (core/tests/test_tvbox.py) run this file as it is.

Each control is a usage Linux already knows, so the box needs no driver and no settings:
  - the listen button is Play/Pause (Consumer page): KEY_PLAYPAUSE, one of the box's button keys;
  - the power button is Power (Consumer page): KEY_POWER, which systemd-logind answers by switching the box off
    cleanly; it is sent only once the button has been held for POWER_HOLD, so a knock does nothing;
  - the mute switch is Phone Mute (Telephony page): KEY_MICMUTE, held down while the switch is on, the same key
    as the Raspberry Pi's mute switch.
None of them is a keyboard key, so Linux does not repeat them while they are held (a switch can be on for days).
"""

LISTEN, POWER, MUTE = 0, 1, 2           # the controls, in the order code.py lists their pins
LISTEN_BIT, POWER_BIT, MUTE_BIT = 1, 2, 4
POWER_HOLD = 1.0      # seconds the power button is held before the box is told
RESEND_EVERY = 1.0    # the state is sent again this often, so a box that has just started learns the switch
REPORT_ID = 1
USAGE_PAGE, USAGE = 0x0C, 0x01          # Consumer Control: the collection the report belongs to

REPORT_DESCRIPTOR = bytes((
    0x05, 0x0C,         # Usage Page (Consumer)
    0x09, 0x01,         # Usage (Consumer Control)
    0xA1, 0x01,         # Collection (Application)
    0x85, REPORT_ID,    #   Report ID (1)
    0x15, 0x00,         #   Logical Minimum (0)
    0x25, 0x01,         #   Logical Maximum (1)
    0x75, 0x01,         #   Report Size (1 bit)
    0x95, 0x02,         #   Report Count (2)
    0x09, 0xCD,         #   Usage (Play/Pause): bit 0, the listen button
    0x09, 0x30,         #   Usage (Power): bit 1, the power button
    0x81, 0x02,         #   Input (Data, Variable, Absolute)
    0x05, 0x0B,         #   Usage Page (Telephony)
    0x95, 0x01,         #   Report Count (1)
    0x09, 0x2F,         #   Usage (Phone Mute): bit 2, the mute switch
    0x81, 0x02,         #   Input (Data, Variable, Absolute)
    0x95, 0x05,         #   Report Count (5)
    0x81, 0x03,         #   Input (Constant): the rest of the byte
    0xC0,               # End Collection
))

# The light on the board (red, green, blue): what it is doing, seen without the screen.
DARK = (0, 0, 0)
MUTED = (24, 0, 0)        # the mute switch is on
LISTENING = (0, 0, 32)    # the listen button is down
POWERING = (32, 12, 0)    # the power button is held: switching off once it has been held long enough


class Buttons:
    """The board's state. event() for each press and release, then report() says what to send (or None), and
    sent() once it has gone."""

    def __init__(self):
        self.state = 0              # the bits the box should see
        self.power_since = None     # when the power button went down, while it is held
        self.last = None            # the last report sent
        self.last_at = None         # and when

    def _set(self, bit, on):
        self.state = (self.state | bit) if on else (self.state & ~bit)

    def event(self, key, down, now):
        if key == LISTEN:
            self._set(LISTEN_BIT, down)
        elif key == MUTE:
            self._set(MUTE_BIT, down)
        elif key == POWER:
            self.power_since = now if down else None
            if not down:
                self._set(POWER_BIT, False)

    def report(self, now):
        """The report to send now: when something changed, and again every RESEND_EVERY."""
        if self.power_since is not None and now - self.power_since >= POWER_HOLD:
            self._set(POWER_BIT, True)
        if self.state != self.last or self.last_at is None or now - self.last_at >= RESEND_EVERY:
            return self.state
        return None

    def sent(self, report, now):
        self.last, self.last_at = report, now

    def colour(self):
        if self.power_since is not None:
            return POWERING
        if self.state & LISTEN_BIT:
            return LISTENING
        if self.state & MUTE_BIT:
            return MUTED
        return DARK
