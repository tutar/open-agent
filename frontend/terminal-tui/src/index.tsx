import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Box, Text, render, useApp, useInput} from 'ink';
import {spawn, type ChildProcessWithoutNullStreams} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

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

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const bridgePath = path.resolve(__dirname, '../scripts/bridge.py');
const sdkRoot = path.resolve(__dirname, '../../');
const pythonBin = process.env.PYTHON ?? 'python3';
const lineLimit = 20;
const helpText =
	'Commands: tool <text>, admin <text>, /new <name>, /switch <name>, /sessions, /approve, /reject, /interrupt, /session, /clear, /help, /exit';
const interactiveTerminal = Boolean(process.stdin.isTTY && process.stdout.isTTY);

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
	const bridgeRef = useRef<ChildProcessWithoutNullStreams | null>(null);
	const stdoutBufferRef = useRef('');

	const pushLine = (line: string) => {
		setLines(prev => [...prev.slice(-(lineLimit - 1)), line]);
	};

	const resetLines = () => {
		setLines([helpText]);
	};

	useEffect(() => {
		const child = spawn(pythonBin, [bridgePath], {
			cwd: sdkRoot,
			stdio: ['pipe', 'pipe', 'pipe'],
		});
		bridgeRef.current = child;

		child.stdout.on('data', chunk => {
			stdoutBufferRef.current += chunk.toString();
			const parts = stdoutBufferRef.current.split('\n');
			stdoutBufferRef.current = parts.pop() ?? '';

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
					pushLine(`bridge> ${message.message}`);
					continue;
				}
				renderBridgeEvent(message, pushLine, setSessionState);
			}
		});

		child.on('exit', code => {
			pushLine(`system> bridge exited (${String(code)})`);
			exit();
		});

		child.stderr.on('data', chunk => {
			const output = chunk.toString().trim();
			if (output) {
				pushLine(`bridge-stderr> ${output}`);
			}
		});

		return () => {
			child.kill();
		};
	}, [exit]);

	const send = (payload: Record<string, unknown>) => {
		const child = bridgeRef.current;
		if (!child) {
			return;
		}
		child.stdin.write(`${JSON.stringify(payload)}\n`);
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
					<Text>python={pythonBin}</Text>
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
	if (message.event_type === 'assistant_message') {
		setSessionState(current => ({
			...current,
			pendingApproval: false,
		}));
		const payload = message.payload as {message?: string};
		pushLine(`assistant> ${payload.message ?? ''}`);
		return;
	}
	if (message.event_type === 'tool_started') {
		const payload = message.payload as {tool_name?: string};
		pushLine(`tool> starting ${payload.tool_name ?? 'unknown'}`);
		return;
	}
	if (message.event_type === 'tool_result') {
		const payload = message.payload as {content?: unknown};
		pushLine(`tool> result ${JSON.stringify(payload.content ?? '')}`);
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

function uniqueSessions(current: string[], sessionName: string): string[] {
	return Array.from(new Set([...current, sessionName])).sort();
}

render(<App />);
