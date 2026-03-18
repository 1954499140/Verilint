# 核心：仅导入无 sklearn 依赖的库
import os
import sys
import shutil
import torch
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma

# -------------------------- 全局配置（彻底禁用遥测 + 警告） --------------------------
LOCAL_MODEL_PATH = "E:\\deeplearning\\local_unixcoder"
# 1. 彻底禁用 Chroma 遥测（消除警告）
os.environ["CHROMA_TELEMETRY_ENABLED"] = "False"
os.environ["CHROMA_DISABLE_TELEMETRY"] = "True"
os.environ["POSTHOG_DISABLED"] = "True"  # 新增：禁用 posthog 遥测
# 2. 禁用其他警告
os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# -------------------------- 关键：删除旧向量库（不保存 RAG） --------------------------
VECTOR_DB_DIR = "./verilog_db"
if os.path.exists(VECTOR_DB_DIR):
    shutil.rmtree(VECTOR_DB_DIR)
    print(f"✅ 已删除旧向量库目录：{VECTOR_DB_DIR}")

# -------------------------- 屏蔽 transformers 隐式依赖 --------------------------
import transformers

transformers.generation.candidate_generator = None
from transformers import AutoTokenizer, AutoModel


# -------------------------- Verilog 嵌入模型 --------------------------
class VerilogASTEmbeddings:
    def __init__(self, model_path=LOCAL_MODEL_PATH):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(
                model_path, local_files_only=True, trust_remote_code=True
            )
            self.model = AutoModel.from_pretrained(
                model_path, local_files_only=True, trust_remote_code=True
            ).to(self.device)
            print(f"✅ 本地模型加载成功：{model_path}")
        except:
            print("⚠️ 从国内镜像源下载模型...")
            self.tokenizer = AutoTokenizer.from_pretrained("microsoft/unixcoder")
            self.model = AutoModel.from_pretrained("microsoft/unixcoder").to(self.device)
            self.tokenizer.save_pretrained(LOCAL_MODEL_PATH)
            self.model.save_pretrained(LOCAL_MODEL_PATH)
            print(f"✅ 模型已保存到本地：{LOCAL_MODEL_PATH}")

    def get_embedding(self, text):
        # 过滤空文本（关键：避免空嵌入）
        if not text or text.strip() == "":
            return [0.0] * 768  # 返回默认向量（适配 unixcoder-base 768维）
        inputs = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=512, padding="max_length"
        ).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        return outputs.last_hidden_state[:, 0, :].squeeze().cpu().numpy().tolist()

    def embed_query(self, text):
        return self.get_embedding(text)

    def embed_documents(self, texts):
        return [self.get_embedding(t) for t in texts]


# -------------------------- 构建向量库（修复 ID 为空问题） --------------------------
def build_verilog_vector_db(doc_path: str = "./verilog_docs.txt"):
    """构建 Verilog 向量库（过滤空文本块，避免 ID 为空）"""
    # 初始化嵌入模型
    embedding_model = VerilogASTEmbeddings()

    # 生成 Verilog 示例文档（确保内容有效）
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write("""
Source:  (at 1)
  Description:  (at 1)
    ModuleDef: fsm_module (at 1)
      Paramlist:  (at 0)
      Portlist:  (at 1)
        Port: clk, None (at 1)
        Port: reset, None (at 1)
        Port: in, None (at 1)
        Port: out, None (at 1)
      Decl:  (at 2)
        Parameter: zero, False (at 2)
          Rvalue:  (at 2)
            IntConst: 2 (at 2)
        Parameter: one, False (at 2)
          Rvalue:  (at 2)
            IntConst: 3 (at 2)
        Parameter: two, False (at 2)
          Rvalue:  (at 2)
            IntConst: 0 (at 2)
        Parameter: three, False (at 2)
          Rvalue:  (at 2)
            IntConst: 1 (at 2)
      Decl:  (at 3)
        Output: out, False (at 3)
      Decl:  (at 4)
        Input: clk, False (at 4)
        Input: reset, False (at 4)
        Input: in, False (at 4)
      Decl:  (at 5)
        Reg: out, False (at 5)
      Decl:  (at 6)
        Reg: current_state, False (at 6)
          Width:  (at 6)
            IntConst: 1 (at 6)
            IntConst: 0 (at 6)
        Reg: next_state, False (at 6)
          Width:  (at 6)
            IntConst: 1 (at 6)
            IntConst: 0 (at 6)
      Always:  (at 8)
        SensList:  (at 8)
          Sens: posedge (at 8)
            Identifier: clk (at 8)
          Sens: posedge (at 8)
            Identifier: reset (at 8)
        Block: None (at 8)
          IfStatement:  (at 9)
            Identifier: reset (at 9)
            Block: None (at 9)
              NonblockingSubstitution:  (at 10)
                Lvalue:  (at 10)
                  Identifier: current_state (at 10)
                Rvalue:  (at 10)
                  Identifier: zero (at 10)
            Block: None (at 11)
              NonblockingSubstitution:  (at 12)
                Lvalue:  (at 12)
                  Identifier: current_state (at 12)
                Rvalue:  (at 12)
                  Identifier: next_state (at 12)
      Always:  (at 16)
        SensList:  (at 16)
          Sens: level (at 16)
            Identifier: current_state (at 16)
          Sens: level (at 16)
            Identifier: in (at 16)
        Block: None (at 16)
          CaseStatement:  (at 17)
            Identifier: current_state (at 17)
            Case:  (at 18)
              Identifier: zero (at 18)
              Block: None (at 18)
                IfStatement:  (at 19)
                  Identifier: in (at 19)
                  Block: None (at 19)
                    BlockingSubstitution:  (at 20)
                      Lvalue:  (at 20)
                        Identifier: next_state (at 20)
                      Rvalue:  (at 20)
                        Identifier: one (at 20)
                  Block: None (at 21)
                    BlockingSubstitution:  (at 22)
                      Lvalue:  (at 22)
                        Identifier: next_state (at 22)
                      Rvalue:  (at 22)
                        Identifier: zero (at 22)
            Case:  (at 25)
              Identifier: one (at 25)
              Block: None (at 25)
                IfStatement:  (at 26)
                  Identifier: in (at 26)
                  Block: None (at 26)
                    BlockingSubstitution:  (at 27)
                      Lvalue:  (at 27)
                        Identifier: next_state (at 27)
                      Rvalue:  (at 27)
                        Identifier: two (at 27)
                  Block: None (at 28)
                    BlockingSubstitution:  (at 29)
                      Lvalue:  (at 29)
                        Identifier: next_state (at 29)
                      Rvalue:  (at 29)
                        Identifier: zero (at 29)
            Case:  (at 32)
              Identifier: two (at 32)
              Block: None (at 32)
                IfStatement:  (at 33)
                  Identifier: in (at 33)
                  Block: None (at 33)
                    BlockingSubstitution:  (at 34)
                      Lvalue:  (at 34)
                        Identifier: next_state (at 34)
                      Rvalue:  (at 34)
                        Identifier: two (at 34)
                  Block: None (at 35)
                    BlockingSubstitution:  (at 36)
                      Lvalue:  (at 36)
                        Identifier: next_state (at 36)
                      Rvalue:  (at 36)
                        Identifier: zero (at 36)
            Case:  (at 39)
              Identifier: three (at 39)
              Block: None (at 39)
                BlockingSubstitution:  (at 40)
                  Lvalue:  (at 40)
                    Identifier: next_state (at 40)
                  Rvalue:  (at 40)
                    Identifier: three (at 40)
            Case:  (at 42)
              Block: None (at 42)
                BlockingSubstitution:  (at 43)
                  Lvalue:  (at 43)
                    Identifier: next_state (at 43)
                  Rvalue:  (at 43)
                    Identifier: zero (at 43)
      Always:  (at 48)
        SensList:  (at 48)
          Sens: level (at 48)
            Identifier: current_state (at 48)
        Block: None (at 48)
          CaseStatement:  (at 49)
            Identifier: current_state (at 49)
            Case:  (at 50)
              Identifier: zero (at 50)
              Block: None (at 50)
                NonblockingSubstitution:  (at 51)
                  Lvalue:  (at 51)
                    Identifier: out (at 51)
                  Rvalue:  (at 51)
                    IntConst: 0 (at 51)
            Case:  (at 53)
              Identifier: one (at 53)
              Block: None (at 53)
                NonblockingSubstitution:  (at 54)
                  Lvalue:  (at 54)
                    Identifier: out (at 54)
                  Rvalue:  (at 54)
                    IntConst: 0 (at 54)
            Case:  (at 56)
              Identifier: two (at 56)
              Block: None (at 56)
                NonblockingSubstitution:  (at 57)
                  Lvalue:  (at 57)
                    Identifier: out (at 57)
                  Rvalue:  (at 57)
                    IntConst: 1 (at 57)
            Case:  (at 59)
              Identifier: three (at 59)
              Block: None (at 59)
                NonblockingSubstitution:  (at 60)
                  Lvalue:  (at 60)
                    Identifier: out (at 60)
                  Rvalue:  (at 60)
                    IntConst: 0 (at 60)
        """.strip())  # 去除首尾空行，避免空文本

    # 加载文档
    loader = TextLoader(doc_path, encoding="utf-8")
    documents = loader.load()

    # 分割文档（优化分割规则，避免空块）
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "//", "module", "endmodule"],
        keep_separator=True,  # 保留分隔符，避免文本块为空
        strip_whitespace=True  # 去除空白字符
    )
    splits = text_splitter.split_documents(documents)

    # 关键：过滤空的文档块（核心修复 ID 为空）
    valid_splits = []
    for split in splits:
        if split.page_content and split.page_content.strip() != "":
            valid_splits.append(split)
    print(f"✅ 文档分割完成：原始 {len(splits)} 块 → 有效 {len(valid_splits)} 块")

    # 构建向量库（不保存，仅内存运行）
    vector_db = Chroma.from_documents(
        documents=valid_splits,  # 仅用有效文本块
        embedding=embedding_model,
        # 不指定 persist_directory = 禁用持久化
        collection_name="verilog_temp"  # 临时集合名
    )

    return vector_db, embedding_model


# -------------------------- 主函数 --------------------------
if __name__ == "__main__":
    print(f"当前 Python 版本：{sys.version[:6]}")
    print(f"当前 Torch 版本：{torch.__version__}")
    print(f"当前 Transformers 版本：{transformers.__version__}")

    try:
        # 构建向量库（修复 ID 为空）
        vector_db, embed_model = build_verilog_vector_db()
        retriever = vector_db.as_retriever(search_kwargs={"k": 1})

        # 测试检索
        test_questions = [
            """      Decl:  (at 3)
                        Output: out, False (at 3)
            """
        ]

        print("\n=== Verilog RAG 检索结果（不保存 RAG）===")
        for idx, question in enumerate(test_questions, 1):
            print(f"\n【问题 {idx}】：{question}")
            results = retriever.invoke(question)
            if results:
                content = results[0].page_content.strip()[:300]
                print(f"【匹配结果】：\n{content}...")
            else:
                print("【匹配结果】：无相关内容")
            print("-" * 30)

    except Exception as e:
        print(f"\n❌ 运行错误：{str(e)}")
        import traceback

        traceback.print_exc()