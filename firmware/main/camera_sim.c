/* The simulated board's camera: a device in the emulator that returns the picture the rig's
 * camera is taking of the phone at that moment (sim/qemu/esp32s3_simcam.c). */
#include "device.h"

#include <string.h>

#define SIMCAM ((volatile uint32_t *) 0x600d1000)
enum { ID, CAPTURE, LENGTH, DATA };

static size_t remaining;

bool camera_start(void)
{
    return SIMCAM[ID] == 0x4d41434b;
}

bool camera_capture(size_t *length)
{
    SIMCAM[CAPTURE] = 1;
    *length = remaining = SIMCAM[LENGTH];
    return remaining != 0;
}

size_t camera_read(uint8_t *buffer, size_t size)
{
    size_t count = size < remaining ? size : remaining;

    for (size_t at = 0; at < count; at += 4) {
        uint32_t word = SIMCAM[DATA];
        memcpy(buffer + at, &word, count - at < 4 ? count - at : 4);
    }
    remaining -= count;
    return count;
}

void camera_done(void)
{
    remaining = 0;
}
