# Verilint VSCode 扩展 - 快速开始

## 安装步骤

### 1. 准备环境
确保已安装：
- VSCode 1.74.0 或更高版本
- Node.js 和 npm
- Python 3.x

### 2. 编译扩展
```bash
cd vscode-verilint
npm install
npm run compile
```

### 3. 安装到 VSCode

**方法A - 直接运行（开发模式）：**
```bash
# 在 vscode-verilint 目录下
按 F5
```

**方法B - 打包安装：**
```bash
# 双击运行
install.bat

# 或在 vscode-verilint 目录下
npx vsce package --no-dependencies

# 然后在 VSCode 中安装生成的 .vsix 文件
```

### 4. 配置路径

打开 VSCode 设置 (Ctrl+,)，搜索 "verilint"，设置：

```json
{
  "verilint.executablePath": "E:/Verilint/final_lint/verilint_checker.py"
}
```

如果留空，扩展会自动在工作区中查找。

## 使用

### 自动检查
保存 Verilog 文件时自动运行检查（默认开启）。

### 手动运行
- 点击编辑器右上角的 ▶️ 按钮
- 或按 `Ctrl+Shift+P`，输入 "Run Verilint"
- 或点击状态栏的 "Verilint" 按钮

### 查看结果
- **编辑器内**：错误用红色波浪线标出
- **问题面板**：按 `Ctrl+Shift+M` 查看所有问题
- **悬停提示**：鼠标悬停查看详细错误信息

## 错误图标说明

| 图标 | 颜色 | 含义 |
|------|------|------|
| 🔴 红色 | Error | 必须修复的错误 |
| 🟡 黄色 | Warning | 建议修复的警告 |
| 🔵 蓝色 | Info | 仅供参考的信息 |

## 常见问题

**Q: 提示 "Verilint checker not found"**
A: 检查 `verilint.executablePath` 设置是否正确指向 `verilint_checker.py`。

**Q: 没有自动检查**
A: 检查设置 `verilint.runOnSave` 是否为 `true`。

**Q: Python 找不到**
A: 检查设置 `verilint.pythonPath` 是否正确。
