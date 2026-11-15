# IWR-AOP-MODULE firmware, co-simulation build. Needs arm-none-eabi-gcc (Ubuntu: gcc-arm-none-eabi libnewlib-arm-none-eabi).
CC = arm-none-eabi-gcc
CFLAGS = -mcpu=cortex-r4f -mfpu=vfpv3-d16 -mfloat-abi=hard -marm -mlittle-endian -O2 -g \
         -Wall -Wextra -Wno-unused-parameter -ffunction-sections -fdata-sections -ffreestanding
LDFLAGS = -T linker.ld -nostartfiles -Wl,--gc-sections -specs=nano.specs -specs=nosys.specs -Wl,--no-warn-rwx-segments -Wl,-Map=build/fw.map
SRC = src/startup.S src/main.c src/soc.c src/sci.c src/i2c.c src/pmic.c src/radar.c

build/fw.elf: $(SRC) src/*.h linker.ld
	@mkdir -p build
	$(CC) $(CFLAGS) $(SRC) $(LDFLAGS) -o $@
	arm-none-eabi-size $@

clean:
	rm -rf build
.PHONY: clean
