# C6 匹配上位机

本窗口仅接受 J280 C6 固件 `0x56354336`。从上级目录运行 `python -B host/app.py --variant 5`；保留完整 `host`，包括 `vendor` 和 `easy_start_plan.json`。

GOWIN 保留已确认输入；EasyStart 使用 R3 单线输入。两者均由真实 ENC 及其窗口平均角速度显示反馈，保留量化台阶和缺帧。普通手绘仍使用模式 0，文字使用显式模式 1，CRC 包含该模式。

上传经唯一串口所有者和逐请求 ACK，完成后独立读回；不会自动 ZERO、RUN、播放或重发。详见[使用说明](../README_CN.md)与[上传合同](../API-Contract.md)。本轮只验证离线结果，没有烧录或实物验证。
