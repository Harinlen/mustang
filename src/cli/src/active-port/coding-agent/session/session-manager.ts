// -nocheck
export interface SessionContext {
	id?: string;
	title?: string;
	path?: string;
}

export interface SessionInfo extends SessionContext {
	updatedAt?: string;
}

export class SessionManager {
	async getRecentSessions(): Promise<SessionInfo[]> {
		return [];
	}
}

export async function getRecentSessions(): Promise<SessionInfo[]> {
	return [];
}
