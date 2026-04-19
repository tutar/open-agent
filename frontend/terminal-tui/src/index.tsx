import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, render, useApp, useInput} from 'ink';
import net, {type Socket} from 'node:net';

type BridgeEvent = {
	type: 'event';
	event_type: string;
	payload: unknown;
	session_id?: string;
};

type BridgeStatus = {
	type: 'status';
	message: string;
	session_name?: string;
	session_id?: string;
};

type BridgeError = {
	type: 'error';
	message: string;
};

type BridgeSessions = {
	type: 'sessions';
	current_session_name: string;
	sessions: string[];
};

type BridgeMessage = BridgeEvent | BridgeStatus | BridgeError | BridgeSessions;

type SessionState = {
	pendingApproval: boolean;
	lastEventType: string;
	sessionName: string;
	sessionId: string;
	knownSessions: string[];
};

const lineLimit = 20;
const helpText =
	'Commands: tool <text>, admin <text>, /new <name>, /switch <name>, /sessions, /resume, /approve, /reject, /interrupt, /session, /channel, /channel <name>, /channel-config feishu <key> <value>, /clear, /help, /exit';
const interactiveTerminal = Boolean(process.stdin.isTTY && process.stdout.isTTY);
const terminalHost = process.env.OPENAGENT_TERMINAL_HOST ?? '127.0.0.1';
const terminalPort = Number(process.env.OPENAGENT_TERMINAL_PORT ?? '8765');

type ShellProps = {
	input: string;
	pushLine: (line: string) => void;
	send: (payload: Record<string, unknown>) => void;
	sessionState: SessionState;
	exit: () => void;
	setInput: React.Dispatch<React.SetStateAction<string>>;
	resetLines: () => void;
};

function App() {
	const {exit} = useApp();
	const [lines, setLines] = useState<string[]>([helpText]);
	const [input, setInput] = useState('');
	const [ready, setReady] = useState(false);
	const [sessionState, setSessionState] = useState<SessionState>({
		pendingApproval: false,
		lastEventType: 'idle',
		sessionName: 'main',
		sessionId: 'terminal-main-session',
		knownSessions: ['main'],
	});
	const socketRef = useRef<Socket | null>(null);
	const bufferRef = useRef('');
	const streamingAssistantRef = useRef(false);

	const pushLine = (line: string) => {
		setLines(prev => [...prev.slice(-(lineLimit - 1)), line]);
	};

	const resetLines = () => {
		setLines([helpText]);
		streamingAssistantRef.current = false;
	};

	const updateAssistantLine = (content: string) => {
		const line = `assistant> ${content}`;
		setLines(prev => {
			if (
				streamingAssistantRef.current &&
				prev.length > 0 &&
				prev[prev.length - 1]?.startsWith('assistant> ')
			) {
				return [...prev.slice(0, -1), line];
			}
			return [...prev.slice(-(lineLimit - 1)), line];
		});
		streamingAssistantRef.current = true;
	};

	const finalizeAssistantLine = (content: string) => {
		updateAssistantLine(content);
		streamingAssistantRef.current = false;
	};

	useEffect(() => {
		const socket = net.createConnection({host: terminalHost, port: terminalPort});
		socketRef.current = socket;

		socket.on('connect', () => {
			pushLine(`system> connected to host ${terminalHost}:${String(terminalPort)}`);
		});

		socket.on('data', chunk => {
			bufferRef.current += chunk.toString();
			const parts = bufferRef.current.split('\n');
			bufferRef.current = parts.pop() ?? '';

			for (const raw of parts) {
				if (!raw.trim()) {
					continue;
				}
				const message = JSON.parse(raw) as BridgeMessage;
				if (message.type === 'status') {
					pushLine(`system> ${message.message}`);
					if (message.session_name && message.session_id) {
						setSessionState(current => ({
							...current,
							sessionName: message.session_name ?? current.sessionName,
							sessionId: message.session_id ?? current.sessionId,
							knownSessions: uniqueSessions(
								current.knownSessions,
								message.session_name ?? current.sessionName,
							),
						}));
					}
					if (message.message === 'ready') {
						setReady(true);
					}
					continue;
				}
				if (message.type === 'sessions') {
					pushLine(
						`system> sessions=${message.sessions.join(', ')} current=${message.current_session_name}`,
					);
					setSessionState(current => ({
						...current,
						knownSessions: message.sessions,
						sessionName: message.current_session_name,
					}));
					continue;
				}
				if (message.type === 'error') {
					pushLine(`host> ${message.message}`);
					continue;
				}
				renderBridgeEvent(
					message,
					pushLine,
					setSessionState,
					updateAssistantLine,
					finalizeAssistantLine,
				);
			}
		});

		socket.on('error', error => {
			pushLine(
				`system> host unavailable: start openagent-host (terminal=${terminalHost}:${String(terminalPort)})`,
			);
			pushLine(`system> connection error: ${error.message}`);
		});

		socket.on('close', hadError => {
			pushLine(`system> host disconnected${hadError ? ' after error' : ''}`);
			exit();
		});

		return () => {
			socket.end();
			socket.destroy();
		};
	}, [exit]);

	const send = (payload: Record<string, unknown>) => {
		const socket = socketRef.current;
		if (!socket || socket.destroyed) {
			pushLine('system> not connected to host');
			return;
		}
		socket.write(`${JSON.stringify(payload)}\n`);
	};

	const header = useMemo(
		() => (ready ? 'openagent terminal-tui connected' : 'openagent terminal-tui starting...'),
		[ready],
	);

	return (
		<Box flexDirection="column" padding={1} gap={1}>
			<Box justifyContent="space-between">
				<Text color="cyan">{header}</Text>
				<Text color={sessionState.pendingApproval ? 'yellow' : 'gray'}>
					{sessionState.pendingApproval ? 'approval pending' : `last=${sessionState.lastEventType}`}
				</Text>
			</Box>
			{!interactiveTerminal && (
				<Box borderStyle="round" padding={1}>
					<Text color="yellow">
						Non-interactive terminal detected. Start this TUI from a real TTY to enable input.
					</Text>
				</Box>
			)}
			<Box gap={1}>
				<Box flexDirection="column" width="75%" borderStyle="round" padding={1}>
					<Text color="green">Event Log</Text>
					{lines.map((line, index) => (
						<Text key={`${line}-${String(index)}`}>{line}</Text>
					))}
				</Box>
				<Box flexDirection="column" width="25%" borderStyle="round" padding={1}>
					<Text color="magenta">Session</Text>
					<Text>channel=terminal</Text>
					<Text>conversation=terminal-{sessionState.sessionName}</Text>
					<Text>session={sessionState.sessionId}</Text>
					<Text>active={sessionState.sessionName}</Text>
					<Text>known={sessionState.knownSessions.join(', ')}</Text>
					<Text>pending={sessionState.pendingApproval ? 'yes' : 'no'}</Text>
					<Text>host={terminalHost}:{String(terminalPort)}</Text>
				</Box>
			</Box>
			<Box>
				<Text color="green">tui&gt; </Text>
				<Text>{input}</Text>
			</Box>
			{interactiveTerminal && (
				<InteractiveShell
					input={input}
					pushLine={pushLine}
					send={send}
					sessionState={sessionState}
					exit={exit}
					setInput={setInput}
					resetLines={resetLines}
				/>
			)}
		</Box>
	);
}

function InteractiveShell({
	input,
	pushLine,
	send,
	sessionState,
	exit,
	setInput,
	resetLines,
}: ShellProps) {
	useInput((value, key) => {
		if (key.ctrl && value === 'c') {
			exit();
			return;
		}

		if (key.return) {
			const text = input.trim();
			if (!text) {
				return;
			}
			if (text === '/exit') {
				exit();
				return;
			}
			if (text === '/help') {
				pushLine(`system> ${helpText}`);
				setInput('');
				return;
			}
			if (text === '/clear') {
				resetLines();
				pushLine('system> cleared local event view');
				setInput('');
				return;
			}
			if (text === '/sessions') {
				send({kind: 'list_sessions'});
				setInput('');
				return;
			}
			if (text === '/channel' || text.startsWith('/channel ') || text.startsWith('/channel-config ')) {
				send({kind: 'management', command: text});
				setInput('');
				return;
			}
			if (text === '/resume') {
				send({kind: 'control', subtype: 'resume', after: 0});
				pushLine('system> replay requested from event 0');
				setInput('');
				return;
			}
			if (text === '/approve') {
				send({kind: 'control', subtype: 'permission_response', approved: true});
				setInput('');
				return;
			}
			if (text === '/reject') {
				send({kind: 'control', subtype: 'permission_response', approved: false});
				setInput('');
				return;
			}
			if (text === '/interrupt') {
				send({kind: 'control', subtype: 'interrupt'});
				pushLine('system> interrupt requested');
				setInput('');
				return;
			}
			if (text === '/session') {
				pushLine(
					`system> session=${sessionState.sessionId} name=${sessionState.sessionName} pendingApproval=${String(sessionState.pendingApproval)} lastEvent=${sessionState.lastEventType}`,
				);
				setInput('');
				return;
			}
			if (text.startsWith('/new ')) {
				const sessionName = text.slice(5).trim();
				if (!sessionName) {
					pushLine('system> usage: /new <name>');
					setInput('');
					return;
				}
				pushLine(`system> binding new session ${sessionName}`);
				send({kind: 'bind', session_name: sessionName});
				setInput('');
				return;
			}
			if (text.startsWith('/switch ')) {
				const sessionName = text.slice(8).trim();
				if (!sessionName) {
					pushLine('system> usage: /switch <name>');
					setInput('');
					return;
				}
				pushLine(`system> switching to session ${sessionName}`);
				send({kind: 'bind', session_name: sessionName});
				setInput('');
				return;
			}
			send({kind: 'message', content: text});
			pushLine(`you> ${text}`);
			setInput('');
			return;
		}

		if (key.backspace || key.delete) {
			setInput(current => current.slice(0, -1));
			return;
		}

		if (!key.ctrl && !key.meta && value) {
			setInput(current => current + value);
		}
	});
	return null;
}

function renderBridgeEvent(
	message: BridgeEvent,
	pushLine: (line: string) => void,
	setSessionState: React.Dispatch<React.SetStateAction<SessionState>>,
	updateAssistantLine: (content: string) => void,
	finalizeAssistantLine: (content: string) => void,
) {
	setSessionState(current => ({
		...current,
		lastEventType: message.event_type,
		sessionId: message.session_id ?? current.sessionId,
	}));

	if (message.event_type === 'turn_started') {
		pushLine('system> turn started');
		return;
	}
	if (message.event_type === 'assistant_delta') {
		const payload = message.payload as {delta?: string};
		updateAssistantLine(payload.delta ?? '');
		return;
	}
	if (message.event_type === 'assistant_message') {
		setSessionState(current => ({
			...current,
			pendingApproval: false,
		}));
		const payload = message.payload as {message?: string};
		finalizeAssistantLine(payload.message ?? '');
		return;
	}
	if (message.event_type === 'tool_started') {
		const payload = message.payload as {tool_name?: string};
		pushLine(`tool> starting ${payload.tool_name ?? 'unknown'}`);
		return;
	}
	if (message.event_type === 'tool_result') {
		const payload = message.payload as {
			tool_name?: string;
			content?: unknown;
			structured_content?: unknown;
			truncated?: boolean | null;
		};
		pushLine(`tool> ${summarizeToolResult(payload)}`);
		return;
	}
	if (message.event_type === 'requires_action') {
		setSessionState(current => ({
			...current,
			pendingApproval: true,
		}));
		const payload = message.payload as {tool_name?: string};
		pushLine(`system> approval required for ${payload.tool_name ?? 'unknown'} (/approve or /reject)`);
		return;
	}
	if (message.event_type === 'turn_completed') {
		setSessionState(current => ({
			...current,
			pendingApproval: false,
		}));
		pushLine('system> turn completed');
		return;
	}
	if (message.event_type === 'turn_failed') {
		setSessionState(current => ({
			...current,
			pendingApproval: false,
		}));
		pushLine(`system> turn failed ${JSON.stringify(message.payload)}`);
	}
}

function summarizeToolResult(payload: {
	tool_name?: string;
	content?: unknown;
	structured_content?: unknown;
	truncated?: boolean | null;
}): string {
	const toolName = payload.tool_name ?? 'tool';
	if (toolName === 'WebFetch') {
		const structured = payload.structured_content as {url?: string; title?: string} | undefined;
		const title = typeof structured?.title === 'string' && structured.title.trim() ? structured.title.trim() : null;
		const url = typeof structured?.url === 'string' ? structured.url : null;
		const parts = [title, url].filter(Boolean);
		return parts.length > 0 ? `fetched ${parts.join(' | ')}` : 'fetched page content';
	}
	if (toolName === 'WebSearch') {
		const structured = payload.structured_content as {results?: unknown[]} | undefined;
		const resultCount = Array.isArray(structured?.results) ? structured.results.length : null;
		return resultCount !== null ? `search returned ${String(resultCount)} results` : 'search completed';
	}
	const content = Array.isArray(payload.content) ? payload.content[0] : payload.content;
	if (typeof content === 'string' && content.trim()) {
		const trimmed = content.trim().replace(/\s+/g, ' ');
		const preview = trimmed.length > 120 ? `${trimmed.slice(0, 117)}...` : trimmed;
		return `result ${preview}`;
	}
	return payload.truncated ? 'result stored (truncated preview)' : 'result ready';
}

function uniqueSessions(current: string[], sessionName: string): string[] {
	return Array.from(new Set([...current, sessionName])).sort();
}

render(<App />);
