/* The device's TCP service. One host at a time.
 *
 * Request:  operation (1 byte), payload length (4 bytes, little endian), payload.
 * Reply:    0 for done or 1 for failed (1 byte), payload length (4 bytes), payload; a failure's
 *           payload is the reason, as text.
 *
 *   'A'  authenticate: the payload is the device's token. Required first on the real board.
 *   'H'  input: a sequence of HID reports, each {wait before it in ms (2 bytes), interface
 *        (1 byte), report length (1 byte), report}. The device paces them itself, so gesture
 *        timing does not depend on the network, and replies once the phone has taken the last.
 *   'C'  capture: replies with one JPEG picture from the camera.
 *   'S'  status: replies with JSON.
 */
#include "device.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "sdkconfig.h"

#define MAX_PAYLOAD 65536
#ifdef CONFIG_PHONE_KVM_SIM
#define SIMULATED "true"
#else
#define SIMULATED "false"
#endif

static bool receive(int client, void *buffer, size_t length)
{
    for (size_t at = 0; at < length;) {
        int count = recv(client, (char *) buffer + at, length - at, 0);
        if (count <= 0) {
            return false;
        }
        at += count;
    }
    return true;
}

static bool transmit(int client, const void *buffer, size_t length)
{
    for (size_t at = 0; at < length;) {
        int count = send(client, (const char *) buffer + at, length - at, 0);
        if (count <= 0) {
            return false;
        }
        at += count;
    }
    return true;
}

static bool reply_head(int client, bool failed, uint32_t length)
{
    uint8_t head[5] = { failed, length, length >> 8, length >> 16, length >> 24 };

    return transmit(client, head, sizeof(head));
}

static bool done(int client, const void *payload, uint32_t length)
{
    return reply_head(client, false, length) && transmit(client, payload, length);
}

static bool failed(int client, const char *reason)
{
    return reply_head(client, true, strlen(reason)) && transmit(client, reason, strlen(reason));
}

static bool input(int client, const uint8_t *payload, size_t length)
{
    int64_t due = esp_timer_get_time();

    if (!usb_mounted()) {
        return failed(client, "the device is not plugged into a phone");
    }
    for (size_t at = 0; at < length;) {
        if (length - at < 4 || length - at - 4 < payload[at + 3]) {
            return failed(client, "malformed input");
        }
        due += (payload[at] | payload[at + 1] << 8) * 1000;
        while (esp_timer_get_time() < due) {
            vTaskDelay(1);
        }
        if (!usb_send(payload[at + 2], payload + at + 4, payload[at + 3])) {
            return failed(client, "the phone is not taking input");
        }
        at += 4 + payload[at + 3];
    }
    if (!usb_flush()) {
        return failed(client, "the phone is not taking input");
    }
    return done(client, NULL, 0);
}

static bool capture(int client)
{
    static uint8_t chunk[2048];
    size_t length;
    bool sent;

    if (!camera_capture(&length)) {
        camera_done();
        return failed(client, "the camera returned no picture");
    }
    sent = reply_head(client, false, length);
    while (sent && length) {
        size_t count = camera_read(chunk, sizeof(chunk));
        sent = count && transmit(client, chunk, count);
        length -= count;
    }
    camera_done();
    return sent;
}

static bool status(int client)
{
    char text[160];
    int length = snprintf(text, sizeof(text),
                          "{\"firmware\": \"phone-kvm 1\", \"simulated\": %s, \"plugged\": %s, \"leds\": %u}",
                          SIMULATED,
                          usb_mounted() ? "true" : "false", usb_keyboard_leds());

    return done(client, text, length);
}

static void serve(int client)
{
    static uint8_t payload[MAX_PAYLOAD];
    bool trusted = !net_needs_token();
    uint8_t head[5];

    while (receive(client, head, sizeof(head))) {
        uint32_t length = head[1] | head[2] << 8 | head[3] << 16 | (uint32_t) head[4] << 24;
        bool alive;

        if (length > MAX_PAYLOAD || !receive(client, payload, length)) {
            return;
        }
        if (head[0] == 'A') {
            trusted = trusted || net_token_matches(payload, length);
            alive = trusted ? done(client, NULL, 0) : failed(client, "wrong token");
        } else if (!trusted) {
            failed(client, "authenticate first");
            return;
        } else if (head[0] == 'H') {
            alive = input(client, payload, length);
        } else if (head[0] == 'C') {
            alive = capture(client);
        } else if (head[0] == 'S') {
            alive = status(client);
        } else {
            alive = failed(client, "unknown operation");
        }
        if (!alive) {
            return;
        }
    }
}

void server_run(void)
{
    struct sockaddr_in address = { .sin_family = AF_INET, .sin_port = htons(CONFIG_PHONE_KVM_PORT) };
    int listener = socket(AF_INET, SOCK_STREAM, 0), yes = 1;

    setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    if (bind(listener, (struct sockaddr *) &address, sizeof(address)) || listen(listener, 1)) {
        ESP_LOGE("phone-kvm", "cannot listen on port %d", CONFIG_PHONE_KVM_PORT);
        return;
    }
    ESP_LOGI("phone-kvm", "listening on port %d", CONFIG_PHONE_KVM_PORT);
    for (;;) {
        int client = accept(listener, NULL, NULL);

        if (client < 0) {
            continue;
        }
        setsockopt(client, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
        serve(client);
        close(client);
    }
}
