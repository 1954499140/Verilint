# Verilint - VSCode Extension

Verilog 静态分析和 Lint 工具的 VSCode 集成。

## 功能

- 🔍 **实时代码检查**: 保存时自动运行 Verilint 检查
- 🐛 **可视化错误展示**: 在编辑器中直接显示错误、警告和信息
- 📊 **问题面板集成**: 所有问题汇总在 VSCode 问题面板
- 🎯 **CodeLens 支持**: 文件顶部提供快速运行按钮
- 🎨 **分类图标**: 不同类型的问题用不同图标区分

## 安装

### 1. 编译扩展

```bash
cd vscode-verilint
npm install
npm run compile
```

### 2. 安装到 VSCode

按 `F5` 在扩展开发模式下启动，或打包安装：

```bash
npm install -g @vscode/vsce
vsce package
# 然后在 VSCode 中安装生成的 .vsix 文件
```

### 3. 配置 Verilint 路径

在 VSCode 设置中配置：

```json
{
  "verilint.executablePath": "E:/Verilint/final_lint/verilint_checker.py",
  "verilint.pythonPath": "python"
}
```

如果不设置，扩展会自动在工作区中查找 `verilint_checker.py`。

## 使用

### 命令面板

- `Verilint: Check Current File` - 检查当前文件
- `Verilint: Clear All Diagnostics` - 清除所有诊断信息
- `Verilint: Show Output` - 显示详细输出

### 配置选项

| 设置 | 默认值 | 说明 |
|------|--------|------|
| `verilint.executablePath` | "" | Verilint 脚本路径 |
| `verilint.pythonPath` | "python" | Python 可执行文件路径 |
| `verilint.runOnSave` | true | 保存时自动检查 |
| `verilint.runOnType` | false | 输入时实时检查（可能较慢）|
| `verilint.showInfo` | true | 显示 INFO 级别的诊断 |

## 截图

### 错误可视化
- 错误: 红色波浪线
- 警告: 黄色波浪线
- 信息: 蓝色波浪线

### 悬停提示
鼠标悬停在错误位置可查看详细信息，包括错误代码和说明。

### 问题面板
所有问题按文件分类，点击可跳转到对应位置。

## 错误代码对照

| 代码 | 类型 | 说明 |
|------|------|------|
| REG001 | Error | 寄存器使用前未驱动 |
| REG002 | Warning | 寄存器驱动后未使用 |
| REG003 | Error | 寄存器多驱动 |
| WID002 | Error | 位宽不匹配（截断）|
| WID003 | Info | 位宽不匹配（扩展）|
| RST001 | Error | 信号缺少复位 |
| ... | ... | ... |

## 依赖

- VSCode 1.74.0 或更高版本
- Python 3.x
- Verilint 工具

## 开发

```bash
# 编译
npm run compile

# 监视模式
npm run watch

# 调试
按 F5 启动扩展开发模式
```
