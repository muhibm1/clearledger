// The voice call. ElevenAgents runs the conversation; every tool the agent calls runs through
// this page's server (POST /api/tools/<name>), and each call is reported to the page so the
// screen can follow it: open the customer the agent is talking about, light up the records
// behind its answer.

import { useCallback, useRef, useState } from 'react';

const TOOLS = ['find_customers', 'customer_summary', 'list_invoices', 'list_deals', 'query', 'check_citations'];

// Runs one tool call on the server and returns the result as text for the agent.
async function runTool(name, params) {
  const res = await fetch(`/api/tools/${name}`, { method: 'POST', body: JSON.stringify(params) });
  const text = await res.text();
  if (!res.ok) throw new Error(JSON.parse(text).error || `tool ${name} failed`);
  return text;
}

export function useVoice({ onTool }) {
  const [status, setStatus] = useState('idle'); // idle | connecting | listening | speaking | error
  const [lines, setLines] = useState([]); // [{ who: 'you' | 'agent' | 'tool', text, ...}]
  const [error, setError] = useState('');
  const convo = useRef(null);
  const toolRef = useRef(onTool);
  toolRef.current = onTool;

  const add = (line) => setLines((all) => [...all, { at: Date.now(), ...line }]);

  // Starts a call: asks the server how to connect (a signed address or the agent id), then opens it.
  const start = useCallback(async () => {
    setError('');
    setStatus('connecting');
    try {
      const session = await (await fetch('/api/voice/session')).json();
      if (!session.ready) throw new Error(session.reason);
      const clientTools = Object.fromEntries(TOOLS.map((name) => [name, async (params) => {
        const started = performance.now();
        const { tool_call_id: _, ...args } = params || {};
        try {
          const text = await runTool(name, args);
          const result = JSON.parse(text);
          add({ who: 'tool', name, args, ms: Math.round(performance.now() - started) });
          toolRef.current?.(name, args, result);
          return text;
        } catch (e) {
          add({ who: 'tool', name, args, error: e.message });
          throw e;
        }
      }]));
      // Loaded on the first call, so the page itself stays small.
      const { Conversation } = await import('@elevenlabs/client');
      convo.current = await Conversation.startSession({
        ...(session.signed_url ? { signedUrl: session.signed_url } : { agentId: session.agent_id }),
        clientTools,
        onMessage: ({ message, source }) => add({ who: source === 'user' ? 'you' : 'agent', text: message }),
        onModeChange: ({ mode }) => setStatus(mode),
        onDisconnect: () => { convo.current = null; setStatus('idle'); },
        onError: (message) => { setError(message); },
      });
    } catch (e) {
      convo.current = null;
      setError(e.message || String(e));
      setStatus('error');
    }
  }, []);

  // Ends the call.
  const stop = useCallback(async () => {
    await convo.current?.endSession();
    convo.current = null;
    setStatus('idle');
  }, []);

  // Clears the last call's transcript and any error.
  const dismiss = useCallback(() => { setLines([]); setError(''); setStatus('idle'); }, []);

  return { status, lines, error, start, stop, dismiss, live: ['connecting', 'listening', 'speaking'].includes(status) };
}
