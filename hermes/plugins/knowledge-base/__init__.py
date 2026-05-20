import sys, os
sys.path.insert(0, os.path.expanduser("~/ai/ai"))
from knowledge_base_tool import knowledge_search, knowledge_read, OBSIDIAN_SEARCH_SCHEMA, OBSIDIAN_READ_SCHEMA

def register(ctx):
    ctx.register_tool(
        name="knowledge_search",
        toolset="hermes-knowledge",
        schema=OBSIDIAN_SEARCH_SCHEMA,
        handler=lambda **kw: knowledge_search(kw.get("query", "")),
        description="搜索知识库中的 281 篇笔记",
    )
    ctx.register_tool(
        name="knowledge_read",
        toolset="hermes-knowledge",
        schema=OBSIDIAN_READ_SCHEMA,
        handler=lambda **kw: knowledge_read(kw.get("path_or_title", "")),
        description="读取知识库中指定笔记的全文",
    )
