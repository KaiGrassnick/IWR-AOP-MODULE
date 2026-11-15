/* VIM, RTI and IOMUX drivers for the IWR6843 MSS. Register references in hw.h. */
#include "soc.h"
#include "board.h"

/* ---------------------------------------------------------------- VIM (SWRU522E 8) */
static void phantom_isr(void) {}

void vim_init(void)
{
    /* disable every channel except 0/1 (ESM high-level and reserved, always enabled) */
    for (unsigned i = 0; i < 4; i++)
        REG32(VIM_BASE + VIM_REQENACLR0 + 4 * i) = i == 0 ? 0xFFFFFFFCu : 0xFFFFFFFFu;
    for (unsigned i = 0; i < 4; i++)
        REG32(VIM_BASE + VIM_FIRQPR0 + 4 * i) = i == 0 ? 3u : 0u; /* all IRQ, 0/1 FIQ */
    for (unsigned i = 0; i < 129; i++)
        REG32(VIM_RAM_BASE + 4 * i) = (uint32_t)(uintptr_t)phantom_isr;
}

void vim_attach(unsigned ch, isr_t isr)
{
    /* VIM RAM entry n+1 belongs to channel n; entry 0 is the phantom vector */
    REG32(VIM_RAM_BASE + 4 * (ch + 1)) = (uint32_t)(uintptr_t)isr;
    REG32(VIM_BASE + VIM_REQENASET0 + 4 * (ch / 32)) = 1u << (ch % 32);
}

/* ---------------------------------------------------------------- RTI (SWRU522E 16) */
static volatile uint32_t tick_ms;

static void rti_isr(void)
{
    REG32(RTIA_BASE + RTI_INTFLAG) = 1u; /* clear compare 0 */
    tick_ms++;
}

void rti_init(void)
{
    REG32(RTIA_BASE + RTI_GCTRL) = 0;              /* stop counters */
    REG32(RTIA_BASE + RTI_COMPCTRL) = 0;           /* compare 0 uses FRC0 */
    REG32(RTIA_BASE + RTI_UC0) = 0;
    REG32(RTIA_BASE + RTI_FRC0) = 0;
    REG32(RTIA_BASE + RTI_CPUC0) = RTICLK_HZ / 1000000u - 1u; /* FRC0 counts microseconds */
    REG32(RTIA_BASE + RTI_COMP0) = 1000u;
    REG32(RTIA_BASE + RTI_UDCP0) = 1000u;
    REG32(RTIA_BASE + RTI_INTFLAG) = 0x7FFFFu;
    vim_attach(VIM_CH_RTIA_COMP0, rti_isr);
    REG32(RTIA_BASE + RTI_SETINTENA) = 1u;
    REG32(RTIA_BASE + RTI_GCTRL) = 1u;             /* CNT0EN */
}

uint32_t time_us(void) { return REG32(RTIA_BASE + RTI_FRC0); }
uint32_t time_ms(void) { return tick_ms; }

void delay_us(uint32_t us)
{
    uint32_t t0 = time_us();
    while (time_us() - t0 < us)
        ;
}

/* ---------------------------------------------------------------- IOMUX (SWRS237C 6) */
static const struct pinmux_entry pinmux[] = {BOARD_PINMUX};

void pinmux_apply(void)
{
    /* Writing FUNC_SEL with OE/IE_OVERRIDE_CTRL = 0 hands the output and input enables to
     * the selected peripheral (Table 6-3); the reset value keeps the outputs overridden off. */
    for (unsigned i = 0; i < sizeof pinmux / sizeof pinmux[0]; i++)
        REG32(pinmux[i].pad) = pinmux[i].mode & PAD_FUNC_SEL_MASK;
}

uint32_t pinmux_mode(uint32_t pad) { return REG32(pad) & PAD_FUNC_SEL_MASK; }
