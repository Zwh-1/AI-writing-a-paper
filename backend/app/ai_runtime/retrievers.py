"""Preview-only LangChain Documents preserve current source handles and scopes."""
from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from pydantic import PrivateAttr
from typing import Any


class WorkspaceRetriever(BaseRetriever):
    project_id: str
    section_id: str
    # Preserve raw caller values until the existing domain validators run;
    # Pydantic coercion must not turn True into an accepted integer limit.
    limit: Any=8
    channels: Any=None
    source_refs: Any=None
    remote: bool=True
    _result: dict=PrivateAttr(default_factory=dict)

    def _get_relevant_documents(self,query,*,run_manager):
        from ..writing_retrieval import search
        result=search(self.project_id,self.section_id,query,limit=self.limit,
                      channels=self.channels,source_refs=self.source_refs,remote=self.remote)
        self._result=result
        return [Document(page_content=item.get('preview_text',''),metadata={
            'project_id':self.project_id,'source_ref':item['source_ref'],
            'locator':item.get('locator',{}),'read_scope':'search_preview_not_evidence',
            'semantic_support':'not_checked','channel':item.get('channel'),
            'native_hit':item}) for item in result['items']]


def search(pid,sid,query,*,limit=8,channels=None,source_refs=None,remote=True):
    from langsmith import tracing_context
    retriever=WorkspaceRetriever(project_id=pid,section_id=sid,limit=limit,
        channels=channels,source_refs=source_refs,remote=remote)
    with tracing_context(enabled=False):
        documents=retriever.invoke(query,config={'callbacks':[]})
    return {**retriever._result,'items':[document.metadata['native_hit'] for document in documents]}
