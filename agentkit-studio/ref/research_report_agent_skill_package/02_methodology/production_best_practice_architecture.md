# Production Best-Practice Architecture

```mermaid
flowchart LR
    U[User Request] --> IG[Input Guardrail]
    IG --> P[Planner / Pi Reasoning Layer]
    P --> QP[Research Query Planner]
    QP --> C[Craft Orchestrator]

    C --> WS[Web Search]
    C --> DR[Document Reader]
    C --> CR[Code Runner]
    C --> DG[Diagram Generator]
    C --> FW[File Writer]

    WS --> EV[Evidence Validator]
    DR --> EV
    CR --> EV
    DG --> EV

    EV --> MEM[(Checkpoint + Evidence Store)]
    MEM --> SYN[Synthesis Writer]
    SYN --> OE[Output Evaluator]
    OE -->|Revise| P
    OE -->|Pass| OG[Output Guardrail]
    OG --> PKG[Final Report Package]
```

## Production design notes

| Layer | Best practice |
|---|---|
| Input guardrail | Block unsafe or impossible requests before expensive processing |
| Planner | Produce explicit plan, ToC, research questions, and stopping conditions |
| Tool orchestration | Validate tool parameters before execution |
| Evidence store | Save source notes, citations, reliability status, and claim mapping |
| Checkpointing | Save progress after every major stage |
| Evaluator | Use weighted scorecard and hard-fail checks |
| Tracing | Record each model call, tool call, guardrail event, and revision |
| Human review | Required for high-impact business, legal, medical, or security reports |
