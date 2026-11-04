# C6 closed-loop lettering specification

## Objective and constraints

Correct the actual current production lettering path so actual encoder-based GOWIN andEasyStart traces are recognizable and quantitatively follow their intended states under the existing frozen representative plant. Keep all accepted releases immutable. No hardware actions. A model-only result doesnot prove hardware performance.

## Sequence

1. AuditC5versusC03. Freeze baseline source/input hashes, originalplant.m,5mssample,one-sampleinterface delay,original-kit mapping and20/22.4s displaywindows. Run actualC5 ModelSim/Simulink before changing productioncode.
2. Separate reference-path fidelity from plant/controller following. Use independent MATLAB calculations only for analysis and bounded candidate screening,then validate chosen actualRTL with Simulink. Do not modify plant coefficients,visual coordinate geometry,or output filtering to force a pass.
3. Prefer simple actual reference/rate corrections using existing primitives. Keep ADC/PWM/encoder/safety and anglePID frozen. If trajectory timing or position-loop settings must change,declare them,maintain explicit defaults/readback,report latency,and verify stable transitions;never silently tune simulation-only gains while shipping differentRTL.
4. Run both fullword jobs includingpre-motion andreturn. Retain rawdata andnativeModelSim PS/WLF plusMATLAB/Simulink nativeexports. Regressiontest job admission,upload,STOP/cancel andunchangedHOLD behavior asapplicable.
5. Freeze successful candidate,check meaningful adverse input/model variation without redefining the nominalplant,then run source-bound synthesis gate and final implementation when applicable. No programming.

## Predeclared acceptance

Bothwords: no nonfinite values or protectionfaults,actualmotor bounded6900,completeactualRTLtransaction counts,returnwithinproduction settlecriteria. Worddata remain causally generated;no target->encoderfeed,no posthoc lagselection,smoothing,fontreplacement orplot-axistrick.

For nominal-word windows compare same-time target and actualENC-derived state coordinates: positionRMS<=5%ofintendedwordwidth;actualspan>=85%and<=115%ofintendedspan;meanvelocityRMS<=10%ofintendedverticalspan;bothcoordinatescorrelation>=0.95. Rawfigures mustalso show recognizableletterstructure; numeric thresholds alone donot establish readability. Any declaredglobaltimescalemustapplytoall programtime andwindowT before run,withreportedlatency. Keep original-rate result alongside slowedvariants; do not hide failures. Check final candidate at bothencoderkit mappings andstatedinitialconditions where feasible.

## Scope of possible delivery

Sourcepatch,matching configuration/host changes if needed,old/new nativefigures,metrics,model/bench/runfiles andexplicitremaininghardwarelimits. Do not deliver originalC5FSasifitincludes C6changes. Ifnativeimplementationhasnotpassed,no qualifiednewFSclaim.

## Owner steering: revise EasyStart only

The owner approved the current GOWIN shape and rejected EasyStart readability. Keep GOWIN input and both controller/plant implementations frozen. Author a clearer single-line EasyStart INPUT skeleton with distinguishable E crossbars, a loops, s/S curves, y descender, t crossbars and r shoulder. Increased nominal EasyStart width160counts and vertical encoding span48counts are declared before screening; the original95s timeline and44.8s averaging window remain. No postprocessing may substitute the new font for actual feedback. The old EasyStart traces remain as failure/comparison evidence.

For the revised EasyStart, use its declared160count intended width in the same5percentRMS and85to115percentspan criteria; velocity error remains atmost10percentofreferenceverticalspan andbothcorrelationsatleast.95. Independently review the exact quantized reference and actual closed-loop trace: matching an unreadable reference alone doesnot close this request. Keep full per-gesture safety/time/travel limits and all GOWIN data unchanged. If the declared input cannot satisfy existing constraints, preserve that failed attempt before a new input proposal.

Font attempt1 was rejected by the original Q4 guards atnode13: the increased first position cannot be reached using the old21knot bridge. Its source and failure log are preserved inEasyStart_Font2/attempt1. Before retry, declare seek35, interphase bridge45, return30, writing195knots:508points/101s total,55.6s pre-motion,39s writing,T48s. The original512point/150sjob limits, motion derivative limits and mechanical range remain unchanged. This is additional input time allocation, not a relaxed guard.

The first feasible input used4convolutions, which erased important short crossbars. Later geometry keeps only1input convolution, allocates extra time at turns and uses10knot start/end speed ramps. The input integer projection may move a knot by atmost1count to satisfy the unchanged original discrete speed/acceleration/jerk bounds; retain the actual maximum error. Failed tighter.125count projections atnodes42/48 are retained. This changes neither measured output nor any hardware guard.

The native MATLAB convex projection proved a1count corridor infeasible. Replace the pruned beam authoring search with a whole-input least-squares projection using installedquadprog and conservative rounding margins95/30/12Q4LSB before rounding to the unchanged96/32/16limits. Declare a4count maximum authoring deviation before inspection/closed-loop execution; report the actual value and reject a larger deviation. This input-authoring change is offline only, adds no FPGA or Host solver, and doesnot relax any hardware constraint.
