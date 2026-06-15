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
    max_tokens=10000   # 限制回答的最大令牌数
)

prompt = ChatPromptTemplate.from_messages([
    ("system", """Now you are expert in HDL(Hardware Description Language).
                    Your task involves debugging in HDL. You are given snippets of HDL
                    script that contain errors.
                    Your objective is to identify these errors and provide helpful
                    instructions to fix the bug"""),
            ("user", """# Core Requirements
                ## 1. Vulnerability Analysis Dimensions
                - Clearly identify vulnerability types (e.g., syntax errors, timing violations, combinational logic loops, uninitialized variables, improper cross-clock domain handling, resource waste, synthesizability violations, missing assertions);
                - Analyze the specific impact of vulnerabilities on functionality, performance, power consumption, maintainability, and synthesizability;
                - Precisely locate vulnerabilities in the source code (module name, line number, signal/statement name).
                
                ## 2. Modification Solution Requirements
                - Provide complete modified code snippets (with modified lines marked) instead of vague textual descriptions;
                - Ensure modified code complies with Verilog synthesizable specifications and has no new syntax/logic errors;
                - Prioritize industry-standard repair solutions (e.g., double-register synchronization for cross-clock domain signals, initialization of all registers, elimination of combinational loops);
                - If multiple repair solutions exist, compare their pros/cons (e.g., resource usage, timing performance, implementation complexity) and recommend the optimal one.
                
                ## 3. Output Structure
                ### [Vulnerability Summary]
                - Total Vulnerabilities: X
                - High-Risk Vulnerabilities: X (e.g., timing violations, functional errors)
                - Medium-Risk Vulnerabilities: X (e.g., resource waste, poor maintainability)
                - Low-Risk Vulnerabilities: X (e.g., inconsistent coding standards)
                
                ### [Detailed Vulnerabilities & Modification Solutions]
                1. Vulnerability 1: [Vulnerability Type]
                   - Original Detection Result: [Paste the corresponding entry from static analysis output]
                   - Vulnerability Location: Module [XXX], Line [XXX], Signal/Statement [XXX]
                   - Vulnerability Impact: [Specific description, e.g., "Causes uncertain register values during reset phase, potentially leading to functional anomalies"]
                   - Code Before Modification:
                     ```verilog
                     // Paste corresponding code snippet (mark line numbers)
                here is the code:{code}
                the word behind the verilog code is the error list, which contains the errors and their lineno
                all errors need to be solved
                you just need to give me the modified code and their notes
                remenber, don't change the parameter, you just change their type or init them
                Please provide efficient and clear instructions for modifying the given
                buggy code based on the error messages and correct code provided.
                Your instructions should help improve the buggy code without
                directly including any information from the correct code. The helpful
                instruction is: Based on the error message.
                我需要你给出的是中文""")  # {question} 是动态参数，接收用户输入
])

# 3. 定义输出解析器（将模型返回的复杂格式转为纯文本）
output_parser = StrOutputParser()

# 4. 构建链式调用（LangChain 核心：将 prompt + llm + parser 串联）
chain = prompt | llm | output_parser

# 5. 调用链并获取结果
def ask_question(question):
    """封装调用逻辑，接收用户问题并返回回答"""
    try:
        response = chain.invoke({"code": question},)
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
    with open('agent_log.txt', 'w', encoding='utf-8') as f:
        f.write(log_msg + '\n')
if __name__ == "__main__":
    code = read_code_file("finish/fsm_with_unreachable_state.v")
    error = read_code_file("./dma_log.txt")
    question=code+error
    answer = ask_question(question)
    log_print(f"模型回答：{answer}")


