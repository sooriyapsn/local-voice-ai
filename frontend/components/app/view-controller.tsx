'use client';

import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import type { CharacterId } from '@/components/app/agent-character';
import { SessionView } from '@/components/app/session-view';
import { WelcomeView } from '@/components/app/welcome-view';
import { useStackStatus } from '@/hooks/useStackStatus';

const MotionWelcomeView = motion.create(WelcomeView);
const MotionSessionView = motion.create(SessionView);

const VIEW_MOTION_PROPS = {
  variants: {
    visible: {
      opacity: 1,
    },
    hidden: {
      opacity: 0,
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: {
    duration: 0.5,
    ease: 'linear',
  },
};

interface ViewControllerProps {
  appConfig: AppConfig;
  character: CharacterId;
  onSelectCharacter: (character: CharacterId, language: string) => void;
}

export function ViewController({ appConfig, character, onSelectCharacter }: ViewControllerProps) {
  const { isConnected } = useSessionContext();
  const stackStatus = useStackStatus();

  return (
    <AnimatePresence mode="wait">
      {/* Welcome view */}
      {!isConnected && (
        <MotionWelcomeView
          key="welcome"
          {...VIEW_MOTION_PROPS}
          onSelectCharacter={onSelectCharacter}
          stackReady={stackStatus.ready}
          stackChildren={stackStatus.children}
          wakeWord={stackStatus.wakeWord}
          availableLanguages={stackStatus.languages}
        />
      )}
      {/* Session view */}
      {isConnected && (
        <MotionSessionView
          key="session-view"
          {...VIEW_MOTION_PROPS}
          appConfig={appConfig}
          character={character}
          timeLimitMinutes={stackStatus.timeLimitMinutes}
        />
      )}
    </AnimatePresence>
  );
}
