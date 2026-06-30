# Demo Research Agent Report

## Goal

Create a concise, source-grounded report about agent architecture.

## Synthesized Notes

- **ReAct pattern**: Reasoning and acting are interleaved so tool observations can update future reasoning steps. Source: https://arxiv.org/abs/2210.03629
- **Building effective agents**: Start with the simplest viable pattern, add complexity only when it improves outcomes, and design tools carefully. Source: https://www.anthropic.com/engineering/building-effective-agents
- **Pi agent toolkit**: Pi is positioned as an AI agent toolkit with a core runtime for agent loops, tool calling, and state management. Source: https://github.com/earendil-works/pi
- **Craft Agents**: Craft Agents supports sources, workspaces, sessions, reusable skills, and permission modes for agent execution. Source: https://agents.craft.do/docs/getting-started/introduction

## Validation Checks

- has_bullets: True
- has_sources: True
- reasonable_length: True

## Reflection

The important lesson is not that this simple script is autonomous. The lesson is that autonomy should be built from small, inspectable parts: planning, execution, validation, memory, and final synthesis.
