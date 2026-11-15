/*
 * IWR6843AOP master subsystem (Cortex-R4F) register map, as far as this firmware uses it.
 *
 * Every address and bit below is taken from TI SWRU522E (xWR14xx/16xx/18xx/68xx/64xx TRM,
 * revised June 2020) or SWRS237C (IWR6843AOP datasheet). The section is noted next to each
 * block so it can be checked against the documents. The co-simulation's peripheral models
 * (cosim/sim/soc/) are written from the same sections, so a misreading shared by both would
 * not be caught by the simulation; first silicon is the real check.
 */
#ifndef HW_H
#define HW_H

#include <stdint.h>

#define REG32(a) (*(volatile uint32_t *)(uintptr_t)(a))

/* MSS clocks. VCLK 200 MHz is the mmWave SDK value for xWR68xx (sysClockFrequency). */
#define VCLK_HZ 200000000u
#define RTICLK_HZ VCLK_HZ

/* ---- Memory map, SWRU522E Table 3-2 ------------------------------------------------ */
#define TCMA_BASE 0x00000000u /* program RAM after ROM eclipse, 512 KiB */
#define TCMB_BASE 0x08000000u /* data RAM, 192 KiB */

/* ---- SCI (UART), SWRU522E 26.7, Table 26-3 ---------------------------------------- */
#define SCIA_BASE 0xFFF7E500u /* MSS_SCIA, UARTA: control / CLI */
#define SCIB_BASE 0xFFF7E700u /* MSS_SCIB, UARTB: data stream */
#define SCI_GCR0 0x00u
#define SCI_GCR1 0x04u
#define SCI_SETINT 0x0Cu
#define SCI_CLEARINT 0x10u
#define SCI_SETINTLVL 0x14u
#define SCI_CLEARINTLVL 0x18u
#define SCI_FLR 0x1Cu
#define SCI_FORMAT 0x28u
#define SCI_BRS 0x2Cu
#define SCI_RD 0x34u
#define SCI_TD 0x38u
#define SCI_PIO0 0x3Cu
/* SCIGCR1 (Table 26-5) */
#define SCI_GCR1_TXENA (1u << 25)
#define SCI_GCR1_RXENA (1u << 24)
#define SCI_GCR1_SWNRST (1u << 7)
#define SCI_GCR1_CLOCK (1u << 5)  /* internal clock */
#define SCI_GCR1_TIMING (1u << 1) /* asynchronous */
/* SCIFLR (Table 26-10) */
#define SCI_FLR_FE (1u << 26)
#define SCI_FLR_OE (1u << 25)
#define SCI_FLR_PE (1u << 24)
#define SCI_FLR_TXEMPTY (1u << 11)
#define SCI_FLR_RXRDY (1u << 9)
#define SCI_FLR_TXRDY (1u << 8)
/* SCISETINT (Table 26-6) */
#define SCI_INT_RX (1u << 9)
#define SCI_INT_TX (1u << 8)
/* SCIPIO0 (Table 26-21) */
#define SCI_PIO0_TXFUNC (1u << 2)
#define SCI_PIO0_RXFUNC (1u << 1)

/* ---- I2C, SWRU522E 25.6, Table 25-3 ----------------------------------------------- */
#define I2C_BASE 0xFFF7D400u
#define I2C_OAR 0x00u
#define I2C_IMR 0x04u
#define I2C_STR 0x08u
#define I2C_CLKL 0x0Cu
#define I2C_CLKH 0x10u
#define I2C_CNT 0x14u
#define I2C_DRR 0x18u
#define I2C_SAR 0x1Cu
#define I2C_DXR 0x20u
#define I2C_MDR 0x24u
#define I2C_PSC 0x30u
/* ICSTR (Table 25-7) */
#define I2C_STR_BB (1u << 12)
#define I2C_STR_SCD (1u << 5)
#define I2C_STR_XRDY (1u << 4)
#define I2C_STR_RRDY (1u << 3)
#define I2C_STR_ARDY (1u << 2)
#define I2C_STR_NACK (1u << 1)
#define I2C_STR_AL (1u << 0)
/* ICMDR (Table 25-14) */
#define I2C_MDR_FREE (1u << 14)
#define I2C_MDR_STT (1u << 13)
#define I2C_MDR_STP (1u << 11)
#define I2C_MDR_MST (1u << 10)
#define I2C_MDR_TRX (1u << 9)
#define I2C_MDR_IRS (1u << 5)

/* ---- RTI, SWRU522E 16.3, Table 16-1 ----------------------------------------------- */
#define RTIA_BASE 0xFFFFFC00u
#define RTI_GCTRL 0x00u
#define RTI_COMPCTRL 0x0Cu
#define RTI_FRC0 0x10u
#define RTI_UC0 0x14u
#define RTI_CPUC0 0x18u
#define RTI_COMP0 0x50u
#define RTI_UDCP0 0x54u
#define RTI_SETINTENA 0x80u
#define RTI_CLEARINTENA 0x84u
#define RTI_INTFLAG 0x88u

/* ---- VIM, SWRU522E 8.8, Table 8-5 (control registers at 0xFFFFFE00) ---------------- */
#define VIM_BASE 0xFFFFFE00u
#define VIM_IRQINDEX 0x00u
#define VIM_FIRQPR0 0x10u
#define VIM_REQENASET0 0x30u
#define VIM_REQENACLR0 0x40u
#define VIM_IRQVECREG 0x70u
#define VIM_RAM_BASE 0xFFF82000u /* MSS_VIM_MEM, entry 0 = phantom vector */

/* VIM channels, SWRU522E Table 3-11 */
#define VIM_CH_RTIA_COMP0 2u
#define VIM_CH_SCIA_LVL0 64u
#define VIM_CH_SCIB_LVL0 65u
#define VIM_CH_I2C 66u
#define VIM_CH_BSS_MBOX 95u     /* RADARSS to MSS mailbox interrupt */
#define VIM_CH_BSS_MBOX_RD 96u  /* RADARSS mailbox read complete */
#define VIM_CH_FRAME_START 98u  /* MSS_DMM 29 / frame start */

/* ---- Mailbox, SWRU522E Table 3-2 and chapter 18 ----------------------------------- */
#define MSS_MBOX4BSS_MEM 0xF0601000u   /* BSS -> MSS messages */
#define BSS_MBOX4MSS_MEM 0xF0602000u   /* MSS -> BSS messages */
#define BSS_MBOX4MSS_REG 0xF0608000u
#define MSS_MBOX4BSS_REG 0xF0608600u
#define MBOX_INT_STS_CLR 0x18u
#define MBOX_INT_ACK 0x20u
#define MBOX_INT_TRIG 0x28u
#define MBOX_INT_STS_RAW 0x38u

/* ---- IOMUX pad registers, SWRS237C Table 6-2 / 6-3 -------------------------------- */
#define PAD_B2_SPI_HOST_INTR 0xFFFFEA00u
#define PAD_F2_SPIA_MOSI 0xFFFFEA0Cu
#define PAD_D1_SPIA_MISO 0xFFFFEA10u
#define PAD_D2_SPIA_CLK 0xFFFFEA14u
#define PAD_C2_SPIA_CS_N 0xFFFFEA18u
#define PAD_G1_SPIB_MOSI 0xFFFFEA1Cu
#define PAD_G3_SPIB_MISO 0xFFFFEA20u
#define PAD_E2_SPIB_CLK 0xFFFFEA24u
#define PAD_K3_GPIO_2 0xFFFFEA64u
#define PAD_U12_SYNC_IN 0xFFFFEA6Cu
#define PAD_V16_RS232_RX 0xFFFFEA74u
#define PAD_U16_RS232_TX 0xFFFFEA78u
#define PAD_FUNC_SEL_MASK 0xFu
#define PAD_OE_OVERRIDE (1u << 7)
#define PAD_OE_OVERRIDE_CTRL (1u << 6)

#endif
