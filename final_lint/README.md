# Verilint - Verilog 静态分析工具

Verilint 是一个针对 Verilog HDL 的静态代码分析工具，用于检测常见的编码问题、潜在错误和风格违规。

## 功能特性

- **寄存器检查 (REG)** - 检测未初始化使用、多驱动、未使用寄存器等
- **实例化检查 (INST)** - 检测悬空端口、反向连接、未解析模块等
- **宽度检查 (WID)** - 检测位宽不匹配、截断、扩展等问题
- **FSM 检查 (FSM)** - 检测死状态、不可达状态、缺少默认分支等
- **分支覆盖检查 (BRH)** - 检测分支不完整、不可达条件等
- **复位检查 (RST)** - 检测缺少复位的寄存器
- **数组边界检查 (ARR)** - 检测数组越界访问
- **循环依赖检查 (CYC)** - 检测组合逻辑环路

## 安装

### 环境要求

- Python 3.8+
- Pyverilog
- Z3 Solver (可选，用于精确分支分析)

### 安装依赖

```bash
pip install pyverilog z3-solver
```

### 克隆项目

cd backend

## 命令行使用

### 基本用法

```bash
# 检查单个文件
python verilint_checker.py design.v

# 检查项目（递归扫描目录），这是配置文件项目
python verilint_checker.py 文件名 --project 项目名

```

### 常用选项

```bash
# 添加 include 路径
python verilint_checker.py design.v -I ./includes -I ./rtl

# 忽略特定错误代码
python verilint_checker.py design.v --ignore WID008 --ignore REG002

```

### 完整选项列表

| 选项                | 说明                  |
| ------------------- | --------------------- |
| `--project <dir>` | 指定项目根目录        |
| `-I <path>`       | 添加 include 搜索路径 |
| `--ignore <code>` | 忽略指定错误代码      |

## 错误代码参考

### 寄存器问题 (REG)

| 代码   | 描述               | 严重级别           |
| ------ | ------------------ | ------------------ |
| REG001 | 寄存器使用前未驱动 | Error              |
| REG002 | 寄存器驱动后未使用 | Warning (默认忽略) |
| REG003 | 寄存器多驱动       | Error              |

### 实例化问题 (INST)

| 代码    | 描述                                                            | 严重级别 |
| ------- | --------------------------------------------------------------- | -------- |
| INST001 | 端口悬空                                                        | Warning  |
| INST002 | 分支判断为常数                                                  | Warning  |
| INST003 | 输入/输出接反                                                   | Warning  |
| INST004 | 循环依赖                                                        | Error    |
| INST005 | 未解析模块（即模块未定义，一般是Pyverilog无法解析某个模块文件） | Error    |

### 位宽问题 (WID)

| 代码   | 描述             | 严重级别 |
| ------ | ---------------- | -------- |
| WID001 | 位宽不匹配       | Warning  |
| WID002 | 数据截断         | Warning  |
| WID003 | 数据扩展         | Info     |
| WID009 | 拼接宽度超过目标 | Warning  |

### FSM 问题 (FSM)

| 代码   | 描述         | 严重级别 |
| ------ | ------------ | -------- |
| FSM003 | case 不完整  | Info     |
| FSM004 | 缺少 default | Info     |
| FSM001 | 死状态       | Error    |
| FSM002 | 不可达状态   | Warning  |

### 分支覆盖问题 (BRH)

| 代码   | 描述         | 严重级别 |
| ------ | ------------ | -------- |
| BRH001 | case 不完整  | Warning  |
| BRH002 | 缺少 default | Info     |
| BRH003 | 不可达条件   | Warning  |
| BRH005 | 条件重叠     | Warning  |
| BRH006 | 缺少 else    | Info     |

### 复位问题 (RST)

| 代码   | 描述           | 严重级别 |
| ------ | -------------- | -------- |
| RST001 | 寄存器缺少复位 | Error    |

### 语法问题 (SYNTAX)

注意，这个语法错误是基于Pyverilog工具解析的，仅支持标准的Verilog-200，如果后续需要进行修改，建议参考Qihe框架以及本工具对于FSM的处理，最好更换语法解析工具进行重构

| 代码      | 描述     | 严重级别         |
| --------- | -------- | ---------------- |
| SYNTAX001 | 解析错误 | Error (默认忽略) |

### Z3 错误

如果看到 Z3 相关错误，可以忽略（不影响主要功能）：

```bash
# 静默 Z3 错误
export PYTHONWARNINGS="ignore"
```

## 参考

南京大学 Qihe [骑河 - 面向 Verilog 的静态分析](https://qihe.pascal-lab.net/)

## 工具重构改进建议

建议更换语法分析器，选择能够处理更多版本的Verilog代码语言

此外可以参考Qihe对于寄存器按位进行分析，将寄存器的每一位认为是单独的寄存器来处理，对于后续寄存器分析的准确性会有所提高。

更多可以参考Qihe对于框架的处理
