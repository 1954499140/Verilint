import os
import glob
import re
import tempfile
from typing import List, Optional, Dict, Any


class File:
    """表示项目中的单个 Verilog 文件"""

    def __init__(self, project: 'Project', file_path: str):
        self.project = project
        self.file_path = file_path  # 完整路径
        self.filename = os.path.basename(file_path)  # 文件名
        self.modules: List[str] = []  # 该文件中定义的模块名列表（解析后填充）
        self.ast = None  # 解析后的 AST（解析后填充）

    def __repr__(self):
        return f"File({self.filename}, modules={self.modules})"

    def __str__(self):
        return self.filename


class Project:
    """
    Verilog 项目类

    管理项目中的所有 Verilog 文件，提供批量解析和检查功能。
    """

    def __init__(self, project_name: str):
        self.project_name = project_name
        self.files: List[File] = []  # 项目中的所有文件
        self.modules: Dict[str, Dict] = {}  # module_name -> module_info，包含所有文件的模块
        self.include_paths: List[str] = []  # include 路径

    def __repr__(self):
        return f"Project({self.project_name}, {len(self.files)} files, {len(self.modules)} modules)"

    def __str__(self):
        return f"{self.project_name} ({len(self.files)} files, {len(self.modules)} modules)"

    def add_file(self, file_path: str) -> File:
        """
        添加单个文件到项目

        Args:
            file_path: Verilog 文件路径

        Returns:
            创建的 File 对象
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_obj = File(self, file_path)
        self.files.append(file_obj)
        return file_obj

    def add_directory(self, directory: str, recursive: bool = True) -> List[File]:
        """
        添加文件夹中的所有 .v 文件到项目

        Args:
            directory: 要扫描的文件夹路径
            recursive: 是否递归扫描子文件夹，默认为 True

        Returns:
            添加的 File 对象列表
        """
        # 规范化路径（处理 Windows 反斜杠）
        directory = os.path.normpath(directory)

        # Debug output removed for cleaner JSON output
        # print(f"[DEBUG] Scanning directory: {directory}", file=sys.stderr)
        # print(f"[DEBUG] Recursive: {recursive}", file=sys.stderr)

        if not os.path.exists(directory):
            raise FileNotFoundError(f"Directory not found: {directory}")

        if not os.path.isdir(directory):
            raise NotADirectoryError(f"Path is not a directory: {directory}")

        # 构建搜索模式 - 使用正斜杠以确保跨平台兼容
        if recursive:
            pattern = os.path.join(directory, "**", "*.v").replace("\\", "/")
        else:
            pattern = os.path.join(directory, "*.v").replace("\\", "/")

        # 查找所有 .v 文件
        file_paths = glob.glob(pattern, recursive=recursive)

        # 同时查找 .sv 文件（SystemVerilog）
        if recursive:
            sv_pattern = os.path.join(directory, "**", "*.sv").replace("\\", "/")
        else:
            sv_pattern = os.path.join(directory, "*.sv").replace("\\", "/")

        sv_files = glob.glob(sv_pattern, recursive=recursive)

        file_paths.extend(sv_files)

        # 创建 File 对象并添加到项目
        added_files = []
        for file_path in file_paths:
            file_obj = File(self, file_path)
            self.files.append(file_obj)
            added_files.append(file_obj)

        # print(f"Added {len(added_files)} files from {directory}")
        return added_files

    def get_files(self) -> List[File]:
        """获取项目中的所有文件"""
        return self.files

    def get_file_by_name(self, filename: str) -> Optional[File]:
        """根据文件名查找文件"""
        for file_obj in self.files:
            if file_obj.filename == filename:
                return file_obj
        return None

    def set_include_paths(self, include_paths: List[str]):
        """设置 include 路径"""
        self.include_paths = include_paths

    def add_include_path(self, include_path: str):
        """添加单个 include 路径"""
        if include_path not in self.include_paths:
            self.include_paths.append(include_path)

    def clear(self):
        """清空项目中的所有文件和模块信息"""
        self.files.clear()
        self.modules.clear()

    def _preprocess_file(self, file_path: str) -> tuple:
        """
        预处理单个文件，处理 `include 并保留行号映射

        Returns:
            (preprocessed_content, line_mapping)
            line_mapping: 预处理后的行号 -> (原始文件路径, 原始行号)
        """
        import re
        import tempfile
        import os

        line_mapping = {}
        output_lines = []
        processed_files = set()

        def process_file(fp: str, current_line: int = 1):
            """递归处理文件，展开 include"""
            if fp in processed_files:
                return
            processed_files.add(fp)

            abs_path = os.path.abspath(fp)

            try:
                with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except Exception as e:
                return

            for i, line in enumerate(lines):
                line_content = line.rstrip('\n\r')

                # 检查是否是 `include 行
                include_match = re.match(r'`include\s+"([^"]+)"', line_content.strip())

                if include_match:
                    include_file = include_match.group(1)
                    include_path = None

                    # 首先在当前文件目录查找
                    current_dir = os.path.dirname(os.path.abspath(fp))
                    candidate = os.path.join(current_dir, include_file)
                    if os.path.exists(candidate):
                        include_path = candidate

                    # 然后在 include 路径中查找
                    if not include_path:
                        for inc_dir in self.include_paths or []:
                            candidate = os.path.join(inc_dir, include_file)
                            if os.path.exists(candidate):
                                include_path = candidate
                                break

                    if include_path and os.path.exists(include_path):
                        # 递归处理 include 文件
                        process_file(include_path, 1)
                    else:
                        # include 文件找不到，保留原行
                        output_lines.append(line_content)
                        line_mapping[len(output_lines)] = (abs_path, i + 1)
                else:
                    # 普通行，直接添加
                    output_lines.append(line_content)
                    line_mapping[len(output_lines)] = (abs_path, i + 1)

        # 开始处理主文件
        process_file(file_path)

        preprocessed = '\n'.join(output_lines)
        return preprocessed, line_mapping

    def parse_all(self, debug: bool = False) -> Dict[str, Any]:
        """
        解析项目中所有文件，提取模块信息

        Args:
            debug: 是否输出调试信息

        Returns:
            解析结果统计
        """
        from pyverilog.vparser.parser import parse
        from pyverilog.vparser.ast import ModuleDef

        parsed_count = 0
        error_count = 0
        total_modules = 0

        for file_obj in self.files:
            # if debug:
            #     print(f"Parsing: {file_obj.file_path}")

            try:
                # 使用自定义预处理，避免 iverilog 预处理问题
                preprocessed, line_mapping = self._preprocess_file(file_obj.file_path)

                if preprocessed and line_mapping:
                    # 使用预处理后的内容解析
                    with tempfile.NamedTemporaryFile(
                        mode='w', suffix='.v', delete=False
                    ) as tmp_file:
                        tmp_file.write(preprocessed)
                        tmp_path = tmp_file.name

                    try:
                        # 已经预处理好，不需要再次预处理
                        ast, _ = parse([tmp_path], preprocess_include=[])
                        file_obj.ast = ast
                    finally:
                        import os
                        os.unlink(tmp_path)
                else:
                    # 回退到直接解析
                    ast, _ = parse([file_obj.file_path], preprocess_include=[])
                    file_obj.ast = ast

                # 提取模块信息
                if ast and hasattr(ast, 'description'):
                    for definition in ast.description.definitions:
                        if isinstance(definition, ModuleDef):
                            module_name = definition.name
                            file_obj.modules.append(module_name)

                            # 存储到项目级别的 modules 字典
                            self.modules[module_name] = {
                                'file': file_obj,
                                'ast_node': definition,
                                'lineno': definition.lineno
                            }
                            total_modules += 1

                            # if debug:
                            #     print(f"  Found module: {module_name}")

                parsed_count += 1

            except Exception as e:
                if debug:
                    print(f"  Error: {e}")
                error_count += 1

        return {
            'parsed_files': parsed_count,
            'errors': error_count,
            'total_modules': total_modules,
            'modules': list(self.modules.keys())
        }

    def get_modules(self) -> Dict[str, Dict]:
        """
        获取项目中所有模块信息

        Returns:
            module_name -> module_info 的字典，包含所有文件的模块
        """
        return self.modules

    def get_module(self, module_name: str) -> Optional[Dict]:
        """
        根据模块名获取模块信息

        Args:
            module_name: 模块名

        Returns:
            模块信息字典，如果未找到则返回 None
        """
        return self.modules.get(module_name)

    def get_module_file(self, module_name: str) -> Optional[File]:
        """
        获取定义指定模块的文件

        Args:
            module_name: 模块名

        Returns:
            包含该模块的 File 对象
        """
        module_info = self.modules.get(module_name)
        if module_info:
            return module_info.get('file')
        return None


if __name__ == "__main__":
    # 测试代码
    project = Project("TestProject")

    # 测试添加目录
    project.add_directory("../finish/dark", recursive=True)
    project.add_include_path("../finish/dark")

    print(f"\nProject: {project}")
    print(f"Files:")
    for file_obj in project.get_files():
        print(f"  - {file_obj}")

    # 测试解析所有文件
    print(f"\nParsing all files...")
    result = project.parse_all(debug=True)
    print(f"\nParse result: {result}")

    # 查看模块信息
    print(f"\nAll modules in project:")
    for module_name, module_info in project.get_modules().items():
        print(f"  - {module_name} (in {module_info['file'].filename})")
