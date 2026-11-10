/*
 * ESP32-S3 USB OTG controller (Synopsys DWC2), device mode, as far as a device driver in
 * slave (FIFO) mode uses it. The USB cable is a character device: every packet the controller
 * would put on or take off the bus is one frame on it, so the host at the other end can be
 * anything that speaks the framing (phone-kvm's rig/cable.py, a USB/IP server).
 *
 * Frame: type (1 byte), endpoint number (1 byte), length (2 bytes, little endian), data.
 *
 *   host to device   'R' bus reset      'S' SETUP packet (8 bytes)     'O' OUT packet
 *                    'K' the host took the IN packet last sent on this endpoint
 *   device to host   'C' pulled D+ up   'D' released D+                'I' IN packet
 *                    'A' took the OUT packet    'T' STALL    'F' dropped unsent IN packets
 *
 * This program is free software; you can redistribute it and/or modify it under the terms of
 * the GNU General Public License version 2 or (at your option) any later version.
 */
#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/hw.h"
#include "hw/irq.h"
#include "hw/sysbus.h"
#include "hw/qdev-properties.h"
#include "hw/qdev-properties-system.h"
#include "chardev/char-fe.h"

#define TYPE_ESP32S3_USB_OTG "esp32s3.usb_otg"
OBJECT_DECLARE_SIMPLE_TYPE(Esp32s3UsbOtgState, ESP32S3_USB_OTG)

#define ENDPOINTS 7
#define MAX_PACKET 1024
#define RX_ENTRIES 32
#define REGION 0x40000
#define FIFO_BASE 0x1000

#define GOTGCTL 0x000
#define GOTGINT 0x004
#define GAHBCFG 0x008
#define GRSTCTL 0x010
#define GINTSTS 0x014
#define GINTMSK 0x018
#define GRXSTSR 0x01c
#define GRXSTSP 0x020
#define GSNPSID 0x040
#define GHWCFG1 0x044
#define GHWCFG2 0x048
#define GHWCFG3 0x04c
#define GHWCFG4 0x050
#define DCTL 0x804
#define DSTS 0x808
#define DIEPMSK 0x810
#define DOEPMSK 0x814
#define DAINT 0x818
#define DAINTMSK 0x81c
#define DIEPEMPMSK 0x834
#define DIEP 0x900
#define DOEP 0xb00
#define EP_CTL 0x00
#define EP_INT 0x08
#define EP_TSIZ 0x10
#define EP_TXFSTS 0x18

#define GAHBCFG_GINT (1u << 0)
#define GAHBCFG_DMAEN (1u << 5)
#define GRSTCTL_CSRST (1u << 0)
#define GRSTCTL_RXFFLSH (1u << 4)
#define GRSTCTL_TXFFLSH (1u << 5)
#define GRSTCTL_AHBIDL (1u << 31)
#define GINTSTS_RXFLVL (1u << 4)
#define GINTSTS_BOUTNAKEFF (1u << 7)
#define GINTSTS_USBRST (1u << 12)
#define GINTSTS_ENUMDNE (1u << 13)
#define GINTSTS_IEPINT (1u << 18)
#define GINTSTS_OEPINT (1u << 19)
#define GINTSTS_READONLY (GINTSTS_RXFLVL | GINTSTS_BOUTNAKEFF | GINTSTS_IEPINT | GINTSTS_OEPINT)
#define DCTL_SDIS (1u << 1)
#define DCTL_SGONAK (1u << 9)
#define DCTL_CGONAK (1u << 10)
#define CTL_NAKSTS (1u << 17)
#define CTL_STALL (1u << 21)
#define CTL_CNAK (1u << 26)
#define CTL_SNAK (1u << 27)
#define CTL_EPDIS (1u << 30)
#define CTL_EPENA (1u << 31)
#define CTL_COMMANDS (CTL_CNAK | CTL_SNAK | (3u << 28) | CTL_EPDIS | CTL_EPENA)
#define INT_XFRC (1u << 0)
#define INT_EPDISD (1u << 1)
#define DIEPINT_INEPNE (1u << 6)
#define DIEPINT_TXFE (1u << 7)
#define TSIZ_SIZE 0x7ffffu
#define TSIZ_PACKETS (0x3ffu << 19)

enum { RX_DATA = 2, RX_COMPLETE = 3, SETUP_DONE = 4, SETUP_RX = 6 };

typedef struct {
    uint32_t status;
    uint16_t length;
    uint8_t data[MAX_PACKET];
} RxEntry;

struct Esp32s3UsbOtgState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    CharBackend cable;

    uint32_t regs[FIFO_BASE / 4];
    bool pulled_up;

    /* The receive FIFO: packet status entries, each with its data. */
    RxEntry rx[RX_ENTRIES];
    unsigned rx_head, rx_count;
    RxEntry popped;
    unsigned popped_at;

    /* Per IN endpoint: bytes written to its FIFO, and packets sent the host has not taken. */
    uint8_t tx[ENDPOINTS][MAX_PACKET + 4];
    unsigned tx_length[ENDPOINTS];
    unsigned tx_unacked[ENDPOINTS];

    /* Per OUT endpoint: a packet the host sent while the endpoint was not accepting. */
    uint8_t held[ENDPOINTS][MAX_PACKET];
    int held_length[ENDPOINTS];

    /* Frames arriving from the cable. */
    uint8_t in[4 + MAX_PACKET];
    unsigned in_length;
};

#define REG(s, offset) ((s)->regs[(offset) / 4])
#define IN_EP(s, n, r) REG(s, DIEP + (n) * 0x20 + (r))
#define OUT_EP(s, n, r) REG(s, DOEP + (n) * 0x20 + (r))

static void cable_send(Esp32s3UsbOtgState *s, char type, unsigned ep, const uint8_t *data, unsigned length)
{
    uint8_t frame[4 + MAX_PACKET] = { type, ep, length & 0xff, length >> 8 };

    if (length) {
        memcpy(frame + 4, data, length);
    }
    qemu_chr_fe_write_all(&s->cable, frame, 4 + length);
}

static uint32_t daint(Esp32s3UsbOtgState *s)
{
    uint32_t value = 0;

    for (unsigned n = 0; n < ENDPOINTS; n++) {
        uint32_t mask = REG(s, DIEPMSK) | ((REG(s, DIEPEMPMSK) >> n & 1) ? DIEPINT_TXFE : 0);
        if ((IN_EP(s, n, EP_INT) | DIEPINT_TXFE) & mask) {
            value |= 1u << n;
        }
        if (OUT_EP(s, n, EP_INT) & REG(s, DOEPMSK)) {
            value |= 1u << (16 + n);
        }
    }
    return value;
}

static uint32_t gintsts(Esp32s3UsbOtgState *s)
{
    uint32_t value = REG(s, GINTSTS) & ~(GINTSTS_RXFLVL | GINTSTS_IEPINT | GINTSTS_OEPINT);
    uint32_t pending = daint(s) & REG(s, DAINTMSK);

    if (s->rx_count) {
        value |= GINTSTS_RXFLVL;
    }
    if (pending & 0xffff) {
        value |= GINTSTS_IEPINT;
    }
    if (pending >> 16) {
        value |= GINTSTS_OEPINT;
    }
    return value;
}

static void update_irq(Esp32s3UsbOtgState *s)
{
    qemu_set_irq(s->irq, (REG(s, GAHBCFG) & GAHBCFG_GINT) && (gintsts(s) & REG(s, GINTMSK)));
}

static void rx_push(Esp32s3UsbOtgState *s, unsigned status, unsigned ep, const uint8_t *data, unsigned length)
{
    RxEntry *entry;

    if (s->rx_count == RX_ENTRIES) {
        qemu_log_mask(LOG_GUEST_ERROR, "esp32s3.usb_otg: receive FIFO overflow\n");
        return;
    }
    entry = &s->rx[(s->rx_head + s->rx_count++) % RX_ENTRIES];
    entry->status = ep | length << 4 | status << 17;
    entry->length = length;
    if (length) {
        memcpy(entry->data, data, length);
    }
}

static unsigned max_packet(uint32_t ctl, unsigned ep)
{
    return ep ? (ctl & 0x7ff) : (64u >> (ctl & 3));
}

/* Send the packets of an enabled IN transfer whose data is already in the FIFO. */
static void in_send(Esp32s3UsbOtgState *s, unsigned n)
{
    uint32_t *ctl = &IN_EP(s, n, EP_CTL), *tsiz = &IN_EP(s, n, EP_TSIZ);

    while ((*ctl & CTL_EPENA) && (*tsiz & TSIZ_PACKETS)) {
        unsigned size = MIN(*tsiz & TSIZ_SIZE, max_packet(*ctl, n));
        unsigned words = ROUND_UP(size, 4);

        if (s->tx_length[n] < words) {
            return;
        }
        s->tx_unacked[n]++;
        cable_send(s, 'I', n, s->tx[n], size);
        memmove(s->tx[n], s->tx[n] + words, s->tx_length[n] - words);
        s->tx_length[n] -= words;
        *tsiz = (*tsiz & ~(TSIZ_SIZE | TSIZ_PACKETS)) | ((*tsiz & TSIZ_SIZE) - size)
                | ((*tsiz & TSIZ_PACKETS) - (1u << 19));
    }
}

static void in_drop(Esp32s3UsbOtgState *s, unsigned n)
{
    if (s->tx_unacked[n]) {
        cable_send(s, 'F', n, NULL, 0);
    }
    s->tx_unacked[n] = 0;
    s->tx_length[n] = 0;
}

/* Take an OUT packet into the receive FIFO, if the endpoint is accepting. */
static bool out_take(Esp32s3UsbOtgState *s, unsigned n, const uint8_t *data, unsigned length)
{
    uint32_t *ctl = &OUT_EP(s, n, EP_CTL), *tsiz = &OUT_EP(s, n, EP_TSIZ);
    uint32_t size, packets;

    if (!(*ctl & CTL_EPENA) || (REG(s, GINTSTS) & GINTSTS_BOUTNAKEFF)) {
        return false;
    }
    size = *tsiz & TSIZ_SIZE;
    packets = (*tsiz & TSIZ_PACKETS) >> 19;
    size -= MIN(size, length);
    packets -= MIN(packets, 1);
    *tsiz = (*tsiz & ~(TSIZ_SIZE | TSIZ_PACKETS)) | size | packets << 19;
    rx_push(s, RX_DATA, n, data, length);
    if (!packets || length < max_packet(*ctl, n)) {
        rx_push(s, RX_COMPLETE, n, NULL, 0);
        *ctl = (*ctl & ~CTL_EPENA) | CTL_NAKSTS;
        OUT_EP(s, n, EP_INT) |= INT_XFRC;
    }
    cable_send(s, 'A', n, NULL, 0);
    return true;
}

static void out_retry(Esp32s3UsbOtgState *s, unsigned n)
{
    if (s->held_length[n] >= 0 && out_take(s, n, s->held[n], s->held_length[n])) {
        s->held_length[n] = -1;
    }
}

static void bus_idle(Esp32s3UsbOtgState *s)
{
    s->rx_count = 0;
    s->popped.length = s->popped_at = 0;
    for (unsigned n = 0; n < ENDPOINTS; n++) {
        s->tx_length[n] = s->tx_unacked[n] = 0;
        s->held_length[n] = -1;
    }
}

static void frame_from_host(Esp32s3UsbOtgState *s, char type, unsigned n, const uint8_t *data, unsigned length)
{
    if (n >= ENDPOINTS || !s->pulled_up) {
        return;
    }
    switch (type) {
    case 'R':
        bus_idle(s);
        REG(s, GINTSTS) |= GINTSTS_USBRST | GINTSTS_ENUMDNE;
        break;
    case 'S':
        /* A SETUP packet ends whatever the control endpoint was doing, including a stall. */
        s->held_length[0] = -1;
        in_drop(s, 0);
        IN_EP(s, 0, EP_CTL) &= ~CTL_STALL;
        OUT_EP(s, 0, EP_CTL) &= ~CTL_STALL;
        rx_push(s, SETUP_RX, 0, data, 8);
        rx_push(s, SETUP_DONE, 0, NULL, 0);
        break;
    case 'O':
        if (OUT_EP(s, n, EP_CTL) & CTL_STALL) {
            cable_send(s, 'T', n, NULL, 0);
        } else if (!out_take(s, n, data, length)) {
            memcpy(s->held[n], data, length);
            s->held_length[n] = length;
        }
        break;
    case 'K':
        if (s->tx_unacked[n] && !--s->tx_unacked[n] && !(IN_EP(s, n, EP_TSIZ) & TSIZ_PACKETS)
            && (IN_EP(s, n, EP_CTL) & CTL_EPENA)) {
            IN_EP(s, n, EP_CTL) &= ~CTL_EPENA;
            IN_EP(s, n, EP_INT) |= INT_XFRC;
        }
        break;
    }
    update_irq(s);
}

static int cable_can_receive(void *opaque)
{
    Esp32s3UsbOtgState *s = opaque;

    return sizeof(s->in) - s->in_length;
}

static void cable_receive(void *opaque, const uint8_t *data, int size)
{
    Esp32s3UsbOtgState *s = opaque;

    memcpy(s->in + s->in_length, data, size);
    s->in_length += size;
    for (;;) {
        unsigned length;

        if (s->in_length < 4) {
            return;
        }
        length = s->in[2] | s->in[3] << 8;
        if (length > MAX_PACKET) {
            s->in_length = 0;   /* not a frame: drop what arrived */
            return;
        }
        if (s->in_length < 4 + length) {
            return;
        }
        frame_from_host(s, s->in[0], s->in[1], s->in + 4, length);
        memmove(s->in, s->in + 4 + length, s->in_length - 4 - length);
        s->in_length -= 4 + length;
    }
}

static void cable_event(void *opaque, QEMUChrEvent event)
{
    Esp32s3UsbOtgState *s = opaque;

    if (event == CHR_EVENT_OPENED) {
        s->in_length = 0;
        if (s->pulled_up) {
            cable_send(s, 'C', 0, NULL, 0);
        }
    }
}

static void pull_up(Esp32s3UsbOtgState *s, bool up)
{
    if (up != s->pulled_up) {
        s->pulled_up = up;
        bus_idle(s);
        cable_send(s, up ? 'C' : 'D', 0, NULL, 0);
    }
}

static uint64_t usb_read(void *opaque, hwaddr addr, unsigned size)
{
    Esp32s3UsbOtgState *s = opaque;
    uint32_t value = 0;

    if (addr >= FIFO_BASE) {
        /* Every FIFO address reads the packet last popped from the receive FIFO. */
        memcpy(&value, s->popped.data + s->popped_at, MIN(4, MAX_PACKET - s->popped_at));
        s->popped_at = MIN(s->popped_at + 4, MAX_PACKET);
        return value;
    }
    switch (addr) {
    case GRSTCTL:
        return GRSTCTL_AHBIDL;
    case GINTSTS:
        return gintsts(s);
    case GRXSTSR:
        return s->rx_count ? s->rx[s->rx_head].status : 0;
    case GRXSTSP:
        if (!s->rx_count) {
            return 0;
        }
        s->popped = s->rx[s->rx_head];
        s->popped_at = 0;
        s->rx_head = (s->rx_head + 1) % RX_ENTRIES;
        s->rx_count--;
        update_irq(s);
        return s->popped.status;
    case GSNPSID:
        return 0x4f54400a;
    case GHWCFG1:
        return 0;
    case GHWCFG2:
        return 0x224dd930;
    case GHWCFG3:
        return 0x00c804b5;
    case GHWCFG4:
        return 0xd3f0a030;
    case DSTS:
        /* Enumerated at full speed; the frame number is the millisecond. */
        return 3u << 1 | (qemu_clock_get_ms(QEMU_CLOCK_VIRTUAL) & 0x3fff) << 8;
    case DAINT:
        return daint(s);
    }
    if (addr >= DIEP && addr < DIEP + ENDPOINTS * 0x20) {
        switch ((addr - DIEP) % 0x20) {
        case EP_INT:
            return REG(s, addr) | DIEPINT_TXFE | DIEPINT_INEPNE;
        case EP_TXFSTS:
            return 0x100;
        }
    }
    return REG(s, addr);
}

static void endpoint_control(Esp32s3UsbOtgState *s, bool in, unsigned n, uint32_t value)
{
    uint32_t *ctl = in ? &IN_EP(s, n, EP_CTL) : &OUT_EP(s, n, EP_CTL);
    uint32_t *status = in ? &IN_EP(s, n, EP_INT) : &OUT_EP(s, n, EP_INT);
    uint32_t state = *ctl & (CTL_EPENA | CTL_NAKSTS);
    bool was_stalled = *ctl & CTL_STALL;

    if (value & CTL_SNAK) {
        state |= CTL_NAKSTS;
    }
    if (value & CTL_CNAK) {
        state &= ~CTL_NAKSTS;
    }
    if (value & CTL_EPENA) {
        state |= CTL_EPENA;
    }
    if (value & CTL_EPDIS) {
        state &= ~CTL_EPENA;
        *status |= INT_EPDISD;
        if (in) {
            in_drop(s, n);
        }
    }
    *ctl = (value & ~(CTL_COMMANDS | CTL_NAKSTS)) | state;
    if ((value & CTL_STALL) && !was_stalled && (in || n)) {
        cable_send(s, 'T', n, NULL, 0);
    }
    if (!(value & CTL_EPENA) || (value & CTL_EPDIS)) {
        return;
    }
    if (in) {
        in_send(s, n);
    } else {
        out_retry(s, n);
    }
}

static void usb_write(void *opaque, hwaddr addr, uint64_t value, unsigned size)
{
    Esp32s3UsbOtgState *s = opaque;

    if (addr >= FIFO_BASE) {
        unsigned n = (addr - FIFO_BASE) / 0x1000;

        if (n < ENDPOINTS && s->tx_length[n] <= MAX_PACKET) {
            memcpy(s->tx[n] + s->tx_length[n], &value, 4);
            s->tx_length[n] += 4;
            in_send(s, n);
        }
        update_irq(s);
        return;
    }
    switch (addr) {
    case GAHBCFG:
        if (value & GAHBCFG_DMAEN) {
            qemu_log_mask(LOG_UNIMP, "esp32s3.usb_otg: DMA mode is not modelled\n");
        }
        REG(s, addr) = value;
        break;
    case GRSTCTL:
        if (value & (GRSTCTL_CSRST | GRSTCTL_RXFFLSH)) {
            s->rx_count = 0;
        }
        if (value & (GRSTCTL_CSRST | GRSTCTL_TXFFLSH)) {
            for (unsigned n = 0; n < ENDPOINTS; n++) {
                s->tx_length[n] = 0;
            }
        }
        break;
    case GINTSTS:
        REG(s, addr) &= ~(value & ~GINTSTS_READONLY);
        break;
    case GOTGINT:
        REG(s, addr) &= ~value;
        break;
    case DCTL:
        if (value & DCTL_SGONAK) {
            REG(s, GINTSTS) |= GINTSTS_BOUTNAKEFF;
        }
        if (value & DCTL_CGONAK) {
            REG(s, GINTSTS) &= ~GINTSTS_BOUTNAKEFF;
        }
        REG(s, addr) = value & ~(DCTL_SGONAK | DCTL_CGONAK);
        pull_up(s, !(value & DCTL_SDIS));
        break;
    case GRXSTSR:
    case GRXSTSP:
    case GSNPSID:
    case GHWCFG1:
    case GHWCFG2:
    case GHWCFG3:
    case GHWCFG4:
    case DSTS:
    case DAINT:
        break;
    default:
        if (addr >= DIEP && addr < DIEP + ENDPOINTS * 0x20) {
            unsigned n = (addr - DIEP) / 0x20;

            switch ((addr - DIEP) % 0x20) {
            case EP_CTL:
                endpoint_control(s, true, n, value);
                break;
            case EP_INT:
                REG(s, addr) &= ~value;
                break;
            default:
                REG(s, addr) = value;
            }
        } else if (addr >= DOEP && addr < DOEP + ENDPOINTS * 0x20) {
            unsigned n = (addr - DOEP) / 0x20;

            switch ((addr - DOEP) % 0x20) {
            case EP_CTL:
                endpoint_control(s, false, n, value);
                break;
            case EP_INT:
                REG(s, addr) &= ~value;
                break;
            default:
                REG(s, addr) = value;
            }
        } else {
            REG(s, addr) = value;
        }
    }
    update_irq(s);
}

static const MemoryRegionOps usb_ops = {
    .read = usb_read,
    .write = usb_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
};

static void usb_reset(DeviceState *dev)
{
    Esp32s3UsbOtgState *s = ESP32S3_USB_OTG(dev);

    memset(s->regs, 0, sizeof(s->regs));
    REG(s, DCTL) = DCTL_SDIS;
    pull_up(s, false);
    bus_idle(s);
    update_irq(s);
}

static void usb_realize(DeviceState *dev, Error **errp)
{
    Esp32s3UsbOtgState *s = ESP32S3_USB_OTG(dev);

    qemu_chr_fe_set_handlers(&s->cable, cable_can_receive, cable_receive, cable_event, NULL, s, NULL, true);
}

static void usb_init(Object *obj)
{
    Esp32s3UsbOtgState *s = ESP32S3_USB_OTG(obj);

    memory_region_init_io(&s->iomem, obj, &usb_ops, s, TYPE_ESP32S3_USB_OTG, REGION);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
}

static Property usb_properties[] = {
    DEFINE_PROP_CHR("chardev", Esp32s3UsbOtgState, cable),
    DEFINE_PROP_END_OF_LIST(),
};

static void usb_class_init(ObjectClass *klass, void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = usb_realize;
    device_class_set_legacy_reset(dc, usb_reset);
    device_class_set_props(dc, usb_properties);
}

static const TypeInfo usb_info = {
    .name = TYPE_ESP32S3_USB_OTG,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Esp32s3UsbOtgState),
    .instance_init = usb_init,
    .class_init = usb_class_init,
};

static void usb_register_types(void)
{
    type_register_static(&usb_info);
}

type_init(usb_register_types)
