# ADR-001 - Hexagonal architecture

## Status
Accepted

## Context
The project must demonstrate production-oriented AI engineering, not just a chatbot demo.

## Decision
Use a ports and adapters architecture separating domain, application, retrieval, agent orchestration, infrastructure, API, UI and deployment.

## Consequences
The LLM provider, vector store, operational database and UI can change without rewriting core use cases.
