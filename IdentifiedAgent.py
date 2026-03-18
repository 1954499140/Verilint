# 导入必要的库
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 加载环境变量（建议将 API 密钥存放在 .env 文件中，避免硬编码）
load_dotenv()

# 1. 配置模型（以 OpenAI 的 gpt-3.5-turbo 为例）
# 替换为你的 OpenAI API 密钥（也可直接写在代码里，但不推荐）
openai_api_key = os.getenv("OPENAI_API_KEY")

# 初始化 ChatOpenAI 模型实例
llm = ChatOpenAI(
    model="qwen3.5-plus",  # 指定模型版本
    api_key="sk-3df2e316304d4c82ac54c097c262e342",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    temperature=0.7,  # 控制回答的随机性，0 偏严谨，1 偏灵活
    max_tokens=10000  # 限制回答的最大令牌数
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """Now you are expert in HDL(Hardware Description Language).
                    Your task involves debugging in HDL. You are given snippets of HDL
                    script that contain errors.
                    Your objective is to identify the reset signal"""),
    ("user", """here is the code:{code}
                你需要给我一个文档
                ### 文档要求：
                1. 文档结构：文档标题→修订历史→模块概述→端口列表→信号定义→复位策略说明→注意事项；
                2. 端口列表：按“端口名|方向|位宽|类型（时钟/复位/数据/控制）|功能说明|约束要求”格式整理所有端口；
                3. 复位策略说明：明确标注“复位信号为隐式推导（非直接顶层信号）”，列出所有复位相关内部信号（rst_async_low/rst_sync_high/fake_rst）的推导逻辑和实际作用；
                4. 注意事项：提示“子模块en端口实际为复位信号，存在复位伪装风险”；
                5. 格式：使用Markdown表格+分级标题，语言为中文，专业且简洁，符合芯片设计行业术语规范。""")  # {question} 是动态参数，接收用户输入
])

# 3. 定义输出解析器（将模型返回的复杂格式转为纯文本）
output_parser = StrOutputParser()

# 4. 构建链式调用（LangChain 核心：将 prompt + llm + parser 串联）
chain = prompt | llm | output_parser


# 5. 调用链并获取结果
def ask_question(question):
    """封装调用逻辑，接收用户问题并返回回答"""
    try:
        response = chain.invoke({"code": question}, )
        return response
    except Exception as e:
        return f"调用失败：{str(e)}"


def read_code_file(file_path: str, encoding: str = 'utf-8'):  # 替换 tuple->Tuple，list->List
    """Read code/log file, return raw lines + cleaned full content"""
    try:
        with open(file_path, 'r', encoding=encoding, newline='') as file:
            lines = file.readlines()
            full_content = ''.join(lines)

        # Clean empty lines (preserve code structure)
        cleaned_lines = [line.rstrip('\n') for line in lines if line.strip()]
        cleaned_full_content = '\n'.join(cleaned_lines)

        return cleaned_full_content

    except FileNotFoundError:
        raise FileNotFoundError(f"❌ File not found: {file_path}")
    except PermissionError:
        raise PermissionError(f"❌ No read permission for: {file_path}")
    except UnicodeDecodeError:
        raise UnicodeDecodeError(f"❌ Encoding error (try encoding='latin-1'): {file_path}")
    except Exception as e:
        raise IOError(f"❌ Failed to read file: {str(e)}")


def log_print(msg):
    log_msg = f"{msg}"
    # 追加写入文件
    with open('Identifyagent_log.txt', 'w', encoding='utf-8') as f:
        f.write(log_msg + '\n')


if __name__ == "__main__":
    code = read_code_file("./example/reset.v")
    # error = read_code_file("./dma_log.txt")
    question = code
    answer = ask_question(question)
    log_print(f"模型回答：{answer}")


