# GOWIN Real-Coordinate Source Index

## Current owner confirmation

On 2026-11-04, the owner confirmed that lettering is currently successful and the visual result is good. This is the current project status. Older records are retained only to identify versions, inputs and data sources. No new field trial is performed during this classification.

## A. Word control and measured waveform generation

Source archive found at `J280/C6_Presentation_Delivery_20261030/J280_C6_Complete_With_Figures_20261030.zip` in the original workspace. The copy is [画字控制与波形绘制](../05_GOWIN画字与真实波形/画字控制与波形绘制/).

| Role | Copied source | Evidence |
| --- | --- | --- |
| Word input construction | [word_trajectory.py](../05_GOWIN画字与真实波形/画字控制与波形绘制/Source/host/word_trajectory.py) | `generate` creates motion inputs; `measured_trace` uses timestamped encoder endpoints for measured output |
| Word host interaction | [word_editor.py](../05_GOWIN画字与真实波形/画字控制与波形绘制/Source/host/word_editor.py) | Original host code and GOWIN input are retained in the same Source tree |
| Position / angle time traces | [motion_plot.py](../05_GOWIN画字与真实波形/画字控制与波形绘制/Source/host/motion_plot.py) | Samples come from control history or coherent BLE frames; missing samples remain gaps |
| Physical field-log provenance | [C4_GOWIN_PROVENANCE.json](../05_GOWIN画字与真实波形/画字控制与波形绘制/Source/evidence/C4_GOWIN_PROVENANCE.json) | Original GOWIN log snapshot provenance and field names, with matching `C4_GOWIN_saved_rows.npz` |
| Native simulation and plot export | [export_word_results.m](../05_GOWIN画字与真实波形/画字控制与波形绘制/Simulation/EasyStart_R3/export_word_results.m) | Closed-loop encoder data and actual RTL reference are exported in separate columns / panels; WLF, MAT, CSV and SLX remain in this tree |

Measured coordinates use the actual ENC and a fixed trailing average-velocity window. Font definitions and interpolation in the input generator create reference commands; they do not overwrite encoder coordinates. Encoder kit conversion and axis scaling are unit / viewport handling, not glyph presentation remapping.

## B. Actual-coordinate sequential GOWIN chain

Original local source: `J280/V5_G_Regression_20261102/sequential_motion`. Copy: [原始坐标采集与联合仿真](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/).

| Role | Copied source | Evidence |
| --- | --- | --- |
| Measured coordinate calculation | [measured_sequence.py](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/host/measured_sequence.py) | `x = ENC - fixed_origin`; `y = (ENC(t) - ENC(t-T)) * 2*pi / (CPR*T_seconds)` |
| Original raw-coordinate viewer | [sequence_viewer.py](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/work/host/sequence_viewer.py) | Reads raw-frame records, uses SequenceTrace points directly, and rejects mapped glyph exports |
| Saved-data reconstruction | [build_native_replay.py](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/tools/build_native_replay.py) | Reads completed native CSV and preserves frame, phase, origin and source-index provenance |
| MATLAB export | [export_native.m](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/tools/export_native.m) | Recomputes actual coordinates from `FULL_1KHZ.csv`, independently of the later glyph display |
| Native execution source / data | [sequence_r03](../05_GOWIN画字与真实波形/原始坐标采集与联合仿真/cosim/cases/sequence_r03/) | Source snapshot, execution receipt, FULL_1KHZ.csv, RAW_TRACE.mat and actual_rtl.wlf |

The selected sequence_r03 sample is native RTL / Simulink feedback, identified as `conditional_model`. It is categorized separately from the physical-log sample. The copied source keeps original tool paths and frozen dependencies; configuring another simulator installation is outside this classification. The preparation helper also refers to the original adjacent balance_port baseline.

## Mapped presentation versions: index only

The following originals stay in the existing workspace and are not copied into this real-coordinate selection:

- `J280/V5_G_Regression_20261102/sequential_motion/work/host/sequential_glyph.py`
- `J280/V5_G_Regression_20261102/sequential_motion/work/host/sequential_glyph_viewer.py`
- `J280/V5_G_Regression_20261102/sequential_motion/work/host/glyph_visualization.py`
- `J280/V5_G_Regression_20261102/sequential_motion/work/host/word_visualization.py`
- `J280/V5_G_Regression_20261102/CURRENT_GOWIN_SEQUENTIAL_DISPLAY.json` (`display_mapping=true`)

The original physical-log fixture, original word controller and native sequential feedback are kept distinct in the catalog. No mapped presentation code is used to establish this source selection.
