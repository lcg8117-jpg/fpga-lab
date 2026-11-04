# C6 word tracking extension

Firmware UserCode56354336,wire5/schema46443405. Frame layouts and all prior mode0 fields remain unchanged. C5 is56354335 andmustnotbe accepted asC6bythematchedHost.

## Group252 staged program

WRITE field7 accepts integer0or1. It invalidates an old commit asother authorized configurationwritesdo,andstages a one-shot modefornextBEGIN. It doesnotmove the motor orchangePID. Unauthorized/busy writes are rejected withoutmutation. BEGIN consumes thependingmodeandresetsit to0; committedtracking_modeislatchedfortheentireprogram. READ field7 returnslatchedmode,includingafterCOMMIT. EveryHostupload explicitlywritesfield7beforeBEGIN,includingmode0.

Mode0: C5legacyQ4knots,20actual5mssamples/100mssegment,noinputcorrection. CRCseed=0x6a09e667 XOR count. All oldbounds/endpoints/order/travel/abort/timeoutchecksremain.

Mode1: explicitexperimentalwordtracking,40actual5mssamples/200mssegment. CRCseed=0x6a09e667 XOR count XOR1,sochangingmodecannotreuseacommitchecksum. SameQ4inputknots andoriginalphysical speed/acceleration/jerk/positionlimits;tempo2reducescommandrates. Filteredvelocity/accelerationinputleadadjustsonlythePIDvirtualpositionerror;nominaltarget,actualENCandtrackingerrorretainauditablephysicalmeanings. Thecontrollergains49/0/251,25/0/610remainunchanged. Feedforwardcorrectionmax256counts,compositevirtualgoalwithintheoriginaluploadedjob±345counts;violationsfault,never silentlycliporrepaintmeasuredoutput.

MatchedHostverifies READ0,1,2,3,4,7afterCOMMITbeforeenablingexplicitPLAY. Normalhanddrawusesmode0;GOWIN/EasyStartusemode1. STOP/ESTOP/fault/cancellationdonotautoRUN,replayorgrantmotorpermission. Read-onlygroup253field10exposessignedinputcorrectioncount.

## Time and plotting

TransportJSONsample_ms100describesthebaseknotgrid;tracking_mode1andplayback_sample_ms200explicitlydeclareactualexecutionrate. Metadataandstatecoordinateswindowsarescaledbeforeexecution: GOWIN87s/45.6spre-motion/36sword/T40s;EasyStart95s/49.6spre-motion/40sword/T44.8s. Hostgeneratestheseconsistentmetadatabeforeupload. Retain24000rows/120swordhistorywithoutchangingrate. No outputfontreplacement,interpolation,posthoclagoroutputfiltering.

ExistingC5quantization/INPUTsplinearepreserved. Modelingcoefficientsarenotphysicalplantidentification;nominalsimulationsuccessmustnotbeclaimedasfieldproof.

## Final EasyStart R3 input

The owner requested a clearer EasyStart while freezing GOWIN. EasyStart now uses508knots,CRC4A124384,width160counts,101stotal,55.6spre-motion,39swriting,T48s. GOWIN remains byte-identical at438knots/CRC8FC95613/87s/T40s. These are uploaded input changes and use the same C6 FPGA image; no second lettering solver is added to the FPGA. The Host loads the MATLAB-authored EasyStart knots and independently recomputes the exact planned preview and CRC. Maximum input projection deviation is1.96509counts within the declared4count authoring corridor. Actual feedback remains unmodified.
