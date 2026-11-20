from __future__ import annotations

from dataclasses import dataclass

from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import (
    MetadataFilter,
    MetadataFilters,
    VectorStoreQuery,
)
from llama_index.vector_stores.postgres import PGVectorStore

from backend.app.config import Settings
from backend.app.rag.chunking import Chunk


@dataclass(frozen=True)
class RetrievedChunk:
    text: str
    score: float
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "score": self.score, **self.metadata}


def create_vector_store(settings: Settings, table_name: str) -> PGVectorStore:
    return PGVectorStore.from_params(
        connection_string=settings.sync_database_url,
        async_connection_string=settings.sqlalchemy_url,
        table_name=table_name,
        embed_dim=settings.embedding_dim,
        use_jsonb=True,
        perform_setup=settings.pgvector_enabled,
    )


def build_nodes(chunks: list[Chunk], *, version_id: int, source: str) -> list[TextNode]:
    return [
        TextNode(
            text=chunk.text,
            metadata={
                "source": source,
                "section": chunk.section,
                "operation": chunk.operation or "",
                "endpoint": chunk.endpoint or "",
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "knowledge_version_id": version_id,
            },
            excluded_embed_metadata_keys=[
                "source",
                "operation",
                "line_start",
                "line_end",
                "knowledge_version_id",
            ],
            excluded_llm_metadata_keys=[
                "line_start",
                "line_end",
                "knowledge_version_id",
            ],
        )
        for chunk in chunks
    ]


async def retrieve(
    store: PGVectorStore,
    query_embedding: list[float],
    *,
    top_k: int,
    section: str | None = None,
) -> list[RetrievedChunk]:
    filters = (
        MetadataFilters(filters=[MetadataFilter(key="section", value=section)]) if section else None
    )
    result = await store.aquery(
        VectorStoreQuery(
            query_embedding=query_embedding,
            similarity_top_k=top_k,
            filters=filters,
        )
    )
    chunks: list[RetrievedChunk] = []
    for node, score in zip(result.nodes, result.similarities or [], strict=False):
        metadata = dict(node.metadata or {})
        chunks.append(
            RetrievedChunk(
                text=node.get_content(),
                score=float(score) if score is not None else 0.0,
                metadata=metadata,
            )
        )
    return chunks


async def insert_nodes(
    store: PGVectorStore,
    nodes: list[TextNode],
    embeddings: list[list[float]],
) -> None:
    """Attach embeddings and persist nodes to pgvector."""

    if len(nodes) != len(embeddings):
        raise ValueError("nodes and embeddings must have equal length")
    for node, vector in zip(nodes, embeddings, strict=True):
        node.embedding = vector
    await store.async_add(nodes)
