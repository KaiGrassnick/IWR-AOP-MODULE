/* SCI (UART) driver, SWRU522E chapter 26. */
#include <stdarg.h>

#include "soc.h"

sci_t sci_cli, sci_data;

static uint32_t brs_for(uint32_t baud)
{
    /* asynchronous: baud = VCLK / (16 (BRS + 1)), rounded to the nearest divider */
    uint32_t div16 = 16u * baud;
    return (VCLK_HZ + div16 / 2u) / div16 - 1u;
}

void sci_init(sci_t *s, uint32_t base, uint32_t baud, bool rx_irq)
{
    s->base = base;
    s->rx_head = s->rx_tail = 0;
    s->rx_errors = 0;
    REG32(base + SCI_GCR0) = 0;               /* module in reset */
    REG32(base + SCI_GCR0) = 1;               /* out of reset */
    REG32(base + SCI_GCR1) = 0;               /* SWnRST = 0 while configuring */
    REG32(base + SCI_CLEARINT) = 0xFFFFFFFFu;
    REG32(base + SCI_CLEARINTLVL) = 0xFFFFFFFFu;
    REG32(base + SCI_GCR1) = SCI_GCR1_TIMING | SCI_GCR1_CLOCK; /* async, internal clock, 1 stop, no parity */
    REG32(base + SCI_FORMAT) = 7u;            /* 8 data bits */
    uint32_t brs = brs_for(baud);
    REG32(base + SCI_BRS) = brs;
    s->baud_set = VCLK_HZ / (16u * (brs + 1u));
    REG32(base + SCI_PIO0) = SCI_PIO0_TXFUNC | SCI_PIO0_RXFUNC;
    REG32(base + SCI_GCR1) |= SCI_GCR1_TXENA | SCI_GCR1_RXENA;
    if (rx_irq)
        REG32(base + SCI_SETINT) = SCI_INT_RX;
    REG32(base + SCI_GCR1) |= SCI_GCR1_SWNRST;
}

void sci_putc(sci_t *s, char c)
{
    while (!(REG32(s->base + SCI_FLR) & SCI_FLR_TXRDY))
        ;
    REG32(s->base + SCI_TD) = (uint8_t)c;
}

void sci_write(sci_t *s, const void *buf, size_t n)
{
    const uint8_t *p = buf;
    while (n--)
        sci_putc(s, (char)*p++);
}

void sci_puts(sci_t *s, const char *str)
{
    while (*str) {
        if (*str == '\n')
            sci_putc(s, '\r');
        sci_putc(s, *str++);
    }
}

void sci_flush(sci_t *s)
{
    while (!(REG32(s->base + SCI_FLR) & SCI_FLR_TXEMPTY))
        ;
}

int sci_getc(sci_t *s)
{
    uint32_t key = irq_disable();
    int c = -1;
    if (s->rx_tail != s->rx_head) {
        c = s->rx[s->rx_tail];
        s->rx_tail = (uint16_t)((s->rx_tail + 1u) % sizeof s->rx);
    }
    irq_restore(key);
    return c;
}

void sci_isr_cli(void)
{
    sci_t *s = &sci_cli;
    uint32_t flr = REG32(s->base + SCI_FLR);
    if (flr & (SCI_FLR_FE | SCI_FLR_OE | SCI_FLR_PE)) {
        s->rx_errors++;
        REG32(s->base + SCI_FLR) = SCI_FLR_FE | SCI_FLR_OE | SCI_FLR_PE; /* write 1 to clear */
    }
    if (flr & SCI_FLR_RXRDY) {
        uint8_t c = (uint8_t)REG32(s->base + SCI_RD); /* reading SCIRD clears RXRDY */
        uint16_t next = (uint16_t)((s->rx_head + 1u) % sizeof s->rx);
        if (next != s->rx_tail) {
            s->rx[s->rx_head] = c;
            s->rx_head = next;
        }
    }
}

/* ------------------------------------------------------------------ tiny printf */
static void put_uint(sci_t *s, uint32_t v, unsigned base, int width, char pad)
{
    char buf[12];
    int n = 0;
    do {
        unsigned d = v % base;
        buf[n++] = (char)(d < 10 ? '0' + d : 'A' + d - 10);
        v /= base;
    } while (v);
    while (n < width--)
        sci_putc(s, pad);
    while (n)
        sci_putc(s, buf[--n]);
}

void uprintf(sci_t *s, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    for (; *fmt; fmt++) {
        if (*fmt != '%') {
            if (*fmt == '\n')
                sci_putc(s, '\r');
            sci_putc(s, *fmt);
            continue;
        }
        fmt++;
        char pad = ' ';
        int width = 0;
        if (*fmt == '0') {
            pad = '0';
            fmt++;
        }
        while (*fmt >= '0' && *fmt <= '9')
            width = width * 10 + (*fmt++ - '0');
        switch (*fmt) {
        case 'd': {
            int v = va_arg(ap, int);
            if (v < 0) {
                sci_putc(s, '-');
                v = -v;
            }
            put_uint(s, (uint32_t)v, 10, width, pad);
            break;
        }
        case 'u':
            put_uint(s, va_arg(ap, uint32_t), 10, width, pad);
            break;
        case 'x':
        case 'X':
            put_uint(s, va_arg(ap, uint32_t), 16, width, pad);
            break;
        case 's':
            sci_puts(s, va_arg(ap, const char *));
            break;
        case 'c':
            sci_putc(s, (char)va_arg(ap, int));
            break;
        default:
            sci_putc(s, '%');
            sci_putc(s, *fmt);
        }
    }
    va_end(ap);
}
