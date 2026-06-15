# Verilint Checker 列表

## 已集成的 Checker（共 8 个）

### 1. Register Checker (`Checker/register_checker.py`)
**功能**: 检测寄存器使用问题
- **REG001** - `use_before_drive`: 寄存器在被驱动前就被使用
- **REG002** - `drive_without_use`: 寄存器被驱动但从未使用
- **REG003** - `multi_drive`: 寄存器被多个并行块驱动

**关键特性**:
- 区分时序逻辑和组合逻辑
- 跨模块多驱动检测（只在同一模块内检查）
- 支持 instance 端口信号的驱动/使用追踪

---

### 2. Instance Checker (`Checker/instance_checker.py`)
**功能**: 检测模块实例化问题
- **INST001** - `floating_port`: 接口悬空（未连接）
- **INST002** - `constant_branch`: 输入用于分支判断但连接常数
- **INST003** - `reversed_connection`: input/output 接反

**关键特性**:
- 支持位置端口连接和命名端口连接
- 需要子模块定义来确定端口方向
- 保守策略：未知端口方向的信号视为既被驱动也被使用

---

### 3. Glitch Checker (`Checker/glitch_checker.py`)
**功能**: 检测组合逻辑毛刺（竞争冒险）
- **GLT001** - `inverted_signal_pair`: 信号和其反相同时使用

**检测模式**:
```verilog
assign z = (sel & a) | (~sel & b);  // 毛刺风险
```

**关键特性**:
- 追踪中间变量反相关系
- 支持多级信号展开

---

### 4. Combdly Checker (`Checker/combdly_checker.py`)
**功能**: 检测组合逻辑敏感列表问题
- **CMB001** - `incomplete_sensitivity`: 敏感列表不完整
- **CMB002** - `missing_signal`: 缺少敏感信号
- **CMB003** - `extra_signal`: 多余的敏感信号

**关键特性**:
- 支持 `@*` 自动敏感列表（不报告为不完整）
- 正确解析显式敏感列表 `(a or b)`
- 只检查组合逻辑，跳过时序逻辑

---

### 5. FSM Checker (`Checker/fsm_checker.py`)
**功能**: 检测有限状态机问题
- **FSM001** - `dead_state`: 死状态（进入后无法离开）
- **FSM002** - `unreachable_state`: 不可达状态
- **FSM003** - `incomplete_case`: case 不完全覆盖
- **FSM004** - `missing_default`: 缺少 default
- **FSM005** - `not_one_hot`: 未使用 one-hot 编码
- **FSM006** - `invalid_transition`: 无效跳转
- **FSM007** - `state_overflow`: 状态值超出编码宽度

**关键特性**:
- 自动提取 FSM 状态图
- 检测死状态和不可达状态
- 支持 one-hot 编码检查

---

### 6. Branch Coverage Checker (`Checker/branch_coverage_checker.py`)
**功能**: 检测分支覆盖与可达性
- **BRH001** - `incomplete_case`: case 不完全覆盖
- **BRH002** - `missing_default`: 缺少 default
- **BRH003** - `unreachable_condition`: 条件不可达
- **BRH004** - `redundant_condition`: 冗余条件
- **BRH005** - `overlapping_condition`: 重叠条件
- **BRH006** - `missing_else`: 缺少 else
- **BRH007** - `empty_branch`: 空分支

**关键特性**:
- 分析条件表达式可达性
- 检测冗余和重叠条件
- 检查 case/if 完整性

---

### 7. Reset Checker (`Checker/reset_checker.py`)
**功能**: 基于 CycleTable 的复位检测（保守策略）
- **RST001** - `not_reset`: 信号未复位
- **RST002** - `unstable`: 条件依赖导致状态不稳定

**关键特性**:
- 只检查复位条件分支中的赋值
- 保守策略：宁可误报，也不漏报
- 基于 CycleTable 分析

---

### 8. Array Bound Checker (`Checker/array_bound_checker.py`)
**功能**: 检测数组越界访问
- **ARR001** - `array_index_out_of_bounds`: 数组索引越界
- **ARR002** - `bit_select_out_of_bounds`: 位选择越界
- **ARR003** - `vector_out_of_bounds`: 向量访问越界

**关键特性**:
- 分析数组声明的维度
- 检测常量索引是否越界
- 支持位选择和数组索引检查

---

## 综合检查器

### Verilint Checker (`verilint_checker.py`)
**功能**: 整合所有 8 个检查器，统一接口和错误格式

**输出格式**:
- **Text**: 人类可读的报告
- **JSON**: VSCode 集成格式
- **LSP**: Language Server Protocol 格式

**使用方式**:
```bash
# 文本输出
python test_verilint.py file.v

# JSON 输出（VSCode）
python test_verilint.py file.v --json

# LSP 输出
python test_verilint.py file.v --lsp

# 调试模式
python test_verilint.py file.v --debug
```

**Python API**:
```python
from verilint_checker import check_file

# 检查文件
issues = check_file("path/to/file.v", debug=False, output_format="json")

# 处理结果
for issue in issues:
    print(f"Line {issue.line}: [{issue.code}] {issue.message}")
```

---

## 错误代码对照表

| 代码 | 类别 | 描述 | 严重程度 |
|------|------|------|----------|
| REG001 | Register | Use before drive | Error |
| REG002 | Register | Drive without use | Warning |
| REG003 | Register | Multi-drive | Error |
| INST001 | Instance | Floating port | Warning |
| INST002 | Instance | Constant branch | Error |
| INST003 | Instance | Reversed connection | Error |
| GLT001 | Glitch | Inverted signal pair | Warning |
| CMB001 | Combdly | Incomplete sensitivity | Error |
| CMB002 | Combdly | Missing signal | Warning |
| CMB003 | Combdly | Extra signal | Info |
| FSM001 | FSM | Dead state | Error |
| FSM002 | FSM | Unreachable state | Warning |
| FSM003 | FSM | Incomplete case | Warning |
| FSM004 | FSM | Missing default | Warning |
| FSM005 | FSM | Not one-hot | Info |
| FSM006 | FSM | Invalid transition | Error |
| FSM007 | FSM | State overflow | Error |
| BRH001 | Branch | Incomplete case | Warning |
| BRH002 | Branch | Missing default | Warning |
| BRH003 | Branch | Unreachable condition | Warning |
| BRH004 | Branch | Redundant condition | Info |
| BRH005 | Branch | Overlapping condition | Warning |
| BRH006 | Branch | Missing else | Info |
| BRH007 | Branch | Empty branch | Info |
| RST001 | Reset | Not reset | Error |
| RST002 | Reset | Unstable | Warning |
| ARR001 | Array | Array index out of bounds | Error |
| ARR002 | Array | Bit select out of bounds | Error |
| ARR003 | Array | Vector out of bounds | Error |
| SYNTAX001 | Syntax | Parse error | Error |

---

## 集成方式

### VSCode 扩展集成
1. 使用 `--json` 输出格式
2. 解析 JSON 并转换为 VSCode Diagnostic
3. 显示在问题面板中

### LSP 集成
1. 使用 `--lsp` 输出格式
2. 符合 LSP Diagnostic 标准
3. 支持实时错误提示

### 命令行使用
```bash
# 单个文件检查
python test_verilint.py file.v

# JSON 格式（用于脚本处理）
python test_verilint.py file.v --json > result.json

# LSP 格式（用于 LSP Server）
python test_verilint.py file.v --lsp
```
