# C6 project context

2026-10-30: The owner postponed programming and requested code correction against the existing ModelSim/Simulink closed-loop simulation. Current source descends from C5, not old C03. Its original PID and protected board paths are unchanged. Explicit tracking mode1 adds half-rate input playback and a filtered model-based input lead. Normal mode0 behavior remains separate.

The frozen plant is the supplied uncalibrated count-domain model. Nominal dynamics use actual RTL motor commands with5ms samples and one-sample interface delay. Full-board ADC/PWM timing and real mechanics are not represented; never claim physical success or adaptive identification from these results.

The owner approved GOWIN and rejected the old EasyStart. GOWIN remains438knots/CRC8FC95613/87s/T40s. The new EasyStart R3 is508knots/CRC4A124384/101s/T48s, nominal160count width. It is generated offline by MATLAB; Host validates static knots and independently recomputes the exact planned reference. Input projection deviation1.96509counts is within the declared4count authoring bound and all original motion limits. Feedback has no font replacement or output smoothing.

Fresh original-device native implementation accepted firmware56354336: Logic39069/54720,Register16765,BSRAM10,DSP13,finalFmax50.044MHz/setup+.018ns/hold+.205ns,zeroSTAviolations. FS SHA256951055f424414428073d8dd1887ebe581893ee7ed8085b1f970183180b06f26d. Native passing is not physical acceptance; setup margin is narrow. Original C5 and earlier failures remain preserved. Refer to the delivery verification report for final source-bound metrics; no programming or motor action occurred.
