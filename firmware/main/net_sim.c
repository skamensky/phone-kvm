/* The simulated board's network: the emulator's wired Ethernet, addressed by DHCP. Only the
 * machine running the emulator can reach it, so connections need no token. */
#include "device.h"

#include "esp_eth.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static SemaphoreHandle_t addressed;

static void got_address(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    const ip_event_got_ip_t *event = data;

    ESP_LOGI("phone-kvm", "address " IPSTR, IP2STR(&event->ip_info.ip));
    xSemaphoreGive(addressed);
}

void net_start(void)
{
    eth_mac_config_t mac_config = ETH_MAC_DEFAULT_CONFIG();
    eth_phy_config_t phy_config = ETH_PHY_DEFAULT_CONFIG();
    esp_netif_config_t netif_config = ESP_NETIF_DEFAULT_ETH();
    esp_eth_handle_t ethernet;

    addressed = xSemaphoreCreateBinary();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_ETH_GOT_IP, got_address, NULL));

    phy_config.autonego_timeout_ms = 100;
    esp_eth_config_t config = ETH_DEFAULT_CONFIG(esp_eth_mac_new_openeth(&mac_config),
                                                 esp_eth_phy_new_dp83848(&phy_config));
    ESP_ERROR_CHECK(esp_eth_driver_install(&config, &ethernet));
    ESP_ERROR_CHECK(esp_netif_attach(esp_netif_new(&netif_config), esp_eth_new_netif_glue(ethernet)));
    ESP_ERROR_CHECK(esp_eth_start(ethernet));
    xSemaphoreTake(addressed, portMAX_DELAY);
}

bool net_needs_token(void)
{
    return false;
}

bool net_token_matches(const uint8_t *token, size_t length)
{
    return true;
}
