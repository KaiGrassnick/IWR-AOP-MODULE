# IWR-AOP-MODULE firmware, co-simulation build. Needs arm-none-eabi-gcc (Ubuntu: gcc-arm-none-eabi libnewlib-arm-none-eabi).
CC = arm-none-eabi-gcc
CFLAGS = -mcpu=cortex-r4f -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -mlittle-endian -O2 -g \
         -Wall -Wextra -Wno-unused-parameter -ffunction-sections -fdata-sections -ffreestanding
LDFLAGS = -T linker.ld -nostartfiles -Wl,--gc-sections -specs=nano.specs -specs=nosys.specs -Wl,--no-warn-rwx-segments
SRC = src/startup.S src/main.c src/soc.c src/sci.c src/i2c.c src/pmic.c src/radar.c

# fw.elf: normal build. fw_debug.elf: adds the raw PMIC write command (debugUnlock, pmicWrite).
all: build/fw.elf build/fw_debug.elf

build/fw.elf: $(SRC) src/*.h linker.ld
	@mkdir -p build
	$(CC) $(CFLAGS) $(SRC) $(LDFLAGS) -Wl,-Map=build/fw.map -o $@
	arm-none-eabi-size $@

build/fw_debug.elf: $(SRC) src/*.h linker.ld
	@mkdir -p build
	$(CC) $(CFLAGS) -DFW_DEBUG_PMIC $(SRC) $(LDFLAGS) -Wl,-Map=build/fw_debug.map -o $@
	arm-none-eabi-size $@

clean:
	rm -rf build
.PHONY: all clean
