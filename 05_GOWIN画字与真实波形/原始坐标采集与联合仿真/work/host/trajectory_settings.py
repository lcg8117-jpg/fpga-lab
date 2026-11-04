"""Host-side bounds matching the standalone V5 trajectory contract."""
CHOICES={1:{80,160,320},2:{400,800,1600},6:{0,1},
         8:{40000},9:{1}}

def validate(field,value):
    if type(value) is not int:
        raise ValueError('轨迹参数必须为整数')
    valid=(60<=value<=260) if field==0 else (value==416) if field==7 else value in CHOICES.get(field,set())
    if not valid:
        raise ValueError('轨迹参数越界；速度60..260、固定幅度416、加速度80/160/320、加加速度400/800/1600、固定40000毫秒及1次')
    return value
