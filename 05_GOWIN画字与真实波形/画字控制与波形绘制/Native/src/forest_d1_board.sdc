create_clock -name clk_50m -period 20 -waveform {0 10} [get_ports {clk_50m}]
report_timing -setup -max_paths 500 -max_common_paths 1
# External ADC clock-to-data maximum and board skew are not supplied. No
# invented input delays or false-path exceptions hide this I/O limitation.
