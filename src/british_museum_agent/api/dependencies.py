from functools import lru_cache

from british_museum_agent.adapters_mcp.client import MCPMuseumTools
from british_museum_agent.application.chat_service import ChatService
from british_museum_agent.config import get_settings
from british_museum_agent.generation.answer_generator import (
    GeminiAnswerGenerator,
    GroundedFallbackGenerator,
)
from british_museum_agent.infrastructure.sqlite_repository import SQLiteRepository
from british_museum_agent.retrieval.knowledge_base import KnowledgeBase, KnowledgeRetriever
from british_museum_agent.retrieval.vector_store import (
    LocalSentenceTransformerEmbeddings,
    MultilingualCrossEncoderReranker,
    ResilientKnowledgeBase,
    VectorKnowledgeBase,
    VectorStoreUnavailable,
)


@lru_cache
def get_sqlite_repository() -> SQLiteRepository:
    settings = get_settings()
    return SQLiteRepository(settings.sqlite_path)


@lru_cache
def get_mcp_museum_tools() -> MCPMuseumTools:
    settings = get_settings()
    return MCPMuseumTools(
        settings.mcp_server_url,
        internal_token=settings.mcp_internal_token_value,
    )


@lru_cache
def get_knowledge_retriever() -> KnowledgeRetriever:
    settings = get_settings()
    fallback = KnowledgeBase(settings.index_path)
    if settings.retrieval_backend != "chroma":
        return ResilientKnowledgeBase(
            None,
            fallback,
            reason=f"RETRIEVAL_BACKEND={settings.retrieval_backend}",
        )

    try:
        primary = VectorKnowledgeBase(
            settings.chroma_path,
            settings.chroma_collection_name,
            LocalSentenceTransformerEmbeddings(settings.embedding_model_name),
            MultilingualCrossEncoderReranker(settings.reranker_model_name),
            candidate_k=settings.retrieval_candidate_k,
        )
        return ResilientKnowledgeBase(primary, fallback)
    except VectorStoreUnavailable as exc:
        return ResilientKnowledgeBase(None, fallback, reason=str(exc))
    except Exception as exc:
        return ResilientKnowledgeBase(
            None,
            fallback,
            reason=f"Chroma could not initialize ({type(exc).__name__})",
        )


@lru_cache
def get_answer_generator():
    settings = get_settings()
    if settings.llm_provider.lower() != "gemini":
        return GroundedFallbackGenerator(f"LLM_PROVIDER={settings.llm_provider}")

    api_key = settings.resolved_gemini_api_key
    if not api_key:
        return GroundedFallbackGenerator("missing GOOGLE_API_KEY/GEMINI_API_KEY")

    fallback = GroundedFallbackGenerator("Gemini unavailable")
    try:
        return GeminiAnswerGenerator(
            model_name=settings.gemini_model,
            api_key=api_key,
            fallback=fallback,
        )
    except Exception as exc:
        return GroundedFallbackGenerator(
            f"Gemini could not initialize ({type(exc).__name__})"
        )


@lru_cache
def get_chat_service() -> ChatService:
    settings = get_settings()
    return ChatService(
        get_knowledge_retriever(),
        get_mcp_museum_tools(),
        get_answer_generator(),
        tracing_enabled=settings.langsmith_enabled,
        langsmith_project=settings.langsmith_project,
    )
