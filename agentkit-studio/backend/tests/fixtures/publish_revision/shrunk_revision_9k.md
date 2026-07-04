# Study how to use Pi and Craft to develop agents and create a research report

## Executive Summary

This research report investigates the technical architecture and implementation strategies of the **Pi** agent toolkit, a modular TypeScript-based framework designed for the development of sophisticated AI agents. Through an analysis of its monorepo structure and core logic, this report identifies Pi as a highly extensible system that separates LLM orchestration from agentic logic. Key findings indicate that the toolkit's strength lies in its "Agent Loop" architecture and its ability to programmatically manage sessions. The report concludes that while Pi offers significant potential for automated agentic workflows, developers must account for specific hardcoded constraints in context window management and provider binding.

## Scope and Research Questions

The scope of this research is limited to the technical analysis of the Pi toolkit's architecture, its modular package structure, and its programmatic capabilities for agent development. The research focuses on the relationship between the core agent loop and its extensibility layers.

The primary research questions addressed in this report are:
1.  **Architectural Design:** How does the modular structure of the Pi toolkit facilitate the separation of concerns between LLM communication and agent logic?
2.  **Agentic Workflow:** How does the `pi-agent-core` implement the iterative loop required for autonomous tool execution?
3.  **Extensibility:** In what ways can the toolkit be augmented to support complex, real-world automation (e.g., Git integration, custom UI)?
4.  **Boundary Conditions:** What are the inherent technical limitations in the current implementation that may affect large-scale context utilization?

## Background and Context

As Large Language Models (LLMs) evolve from simple chat interfaces to autonomous actors, the need for robust orchestration frameworks has increased. The Pi toolkit emerges as a response to this need, providing a structured environment for building "agents" rather than mere "chatbots."

Unlike standard API wrappers, Pi is designed as a developer-centric framework. It utilizes a TypeScript monorepo to manage various facets of the agent lifecycle—from low-level model communication to high-level terminal user interfaces (TUI). This modularity allows developers to pick and choose specific components (e.g., using `pi-ai` without `pi-tui`) to suit different deployment environments, such as headless servers or interactive CLI tools.

The ecosystem consists of several integrated components:
*   **The Pi Toolkit:** A layered TypeScript monorepo designed for building everything from simple LLM calls to complex coding agents (https://nader.substack.com/p/how-to-build-a-custom-agent-framework).
*   **Craft Agents:** An 'Agent Native' tool designed for intuitive multitasking and a document-centric workflow through a beautiful and fluid UI (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md). It utilizes the Claude Agent SDK and the Pi SDK side-by-side (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).
*   **The Stack Hierarchy:** The application layer (e.g., OpenClaw, Slack bots) sits atop specialized layers like `pi-coding-agent` and `pi-tui`, which are supported by the `pi-agent-core` and the base `pi-ai` layer (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).

## Key Findings

The research into the Pi toolkit reveals a highly structured, layered architecture designed for modularity and programmatic control.

### 1. Modular Architecture Overview
The toolkit is organized into specialized packages that separate the "brain" (LLM) from the "body" (tools and UI):

| Package | Primary Responsibility | Key Functionality |
| :--- | :--- | :--- |
| `pi-ai` | LLM Orchestration | Manages communication across various LLM providers. |
| `pi-agent-core` | Agent Logic | Implements the core execution loop and tool-calling logic. |
| `pi-coding-agent` | Specialized Agent | Provides a full coding environment with session persistence. |
| `pi-tui` | User Interface | Provides a Terminal User Interface for interactive CLI use. |

### 2. The Agentic Loop Mechanism
The core of the system is the iterative loop implemented in `pi-agent-core`. The process follows a deterministic cycle:
1.  **Input:** The agent receives a user prompt or a tool output.
2.  **Reasoning:** The LLM processes the context and decides on an action.
3.  **Action:** The agent executes a tool call (if requested).
4.  **Feedback:** The result of the tool execution is fed back into the context.
5.  **Termination/Iteration:** The loop repeats until the model signals completion.

### 3. High-Level Extensibility
Pi is designed to be augmented through an extensibility system. This includes lifecycle event handlers that hook into the agent loop—such as before messages are sent, before compaction runs, or when a tool is called—allowing for behavioral modifications without direct LLM intervention (https://gist.github.com/dubit3/e97dbfe71298b1df4d36542aceb5f158).

## Evidence and Analysis

The relationship between the frameworks can be visualized as an Orchestrator-Executor pattern, where the Pi layer acts as the cognitive brain and the Craft layer acts as the hands for atomic task execution (https://github.com/craft-ai-agents/craft-agents-oss).

### Architectural Design Analysis
The decision to use a monorepo structure for the Pi toolkit is a strategic choice for developer ergonomics. By separating `pi-ai` from `pi-agent-core`, the system ensures that the reasoning engine is decoupled from the execution environment. This separation prevents "reasoning drift," where an agent becomes lost in its own generated text (https://github.com/craft-ai-agents/craft-agents-oss).

The technical relationship is such that the Pi SDK acts as a default provider/interface within the Craft environment (https://github.com/craft-ai-agents/craft-agents-oss/issues/807). This allows Craft Agents to function as an "Agent Native" interface, providing a "no-fluff connection to any API or Service" and supporting shared sessions within its document-centric environment (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).

### Example Code Implementation
The following pseudo-code demonstrates how an agentic loop might be structured based on the findings of the `pi-agent-core` logic:

```python
class ResearchAgent:
    def __init__(self, intelligence_layer, tool_layer):
        self.brain = intelligence_layer
        self.tools = tool_layer

    def execute_research(self, topic):
        print(f"Initiating research on: {topic}")
        # Step 1: Decompose topic into research questions (Reasoning Layer)
        sub_tasks = self.brain.decompose(topic)
        
        research_data = []
        for task in sub_tasks:
            # Step 2: Execute task via tool layer (Execution Layer)
            data = self.tools.fetch_data(task)
            research_data.append(data)
        
        # Step 3: Synthesize findings
        report = self.brain.synthesize(research_data)
        return report
```

In this implementation, the code illustrates the "Brain/Tools" dichotomy. The `intelligence_layer` (the brain) is responsible for the high-level cognitive task of `decompose`, while the `tool_layer` (the hands) handles the empirical `fetch_data` task. The loop is controlled by the `sub_tasks` generated by the brain, ensuring that the execution remains within the bounds of the initial query.

## Implications or Recommendations

1.  **Architect for Provider Switching:** Because agent sessions in the Craft framework are bound to a specific provider at initialization (https://github.com/craft-ai-agents/craft-agents-oss/issues/350), developers should design external session management logic to handle model hand-offs if necessary.
2.  **Leverage Lifecycle Handlers for Governance:** Use the lifecycle event handlers (e.g., hooks before tool calls) to implement "Human-in-the-loop" safety gates. This allows the Pi layer to pause for human approval before the Craft layer executes high-risk actions (https://github.com/craft-ai-agents/craft-agents-oss).
3.  **Optimize for Modularity:** When building production-grade software, utilize the specific package granularity. For example, use `pi-ai` for headless server-side logic while reserving `pi-tui` for developer-facing CLI tools.

## Limitations and Uncertainty

*   **Context Window Constraints:** While the architecture is robust, specific hardcoded constraints in context window management remain a factor for large-scale automation.
*   **Session Binding:** The current implementation's requirement to bind sessions to a specific provider at initialization may limit seamless transitions between different LLM backends during a single active session.

## References

*   https://github.com/craft-ai-agents/craft-agents-oss
*   https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md
*   https://github.com/craft-ai-agents/craft-agents-oss/issues/350
*   https://github.com/craft-ai-agents/craft-agents-oss/issues/807
*   https://github.com/craft-ai-agents/craft-agents-oss/blob/main/package.json
*   https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158
*   https://nader.substack.com/p/how-to-build-a-custom-agent-framework
*   https://github.com/earendil-works/pi
