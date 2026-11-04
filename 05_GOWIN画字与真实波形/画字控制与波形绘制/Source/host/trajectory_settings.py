"""Host-side bounds matching the standalone V5 trajectory contract."""
CHOICES={1:{80,160,320},2:{400,800,1600},6:{0,1},
         8:{16000,20000,24000,30000,40000,48000,60000},9:{1,2}}

def validate(field,value):
    if type(value) is not int:
        raise ValueError('轨迹参数必须为整数')
    valid=(60<=value<=260) if field==0 else (10<=value<=416) if field==7 else value in CHOICES.get(field,set())
    if not valid:
        raise ValueError('轨迹参数越界；速度60..260、固定尺度10..416、加速度80/160/320、加加速度400/800/1600、周期16..60秒指定档、1..2周期')
    return value
