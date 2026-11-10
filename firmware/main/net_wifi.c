/* The board's network: Wi-Fi, joining the network set over the serial console. Anything on
 * that network can reach the device, so a connection must first present the device's token. */
#include "device.h"
#include "settings.h"

#include <string.h>

#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static SemaphoreHandle_t addressed;

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && (id == WIFI_EVENT_STA_START || id == WIFI_EVENT_STA_DISCONNECTED)) {
        gpio_set_level(CONFIG_PHONE_KVM_LED_GPIO, 0);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;

        ESP_LOGI("phone-kvm", "address " IPSTR, IP2STR(&event->ip_info.ip));
        gpio_set_level(CONFIG_PHONE_KVM_LED_GPIO, 1);
        xSemaphoreGive(addressed);
    }
}

void net_start(void)
{
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    wifi_config_t wifi = { 0 };

    settings_start();
    console_start();
    gpio_set_direction(CONFIG_PHONE_KVM_LED_GPIO, GPIO_MODE_OUTPUT);

    while (!settings_get("ssid", (char *) wifi.sta.ssid, sizeof(wifi.sta.ssid))
           || !settings_get("password", (char *) wifi.sta.password, sizeof(wifi.sta.password))) {
        ESP_LOGW("phone-kvm", "no Wi-Fi network set; on this console: wifi <network name> <password>");
        vTaskDelay(pdMS_TO_TICKS(5000));
    }

    addressed = xSemaphoreCreateBinary();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    /* Power save delays packets by up to a beacon interval, which shows as input lag. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_start());
    xSemaphoreTake(addressed, portMAX_DELAY);
}

bool net_needs_token(void)
{
    return true;
}

bool net_token_matches(const uint8_t *token, size_t length)
{
    char expected[65];
    uint8_t difference = 0;

    if (!settings_get("token", expected, sizeof(expected)) || strlen(expected) != length) {
        return false;
    }
    for (size_t i = 0; i < length; i++) {
        difference |= token[i] ^ (uint8_t) expected[i];
    }
    return difference == 0;
}
