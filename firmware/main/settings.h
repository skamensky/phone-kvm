/* What the device is told once, over its serial console, and keeps in flash: the Wi-Fi network
 * to join and the token the host software must present. */
#pragma once

#include <stdbool.h>
#include <stddef.h>

void settings_start(void);
/* Read a setting into `value`; false when it was never set. */
bool settings_get(const char *name, char *value, size_t size);
void settings_set(const char *name, const char *value);

/* console.c: the serial console that sets them. */
void console_start(void);
