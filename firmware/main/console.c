/* The serial console (UART0, the board's four-pin header): where the device is given its Wi-Fi
 * network and token, so that neither is ever in the firmware image or the repository.
 *
 *   wifi <network name> <password>     join this network (restarts)
 *   token <token>                      the token the host software must present
 *   status                             what is set, without the secrets
 */
#include "settings.h"

#include <stdio.h>
#include <string.h>

#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static void console(void *arg)
{
    char line[200], value[65];

    for (;;) {
        if (!fgets(line, sizeof(line), stdin)) {
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }
        line[strcspn(line, "\r\n")] = 0;
        char *command = strtok(line, " "), *first = strtok(NULL, " "), *rest = strtok(NULL, "");

        if (!command) {
            continue;
        }
        if (!strcmp(command, "wifi") && first && rest) {
            settings_set("ssid", first);
            settings_set("password", rest);
            printf("ok: joining %s\n", first);
            vTaskDelay(pdMS_TO_TICKS(100));
            esp_restart();
        } else if (!strcmp(command, "token") && first && strlen(first) >= 16) {
            settings_set("token", first);
            printf("ok: token set\n");
        } else if (!strcmp(command, "status")) {
            printf("wifi: %s\n", settings_get("ssid", value, sizeof(value)) ? value : "not set");
            printf("token: %s\n", settings_get("token", value, sizeof(value)) ? "set" : "not set");
        } else {
            printf("commands: wifi <network name> <password> | token <16 or more characters> | status\n");
        }
    }
}

void console_start(void)
{
    xTaskCreate(console, "console", 4096, NULL, 2, NULL);
}
