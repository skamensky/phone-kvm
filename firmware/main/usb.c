/* The USB device: four HID interfaces, none with report IDs.
 *
 *   0 keyboard  boot-protocol keyboard, 8-byte reports
 *   1 touch     five-finger touchscreen digitizer, 31-byte reports; Android shows no cursor
 *   2 mouse     relative mouse with wheel, 4-byte reports; Android shows a cursor
 *   3 consumer  one 16-bit consumer-page usage: Home, Back, power, volume, media
 */
#include "device.h"

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "tinyusb.h"
#include "class/hid/hid_device.h"

#define CONTACTS 5
#define TOUCH_MAX 32767

static const uint8_t keyboard_report[] = {
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01,              /* Generic Desktop / Keyboard, Application */
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7,              /* modifiers LeftCtrl..RightGUI */
    0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x03,              /* reserved byte */
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01, 0x29, 0x05, 0x91, 0x02,  /* LEDs (output) */
    0x95, 0x01, 0x75, 0x03, 0x91, 0x03,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,  /* six key slots */
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00,
    0xC0,
};

#define FINGER \
    0x09, 0x22, 0xA1, 0x02,                          /* Finger, Logical */ \
    0x09, 0x42, 0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x01, 0x81, 0x02,  /* Tip Switch */ \
    0x95, 0x07, 0x81, 0x03,                          /* padding */ \
    0x09, 0x51, 0x25, CONTACTS - 1, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,      /* Contact ID */ \
    0x05, 0x01, 0x26, TOUCH_MAX & 0xFF, TOUCH_MAX >> 8, 0x75, 0x10, 0x95, 0x01, \
    0x09, 0x30, 0x81, 0x02,                          /* X */ \
    0x09, 0x31, 0x81, 0x02,                          /* Y */ \
    0x05, 0x0D, \
    0xC0

static const uint8_t touch_report[] = {
    0x05, 0x0D, 0x09, 0x04, 0xA1, 0x01,              /* Digitizer / Touch Screen, Application */
    FINGER, FINGER, FINGER, FINGER, FINGER,
    0x09, 0x54, 0x25, CONTACTS, 0x75, 0x08, 0x95, 0x01, 0x81, 0x02,  /* Contact Count */
    0x09, 0x55, 0x25, CONTACTS, 0xB1, 0x02,          /* Contact Count Maximum (feature) */
    0xC0,
};

static const uint8_t mouse_report[] = {
    0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x09, 0x01, 0xA1, 0x00,  /* Mouse / Pointer */
    0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x15, 0x00, 0x25, 0x01,
    0x95, 0x03, 0x75, 0x01, 0x81, 0x02,              /* three buttons */
    0x95, 0x01, 0x75, 0x05, 0x81, 0x03,
    /* X and Y are 16 bits wide so that one report carries a whole move: Android scales a
       report that follows a pause by a constant, and a run of reports by their speed. */
    0x05, 0x01, 0x09, 0x30, 0x09, 0x31,
    0x16, 0x01, 0x80, 0x26, 0xFF, 0x7F, 0x75, 0x10, 0x95, 0x02, 0x81, 0x06,
    0x09, 0x38, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x01, 0x81, 0x06,  /* wheel */
    0xC0, 0xC0,
};

static const uint8_t consumer_report[] = {
    0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01,              /* Consumer Control */
    0x15, 0x00, 0x26, 0xFF, 0x03, 0x19, 0x00, 0x2A, 0xFF, 0x03,
    0x75, 0x10, 0x95, 0x01, 0x81, 0x00,              /* one 16-bit usage */
    0xC0,
};

static const uint8_t *const reports[INTERFACES] = { keyboard_report, touch_report, mouse_report, consumer_report };

static const tusb_desc_device_t device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = 0x0200,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = 0x1209,
    .idProduct = 0x0001,
    .bcdDevice = 0x0100,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

static const char *strings[] = { "\x09\x04", "phone-kvm", "phone-kvm keyboard, touchscreen and mouse", "0001" };

#define CONFIGURATION_LENGTH (TUD_CONFIG_DESC_LEN + INTERFACES * TUD_HID_DESC_LEN)
static const uint8_t configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, INTERFACES, 0, CONFIGURATION_LENGTH, 0, 100),
    TUD_HID_DESCRIPTOR(0, 0, HID_ITF_PROTOCOL_KEYBOARD, sizeof(keyboard_report), 0x81, 8, 1),
    TUD_HID_DESCRIPTOR(1, 0, HID_ITF_PROTOCOL_NONE, sizeof(touch_report), 0x82, 32, 1),
    TUD_HID_DESCRIPTOR(2, 0, HID_ITF_PROTOCOL_NONE, sizeof(mouse_report), 0x83, 8, 1),
    TUD_HID_DESCRIPTOR(3, 0, HID_ITF_PROTOCOL_NONE, sizeof(consumer_report), 0x84, 8, 1),
};

static uint8_t leds;

const uint8_t *tud_hid_descriptor_report_cb(uint8_t instance)
{
    return reports[instance];
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t type, uint8_t *buffer,
                               uint16_t length)
{
    /* The touchscreen's one feature report: how many fingers it tracks. */
    if (instance == 1 && type == HID_REPORT_TYPE_FEATURE && length >= 1) {
        buffer[0] = CONTACTS;
        return 1;
    }
    return 0;
}

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t type, const uint8_t *buffer,
                           uint16_t length)
{
    if (instance == 0 && type == HID_REPORT_TYPE_OUTPUT && length >= 1) {
        leds = buffer[0];
    }
}

static bool wait_ready(unsigned interface)
{
    int64_t deadline = esp_timer_get_time() + 1000000;

    while (!tud_hid_n_ready(interface)) {
        if (esp_timer_get_time() > deadline) {
            return false;
        }
        vTaskDelay(1);
    }
    return true;
}

void usb_start(void)
{
    const tinyusb_config_t config = {
        .device_descriptor = &device,
        .string_descriptor = strings,
        .string_descriptor_count = sizeof(strings) / sizeof(strings[0]),
        .configuration_descriptor = configuration,
    };

    ESP_ERROR_CHECK(tinyusb_driver_install(&config));
}

bool usb_mounted(void)
{
    return tud_mounted();
}

bool usb_send(unsigned interface, const uint8_t *report, size_t length)
{
    return interface < INTERFACES && wait_ready(interface) && tud_hid_n_report(interface, 0, report, length);
}

bool usb_flush(void)
{
    for (unsigned interface = 0; interface < INTERFACES; interface++) {
        if (!wait_ready(interface)) {
            return false;
        }
    }
    return true;
}

uint8_t usb_keyboard_leds(void)
{
    return leds;
}
