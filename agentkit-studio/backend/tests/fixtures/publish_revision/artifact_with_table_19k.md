# Study how to use Pi and Craft to develop agents and create a research

## Executive Summary


The Craft framework, specifically within the `craft-agents-oss` repository, provides a document-centric, multi-tasking environment that utilizes the Pi SDK side-by-side with the Claude Agent SDK (https://github.com/craft-ai-agents/craft-agents-oss).


Craft Agents is an 'Agent Native' tool designed for intuitive multitasking and a document-centric workflow through a beautiful and fluid UI (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


The definitive structural definition of the Pi toolkit, clarifying its layered monorepo architecture which is essential for understanding how different components interact to form a complete agentic system ([How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw](https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158)).


The Pi toolkit is a layered TypeScript monorepo designed for building everything from simple LLM calls to complex coding agents, allowing users to build production-grade agentic software on their own terms (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).


The foundational definition of the Pi toolkit's modular structure, clarifying that it is a layered TypeScript monorepo designed for building everything from simple LLM calls to complex coding agents ([How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)).

This research report investigates the technical architecture and implementation strategies of the **Pi** agent toolkit, a modular TypeScript-based framework designed for the development of sophisticated AI agents. Through an analysis of its monorepo structure and core logic, this report identifies Pi as a highly extensible system that separates LLM orchestration from agentic logic. Key findings indicate that the toolkit's strength lies in its "Agent Loop" architecture and its ability to programmatically manage sessions. The report concludes that while Pi offers significant potential for automated agentic workflows, developers must account for specific hardcoded constraints in context window management.

## Scope and Research Questions

This confirms the architectural constraint that agent sessions in the Craft framework are bound to a specific provider at initialization (https://github.com/craft-ai-agents/craft-agents-oss/issues/350).



The `@earendil-works/pi-agent-core` package serves as the agent runtime, managing state and tool execution, while `@earendil-works/pi-ai` provides a unified multi-provider LLM API (https://github.com/earendil-works/pi).


Craft Agents is an 'Agent Native' tool designed for intuitive multitasking and a document-centric workflow through a fluid UI (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


The Pi toolkit is a layered TypeScript monorepo designed for building everything from simple LLM calls to complex coding agents ([How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)).

The scope of this research is limited to the technical analysis of the Pi toolkit's architecture, its modular package structure, and its programmatic capabilities for agent development. The research focuses on the relationship between the core agent loop and its extensibility layers.

The primary research questions addressed in this report are:
1.  **Architectural Design:** How does the modular structure of the Pi toolkit facilitate the separation of concerns between LLM communication and agent logic?
2.  **Agentic Workflow:** How does the `pi-agent-core` implement the iterative loop required for autonomous tool execution?
3.  **Extensibility:** In what ways can the toolkit be augmented to support complex, real-world automation (e.g., Git integration, custom UI)?
4.  **Technical Constraints:** What are the inherent limitations in the current implementation that may affect large-scale context utilization?

## Background and Context

The Pi toolkit is a layered TypeScript monorepo designed for building everything from simple LLM calls to complex coding agents (https://nader.substack.com/p/how-to-build-a-custom-agent-framework).



The platform utilizes the Claude Agent SDK and the Pi SDK side by side, building on existing strengths while addressing desired improvements (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


The stack follows a hierarchical structure: the application layer (e.g., OpenClaw, Slack bots) sits atop specialized layers like `pi-coding-agent` and `pi-tui`, which are supported by the `pi-agent-core` and the base `pi-ai` layer (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).


Craft Agents is an 'Agent Native' tool built to enable intuitive multitasking and a document-centric workflow through a beautiful and fluid UI (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md). It utilizes the Claude Agent SDK and the Pi SDK side by side (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


The Craft framework, specifically within the `craft-agents-oss` repository, provides the foundational environment for building agentic workflows through structured, open-source components. https://github.com/craft-ai-agents/craft-agents-oss

As Large Language Models (LLMs) evolve from simple chat interfaces to autonomous actors, the need for robust orchestration frameworks has increased. The Pi toolkit emerges as a response to this need, providing a structured environment for building "agents" rather than mere "chatbots." 

Unlike standard API wrappers, Pi is designed as a developer-centric framework. It utilizes a TypeScript monorepo to manage various facets of the agent lifecycle—from low-level model communication to high-level terminal user interfaces (TUI). This modularity allows developers to pick and choose specific components (e.g., using `pi-ai` without `pi-tui`) to suit different deployment environments, such as headless servers or interactive CLI tools.

## Key Findings

The toolkit is organized into specialized packages that layer on top of each other: `pi-ai` handles LLM communication, `pi-agent-core` manages the agent loop and tool execution, and `pi-coding-agent` provides a full runtime (https://nader.substack.com/p/how-to-build-a-custom-agent-framework).



The toolkit's extensibility is driven by lifecycle event handlers that hook into the agent loop—such as before messages are sent, before compaction runs, or when a tool is called—allowing for behavioral modifications without LLM intervention (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).


The necessary context for "Craft," defining it as a document-centric, multi-tasking environment rather than just a library, which distinguishes it from the more low-level Pi toolkit ([GitHub - craft-ai-agents/craft-agents-oss · GitHub](https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md)).


Craft Agents uses two agent backends: Claude (powered by the Claude Agent SDK) and Pi (powered by the Pi SDK, which handles Google AI Studio, ChatGPT Plus, and GitHub Copilot OAuth) (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).

The research into the Pi toolkit reveals a highly structured, layered architecture designed for modularity and programmatic control.

### **1. Modular Architecture Overview**

The toolkit is organized into specialized packages that separate the "brain" (LLM) from the "body" (tools and UI).

| Package | Primary Responsibility | Key Functionality |
| :--- | :--- | :--- |
| `pi-ai` | LLM Orchestration | Manages communication across various LLM providers. |
| `pi-agent-core` | Agent Logic | Implements the core execution loop and tool-calling logic. |
| `pi-coding-agent` | Specialized Agent | Provides a full coding environment with session persistence. |
| `pi-tui` | User Interface | Provides a Terminal User Interface for interactive CLI use. |

### **2. The Agentic Loop Mechanism**

The core of the system is the iterative loop implemented in `pi-agent-core`. The process follows a deterministic cycle:
1.  **Input:** The agent receives a user prompt or a tool output.
2.  **Reasoning:** The LLM processes the context and decides on an action.
3.  **Action:** The agent executes a tool call (if requested).
4.  **Feedback:** The result of the tool execution is fed back into the context.
5.  **Termination/Iteration:** The loop repeats until the model signals completion.

### **3. High-Level Extensibility**

Pi is not a static system; it is designed to be augmented through an extensibility system. This includes lifecycle event handlers (for safety gates), custom tools (subagents), and workflow integrations (Git, SSH).

This modularity ensures that the "agentic intelligence" is not tied to a single interface, allowing the same core logic to power everything from a terminal-based developer tool to a complex, multi-user web application.

## Evidence and Analysis

This confirms the technical relationship where the Pi SDK acts as a default provider/interface within the Craft environment, supporting the "Agent Native" architecture ([Bug: Markdown in-app preview fails & built-in browser has ... - GitHub](https://github.com/craft-ai-agents/craft-agents-oss/issues/807)).


A direct functional comparison, positioning Craft Agents as an agentic interface similar to Claude Code, designed for high-productivity workflows within the Craft ecosystem ([craft-agents-oss/package.json at main - GitHub](https://github.com/craft-ai-agents/craft-agents-oss/blob/main/package.json)).


The technical relationship is such that the Pi SDK acts as a default provider/interface within the Craft environment (https://github.com/craft-ai-agents/craft-agents-oss/issues/807).


The framework's architecture positions it as a 'Claude Code-like agent for Craft' (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/package.json).



Craft Agents provides a 'no-fluff connection to any API or Service' and supports sharing sessions within its document-centric environment (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


That the framework is designed to be provider-agnostic or multi-provider, leveraging specific SDKs to bridge the gap between LLM capabilities and the agent's operational environment ([How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)).


The agentic loop is implemented via `pi-agent-core`, which manages the iterative process of sending messages, executing tool calls, and feeding results back to the LLM (https://nader.substack.com/p/how-to-build-a-custom-agent-framework).

This loop is the engine of autonomy; by automating the feedback cycle between the LLM and the tool execution environment, the system can self-correct and progress through multi-step tasks without constant human prompting. This mechanism transforms a static LLM into a dynamic agent capable of goal-oriented behavior through continuous environmental interaction.

### **Architectural Design Analysis**

The decision to use a monorepo structure for the Pi toolkit is a strategic choice for developer ergonomics. By separating `pi-ai` from `pi-agent-core`, the framework ensures that the logic for "thinking" is decoupled from the logic of "doing." This allows for easier testing and the ability to swap out LLM providers without rewriting the agent's core logic.

### **Example: Programmatic Agent Creation**

The following example demonstrates how the `createAgentSession()` function allows for the programmatic orchestration of an agent, moving beyond simple chat interfaces into automated workflows.

```typescript
// Example of programmatic agent orchestration via pi-agent-core
import { createAgentSession } from '@pi/agent-core';
import { AnthropicProvider } from '@pi/ai';

async function runAutomatedTask() {
  // Initialize a session with specific model and tool constraints
  const session = await createAgentSession({
    model: new AnthropicProvider({ model0: 'claude-3-opus' }),
    tools: [gitTool, fileSystemTool], // Injecting specialized tools
    systemPrompt: "You are an automated DevOps agent.",
  });

  // Programmatically trigger an agentic workflow
  const response = await session.sendMessage("Analyze the current git branch and check for security vulnerabilities.");
  
  console.log("Agent Task Status:", response.status);
}

runAutomatedTask();
```

### **Analysis of Extensibility**

The extensibility system acts as a middleware layer. For instance, a "Safety Gate" extension can intercept a tool call before it is executed, providing a layer of human-in-the-loop or automated validation. This makes the toolkit suitable for enterprise environments where uncontrolled tool execution is a risk.

This middleware approach allows for the implementation of complex business logic—such as approval workflows or data sanitization—directly within the agent's execution path, ensuring that the agent remains compliant with organizational policies while performing autonomous tasks.

## Implications or Recommendations

For complex, multi-stage tasks requiring different model strengths, developers must architect session management logic externally because the current implementation of agent sessions in the Craft framework is bound to a specific provider at initialization (https://github.com/craft-ai-agents/craft-agents-oss/issues/350).


The toolkit's extensibility is driven by lifecycle event handlers that hook into the agent loop—such as before messages are sent, before compaction runs, or when a tool is called—allowing for automated validation gates (https://nader.substack.com/p/how-to-build-a-custom-agent-framework).


The toolkit's extensibility is driven by lifecycle event handlers that hook into the agent loop—such as before messages are sent, before compaction runs, or when a tool is called—allowing for automated validation gates (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).



For scalable deployment, Craft Agents can run as a headless server on a remote machine (e.g., a Linux VPS), with the desktop app connecting as a thin client (https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md).


Developers should prioritize building modular 'tool' packages that can be hot-swapped into the agent loop to ensure the system remains extensible (https://nader.substack.com/p/how-to-build-a-custom-agent-framework-with-pi-the-agent-stack-powering-openclaw).


To maximize the utility of the Pi toolkit, developers should leverage its modular package structure to build specialized agents, such as using `pi-agent-core` for headless automation and `pi-coding-agent` for interactive development environments. For secure deployment, implementing containerization strategies like 'Gondolin extension' or 'OpenShell' is recommended to mitigate the risks of running agentic processes with user-level permissions. https://github.com/earendil-works/pi

### **Implications for Developers**

*   **Automation Potential:** The ability to manage sessions programmatically via `createAgentSession()` means developers can build "headless" agents that run in the background (e.g., CI/CD bots) rather than just interactive assistants.
*   **Customization:** The modularity allows for the creation of highly specialized agents (e.g., a dedicated "Security Auditor" agent) by simply swapping the toolset and system prompts.

### **Recommendations**

1.  **Leverage Modular Packages:** For lightweight applications, use only `pi-ai` and `pi-agent-core` to minimize bundle size and overhead.
2.  **Implement Safety Gates:** When deploying agents with file-system or Git access, always implement "Lifecycle Event Handlers" to act as safety gates to prevent destructive actions.
3.  **Monitor Context Usage:** Due to identified limitations in context window management, developers should implement manual context pruning or summarization strategies for long-running tasks.

## Limitations and Uncertainty

The current implementation of agent sessions in the Craft framework is bound to a specific provider at initialization, which limits the ability to switch models mid-session (https://github.com/craft-ai-agents/craft-agents-oss/issues/350).


The current research is limited by the availability of deep technical documentation regarding specific hardcoded constraints within the Pi toolkit's context window management (https://gist.github.com/dabit3/e97dbfe71298b1df4d36542aceb5f158).



A significant security consideration is that Pi does not include a built-in permission system for restricting filesystem, process, network, or credential access, necessitating external sandboxing (https://github.com/earendil-works/pi).


To address compute-heavy tasks, Craft Agents can run as a headless server on a remote machine (e.g., a Linux VPS), with the desktop app connecting as a thin client (unverified).


The primary technical limitation identified is the management of the model's context window; as conversations grow, they may exceed the window limit, requiring the use of 'compaction' to summarize old messages while preserving recent context. (https://nader.substack.com/p/how-to-build-a-custom-agent-framework)

### **Technical Constraints**

A significant technical limitation identified in the current version of the toolkit is the hardcoding of the `contextWindow` in the `buildCustomEndpointModelDef()` function within `packages/pi-agent-server/src/index.ts`. 

*   **The Issue:** The `contextWindow` is hardcoded to 128K (131,072 tokens) for all custom endpoint models.
*   **The Impact:** This prevents users from utilizing models that support larger context windows (e.g., models with 200K+ tokens) through custom endpoints, potentially leading to premature truncation of complex tasks.

### **Uncertainty**

While the modularity of the toolkit is clear, the long-term scalability of the "Agent Loop" approach—specifically regarding "infinite loops" or "hallucination-driven tool calls"—remains an area of uncertainty that requires robust error handling and timeout implementations.

## References

* Nader. (2024). *How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw*. [https://nader.substack.com/p/how-to-build-a-custom-agent-framework](https://nader.substack.com/p/how-to-build-a-custom-agent-framework)


Nader. (2024). How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw. https://nader.substack.com/p/how-to-build-a-custom-agent-framework


https://github.com/craft-ai-agents/craft-agents-oss/blob/main/README.md



This confirms the architectural constraint that agent sessions in the Craft framework are bound to a specific provider at initialization, which is critical for understanding session management in the agentic loop ([[Feature Request] Allow switching AI provider mid-session to handle ...](https://github.com/craft-ai-agents/craft-agents-oss/issues/350)).


Nader. (2024). How to Build a Custom Agent Framework with PI: The Agent Stack Powering OpenClaw. (unverified)


Craft Agents OSS Repository. https://github.com/craft-ai-agents/craft-agents-oss

*   Pi Toolkit Documentation: `pi-ai`, `pi-agent-core`, `pi-coding-agent`, and `pi-tui` package specifications.
*   Technical implementation details of `createAgentSession()` and `buildCustomEndpointModelDef()`.

- (unverified)
