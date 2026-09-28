# -*- coding: utf-8 -*-
"""采样深色模式截图关键点的颜色，定位异常区域"""
from PIL import Image

img = Image.open(r"C:\Users\HUAWEI\Desktop\苞米agent\ui-redesign\_shots\深色模式-优化后-实机截图.png").convert("RGB")
W, H = img.size
print("size:", W, H)

def sample(x_ratio, y_ratio, label):
    x = int(W * x_ratio / 1000)
    y = int(H * y_ratio / 1000)
    print(f"{label:28s} ({x_ratio:4d},{y_ratio:4d}) -> RGB{img.getpixel((x, y))}")

# 背景层次
sample(60, 400, "侧栏背景(应深墨)")
sample(300, 400, "中间背景(应深墨)")
sample(870, 300, "右栏背景(应略亮深墨)")
# 主按钮 新建对话 (约 70,105)
sample(70, 106, "新建对话按钮底")
# 搜索框下划线区
sample(150, 175, "搜索框文字区")
# 输入框下划线 (约 700, 950 中偏下) 与输入框背景
sample(500, 946, "输入框下划线")
sample(500, 920, "输入框背景")
# 能力卡片
sample(285, 585, "能力卡01背景")
sample(285, 575, "能力卡01文字")
# 品牌红块
sample(36, 40, "品牌标记红块")
# 顶栏
sample(700, 25, "顶栏背景")
# 模式按钮
sample(190, 902, "模式-自动")
# 发送按钮(右下 约965,948)
sample(958, 946, "发送按钮底")
# 资料库 导入按钮
sample(890, 90, "导入按钮底")
# 照片区
sample(500, 360, "照片区(caption附近)")
# toast/状态
sample(60, 952, "在线状态底")
