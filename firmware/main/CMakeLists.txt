set(srcs "main.c" "usb.c" "server.c")
if(CONFIG_PHONE_KVM_SIM)
    list(APPEND srcs "camera_sim.c" "net_sim.c")
else()
    list(APPEND srcs "camera_ov2640.c" "net_wifi.c" "settings.c" "console.c")
endif()
idf_component_register(SRCS ${srcs} INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_netif esp_eth esp_wifi esp_event nvs_flash esp_timer lwip esp_driver_gpio)
