/* IWR6843 MSS: code in TCMA (512 KiB at 0 after ROM eclipse), data in TCMB (192 KiB). SWRU522E Table 3-2. */
ENTRY(_reset)
MEMORY
{
    TCMA (rx)  : ORIGIN = 0x00000000, LENGTH = 512K
    TCMB (rw)  : ORIGIN = 0x08000000, LENGTH = 192K
}
SECTIONS
{
    .vectors 0x0 : { KEEP(*(.vectors)) } > TCMA
    .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > TCMA
    .ARM.exidx : { *(.ARM.exidx*) } > TCMA
    __data_load = LOADADDR(.data);
    .data : { __data_start = .; *(.data*) . = ALIGN(4); __data_end = .; } > TCMB AT > TCMA
    .bss (NOLOAD) : { __bss_start = .; *(.bss*) *(COMMON) . = ALIGN(4); __bss_end = .; } > TCMB
    .stack (NOLOAD) : {
        . = ALIGN(8);
        . += 0x400; __stack_irq_top = .;
        . += 0x100; __stack_abt_top = .;
        . += 0x100; __stack_und_top = .;
        . += 0x200; __stack_svc_top = .;
        . += 0x2000; __stack_sys_top = .;
    } > TCMB
    PROVIDE(end = __bss_end);
    __image_end = LOADADDR(.data) + SIZEOF(.data);
}
