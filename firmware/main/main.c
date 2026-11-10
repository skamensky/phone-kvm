#include "device.h"
#include "esp_log.h"

void app_main(void)
{
    usb_start();
    if (!camera_start()) {
        ESP_LOGE("phone-kvm", "no camera; captures will fail");
    }
    net_start();
    server_run();
}
