# C6 J280 lettering candidate

Read MEMORY.md, README_CN.md and API-Contract.md first. Target is J280 GW2A-LV55PG484C8/I7 at50MHz, firmware56354336. This is an offline ModelSim/Simulink validated candidate, not physical acceptance. The owner currently postpones programming. Never open COM/BLE/JTAG or start the motor as part of offline verification.

Preserve protected ADC/encoder/PWM/safety/CST/SDC and original PID49/0/251,25/0/610. Keep approved GOWIN input CRC8FC95613 unchanged. EasyStart R3 input is a separate MATLAB-authored508knot program CRC4A124384. Do not paint target fonts over measured traces. New code or input changes need matching protocol, actual RTL validation and a fresh native implementation when the FPGA source changes. Preserve old failed cases and report model-specific limits. Use English identifiers/comments and Chinese user-facing guidance.
