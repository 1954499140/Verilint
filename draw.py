import matplotlib.pyplot as plt
import numpy as np

# 数据
projects = ['Sha256', 'Verilog', 'ZipCpu', 'Biriscv', 'Riscv']
lines_k  = [1.183, 1.560, 8.924, 13.57, 13.93]
files    = [4,      13,      18,      39,       41]
times    = [7.56,   36.63,   37.61,   119.09,   140.15]

# 基础配置
plt.rcParams['figure.dpi'] = 400
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['axes.linewidth'] = 0.5
plt.rcParams['grid.alpha'] = 0.1

FONT_SIZE = 9
fig, ax = plt.subplots(figsize=(6, 3.5))

# 线性拟合（虚线延长）
def linear_fit_extend(x, y):
    z = np.polyfit(x, y, 1)
    p = np.poly1d(z)
    x_ext = np.linspace(0, np.max(x) * 1.2, 100)
    return x_ext, p(x_ext)

# 绘制点
ax.plot(lines_k, times, 'o', color='#0072b2', markersize=2)
ax.plot(files, times, 's', color='#e53935', markersize=2)

# 绘制延长虚线
x1, y1 = linear_fit_extend(lines_k, times)
ax.plot(x1, y1, '--', color='#0072b2', alpha=0.6, lw=0.7, label='代码行数（千行）')

x2, y2 = linear_fit_extend(files, times)
ax.plot(x2, y2, '--', color='#e53935', alpha=0.6, lw=0.7, label='文件数量')

# 文字
ax.set_xlabel('代码规模（千行 / 文件数）', fontsize=FONT_SIZE)
ax.set_ylabel('检测时间（s）', fontsize=FONT_SIZE)
ax.tick_params(axis='both', labelsize=FONT_SIZE)
ax.legend(fontsize=FONT_SIZE, frameon=False)

ax.grid(True, linestyle='--', linewidth=0.5)

# ======================================
# 终极修复：保证图片完整不裁切、不截断
# ======================================
plt.savefig(
    'time_final.png',
    bbox_inches='tight',
    pad_inches=0.1,
    dpi=400
)
plt.close()