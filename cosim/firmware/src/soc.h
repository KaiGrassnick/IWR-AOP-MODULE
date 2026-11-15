#ifndef SOC_H
#define SOC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "hw.h"

/* startup.S */
void cpu_wfi(void);
void irq_enable(void);
uint32_t irq_disable(void);
void irq_restore(uint32_t cpsr);

/* VIM */
typedef void (*isr_t)(void);
void vim_init(void);
void vim_attach(unsigned channel, isr_t isr);

/* RTI: free-running microsecond time base on counter 0 and a 1 ms tick on compare 0 */
void rti_init(void);
uint32_t time_us(void);
uint32_t time_ms(void);
void delay_us(uint32_t us);

/* IOMUX */
void pinmux_apply(void);
uint32_t pinmux_mode(uint32_t pad);

/* SCI */
typedef struct {
    uint32_t base;
    uint32_t baud_set;   /* what the divider really gives */
    volatile uint8_t rx[256];
    volatile uint16_t rx_head, rx_tail;
    volatile uint32_t rx_errors;
} sci_t;
extern sci_t sci_cli, sci_data;
void sci_init(sci_t *s, uint32_t base, uint32_t baud, bool rx_irq);
void sci_putc(sci_t *s, char c);
void sci_write(sci_t *s, const void *buf, size_t n);
void sci_puts(sci_t *s, const char *str);
int sci_getc(sci_t *s); /* -1 if empty */
void sci_flush(sci_t *s);
void sci_isr_cli(void);

/* I2C master, polled. Returns 0 on success, negative on error. */
enum { I2C_OK = 0, I2C_ERR_NACK = -1, I2C_ERR_TIMEOUT = -2, I2C_ERR_BUSY = -3, I2C_ERR_AL = -4 };
void i2c_init(uint32_t scl_hz);
uint32_t i2c_scl_hz(void);
int i2c_write_reg(uint8_t addr, uint8_t reg, uint8_t val);
int i2c_read_reg(uint8_t addr, uint8_t reg, uint8_t *val);

/* tiny printf to a UART */
void uprintf(sci_t *s, const char *fmt, ...); /* %u %d %x %s %c, width and 0 pad; arguments are 32-bit */

#endif
