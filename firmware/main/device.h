/* The parts of the phone-kvm device. The simulated board differs from the real one only in
 * the camera behind `camera_*` and the network behind `net_*`. */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define INTERFACES 4 /* keyboard, touch, mouse, consumer: the order of the USB interfaces */

/* usb.c: the USB HID device the phone sees. */
void usb_start(void);
bool usb_mounted(void);
/* Send one input report; false if the phone did not take it within a second. */
bool usb_send(unsigned interface, const uint8_t *report, size_t length);
/* Wait until the phone has taken every report sent. */
bool usb_flush(void);
uint8_t usb_keyboard_leds(void);

/* camera_*.c: one JPEG picture at a time. */
bool camera_start(void);
bool camera_capture(size_t *length);
size_t camera_read(uint8_t *buffer, size_t size);
void camera_done(void);

/* net_*.c: bring the network up; returns once the device has an address. */
void net_start(void);
/* Whether a connection must authenticate, and whether `token` is the device's. */
bool net_needs_token(void);
bool net_token_matches(const uint8_t *token, size_t length);

/* server.c: the TCP service the host software talks to. */
void server_run(void);
