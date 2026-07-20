'use client';

import { useEffect, useState } from 'react';

export interface StackChild {
  name: string;
  ready: boolean;
  running: boolean;
  restarts: number;
  /** Download progress, e.g. "1.2 GB" — present while a model is downloading. */
  detail?: string;
}

export interface StackStatus {
  ready: boolean;
  children: StackChild[];
  wakeWord: boolean;
  /** Which languages the backend can actually speak right now — Telugu/Marathi
   * only appear once the optional indic_tts child is enabled and running. */
  languages: string[];
  /** Parent-configured session time limit, minutes. */
  timeLimitMinutes: number;
}

const POLL_INTERVAL_MS = 2000;

/**
 * Polls /api/status while the backend supervisor is still starting its
 * children — the first boot downloads model weights, which can take a long
 * time, and without this the app is just a start button that doesn't work.
 *
 * Fails open: if the endpoint is missing or unreachable (e.g. `pnpm dev`
 * without the Python backend), the stack is treated as ready.
 */
export function useStackStatus(): StackStatus {
  const [status, setStatus] = useState<StackStatus>({
    ready: false,
    children: [],
    wakeWord: false,
    languages: ['en'],
    timeLimitMinutes: 30,
  });

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      let next: StackStatus;
      try {
        const res = await fetch('/api/status', { cache: 'no-store' });
        if (!res.ok) throw new Error(`status ${res.status}`);
        const data = await res.json();
        next = {
          ready: data.ready,
          children: data.children,
          wakeWord: !!data.wake_word,
          languages: Array.isArray(data.languages) ? data.languages : ['en'],
          timeLimitMinutes:
            typeof data.time_limit_minutes === 'number' ? data.time_limit_minutes : 30,
        };
      } catch {
        // fail open
        next = {
          ready: true,
          children: [],
          wakeWord: false,
          languages: ['en'],
          timeLimitMinutes: 30,
        };
      }
      if (cancelled) return;
      setStatus(next);
      if (!next.ready) {
        timer = setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return status;
}
