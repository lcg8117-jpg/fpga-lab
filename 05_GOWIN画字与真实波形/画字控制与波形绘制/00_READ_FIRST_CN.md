# J280 C6 完整工程、绘图与截图资料

本包已合入最新无电脑名框架图、原生 Simulink 展示模型及 MATLAB / ModelSim 截图资料。原源码、配套上位机、C6 位流、仿真数据和验证报告同时包含在内。建议先解压到 D:/C6_Views 等英文路径，避免旧版 ModelSim 对中文路径的兼容问题。

## 查看框架图与 MATLAB 绘图

- 无电脑名框架图：Presentation/Simulink_framework_no_host.png 或 .pdf。
- 原生展示模型：Presentation/C6_Word_ClosedLoop_Capture.slx。
- 在 MATLAB R2025b 中打开并运行根目录 Open_MATLAB_Views.m，会打开框架和5个原始 FIG 图窗，保留供自行截图；不会启动仿真或驱动硬件。
- FIG、PNG、PDF 原始绘图均在 Simulation/EasyStart_R3/model_exports。GOWIN_word 和 EasyStart_word 为目标/RTL参考/实际反馈三层对照；两个 time_traces 为时域数据；EasyStart_actual_only 为新版实际反馈单图。

框架图只将 ModelSim 模块的 CommShowInfo 显示选项关闭，隐藏电脑名和端口号。39个模块、74条连接与计算参数核对一致。历史计算模型和对照数据保留；展示和文章插图请使用 Presentation 中的新版。

## 打开 ModelSim 自行截图

本机可双击根目录 Open_ModelSim.cmd。该文件使用已安装的 E:/Moildesim/win64/vsim.exe；其他电脑安装位置不同时，请修改这一行的程序路径。

它打开 Simulation/EasyStart_R3/results/word_font2.wlf 的已保存原始波形，默认显示 EasyStart。切换 GOWIN 时，在 ModelSim Transcript 执行：

```tcl
delete wave *
do native_exports/GOWIN_view.do
```

切回 EasyStart：

```tcl
delete wave *
do native_exports/EasyStart_view.do
```

这是只读查看已保存的120秒数据，不会重新编译、仿真或访问板卡。PS原生导出和波形视图DO文件在同级 native_exports 目录。

## 工程与使用边界

- 完整源码、匹配上位机和使用说明：Source。
- 位流：Bitstream/J280_C6_56354336.fs，身份0x56354336。
- 完整编译、布局布线、时序和模型验证结论：VERIFICATION_CN.md、reports/QUALIFICATION.json、Native/RESULT.json。
- 只修改框架显示和打包组织；不需要因此重新综合或布局布线。
- 此前未烧录，本次也没有烧录。代表性模型验证不能代替实物验收；EasyStart有3个采样触及电机指令限幅，详见原报告。

本包的新版框架图也替换了 Simulation/EasyStart_R3/model_exports 下对应的PNG/PDF。历史对照资料原样保留。所有交付文件的SHA256在 RELEASE_MANIFEST.json 中，包外 ARCHIVE_VERIFICATION.json 记录整体校验结果。
