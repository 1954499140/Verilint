class Error:
    def __init__(self, level: int, type: str, lineno: int):
        self.level = level
        self.type = type
        self.lineno = lineno

    def printError(self):
        level = ""
        if self.level == 1:
            level = "【Warning】"
        elif self.level == 2:
            level = "【Error】"
        log_msg = level + " " + self.type + " "
        with open('./verilint/dma_log.txt', 'a', encoding='utf-8') as f:
            f.write(log_msg + '\n')
class ErrorList:
    _instance = None  # 全局单例（整个工具共用一个）
    error_list = []   # 公共错误列表
    exist_set = set() # 用于去重（保证不重复）

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def add(self, error: Error):
        """
        自动去重：
        相同 type + lineno 视为重复错误
        """
        key = (error.type, error.lineno,error.level)
        if key not in self.exist_set:
            self.exist_set.add(key)
            self.error_list.append(error)

    def clear(self):
        """清空错误列表"""
        self.error_list.clear()
        self.exist_set.clear()

    def print_all(self):
        """批量打印所有错误到日志（自动去重后）"""
        for err in self.error_list:
            err.printError()

# ====================== 全局唯一公共错误列表（直接用） ======================
GLOBAL_ERRORS = ErrorList()