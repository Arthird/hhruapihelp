from __future__ import annotations

import argparse
import asyncio
import hashlib
import shutil
from pathlib import Path

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import (
    ensure_database_url,
    get_settings,
    validate_runtime_settings,
)
from backend.app.db import repositories as repo
from backend.app.db.models import KnowledgeVersion
from backend.app.db.session import create_engine, create_session_factory
from backend.app.logging import configure_logging, get_logger
from backend.app.rag.chunking import split_markdown
from backend.app.rag.embeddings import build_embedding
from backend.app.rag.store import build_nodes, create_vector_store, insert_nodes, retrieve

logger = get_logger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def table_name_for(version_id: int) -> str:
    return f"hh_docs_v{version_id}"


async def sync(session: AsyncSession, *, force: bool, skip_activation: bool) -> None:
    settings = get_settings()
    validate_runtime_settings(settings, require_llm=True)
    ensure_database_url(settings)

    source = settings.resolved_knowledge_source_path
    target = settings.resolved_knowledge_path
    if not source.exists():
        raise FileNotFoundError(f"Knowledge source not found: {source}")

    checksum = sha256_file(source)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"knowledge-sync:{checksum}:{settings.embedding_model_name}"},
    )
    existing = await repo.find_reusable_knowledge_version(
        session,
        checksum=checksum,
        embedding_model=settings.embedding_model_name,
    )
    if existing is not None and existing.status == "active" and not force:
        logger.info("knowledge_up_to_date", version_id=existing.id, checksum=checksum)
        return
    if existing is not None and existing.status == "building":
        logger.warning("knowledge_build_resumed", version_id=existing.id)
        await repo.delete_knowledge_version(session, existing)

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    content = target.read_text(encoding="utf-8")
    chunks = split_markdown(
        content,
        max_chars=settings.rag_chunk_size,
        overlap_chars=settings.rag_chunk_overlap,
        source=target.name,
    )
    if not chunks:
        raise ValueError("Documentation produced no chunks")

    version = KnowledgeVersion(
        source_path=str(source),
        source_sha256=checksum,
        embedding_model=settings.embedding_model_name,
        embedding_dim=settings.embedding_dim,
        chunk_count=len(chunks),
        table_name=f"hh_docs_pending_{checksum[:12]}",
        status="building",
    )
    session.add(version)
    await session.flush()

    table_name = table_name_for(version.id)
    version.table_name = table_name
    store = create_vector_store(settings, table_name)
    embedding = build_embedding(settings)
    nodes = build_nodes(chunks, version_id=version.id, source=target.name)

    try:
        vectors = await embedding.aget_text_embedding_batch([node.get_content() for node in nodes])
        await insert_nodes(store, nodes, vectors)

        smoke_query = "Как получить список вакансий?"
        smoke_vector = await embedding.aget_query_embedding(smoke_query)
        smoke = await retrieve(store, smoke_vector, top_k=settings.rag_top_k)
        if not smoke:
            raise RuntimeError("Smoke retrieval returned no chunks")
    except Exception:
        version.status = "failed"
        await session.flush()
        raise

    if skip_activation:
        logger.info("knowledge_built_without_activation", version_id=version.id)
        return

    await session.execute(
        update(KnowledgeVersion)
        .where(KnowledgeVersion.status == "active")
        .values(status="superseded")
    )
    version.status = "active"
    version.activated_at = repo.utcnow()
    await session.flush()
    logger.info(
        "knowledge_activated",
        version_id=version.id,
        chunks=len(chunks),
        checksum=checksum,
    )


async def run(force: bool, skip_activation: bool) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await sync(session, force=force, skip_activation=skip_activation)
            await session.commit()
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or refresh the HH API vector index")
    parser.add_argument("--force", action="store_true", help="Reindex even if checksum matches")
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="Build a new version without switching the active index",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.force, args.skip_activation))
    except Exception as exc:
        logger.error("knowledge_sync_failed", error=type(exc).__name__, detail=str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
