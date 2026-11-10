/*
 * A camera for the simulated phone-kvm board: the picture is whatever JPEG file the `file`
 * property names at the moment of capture. The real board has an OV2640 on the DVP bus; this
 * stands in for the sensor and its bus, so the firmware above the camera driver is the same.
 *
 *   0x00  ID       reads 0x4d41434b ("KCAM")
 *   0x04  CAPTURE  write anything: load the file
 *   0x08  LENGTH   the capture's size in bytes; 0 when there was no file
 *   0x0c  DATA     the capture, four bytes per read
 *
 * This program is free software; you can redistribute it and/or modify it under the terms of
 * the GNU General Public License version 2 or (at your option) any later version.
 */
#include "qemu/osdep.h"
#include "qemu/module.h"
#include "hw/hw.h"
#include "hw/sysbus.h"
#include "hw/qdev-properties.h"

#define TYPE_ESP32S3_SIMCAM "esp32s3.simcam"
OBJECT_DECLARE_SIMPLE_TYPE(Esp32s3SimcamState, ESP32S3_SIMCAM)

struct Esp32s3SimcamState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    char *file;
    gchar *capture;
    gsize length, at;
};

static uint64_t simcam_read(void *opaque, hwaddr addr, unsigned size)
{
    Esp32s3SimcamState *s = opaque;
    uint32_t value = 0;

    switch (addr) {
    case 0x00:
        return 0x4d41434b;
    case 0x08:
        return s->length;
    case 0x0c:
        if (s->at < s->length) {
            memcpy(&value, s->capture + s->at, MIN(4, s->length - s->at));
            s->at += 4;
        }
        return value;
    }
    return 0;
}

static void simcam_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    Esp32s3SimcamState *s = opaque;

    if (addr == 0x04) {
        g_free(s->capture);
        s->capture = NULL;
        s->length = s->at = 0;
        if (s->file) {
            g_file_get_contents(s->file, &s->capture, &s->length, NULL);
        }
    }
}

static const MemoryRegionOps simcam_ops = {
    .read = simcam_read,
    .write = simcam_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void simcam_init(Object *obj)
{
    Esp32s3SimcamState *s = ESP32S3_SIMCAM(obj);

    memory_region_init_io(&s->iomem, obj, &simcam_ops, s, TYPE_ESP32S3_SIMCAM, 0x10);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
}

static Property simcam_properties[] = {
    DEFINE_PROP_STRING("file", Esp32s3SimcamState, file),
    DEFINE_PROP_END_OF_LIST(),
};

static void simcam_class_init(ObjectClass *klass, void *data)
{
    device_class_set_props(DEVICE_CLASS(klass), simcam_properties);
}

static const TypeInfo simcam_info = {
    .name = TYPE_ESP32S3_SIMCAM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Esp32s3SimcamState),
    .instance_init = simcam_init,
    .class_init = simcam_class_init,
};

static void simcam_register_types(void)
{
    type_register_static(&simcam_info);
}

type_init(simcam_register_types)
