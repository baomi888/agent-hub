# -*- coding: utf-8 -*-
"""W1 端到端测试脚本：建库 → 检索 → 问答。"""
import os
import sys

# 把项目根目录加入 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

# ====== 1. Import 检查 ======
print("=== Step 1: Import Check ===")
from core import config
from core.llm import get_llm, get_embeddings
from kb.rag_core import RagPipeline, load_document
from kb import pipelines

print(f"  PROJECT_ROOT = {config.PROJECT_ROOT}")
print(f"  EMBEDDING_BASE_URL = {config.EMBEDDING_BASE_URL[:30]}...")
print(f"  DEEPSEEK_BASE_URL = {config.DEEPSEEK_BASE_URL}")
missing = config.validate()
print(f"  config.validate() = {missing}")
if missing:
    print("  ❌ 配置缺失，先检查 .env")
    sys.exit(1)

# ====== 2. 准备测试文档 ======
print("\n=== Step 2: Find Test Docs ===")
data_dir = config.DATA_DIR
print(f"  data_dir = {data_dir}")
os.makedirs(data_dir, exist_ok=True)

files = []
for f in os.listdir(data_dir):
    p = os.path.join(data_dir, f)
    if os.path.isfile(p):
        files.append(p)
print(f"  existing files = {len(files)}")

# 没有文件就造一个
if not files:
    sample = os.path.join(data_dir, "sample.txt")
    with open(sample, "w", encoding="utf-8") as w:
        w.write("人工智能（Artificial Intelligence，简称AI）是研究如何让计算机模拟人类智能的学科。\n")
        w.write("它涵盖机器学习、深度学习、自然语言处理、计算机视觉等多个方向。\n")
        w.write("大语言模型（LLM）是AI的一个重要分支，通过海量文本预训练获得强大的语言理解和生成能力。\n")
        w.write("LangChain 是一个用于构建大语言模型应用的开源框架，支持多种LLM和向量数据库。\n")
        w.write("Chroma 是一个轻量级的向量数据库，非常适合原型开发和小到中型应用。\n")
        w.write("RAG（检索增强生成）技术通过检索外部知识库来增强LLM的回答质量。\n")
    files = [sample]
    print(f"  ✅ created sample doc: {sample}")

for f in files:
    print(f"    - {f}")

# ====== 3. 构建向量库 ======
print("\n=== Step 3: Build Index ===")
kb_id = "test_w1"
pipe = pipelines.get_or_create(kb_id)
print(f"  collection = {pipe.collection_name}")
try:
    chunks = pipe.build_index(500, 50, files)
    print(f"  ✅ chunks created = {chunks}")
    print(f"  ✅ count() = {pipe.count()}")
except Exception as e:
    print(f"  ❌ BUILD FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

# ====== 4. 检索测试 ======
print("\n=== Step 4: Retrieve ===")
try:
    docs_scores = pipe.retrieve_with_score("什么是人工智能和大语言模型？", 3)
    print(f"  ✅ retrieved {len(docs_scores)} docs")
    for i, (d, s) in enumerate(docs_scores):
        src = os.path.basename(d.metadata.get("source", "?"))
        preview = d.page_content[:60].replace("\n", " ")
        print(f"    [{i+1}] {src} | score={s:.3f} | {preview}...")
except Exception as e:
    from kb.rag_core import describe_api_error
    print(f"  ❌ RETRIEVE FAILED: {describe_api_error(e)}")
    sys.exit(1)

# ====== 5. 同步问答测试 ======
print("\n=== Step 5: Answer (同步) ===")
try:
    answer, refs = pipe.answer("什么是人工智能和大语言模型？", 3)
    print(f"  ✅ ANSWER ({len(answer)} 字): {answer[:300]}")
    print(f"  ✅ REFS ({len(refs)} 字): {refs[:200]}")
except Exception as e:
    from kb.rag_core import describe_api_error
    print(f"  ❌ ANSWER FAILED: {describe_api_error(e)}")
    sys.exit(1)

# ====== 6. 流式问答测试 ======
print("\n=== Step 6: Answer (流式) ===")
try:
    print("  流式输出: ", end="", flush=True)
    acc_text = ""
    token_count = 0
    for text, refs in pipe.answer_stream("LangChain 和 Chroma 是什么关系？", 2):
        if text and len(text) > len(acc_text):
            delta = text[len(acc_text):]
            acc_text = text
            token_count += 1
            # 每 20 个 token 打一个点
            if token_count % 20 == 0:
                print(".", end="", flush=True)
    print()
    print(f"  ✅ 流式完成，{token_count} 个 token，累计 {len(acc_text)} 字")
    print(f"  答案末尾: {acc_text[-100:]}")
except Exception as e:
    from kb.rag_core import describe_api_error
    print(f"\n  ❌ STREAM FAILED: {describe_api_error(e)}")
    sys.exit(1)

print("\n" + "=" * 40)
print("🎉  W1 端到端验证全部通过！")
print("=" * 40)
