// -nocheck
import type { Agent, AgentMessage } from "@oh-my-pi/pi-agent-core";
import type { AssistantMessage, Message } from "@oh-my-pi/pi-ai";

export type AgentSessionEvent =
	| { type: "message"; message?: AssistantMessage | AgentMessage | Message }
	| { type: string; [key: string]: unknown };

export interface AgentSession {
	id?: string;
	title?: string;
	agent: Agent & { model?: { id?: string; name?: string }; messages?: AgentMessage[] };
	on?(listener: (event: AgentSessionEvent) => void): () => void;
	subscribe?(listener: (event: AgentSessionEvent) => void): () => void;
	prompt?(text: string): Promise<unknown>;
	cancel?(): Promise<void> | void;
}
