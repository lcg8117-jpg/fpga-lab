"""Source-audited writable ranges; board READ remains the value authority."""

PROFILES = ("SWM4",)
GAIN_NAMES = ("角度 P", "角度 I", "角度 D", "位置 P", "位置 I", "位置 D")
# (field ID, Chinese label, minimum, maximum), from the current base adapter.
COMMON = (
    (0, "正向力度 %", 1, 90), (1, "反向力度 %", 1, 90),
    (2, "正向时长 ms", 1, 1000), (3, "下垂确认 ms", 1, 1000),
    (4, "等待下垂 ms", 1, 10000), (5, "反向时长 ms", 1, 1000),
    (6, "滑行窗口 ms", 20, 10000), (8, "单次尝试数（固定1）", 1, 1),
    (9, "历史步长接口 ms", 0, 50),
)
PAIRED = ((0, "力度 %", 1, 90), (64, "第一组时长 ms", 1, 255),
          (65, "第二组时长 ms", 1, 255), (66, "第三组时长 ms", 1, 254))
SYSTEM = ((0, "起摆方案（固定SWM4）", 0, 0), (1, "手势方向映射位 0..7", 0, 7),
          (2, "左向为正 0/1", 0, 1), (3, "顺时针为正 0/1", 0, 1),
          (4, "手势开启 0/1", 0, 1), (5, "套件 0=1080 / 1=1040", 0, 1))
AUTORETURN = ((1, "自动回位开启 0/1", 0, 1), (14, "回位最大力度 %", 1, 35),
              (15, "回位超时 ms", 1000, 15000), (16, "静止确认 ms", 250, 1000),
              (17, "编码器容差", 0, 16), (18, "下垂容差 Q4", 1, 64),
              (19, "下垂跨度 Q4", 1, 64))
AUTORETURN_READS = ((0, "协议标识"), (2, "回位参考"), (3, "下垂参考 Q4"),
                    (4, "参考 epoch"), (5, "回位状态"), (6, "尝试序号"),
                    (7, "回位误差"), (8, "静止时间 ms"), (9, "驱动组数"),
                    (10, "结果"), (11, "参考有效"), (12, "参考零点 Q4"),
                    (13, "源启动 ms"), (20, "示教完成 ms"))
PLANNER = ((0, "速度上限 unit/s", 60, 520), (1, "加速度：80/160/320/640/1200", 80, 1200),
           (2, "跃度：400/800/1600/3200/12000", 400, 12000), (6, "历史方向接口 0/1", 0, 1),
           (7, "历史振幅接口 unit", 10, 520), (8, "历史周期接口 ms（离散）", 4000, 60000),
           (9, "历史周期数接口", 1, 8), (10, "指令间隔 ms（离散，默认1000）", 0, 2000))
PLANNER_CHOICES = {1: (80, 160, 320, 640, 1200), 2: (400, 800, 1600, 3200, 12000),
                   8: (4000, 5000, 6000, 8000, 10000, 12000, 16000, 20000, 24000, 30000, 40000, 48000, 60000),
                   10: (0, 200, 300, 500, 750, 1000, 1500, 2000)}


def fields_for(group):
    if group == 0:
        return COMMON
    return {251: PLANNER, 254: SYSTEM}.get(group, ())


def validate_value(group, field_id, value):
    for key, label, minimum, maximum in fields_for(group):
        if key == field_id:
            if not minimum <= value <= maximum:
                raise ValueError(f"{label} 范围 {minimum}..{maximum}")
            if group == 251 and field_id in PLANNER_CHOICES and value not in PLANNER_CHOICES[field_id]:
                raise ValueError(f"{label} 允许值 {PLANNER_CHOICES[field_id]}")
            return value
    raise ValueError("该配置字段不允许从此窗口写入")
