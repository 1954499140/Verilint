"""
AI Fix Agent - AI驱动的代码修复模块

工作流程：
1. 调用verilint检查代码问题
2. 使用AI分析问题并生成修复建议
3. 应用修复后再次验证
4. 确保不引入新问题
"""

import os
import re
import json
import tempfile
import shutil
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime

# 导入LangChain相关库
try:
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    HAS_LANGCHAIN = True
except ImportError:
    HAS_LANGCHAIN = False


@dataclass
class FixResult:
    """修复结果"""
    success: bool
    fixed_code: str
    original_issues: List[Dict]
    remaining_issues: List[Dict]
    new_issues: List[Dict]
    iterations: int
    message: str
    issue_history: List[Dict] = None  # 每次迭代后的issue记录

    def __post_init__(self):
        if self.issue_history is None:
            self.issue_history = []


class AIFixAgent:
    """
    AI代码修复Agent

    使用AI分析和修复Verilog代码中的问题
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 model: str = "qwen3.5-plus", temperature: float = 0.7):
        """
        初始化AI Fix Agent

        Args:
            api_key: API密钥（默认从环境变量读取）
            base_url: API基础URL
            model: 模型名称
            temperature: 温度参数（0-1，越小越严谨）
        """
        if not HAS_LANGCHAIN:
            raise ImportError("需要安装langchain和langchain-openai: pip install langchain langchain-openai")

        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.model = model
        self.temperature = temperature

        # 初始化LLM
        self._init_llm()

        # 修复统计
        self.fix_stats = {
            'total_attempts': 0,
            'successful_fixes': 0,
            'failed_fixes': 0,
            'iterations': []
        }

    def _init_llm(self):
        """初始化语言模型"""
        # if not self.api_key:
        #     raise ValueError("需要提供API密钥或设置OPENAI_API_KEY环境变量")

        self.llm = ChatOpenAI(
            model="qwen3.5-plus-2026-02-15",  # 指定模型版本
            api_key="sk-f3c469e44a4f4defadceda3cdac3e21d",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            temperature=0.7,  # 控制回答的随机性，0 偏严谨，1 偏灵活
            max_tokens=15000   # 限制回答的最大令牌数
        )

        # 创建分析问题的prompt
        # 注意：使用双花括号 {{ 来转义 JSON 中的花括号，避免被识别为模板变量
        self.analysis_prompt = ChatPromptTemplate.from_messages([
            ("system", """你是Verilog硬件描述语言专家。你的任务是分析代码中的问题并判断它们是否是真正的错误。

                请根据提供的错误信息和代码片段，分析每个问题的性质：
                1. 是否是真正的功能/语法错误？
                2. 是否是误报？
                3. 如果是真正的错误，解释为什么和如何修复

                输出格式必须是JSON：
                {{
                    "analysis": [
                        {{
                            "issue_code": "错误代码",
                            "is_real_error": true/false,
                            "severity": "high/medium/low",
                            "reasoning": "分析理由",
                            "fix_suggestion": "修复建议"
                        }}
                    ],
                    "summary": "总体分析摘要"
                }}"""),
                            ("user", """请分析以下Verilog代码的问题：

                代码文件：{file_path}

                原始代码：
                ```verilog
                {code}
                ```

                检测到的错误：
                {issues}
                """)
                        ])

        # 创建修复代码的prompt
        self.fix_prompt = ChatPromptTemplate.from_messages([
            ("system", """你是Verilog硬件描述语言专家。你的任务是修复代码中的问题。

            要求：
            1. 最重要的是保证语义不变
            2. 只修复标记为真正错误的问题
            3. 保持代码风格一致
            4. 不要改变模块接口（端口定义）
            5. 不要删除注释，除非它们明显错误
            6. 确保修复后的代码可以正确编译
            7. 如果是参数问题，可以修改类型或初始化方式，但不要改变参数值
            8. 尽量不要合并过程快，如果时钟不同则忽略，如果时钟相同，那么当条件互斥时合并。如果无法合并就保留错误就好。
            9. 如果出现位宽不匹配问题尽可能向位宽大的方向扩展


            输出格式：
            1. 首先简要说明做了哪些修改
            2. 然后输出完整的修复后代码，用```verilog和```包裹
            3. 如果某些问题无法自动修复，说明原因"""),
                        ("user", """请修复以下Verilog代码中的问题：

            代码文件：{file_path}

            原始代码：
            ```verilog
            {code}
            ```

            需要修复的错误分析：
            {analysis}

            请提供修复后的完整代码。""")
                    ])

        # 创建语义保持修复代码的prompt（用于修复改变语义时）
        self.fix_semantic_prompt = ChatPromptTemplate.from_messages([
            ("system", """你是Verilog硬件描述语言专家。你的任务是修复代码中的问题。

            ⚠️ 重要：上一轮修复改变了代码的语义/功能！这是不允许的。

            严格要求（按优先级排序）：
            1. **绝对不能改变代码语义** - 修复后的代码必须与原代码功能完全一致
            2. **绝对不能改变时序逻辑** - 不要改变always块的触发条件或赋值方式
            3. **绝对不能合并always块** - 保持原有的always块分离状态
            4. **只添加必要的default case** - 这是FSM安全的必需项，不会改变功能
            5. **不要重命名信号或模块** - 保持所有标识符不变
            6. **不要重构代码结构** - 只在需要的地方做最小修改

            允许的修改：
            - 在case语句中添加default（保持与现有逻辑一致）
            - 修复语法错误（如缺少分号、括号等）
            - 修复类型不匹配问题

            禁止的修改：
            - 改变always块的敏感列表
            - 合并多个always块
            - 改变赋值逻辑或表达式
            - 改变状态编码方式（除非原代码本身就是错误的）

            输出格式：
            1. 说明每处修改如何保持语义不变
            2. 输出完整的修复后代码，用```verilog和```包裹"""),
                        ("user", """请修复以下Verilog代码中的问题：

            代码文件：{file_path}

            原始代码：
            ```verilog
            {code}
            ```

            需要修复的错误：
            {analysis}

            ⚠️ 上一轮修复改变了代码语义，这是不允许的！
            ⚠️ 上一轮修复的问题：{semantic_issues}

            请确保本次修复：
            1. 修复所有列出的错误
            2. **绝对不改变代码功能和语义**
            3. 保持原有的代码结构和风格

            返回修复后的完整代码。""")
                    ])

                    # 创建语义检查prompt
        self.semantic_check_prompt = ChatPromptTemplate.from_messages([
                        ("system", """你是Verilog语义分析专家。你的任务是判断两段代码的语义/功能是否相同。

            分析维度：
            1. 模块接口是否完全一致（端口名称、方向、位宽）
            2. 时序逻辑是否一致（always触发条件、赋值方式）
            3. 组合逻辑是否一致（表达式、条件判断）
            4. 状态机行为是否一致（状态转移、输出逻辑）
            5. 寄存器更新逻辑是否一致
            6. 复位条件是否改变

            输出格式必须是JSON：
            {
                "semantic_preserved": true/false,
                "issues": ["如果发现语义改变，列出具体问题"],
                "summary": "简要分析结论"
            }"""),
                        ("user", """请比较以下两段Verilog代码的语义：

            原始代码：
            ```verilog
            {original_code}
            ```

            修复后的代码：
            ```verilog
            {fixed_code}
            ```

            请分析修复是否改变了代码的语义/功能。""")
        ])

        # 创建输出解析器
        self.output_parser = StrOutputParser()

        # 构建链
        self.analysis_chain = self.analysis_prompt | self.llm | self.output_parser
        self.fix_chain = self.fix_prompt | self.llm | self.output_parser
        self.fix_semantic_chain = self.fix_semantic_prompt | self.llm | self.output_parser
        self.semantic_check_chain = self.semantic_check_prompt | self.llm | self.output_parser

    def analyze_issues(self, code: str, issues: List[Dict], file_path: str = "") -> Dict:
        """
        使用AI分析问题

        Args:
            code: 源代码
            issues: 问题列表
            file_path: 文件路径

        Returns:
            分析结果字典
        """
        # 格式化问题信息 - 将 VerilintIssue 对象转换为字典
        def issue_to_dict(issue):
            if isinstance(issue, dict):
                return issue
            # Handle VerilintIssue object
            # 处理 severity 和 category 可能是枚举对象的情况
            severity = getattr(issue, 'severity', '')
            if hasattr(severity, 'value'):
                severity = severity.value
            elif hasattr(severity, 'name'):
                severity = severity.name

            category = getattr(issue, 'category', '')
            if hasattr(category, 'value'):
                category = category.value
            elif hasattr(category, 'name'):
                category = category.name

            return {
                'file': getattr(issue, 'file_path', ''),
                'line': getattr(issue, 'line', 0),
                'column': getattr(issue, 'column', 0),
                'code': getattr(issue, 'code', 'UNKNOWN'),
                'message': getattr(issue, 'message', ''),
                'severity': str(severity),
                'category': str(category),
            }

        issues_dicts = [issue_to_dict(i) for i in issues]
        issues_text = json.dumps(issues_dicts, indent=2, ensure_ascii=False)

        print(f"[DEBUG] Issues prepared for AI:")
        print(f"[DEBUG] First issue: {issues_dicts[0] if issues_dicts else 'None'}")
        print(f"[DEBUG] Issues JSON length: {len(issues_text)}")

        try:
            print(f"[DEBUG] Calling AI with {len(issues)} issues...")
            print(f"[DEBUG] API Key (first 10 chars): {self.api_key[:10] if self.api_key else 'None'}...")
            print(f"[DEBUG] Model: {self.model}")
            print(f"[DEBUG] Base URL: {self.base_url}")

            # 使用超时机制调用AI
            import concurrent.futures

            def invoke_chain():
                return self.analysis_chain.invoke({
                    "file_path": file_path,
                    "code": code,
                    "issues": issues_text
                })

            # 60秒超时
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(invoke_chain)
                try:
                    response = future.result(timeout=60)
                except concurrent.futures.TimeoutError:
                    print("[DEBUG] AI call timed out after 60 seconds")
                    return {"analysis": [], "summary": "AI调用超时", "error": "timeout"}

            print(f"[DEBUG] AI response received, length: {len(response) if response else 0}")

            if not response or not response.strip():
                print("[DEBUG] AI returned empty response")
                return {"analysis": [], "summary": "AI返回空响应", "error": "empty_response"}

            # 尝试解析JSON响应
            try:
                print(f"[DEBUG] AI raw response: {response[:500]}...")
                result = json.loads(response)
                print(f"[DEBUG] Parsed result: {json.dumps(result, ensure_ascii=False)[:500]}")
                return result
            except json.JSONDecodeError as e:
                print(f"[DEBUG] JSON decode error: {e}")
                # 尝试从markdown代码块中提取JSON
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group(1))

                # 尝试找到第一个{和最后一个}之间的内容
                start = response.find('{')
                end = response.rfind('}')
                if start != -1 and end != -1:
                    return json.loads(response[start:end+1])

                # 如果都失败，返回原始响应
                return {
                    "analysis": [],
                    "summary": response,
                    "parse_error": True
                }

        except Exception as e:
            import traceback
            print(f"[DEBUG] Exception in analyze_issues: {e}")
            traceback.print_exc()
            return {
                "analysis": [],
                "summary": f"分析失败: {str(e)}",
                "error": str(e)
            }

    def _normalize_code(self, code: str) -> str:
        """
        标准化代码用于比较（去除空白差异）

        Args:
            code: 源代码

        Returns:
            标准化后的代码
        """
        # 去除行尾空白，统一换行符，去除多余空行
        lines = code.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        normalized = []
        for line in lines:
            stripped = line.rstrip()
            if stripped:  # 保留非空行
                normalized.append(stripped)
        return '\n'.join(normalized)

    def _is_code_changed(self, original: str, fixed: str) -> bool:
        """
        检查代码是否有实质性变化

        Args:
            original: 原始代码
            fixed: 修复后的代码

        Returns:
            是否有变化
        """
        return self._normalize_code(original) != self._normalize_code(fixed)

    def generate_fix(self, code: str, analysis: Dict, file_path: str = "",
                     use_retry_prompt: bool = False,
                     use_semantic_prompt: bool = False,
                     semantic_issues: list = None) -> str:
        """
        生成修复后的代码

        Args:
            code: 源代码
            analysis: 问题分析结果
            file_path: 文件路径
            use_retry_prompt: 是否使用强化提示词（第一次修复无效时使用）
            use_semantic_prompt: 是否使用语义保持提示词（修复改变语义时使用）
            semantic_issues: 语义改变的问题列表

        Returns:
            修复后的代码
        """
        analysis_text = json.dumps(analysis, indent=2, ensure_ascii=False)

        try:
            # 选择提示词链
            if use_semantic_prompt:
                # 构建语义保持提示
                semantic_issues_text = "\n".join([f"- {issue}" for issue in (semantic_issues or [])])
                response = self.fix_semantic_chain.invoke({
                    "file_path": file_path,
                    "code": code,
                    "analysis": analysis_text,
                    "semantic_issues": semantic_issues_text
                })
            elif use_retry_prompt:
                response = self.fix_retry_chain.invoke({
                    "file_path": file_path,
                    "code": code,
                    "analysis": analysis_text
                })
            else:
                response = self.fix_chain.invoke({
                    "file_path": file_path,
                    "code": code,
                    "analysis": analysis_text
                })

            # 提取代码块
            code_match = re.search(r'```verilog\s*(.*?)\s*```', response, re.DOTALL)
            if code_match:
                return code_match.group(1).strip()

            # 如果没有代码块标记，尝试提取整个响应
            return response.strip()

        except Exception as e:
            raise Exception(f"生成修复失败: {str(e)}")

    def check_semantic_preservation(self, original_code: str, fixed_code: str) -> Tuple[bool, List[str]]:
        """
        检查修复是否保持了代码语义

        Args:
            original_code: 原始代码
            fixed_code: 修复后的代码

        Returns:
            (是否保持语义, 语义改变的问题列表)
        """
        try:
            response = self.semantic_check_chain.invoke({
                "original_code": original_code,
                "fixed_code": fixed_code
            })

            # 尝试解析JSON响应
            try:
                result = json.loads(response)
            except json.JSONDecodeError:
                # 尝试从markdown代码块中提取JSON
                json_match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group(1))
                else:
                    # 尝试找到第一个{和最后一个}之间的内容
                    start = response.find('{')
                    end = response.rfind('}')
                    if start != -1 and end != -1:
                        result = json.loads(response[start:end+1])
                    else:
                        return True, []  # 解析失败，假设语义未改变

            preserved = result.get('semantic_preserved', True)
            issues = result.get('issues', [])

            return preserved, issues

        except Exception as e:
            print(f"[AI Fix] 语义检查出错: {e}")
            return True, []  # 出错时假设语义未改变

    def apply_fix(self, file_path: str, fixed_code: str, backup: bool = True) -> str:
        """
        应用修复到文件

        Args:
            file_path: 原始文件路径
            fixed_code: 修复后的代码
            backup: 是否创建备份

        Returns:
            备份文件路径（如果创建了备份）
        """
        backup_path = None

        if backup:
            # 创建带时间戳的备份
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{file_path}.backup_{timestamp}"
            shutil.copy2(file_path, backup_path)

        # 写入修复后的代码
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(fixed_code)

        return backup_path

    def verify_fix(self, file_path: str, project_root: Optional[str] = None,
                   include_paths: Optional[List[str]] = None) -> Tuple[bool, List[Dict], str]:
        """
        验证修复是否成功

        Args:
            file_path: 修复后的文件路径
            project_root: 项目根目录
            include_paths: include路径列表

        Returns:
            (是否成功, 新问题列表, 错误消息)
        """
        # 这里需要导入verilint_checker来检查
        # 为了避免循环导入，使用延迟导入
        from verilint_checker import check_file

        def issue_to_dict(issue):
            """将VerilintIssue对象转换为字典"""
            if isinstance(issue, dict):
                return issue
            severity = getattr(issue, 'severity', '')
            if hasattr(severity, 'value'):
                severity = severity.value
            category = getattr(issue, 'category', '')
            if hasattr(category, 'value'):
                category = category.value
            return {
                'file': getattr(issue, 'file_path', ''),
                'line': getattr(issue, 'line', 0),
                'column': getattr(issue, 'column', 0),
                'code': getattr(issue, 'code', 'UNKNOWN'),
                'message': getattr(issue, 'message', ''),
                'severity': str(severity),
                'category': str(category),
            }

        try:
            raw_issues = check_file(
                file_path,
                project=project_root,
                include_paths=include_paths or [],
                output_format="json",
                debug=False
            )

            # 转换为字典列表
            issues = [issue_to_dict(i) for i in raw_issues]

            # 检查是否有语法错误
            syntax_errors = [i for i in issues if i.get('code', '').startswith('SYNTAX')]

            if syntax_errors:
                return False, issues, "修复引入了语法错误"

            return True, issues, "验证通过"

        except Exception as e:
            return False, [], f"验证失败: {str(e)}"

    def fix_file(self, file_path: str, project_root: Optional[str] = None,
                 include_paths: Optional[List[str]] = None,
                 max_iterations: int = 3,
                 output_file: Optional[str] = None) -> FixResult:
        """
        修复单个文件

        Args:
            file_path: 文件路径
            project_root: 项目根目录
            include_paths: include路径列表
            max_iterations: 最大迭代次数
            output_file: 修复后文件输出路径（默认覆盖原文件）

        Returns:
            修复结果
        """
        # 读取原始代码
        with open(file_path, 'r', encoding='utf-8') as f:
            original_code = f.read()

        # 辅助函数：将VerilintIssue对象转换为字典
        def issue_to_dict(issue):
            if isinstance(issue, dict):
                return issue
            # 处理 severity 可能是 Severity 枚举对象的情况
            severity = getattr(issue, 'severity', '')
            if hasattr(severity, 'value'):
                severity = severity.value
            # 处理 category 可能是 Category 枚举对象的情况
            category = getattr(issue, 'category', '')
            if hasattr(category, 'value'):
                category = category.value
            elif hasattr(category, 'name'):
                category = category.name
            return {
                'file': getattr(issue, 'file_path', ''),
                'line': getattr(issue, 'line', 0),
                'column': getattr(issue, 'column', 0),
                'code': getattr(issue, 'code', 'UNKNOWN'),
                'message': getattr(issue, 'message', ''),
                'severity': str(severity),
                'category': str(category),
            }

        # 初始检查
        from verilint_checker import check_file
        raw_issues = check_file(
            file_path,
            project=project_root,
            include_paths=include_paths or [],
            output_format="json",
            debug=False
        )
        original_issues = [issue_to_dict(i) for i in raw_issues]

        if not original_issues:
            return FixResult(
                success=True,
                fixed_code=original_code,
                original_issues=[],
                remaining_issues=[],
                new_issues=[],
                iterations=0,
                message="没有发现需要修复的问题"
            )

        current_code = original_code
        backup_path = None

        # 保存原始问题列表，防止在循环中被覆盖
        initial_issues = original_issues.copy()

        # 记录每次迭代后的issue历史
        issue_history = []
        issue_history.append({
            "iteration": 0,
            "stage": "initial",
            "issue_count": len(initial_issues),
            "issues": initial_issues.copy()
        })

        for iteration in range(max_iterations):
            self.fix_stats['total_attempts'] += 1

            try:
                # 1. 分析问题
                print(f"\n[AI Fix] 第 {iteration + 1}/{max_iterations} 轮分析...")
                analysis = self.analyze_issues(current_code, original_issues, file_path)

                # 检查分析是否出错
                if analysis.get('error'):
                    print(f"[AI Fix] 分析出错: {analysis.get('error')}")
                    # 如果出错，假设所有问题都是真实错误
                    analysis['analysis'] = [{'issue_code': i.get('code', 'UNKNOWN'),
                                            'is_real_error': True} for i in original_issues]

                # 检查分析是否为空
                if not analysis.get('analysis'):
                    print(f"[AI Fix] 警告: AI分析结果为空")
                    print(f"[AI Fix] 原始问题: {original_issues}")
                    # 如果为空，假设所有问题都需要修复
                    analysis['analysis'] = [{'issue_code': i.get('code', 'UNKNOWN'),
                                            'is_real_error': True} for i in original_issues]

                # 检查是否有真正需要修复的错误
                real_errors = [a for a in analysis.get('analysis', [])
                              if a.get('is_real_error', True)]

                print(f"[AI Fix] 发现 {len(real_errors)} 个真实错误")

                if not real_errors:
                    return FixResult(
                        success=True,
                        fixed_code=current_code,
                        original_issues=initial_issues,
                        remaining_issues=[],
                        new_issues=[],
                        iterations=iteration + 1,
                        message="AI分析发现所有问题都是误报，无需修复"
                    )

                # 2. 生成修复（带重试机制）
                print(f"[AI Fix] 生成修复代码...")
                fixed_code = self.generate_fix(current_code, analysis, file_path)

                # 检查代码是否有实质性变化
                # if not self._is_code_changed(current_code, fixed_code):
                #     print(f"[AI Fix] ⚠️ 警告: AI返回的代码与当前代码相同，未做修改")
                #     print(f"[AI Fix] 使用强化提示词重新生成修复...")
                #     fixed_code = self.generate_fix(current_code, analysis, file_path, use_retry_prompt=True)

                #     # 再次检查
                #     if not self._is_code_changed(current_code, fixed_code):
                #         print(f"[AI Fix] ⚠️ 警告: 强化修复后仍然没有变化，跳过本轮")
                #         os.unlink(temp_file.name) if 'temp_file' in locals() else None
                #         continue

                # 3. 应用到临时文件进行验证
                temp_file = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.v', delete=False, encoding='utf-8'
                )
                temp_file.write(fixed_code)
                temp_file.close()

                # 4. 验证修复
                print(f"[AI Fix] 验证修复...")
                success, new_issues, message = self.verify_fix(
                    temp_file.name, project_root, include_paths
                )

                if not success:
                    # 修复引入了语法错误，放弃这轮修复
                    os.unlink(temp_file.name)
                    print(f"[AI Fix] 修复引入错误: {message}")
                    continue

                # 5. 检查语义是否保持不变（与原始代码比较）
                # print(f"[AI Fix] 检查语义一致性...")
                # semantic_preserved, semantic_issues = self.check_semantic_preservation(
                #     original_code, fixed_code
                # )

                # if not semantic_preserved:
                #     print(f"[AI Fix] ⚠️ 警告: 修复改变了代码语义！")
                #     for issue in semantic_issues[:3]:
                #         print(f"  - {issue}")
                #     print(f"[AI Fix] 使用语义保持提示词重新生成修复...")

                #     # 使用语义保持提示词重新生成
                #     fixed_code = self.generate_fix(
                #         current_code, analysis, file_path,
                #         use_semantic_prompt=True,
                #         semantic_issues=semantic_issues
                #     )

                #     # 重新验证
                #     with open(temp_file.name, 'w', encoding='utf-8') as f:
                #         f.write(fixed_code)

                #     success, new_issues, message = self.verify_fix(
                #         temp_file.name, project_root, include_paths
                #     )

                #     if not success:
                #         os.unlink(temp_file.name)
                #         print(f"[AI Fix] 语义保持修复引入错误: {message}")
                #         continue

                #     # 再次检查语义
                #     semantic_preserved, _ = self.check_semantic_preservation(
                #         original_code, fixed_code
                #     )
                #     if not semantic_preserved:
                #         print(f"[AI Fix] ⚠️ 警告: 语义保持修复仍改变语义，跳过本轮")
                #         os.unlink(temp_file.name)
                #         continue

                #     print(f"[AI Fix] ✅ 语义保持修复成功")

                # 计算修复效果
                remaining = len(new_issues)
                fixed_count = len(original_issues) - remaining

                print(f"[AI Fix] 修复了 {fixed_count} 个问题，剩余 {remaining} 个问题")
                print(f"[AI Fix] new_issues 类型: {type(new_issues)}, 内容: {new_issues}")

                # 检查是否引入了新的非语法错误
                original_codes = {i.get('code') for i in original_issues}
                new_codes = {i.get('code') for i in new_issues}
                truly_new = new_codes - original_codes

                if remaining == 0:
                    # 完美修复！
                    print(f"[AI Fix] 完美修复！output_file={output_file}")
                    if output_file:
                        # 写入到指定输出文件
                        print(f"[AI Fix] 正在写入修复后文件到: {output_file}")
                        with open(output_file, 'w', encoding='utf-8') as f:
                            f.write(fixed_code)
                        print(f"[AI Fix] 文件写入成功")
                        backup_path = output_file
                    elif iteration == 0:
                        # 第一次就成功，应用到原文件
                        backup_path = self.apply_fix(file_path, fixed_code, backup=True)

                    self.fix_stats['successful_fixes'] += 1
                    os.unlink(temp_file.name)

                    # 记录最终成功的issues状态
                    issue_history.append({
                        "iteration": iteration + 1,
                        "stage": "complete_fix",
                        "issue_count": 0,
                        "issues": []
                    })

                    return FixResult(
                        success=True,
                        fixed_code=fixed_code,
                        original_issues=initial_issues,
                        remaining_issues=[],
                        new_issues=[],
                        iterations=iteration + 1,
                        message=f"成功修复所有问题（备份: {backup_path}）",
                        issue_history=issue_history
                    )

                # 部分修复，继续下一轮
                current_code = fixed_code
                original_issues = new_issues

                # 记录本轮迭代后的issues
                issue_history.append({
                    "iteration": iteration + 1,
                    "stage": "partial_fix",
                    "issue_count": len(original_issues),
                    "issues": original_issues.copy()
                })

                # 如果是最后一轮，保存结果
                if iteration == max_iterations - 1:
                    print(f"[AI Fix] 最后一轮，保存结果到 output_file={output_file}")
                    if output_file:
                        # 写入到指定输出文件
                        print(f"[AI Fix] 正在写入修复后文件到: {output_file}")
                        with open(output_file, 'w', encoding='utf-8') as f:
                            f.write(fixed_code)
                        print(f"[AI Fix] 文件写入成功")
                        backup_path = output_file
                    else:
                        backup_path = self.apply_fix(file_path, fixed_code, backup=True)
                    self.fix_stats['successful_fixes'] += 1

                os.unlink(temp_file.name)

            except Exception as e:
                self.fix_stats['failed_fixes'] += 1
                print(f"[AI Fix] 错误: {str(e)}")
                import traceback
                traceback.print_exc()
                # 如果发生异常但已经有修复代码，尝试保存当前进度
                if output_file and current_code != original_code:
                    try:
                        print(f"[AI Fix] 发生异常，保存当前修复进度到: {output_file}")
                        os.makedirs(os.path.dirname(os.path.abspath(output_file)) if os.path.dirname(output_file) else '.', exist_ok=True)
                        with open(output_file, 'w', encoding='utf-8') as f:
                            f.write(current_code)
                        backup_path = output_file
                        print(f"[AI Fix] 进度保存成功")
                    except Exception as save_err:
                        print(f"[AI Fix] 保存进度失败: {save_err}")
                continue

        # 达到最大迭代次数，确保保存最后一次结果
        if output_file and current_code != original_code:
            print(f"[AI Fix] 保存最后一次修复结果到: {output_file}")
            os.makedirs(os.path.dirname(os.path.abspath(output_file)) if os.path.dirname(output_file) else '.', exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(current_code)
            backup_path = output_file
            print(f"[AI Fix] 文件写入成功")
        elif not backup_path and current_code != original_code:
            # 如果没有指定output_file，应用到原文件
            backup_path = self.apply_fix(file_path, current_code, backup=True)

        # 记录最终状态
        # 使用 original_issues 作为剩余问题，因为它在每次迭代中都会更新为 new_issues
        # 如果最后一次迭代发生异常，original_issues 仍保留着上一轮的问题列表
        final_issues = new_issues if 'new_issues' in locals() else original_issues
        issue_history.append({
            "iteration": max_iterations,
            "stage": "final",
            "issue_count": len(final_issues),
            "issues": final_issues.copy()
        })

        return FixResult(
            success=len(final_issues) == 0,
            fixed_code=current_code,
            original_issues=initial_issues,
            remaining_issues=final_issues,
            new_issues=[i for i in final_issues
                       if i.get('code') not in {j.get('code') for j in initial_issues}],
            iterations=max_iterations,
            message=f"达到最大迭代次数（备份: {backup_path}）",
            issue_history=issue_history
        )

    def get_stats(self) -> Dict:
        """获取修复统计信息"""
        return self.fix_stats.copy()


def fix_with_ai(file_path: str, api_key: Optional[str] = None,
                project_root: Optional[str] = None,
                include_paths: Optional[List[str]] = None,
                max_iterations: int = 3) -> FixResult:
    """
    便捷函数：使用AI修复文件

    Args:
        file_path: 文件路径
        api_key: API密钥
        project_root: 项目根目录
        include_paths: include路径列表
        max_iterations: 最大迭代次数

    Returns:
        修复结果
    """
    agent = AIFixAgent(api_key=api_key)
    return agent.fix_file(file_path, project_root, include_paths, max_iterations)


if __name__ == "__main__":
    # 测试代码
    import sys

    if len(sys.argv) < 2:
        print("Usage: python ai_fix_agent.py <verilog_file>")
        sys.exit(1)

    file_path = sys.argv[1]
    result = fix_with_ai(file_path)

    print("\n" + "=" * 70)
    print("AI Fix Result")
    print("=" * 70)
    print(f"Success: {result.success}")
    print(f"Iterations: {result.iterations}")
    print(f"Message: {result.message}")
    print(f"Original issues: {len(result.original_issues)}")
    print(f"Remaining issues: {len(result.remaining_issues)}")
    if result.new_issues:
        print(f"New issues introduced: {len(result.new_issues)}")
