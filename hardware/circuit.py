"""The circuit: every part, its LCSC number and footprint, and every net.

tools/generate.py builds the KiCad schematic and PCB from this file; edit the design here.
Pins are named as in the KiCad symbol (by pin name, or by number where names repeat).

The board is a USB device on the phone's USB-C port (keyboard, touchscreen, mouse) powered by
the phone, with a DVP camera on a 24-pin flex looking at the phone's screen.
"""

R0603 = "Resistor_SMD:R_0603_1608Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
SOT235 = "Package_TO_SOT_SMD:SOT-23-5"
BUTTON = ("Switch:SW_Push", "TS-1187A-B-A-B", "Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A", "C318884")

# ref: (symbol, value, footprint, LCSC part)
PARTS = {
    # 16 MB flash, 8 MB octal PSRAM. GPIO35-37 belong to the PSRAM and stay open.
    "U1": ("RF_Module:ESP32-S3-WROOM-1", "ESP32-S3-WROOM-1-N16R8", "phone-kvm:ESP32-S3-WROOM-1", "C2913202"),

    # USB-C device port: the phone is the host and supplies VBUS.
    "J1": ("Connector:USB_C_Receptacle_USB2.0_16P", "TYPE-C-31-M-12",
           "Connector_USB:USB_C_Receptacle_HRO_TYPE-C-31-M-12", "C165948"),
    "R3": ("Device:R", "5.1k", R0603, "C23186"),  # CC1 Rd: "a device is attached"
    "R4": ("Device:R", "5.1k", R0603, "C23186"),  # CC2 Rd
    "U2": ("Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6", "C7519"),
    # 0.75 A hold, 1.5 A trip, 6 V. A 0.5 A part derates to 0.42 A at 40 C, below the Wi-Fi peaks.
    "F1": ("Device:Polyfuse", "nSMD075", "Fuse:Fuse_1206_3216Metric", "C70078"),

    # 3.3 V: LDL1117S33R, 1.2 A, 350 mV typical dropout at full load, stable with ceramics, in
    # SOT-223 because it dissipates up to 0.6 W. The 1117 pinout: GND, OUT (and tab), IN.
    "U3": ("Regulator_Linear:AMS1117-3.3", "LDL1117S33R", "Package_TO_SOT_SMD:SOT-223-3_TabPin2", "C435835"),
    "C1": ("Device:C", "4.7u", C0603, "C19666"),   # LDO input; USB allows 10 uF across VBUS
    "C2": ("Device:C", "100n", C0603, "C14663"),   # LDO input
    "C3": ("Device:C", "10u", C0603, "C19702"),    # LDO output (needs >= 4.7 uF)
    "C4": ("Device:C", "22u", "Capacitor_SMD:C_0805_2012Metric", "C45783"),  # bulk at the module
    "C5": ("Device:C", "100n", C0603, "C14663"),   # module 3V3 pin
    "C15": ("Device:C", "10u", C0603, "C19702"),   # module 3V3 pin

    # Reset and boot.
    "R1": ("Device:R", "10k", R0603, "C25804"),
    "C6": ("Device:C", "1u", C0603, "C15849"),
    "SW1": BUTTON,  # EN (reset)
    "SW2": BUTTON,  # BOOT (GPIO0)

    # Console and flashing: the USB port belongs to the phone. Not assembled.
    "J2": ("Connector_Generic:Conn_01x06", "UART", "Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical", None),

    # Status LED on GPIO48, lit when the pin is high.
    "R2": ("Device:R", "1k", R0603, "C21190"),
    "D1": ("Device:LED", "KT-0603R", "LED_SMD:LED_0603_1608Metric", "C2286"),

    # Camera: 24-pin 0.5 mm flex, the OV2640/OV5640 DVP module pinout (see CAMERA below).
    # TOP-contact connector: the flex goes in with its contacts up, so the lens looks out of the
    # board's bottom side. The bottom-contact AFC07-S24FCA-00 fits the same footprint but would
    # reverse the pin order; do not substitute it.
    "J3": ("Connector_Generic_MountingPin:Conn_01x24_MountingPin", "AFC07-S24ECA-00",
           "Connector_FFC-FPC:Jushuo_AFC07-S24FCA-00_1x24-1MP_P0.50_Horizontal", "C262643"),
    "U4": ("Regulator_Linear:ME6211C28M5", "ME6211C28M5G-N", SOT235, "C53099"),   # AVDD 2.8 V
    "U5": ("Regulator_Linear:ME6211C12M5", "ME6211C12M5G-N", SOT235, "C236672"),  # DVDD 1.2 V
    "C7": ("Device:C", "1u", C0603, "C15849"),     # 2.8 V LDO input
    "C8": ("Device:C", "4.7u", C0603, "C19666"),   # 2.8 V LDO output
    "C9": ("Device:C", "100n", C0603, "C14663"),   # AVDD at the connector
    "C10": ("Device:C", "1u", C0603, "C15849"),    # 1.2 V LDO input
    "C11": ("Device:C", "1u", C0603, "C15849"),    # 1.2 V LDO output
    "C12": ("Device:C", "1u", C0603, "C15849"),    # DVDD at the connector
    "C13": ("Device:C", "100n", C0603, "C14663"),  # DOVDD at the connector
    "R5": ("Device:R", "4.7k", R0603, "C23162"),   # SIO_D pull-up
    "R6": ("Device:R", "4.7k", R0603, "C23162"),   # SIO_C pull-up
    "R7": ("Device:R", "10k", R0603, "C25804"),    # RESET pull-up
    "C14": ("Device:C", "100n", C0603, "C14663"),  # RESET delay
    "R8": ("Device:R", "10k", R0603, "C25804"),    # PWDN pull-down: camera always on
    # Solder jumpers (copper only, nothing to assemble) for the sensors that differ from the OV2640:
    # JP1 closed: 1.2 V on DVDD (OV2640). Cut it for an OV5640, which makes its own 1.5 V core
    #   supply and must not be fed 1.2 V.
    # JP2 1-2 closed: DOVDD = 3.3 V (OV2640). Cut and bridge 2-3 for 2.8 V (OV5640 allows 3.0 V at most).
    # JP3 open: pin 1 is STROBE, an output, on the OV2640. Close it only for an OV5640 module
    #   whose pin 1 is the autofocus motor supply.
    "JP1": ("Jumper:SolderJumper_2_Bridged", "DVDD", "Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm", None),
    "JP2": ("Jumper:SolderJumper_3_Bridged12", "DOVDD", "Jumper:SolderJumper-3_P1.3mm_Bridged12_RoundedPad1.0x1.5mm", None),
    "JP3": ("Jumper:SolderJumper_2_Open", "AF-VDD", "Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm", None),

    "#FLG1": ("power:PWR_FLAG", "PWR_FLAG", None, None),
    "#FLG2": ("power:PWR_FLAG", "PWR_FLAG", None, None),
    "#FLG3": ("power:PWR_FLAG", "PWR_FLAG", None, None),
}

# Camera connector pin -> (signal, ESP32-S3 GPIO or None). Pin numbers are the camera module's
# own (its drawing: HDF3M-811 OV2640 module), which is also how the ESP32-S3-EYE numbers J4; the
# ESP32-CAM schematic numbers the same order from the other end. The GPIO map is the
# ESP32-S3-EYE / Freenove ESP32-S3 CAM one.
CAMERA = {
    3: ("SIOD", 4), 5: ("SIOC", 5), 7: ("VSYNC", 6), 9: ("HREF", 7), 12: ("Y9", 16), 13: ("XCLK", 15),
    14: ("Y8", 17), 16: ("Y7", 18), 17: ("PCLK", 13), 18: ("Y6", 12), 19: ("Y2", 11), 20: ("Y5", 10),
    21: ("Y3", 9), 22: ("Y4", 8),
}

NETS = {
    "VBUS": ["J1.VBUS", "F1.1", "U2.VBUS", "#FLG1.1"],
    "5V": ["F1.2", "U3.VI", "C1.1", "C2.1", "#FLG2.1"],
    "3V3": ["U3.VO", "C3.1", "C4.1", "C5.1", "C15.1", "U1.3V3", "R1.1", "J2.1",
            "U4.V_{IN}", "U4.CE", "C7.1", "U5.V_{IN}", "U5.CE", "C10.1", "JP2.A"],
    "GND": ["J1.GND", "J1.SHIELD", "R3.2", "R4.2", "U2.GND", "U3.GND", "C1.2", "C2.2", "C3.2", "C4.2",
            "C5.2", "C15.2", "U1.GND", "C6.2", "SW1.2", "SW2.2", "J2.2", "D1.K",
            "J3.2", "J3.15", "J3.MP", "U4.V_{SS}", "U5.V_{SS}", "C7.2", "C8.2", "C9.2", "C10.2", "C11.2",
            "C12.2", "C13.2", "C14.2", "R8.2", "#FLG3.1"],
    "CC1": ["J1.CC1", "R3.1"],
    "CC2": ["J1.CC2", "R4.1"],
    "USB_DP": ["J1.D+", "U2.I/O1", "U1.USB_D+"],   # GPIO20
    "USB_DM": ["J1.D-", "U2.I/O2", "U1.USB_D-"],   # GPIO19
    "EN": ["U1.EN", "R1.2", "C6.1", "SW1.1", "J2.5"],
    "BOOT": ["U1.IO0", "SW2.1", "J2.6"],
    "TXD0": ["U1.TXD0", "J2.3"],                   # GPIO43
    "RXD0": ["U1.RXD0", "J2.4"],                   # GPIO44
    "LED": ["U1.IO48", "R2.1"],
    "LED_A": ["R2.2", "D1.A"],
    "CAM_2V8": ["U4.V_{OUT}", "C8.1", "C9.1", "J3.4", "JP2.B", "JP3.A"],
    "CAM_AF": ["JP3.B", "J3.1"],
    "CAM_1V2": ["U5.V_{OUT}", "C11.1", "JP1.A"],
    "CAM_DVDD": ["JP1.B", "C12.1", "J3.10"],
    "CAM_DOVDD": ["JP2.C", "C13.1", "J3.11", "R5.1", "R6.1", "R7.1"],
    "CAM_RESET": ["J3.6", "R7.2", "C14.1"],
    "CAM_PWDN": ["J3.8", "R8.1"],
}
for pin, (signal, gpio) in CAMERA.items():
    NETS[f"CAM_{signal}"] = [f"J3.{pin}", f"U1.IO{gpio}"]
NETS["CAM_SIOD"].append("R5.2")
NETS["CAM_SIOC"].append("R6.2")

# Footprints the assembly house leaves empty.
NOT_ASSEMBLED = {"J2"}
# Footprints that are only a copper pattern: no part to buy or place.
COPPER_ONLY = {"JP1", "JP2", "JP3"}

# Pins deliberately left open: the PSRAM's GPIO35-37, the unused GPIOs (GPIO3, 45 and 46 are
# strapping pins whose internal defaults are the ones wanted), the USB-C sideband pins, the
# regulators' unused pin and the camera's two low data bits (8-bit capture uses Y2..Y9).
NO_CONNECT = (["U1.IO35", "U1.IO36", "U1.IO37"]
              + [f"U1.IO{n}" for n in (1, 2, 3, 14, 21, 38, 39, 40, 41, 42, 45, 46, 47)]
              + ["J1.SBU1", "J1.SBU2", "U4.NC", "U5.NC", "J3.23", "J3.24"])
