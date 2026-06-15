import os
import glob
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
        self.modules: Dict[str, Dict] = {}  # module_name -> module_info
        self.include_paths: List[str] = []  # include 路径

    def __repr__(self):
        return f"Project({self.project_name}, {len(self.files)} files)"

    def __str__(self):
        return f"{self.project_name} ({len(self.files)} files)"

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
        if not os.path.exists(directory):
            raise FileNotFoundError(f"Directory not found: {directory}")

        if not os.path.isdir(directory):
            raise NotADirectoryError(f"Path is not a directory: {directory}")

        # 构建搜索模式
        if recursive:
            pattern = os.path.join(directory, "**", "*.v")
        else:
            pattern = os.path.join(directory, "*.v")

        # 查找所有 .v 文件
        file_paths = glob.glob(pattern, recursive=recursive)

        # 创建 File 对象并添加到项目
        added_files = []
        for file_path in file_paths:
            file_obj = File(self, file_path)
            self.files.append(file_obj)
            added_files.append(file_obj)

        print(f"Added {len(added_files)} files from {directory}")
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
        self.modules: Dict[str, Dict] = {}  # module_name -> module_info

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
            if debug:
                print(f"Parsing: {file_obj.file_path}")

            try:
                # 解析文件
                ast, _ = parse([file_obj.file_path],
                               preprocess_include=self.include_paths)
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

                            if debug:
                                print(f"  Found module: {module_name}")

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
            module_name -> module_info 的字典
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

    # 测试添加单个文件
    # project.add_file("test.v")

    # 测试添加目录
    # project.add_directory("../finish/dark", recursive=True)

    print(f"Project: {project}")
    for file_obj in project.get_files():
        print(f"  - {file_obj}")
