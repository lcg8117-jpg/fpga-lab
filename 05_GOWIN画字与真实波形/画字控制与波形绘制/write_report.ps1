$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$native=Get-Content -LiteralPath "$taskRoot/reports/NATIVE_RESULT.json" -Raw | ConvertFrom-Json
$digital=Get-Content -LiteralPath "$taskRoot/Regression/RESULT.json" -Raw | ConvertFrom-Json
$final=Get-Content -LiteralPath "$taskRoot/EasyStart_Font2/results/FINAL_CHECK.json" -Raw | ConvertFrom-Json
$audit=Get-Content -LiteralPath "$taskRoot/EasyStart_Font2/results/native_crosscheck.json" -Raw | ConvertFrom-Json
if(-not $native.accepted -or $digital.status -ne 'PASS' -or $final.status -ne 'PASS' -or $audit.status -ne 'PASS'){throw 'Final engineering gates are not complete'}
$hostLog=Get-Content -LiteralPath "$taskRoot/reports/host_tests.log" -Raw
if($hostLog -notmatch 'Ran 44 tests' -or $hostLog -notmatch '(?m)^OK\s*$'){throw 'Matched Host tests have not passed'}
$sourceInputs=$native.source_sha256
foreach($entry in $sourceInputs.PSObject.Properties){
 $path=Join-Path "$taskRoot/Source/V5/project" $entry.Name
 if((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLower() -ne $entry.Value){throw "Native source binding changed: $($entry.Name)"}
}
$plantHash=(Get-FileHash -LiteralPath "$taskRoot/EasyStart_Font2/word_plant.m" -Algorithm SHA256).Hash.ToLower()
if($plantHash -ne 'bbe9f8fabc2a4b648affe970d9c359db72c215fe4ea019a463d306b0812a4bd7'){throw 'The frozen nominal plant changed'}
$g=$audit.results[0];$e=$audit.results[1]
$qualification=[ordered]@{status='OFFLINE_ENGINEERING_PASS';firmware='56354336';hardware_actions=0;physical_validation=$false;
 owner_approved_gowin_preserved=$true;revised_easystart_owner_approval='Not yet obtained; actual feedback image supplied';
 native_source_bound=$true;native_result='reports/NATIVE_RESULT.json';bitstream_sha256=$native.bitstream_sha256;
 unchanged_nominal_plant_sha256=$plantHash;host_tests=44;digital_base_cases=10;final_font_digital_cases=12;
 native_wlf_rows=$audit.native_rows;independent_pid_mismatches=0;plot_model_same_graph=$audit.review_model_functionally_identical;
 criteria=$audit.results;scope='Given representative count-domain model with actual RTL. No physical mechanics/ADC/PWM acceptance.'}
$qualification | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$taskRoot/reports/QUALIFICATION.json" -Encoding utf8NoBOM
$report=@"
# C6 J280 字形闭环修改与验收

本轮已修改代码并完成 ModelSim / Simulink 闭环验证、原始波形交叉核对及高云完整实现。全程没有连接串口、蓝牙、JTAG，没有烧录或驱动电机。结论适用于给定代表性模型，不是实物验收。

## 当前交付

固件身份56354336，J280 / GW2A-LV55PG484C8/I7 / 50MHz。GOWIN 保留用户已认可的输入及闭环结果；EasyStart 改成 R3 单线输入字形，增加间距、y 下伸笔画和 t 横画。主结果在 EasyStart_Font2，Final 中的 EasyStart 是修字形前的对照，不能混称新版。

|项目|GOWIN|EasyStart R3|
|---|---:|---:|
|节点 / 模式 CRC|438 / 8FC95613|508 / 4A124384|
|先行 / 书写 / 总目标时长|45.6 / 36 / 87秒|55.6 / 39 / 101秒|
|窗口平均角速度 T|40秒|48秒|
|输入名义宽度|108计数|160计数|
|全程最大跟踪误差|4计数|5计数|
|字形时段位置 RMS|$([Math]::Round($g.same_time_position_rms_counts,6))计数|$([Math]::Round($e.same_time_position_rms_counts,6))计数|
|实际字形宽度|$($g.actual_span_counts)计数|$($e.actual_span_counts)计数|
|X / Y 同时刻相关系数|$([Math]::Round($g.x_correlation,6)) / $([Math]::Round($g.y_correlation,6))|$([Math]::Round($e.x_correlation,6)) / $([Math]::Round($e.y_correlation,6))|
|平均速度 RMS / 参考纵向范围|$([Math]::Round(100*$g.mean_velocity_rms_fraction,3))%|$([Math]::Round(100*$e.mean_velocity_rms_fraction,3))%|
|模型角度状态峰值|$([Math]::Round($g.maximum_angle_counts,3))计数|$([Math]::Round($e.maximum_angle_counts,3))计数|
|电机请求峰值 / 触及6900的采样数|$($g.maximum_motor_request) / $($g.motor_limit_samples)|$($e.maximum_motor_request) / $($e.motor_limit_samples)|

两路均120秒、24000次真实 RTL 控制事务，最终参考和编码器回到0，进入原到位确认状态。GOWIN 的全部24001行数据与批准版本逐值相同。新版 EasyStart 的美观程度仍应由用户依据蓝色真实反馈图判断；它保留了整数分辨率的台阶，未伪造成光滑字体。

## 根因与修改

准确复现 C5 后，参考自身已能形成原有连笔轮廓，实际跟随仍明显滞后。C5 同模型的字形时段位置 RMS 为27.627085 / 24.454693计数。单独增加位置 P/D 的分析不能同时满足两个坐标要求，因此没有改 PID。

显式文字模式1把节点执行周期改成200ms，保留原100ms模式0；新增滤波输入前馈，只改变 PID 的虚拟位置误差，物理编码器、名义参考和真实跟踪误差分别保留。系数来自原模型：速度先行5.35秒、加速度先行3.3238125秒平方、1/16输入滤波。补偿绝对值256、复合虚拟目标345计数等新检查遇限故障，不隐藏裁剪结果。原 ADC、编码器、PWM、安全、CST、SDC 以及49/0/251、25/0/610未改。

EasyStart 的旧输入本身过于连成起伏曲线，已重新编写单线骨架。MATLAB 仅离线优化输入节点，使其满足原速度/加速度/jerk限制；未把求解器放入 FPGA 或上位机运行链。R3输入累计路程993.375计数，小于原1040限制；最大输入投影偏差1.96509计数，在预先声明的4计数界内。它与旧输入不同，不把不同字形的 RMS 差异包装成相同工况的百分比提升。

## 验证证据

- 实际 ModelSim RTL 与 Simulink 闭环：固定5ms、一个采样周期接口延迟、原模型系数，完整先行/书写/返回。
- 原生 WLF 重新抽取48000行，每行8项；与 Simulink CSV 一致。独立原 PID 数值重算与实际电机请求逐次一致，无时间平移拟合。
- Simulink展示模型与计算模型的39个模块、74条连接及计算参数相同。ModelSim图从原生PS导出，MATLAB原生FIG/PDF/PNG、SLX/MAT/CSV齐备；没有桌面截图加工。
- 10组基础 HDL 回归通过，包括普通模式、连续任务衔接、两套映射、正反方向、3个起点、取消与保护。R3输入额外完成两套映射/正反方向/3个起点的12组精确Q16参考验证。
- 真实串口引脚仿真补测了模式1上传、模式绑定CRC、独立读回、故障态暂存和STOP保留数据。Host的44项离线测试通过，没有打开实物串口。
- Kit1 负初始扰动联合仿真是改 EasyStart 前的补充映射证据；不能用它声称新版字形已在 IP570 实物通过。当前R3完整闭环使用原套件计数映射。

## 高云实现

同 C5 器件、约束和实现选项，先过综合时序门槛再布局布线。最终50.044MHz，最小setup余量+0.018ns，hold+0.205ns，所有STA违例数为0。资源：Logic39069/54720，Register16765/42000，CLS23120/27360，BSRAM10/140，DSP13/20，PRIMARY3/8，LW8/8。

新位流 SHA256：$($native.bitstream_sha256)。它与当前 FPGA 源码绑定，不是把旧 C5 位流改名。第一轮在机器可用内存低于既定门槛时主动退出；相关记录保留，释放本任务已完成的仿真进程后以同样选项重新完整编译成功，没有调整约束或随机搜索实现参数。

## 限制与剩余项

这是未辨识实物的计数域模型。完整 ADC 采样窗口、电机/PWM 电气、机械摩擦和真实负载尚未纳入闭环模型。输入前馈依赖默认 PID 和该模型，不能称作在线辨识或自适应学习。更换 PID、采样时延或机械结构需重新验证。

位置跟踪改善同时增加了角度状态和电机输出需求；新版 EasyStart 有$($e.motor_limit_samples)个采样触及6900限幅。不能据此声称所有稳定性指标全面改善。最终内部时序余量较窄，外部ADC接口时序缺项仍保留。后续上板需要校准和实测；按用户当前要求，本轮不烧录。

## 使用

源码及匹配上位机见Source，运行方式见Source/README_CN.md。主结果看EasyStart_Font2/model_exports/EasyStart_actual_only.png，完整对照看EasyStart_word.png。原GOWIN结果见Final/model_exports/GOWIN_word.png。完整机器可读判定见reports/QUALIFICATION.json；保留此前失败输入、修字形前结果及内存门槛退出记录。
"@
$report | Set-Content -LiteralPath "$taskRoot/VERIFICATION_CN.md" -Encoding utf8NoBOM
$qualification | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$taskRoot/reports/ACTIVE_CHECKPOINT.json" -Encoding utf8NoBOM
Write-Output 'C6_SOURCE_BOUND_REPORT_PASS'
