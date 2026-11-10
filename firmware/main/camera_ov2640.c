/* The board's camera: an OV2640 on the DVP bus, 1600x1200 JPEG into PSRAM. */
#include "device.h"

#include <string.h>

#include "esp_camera.h"

static camera_fb_t *frame;
static size_t at;

bool camera_start(void)
{
    const camera_config_t config = {
        .pin_pwdn = -1, .pin_reset = -1,
        .pin_xclk = 15, .pin_sccb_sda = 4, .pin_sccb_scl = 5,
        .pin_d7 = 16, .pin_d6 = 17, .pin_d5 = 18, .pin_d4 = 12,
        .pin_d3 = 10, .pin_d2 = 8, .pin_d1 = 9, .pin_d0 = 11,
        .pin_vsync = 6, .pin_href = 7, .pin_pclk = 13,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0, .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = FRAMESIZE_UXGA,
        .jpeg_quality = 8,
        .fb_count = 1,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    };

    return esp_camera_init(&config) == ESP_OK;
}

bool camera_capture(size_t *length)
{
    /* The buffered frame was taken when the last capture ended: drop it and take one now. */
    esp_camera_fb_return(esp_camera_fb_get());
    frame = esp_camera_fb_get();
    if (!frame) {
        return false;
    }
    at = 0;
    *length = frame->len;
    return true;
}

size_t camera_read(uint8_t *buffer, size_t size)
{
    size_t count = size < frame->len - at ? size : frame->len - at;

    memcpy(buffer, frame->buf + at, count);
    at += count;
    return count;
}

void camera_done(void)
{
    if (frame) {
        esp_camera_fb_return(frame);
        frame = NULL;
    }
}
