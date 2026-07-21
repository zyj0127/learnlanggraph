import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

from langchain_core.tools import tool
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

PROJECT_DIR = Path(__file__).resolve().parent.parent
DOC_DIR = PROJECT_DIR / 'data' / 'company_handbook.md'
VECTOR_DIR = PROJECT_DIR / 'db' / 'chroma.db'

# 1全局单例初始化 EMBEDDING MODEL
print('加载bge模型')
embeddings = HuggingFaceEmbeddings(
    model_name=os.getenv('EMBEDDING_MODEL'),
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'normalize_embeddings': True},
)


def init_vector_store() -> Chroma:
    '''初始化向量数据库，如果存在则读取，如果不存在则切分文档并加载'''
    if VECTOR_DIR.exists() and any(VECTOR_DIR.iterdir()):
        return Chroma(persist_directory=str(VECTOR_DIR),
                      embedding_function=embeddings)
    print('未检测到本地向量数据库，开始构建rag索引。。。')

    if not DOC_DIR.exists():
        raise FileNotFoundError(f"未找到知识库文件{DOC_DIR}")

    with open(DOC_DIR, 'r', encoding='utf-8') as f:
        markdown_text = f.read()

    # 基于md层级切分
    headers_to_split_on = [
        ('##', 'Chapter'),
        ('###', 'Section'),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    md_header_splits = markdown_splitter.split_text(markdown_text)
    # 为防止某个章节依然过长，再叠加一个字符集滑动窗口切分
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    splits = text_splitter.split_documents(md_header_splits)

    print(f'文档切分完毕，共生成{len(splits)}个文件')

    vectorstore = Chroma.from_documents(
        embedding=embeddings,
        documents=splits,
        persist_directory=str(VECTOR_DIR),

    )
    print(f'向量数据库构建完毕，已落盘在{VECTOR_DIR}')
    return vectorstore


# 初始化全局向量库示例
vector_store = init_vector_store()
# 转化成检索器对象（retriever）
retriever=vector_store.as_retriever(search_kwargs={'k':5})
# retriever = vector_store.as_retriever(search_kwargs={'k': 20,
#                                                      #只返回相似度》=0.5
#                                     'score_threshold': 0.5
#                                                      },
#                                       search_type='similarity_score_threshold',
# )

@tool
def search_hr_policy(query:str)->str:
    """
    搜索公司规章制度，差旅费·报销标准，假期政策，福利等相关信息的必备工具
    输入参数：query 必须是你从u员工问题中提取出来的精准检索
    """
    docs = retriever.invoke(query)
    if not  docs:
        return '知识库中为检索到相关政策，请提示用户查询'
    # 组装召回的上下文，附带metadata 让大模型知道出自哪个章节，有效降低幻觉
    contex_parts = []
    for i, doc in enumerate(docs, 1):
        chapter = doc.metadata.get('Chapter', '未知章节')
        section = doc.metadata.get('Section', '未知段落')
        contex_parts.append(f'来源{i}{chapter}》{section}\n{doc.page_content}')
    merged_context = '\n\n'.join(contex_parts)
    return f'知识库检索结果：\n{merged_context}'
