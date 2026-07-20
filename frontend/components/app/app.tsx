'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { TokenSource } from 'livekit-client';
import {
  RoomAudioRenderer,
  SessionProvider,
  StartAudio,
  useSession,
} from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import type { CharacterId } from '@/components/app/agent-character';
import { ViewController } from '@/components/app/view-controller';
import { Toaster } from '@/components/livekit/toaster';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { useDebugMode } from '@/hooks/useDebug';
import { getSandboxTokenSource } from '@/lib/utils';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();

  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

export function App({ appConfig }: AppProps) {
  // A ref (not state) so the token source's fetch closure — which runs async,
  // after start() is called — always sees the character picked in the same
  // click that triggered it, without fighting React's render/effect timing.
  const selectionRef = useRef<{ character: CharacterId; language: string } | null>(null);
  // Mirrors selectionRef, but as state, so the in-call view can render the
  // picked character's mascot instead of always defaulting to the first one.
  const [selectedCharacter, setSelectedCharacter] = useState<CharacterId>('red');

  const tokenSource = useMemo(() => {
    if (typeof process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT === 'string') {
      return getSandboxTokenSource(appConfig);
    }
    return TokenSource.custom(async (options) => {
      const res = await fetch('/api/connection-details', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character: selectionRef.current?.character,
          language: selectionRef.current?.language,
          ...(options.agentName
            ? { room_config: { agents: [{ agent_name: options.agentName }] } }
            : {}),
        }),
      });
      if (!res.ok) {
        throw new Error(`Failed to fetch connection details: ${res.status}`);
      }
      const data = await res.json();
      return {
        serverUrl: data.serverUrl,
        roomName: data.roomName,
        participantName: data.participantName,
        participantToken: data.participantToken,
      };
    });
  }, [appConfig]);

  const session = useSession(
    tokenSource,
    appConfig.agentName ? { agentName: appConfig.agentName } : undefined
  );

  const handleSelectCharacter = useCallback(
    (character: CharacterId, language: string) => {
      selectionRef.current = { character, language };
      setSelectedCharacter(character);
      // Force the mic on regardless of any muted state persisted from a
      // previous session (usePersistentUserChoices) — this is voice-only, so
      // a kid should never need to find and tap an "unmute" button first.
      session.start({ tracks: { microphone: { enabled: true } } });
    },
    [session]
  );

  return (
    <SessionProvider session={session}>
      <AppSetup />
      <main className="grid h-svh grid-cols-1 place-content-center">
        <ViewController
          appConfig={appConfig}
          character={selectedCharacter}
          onSelectCharacter={handleSelectCharacter}
        />
      </main>
      <StartAudio label="Start Audio" />
      <RoomAudioRenderer />
      <Toaster />
    </SessionProvider>
  );
}
