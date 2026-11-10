#include "settings.h"

#include "esp_err.h"
#include "nvs.h"
#include "nvs_flash.h"

static nvs_handle_t store;

void settings_start(void)
{
    esp_err_t result = nvs_flash_init();

    if (result == ESP_ERR_NVS_NO_FREE_PAGES || result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        result = nvs_flash_init();
    }
    ESP_ERROR_CHECK(result);
    ESP_ERROR_CHECK(nvs_open("phone-kvm", NVS_READWRITE, &store));
}

bool settings_get(const char *name, char *value, size_t size)
{
    return nvs_get_str(store, name, value, &size) == ESP_OK;
}

void settings_set(const char *name, const char *value)
{
    ESP_ERROR_CHECK(nvs_set_str(store, name, value));
    ESP_ERROR_CHECK(nvs_commit(store));
}
