// @ts-nocheck
import type { AgentMessage } from "@oh-my-pi/pi-agent-core";
import type { Message } from "@oh-my-pi/pi-ai";

export const SKILL_PROMPT_MESSAGE_TYPE = "skill-prompt";

export interface SkillPromptDetails {
	name?: string;
	prompt?: string;
}

export function convertToLlm(messages: AgentMessage[]): Message[] {
	return messages as Message[];
}

export function bashExecutionToText(msg: unknown): string {
	return String((msg as { output?: unknown })?.output ?? "");
}

export function pythonExecutionToText(msg: unknown): string {
	return String((msg as { output?: unknown })?.output ?? "");
}
