/*
 * Cortex-R4F start-up for the IWR6843 MSS. After the boot ROM has copied the image into
 * TCMA and released the R4F, execution starts at 0x00000000 in ARM state, supervisor mode,
 * IRQ and FIQ masked.
 *
 * IRQ: irq_entry saves the caller-saved core and VFP registers, reads IRQVECREG
 * (0xFFFFFE70), which returns the VIM RAM entry of the highest-priority pending channel
 * (SWRU522E 8.8.15), and calls it as an ordinary C function.
 */
    .syntax unified
    .arm
    .section .vectors, "ax"
    .global _vectors
_vectors:
    b   _reset
    b   undef_handler
    b   svc_handler
    b   pabt_handler
    b   dabt_handler
    b   .
    b   irq_entry
    b   .

    .text
    .global _reset
_reset:
    /* stacks for every mode we can enter */
    cps #0x12               /* IRQ */
    ldr sp, =__stack_irq_top
    cps #0x17               /* ABT */
    ldr sp, =__stack_abt_top
    cps #0x1b               /* UND */
    ldr sp, =__stack_und_top
    cps #0x13               /* SVC */
    ldr sp, =__stack_svc_top
    cps #0x1f               /* SYS: main runs here */
    ldr sp, =__stack_sys_top

    /* enable the VFPv3-D16: CPACR cp10/cp11 full access, then FPEXC.EN */
    mrc p15, 0, r0, c1, c0, 2
    orr r0, r0, #(0xf << 20)
    mcr p15, 0, r0, c1, c0, 2
    isb
    mov r0, #0x40000000
    vmsr fpexc, r0

    /* .data from its load image in TCMA, .bss to zero */
    ldr r0, =__data_load
    ldr r1, =__data_start
    ldr r2, =__data_end
1:  cmp r1, r2
    ldrlo r3, [r0], #4
    strlo r3, [r1], #4
    blo 1b
    ldr r1, =__bss_start
    ldr r2, =__bss_end
    mov r3, #0
2:  cmp r1, r2
    strlo r3, [r1], #4
    blo 2b

    bl  main
3:  wfi
    b   3b

    .global irq_entry
irq_entry:
    sub lr, lr, #4
    srsdb sp!, #0x12            /* LR_irq and SPSR_irq onto the IRQ stack */
    push {r0-r3, r12, lr}
    vpush {d0-d7}
    vmrs r0, fpscr
    push {r0, r1}
    ldr r0, =0xFFFFFE70         /* VIM IRQVECREG */
    ldr r0, [r0]
    blx r0
    pop {r0, r1}
    vmsr fpscr, r0
    vpop {d0-d7}
    pop {r0-r3, r12, lr}
    rfeia sp!

    .global cpu_wfi
cpu_wfi:
    wfi
    bx lr

    .global irq_enable
irq_enable:
    cpsie i
    bx lr

    .global irq_disable
irq_disable:
    mrs r0, cpsr
    cpsid i
    bx lr

    .global irq_restore
irq_restore:
    msr cpsr_c, r0
    bx lr

undef_handler:
    mov r0, #1
    b   fault_entry
svc_handler:
    mov r0, #2
    b   fault_entry
pabt_handler:
    mov r0, #3
    b   fault_entry
dabt_handler:
    mov r0, #4
fault_entry:
    mov r1, lr
    cps #0x1f
    ldr sp, =__stack_sys_top
    bl  fault
4:  b   4b
