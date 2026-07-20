import { motion } from 'motion/react';
import { type AgentState, type TrackReference, useTrackVolume } from '@livekit/components-react';
import { cn } from '@/lib/utils';

export type CharacterId = 'red' | 'blue' | 'pink';

interface CharacterTheme {
  body: string;
  bodyStroke: string;
  ear: string;
  cheek: string;
  mouth: string;
  accessory: 'star' | 'bolt' | 'heart';
  accessoryColor: string;
  /** Grumpy characters get angled brows and a default frown instead of a smile. */
  grumpy?: boolean;
}

const THEMES: Record<CharacterId, CharacterTheme> = {
  red: {
    body: '#E8503A',
    bodyStroke: '#C43A28',
    ear: '#D4442F',
    cheek: '#FFB199',
    mouth: '#5C1F14',
    accessory: 'bolt',
    accessoryColor: '#FFD166',
    grumpy: true,
  },
  blue: {
    body: '#4A9DFF',
    bodyStroke: '#2E7FDE',
    ear: '#3E8FF2',
    cheek: '#BEE3FF',
    mouth: '#1B3A5C',
    accessory: 'bolt',
    accessoryColor: '#FFE873',
  },
  pink: {
    body: '#FF8FB3',
    bodyStroke: '#E86F97',
    ear: '#FF7FA8',
    cheek: '#FFD1E1',
    mouth: '#8A2F4C',
    accessory: 'heart',
    accessoryColor: '#FF6B9A',
  },
};

interface AgentCharacterProps {
  state: AgentState;
  audioTrack?: TrackReference;
  character?: CharacterId;
  className?: string;
}

const OFFLINE_STATES: AgentState[] = [
  'disconnected',
  'connecting',
  'pre-connect-buffering',
  'failed',
];

function Accessory({ theme }: { theme: CharacterTheme }) {
  if (theme.accessory === 'heart') {
    return (
      <path
        d="M100 28 C94 20 82 20 82 32 C82 42 100 52 100 52 C100 52 118 42 118 32 C118 20 106 20 100 28 Z"
        fill={theme.accessoryColor}
      />
    );
  }
  if (theme.accessory === 'bolt') {
    return <path d="M104 8 L88 30 L98 30 L92 52 L114 24 L102 24 Z" fill={theme.accessoryColor} />;
  }
  return (
    <path
      d="M 100 8 L 104 16 L 113 16 L 106 22 L 108 31 L 100 25 L 92 31 L 94 22 L 87 16 L 96 16 Z"
      fill={theme.accessoryColor}
    />
  );
}

/**
 * A small chibi-style mascot that reacts to the voice agent's state: it
 * blinks and bobs while idle, tilts and glances around while it "thinks",
 * and its mouth opens and closes in sync with the agent's live audio level
 * while it speaks — instead of the plain audio bar visualizer. `character`
 * swaps its color palette, accessory, and (for the grumpy one) resting
 * expression.
 */
export function AgentCharacter({
  state,
  audioTrack,
  character = 'red',
  className,
}: AgentCharacterProps) {
  const volume = useTrackVolume(audioTrack);
  const theme = THEMES[character];

  const isSpeaking = state === 'speaking';
  const isThinking = state === 'thinking';
  const isListening = state === 'listening' || state === 'idle' || state === 'initializing';
  const isOffline = OFFLINE_STATES.includes(state);

  // Live mic/agent amplitude -> how open the mouth is (0 closed, 1 wide open).
  const mouthOpen = isSpeaking ? Math.min(1, Math.max(0.12, volume * 3.5)) : 0;
  const eyeRadius = isListening ? 12 : 10;

  return (
    <div className={cn('flex h-full w-full items-center justify-center', className)}>
      <motion.svg
        viewBox="0 0 200 200"
        className="h-full w-full drop-shadow-md"
        animate={isOffline ? { y: 0, opacity: 0.45 } : { y: [0, -6, 0], opacity: 1 }}
        transition={
          isOffline
            ? { duration: 0.3 }
            : { duration: isSpeaking ? 0.9 : 2.4, repeat: Infinity, ease: 'easeInOut' }
        }
      >
        {/* accessory */}
        <motion.g
          animate={{ rotate: [-6, 6, -6] }}
          transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
          style={{ transformOrigin: '100px 40px' }}
        >
          <line
            x1="100"
            y1="40"
            x2="100"
            y2="20"
            stroke={theme.bodyStroke}
            strokeWidth="4"
            strokeLinecap="round"
          />
          <Accessory theme={theme} />
        </motion.g>

        {/* ears */}
        <circle cx="48" cy="70" r="16" fill={theme.ear} />
        <circle cx="152" cy="70" r="16" fill={theme.ear} />

        {/* head/body */}
        <motion.circle
          cx="100"
          cy="108"
          r="78"
          fill={theme.body}
          stroke={theme.bodyStroke}
          strokeWidth="3"
          animate={isThinking ? { rotate: [-3, 3, -3] } : { rotate: 0 }}
          style={{ transformOrigin: '100px 186px' }}
          transition={{ duration: 1.6, repeat: isThinking ? Infinity : 0, ease: 'easeInOut' }}
        />

        {/* cheeks */}
        <circle cx="52" cy="122" r="12" fill={theme.cheek} opacity="0.55" />
        <circle cx="148" cy="122" r="12" fill={theme.cheek} opacity="0.55" />

        {/* grumpy brows */}
        {theme.grumpy && (
          <>
            <line
              x1="60"
              y1="80"
              x2="82"
              y2="87"
              stroke={theme.mouth}
              strokeWidth="4"
              strokeLinecap="round"
            />
            <line
              x1="140"
              y1="80"
              x2="118"
              y2="87"
              stroke={theme.mouth}
              strokeWidth="4"
              strokeLinecap="round"
            />
          </>
        )}

        {/* eyes (blink loop) */}
        <motion.g
          animate={{ scaleY: [1, 1, 0.08, 1] }}
          transition={{
            duration: 4,
            repeat: Infinity,
            times: [0, 0.9, 0.94, 1],
            ease: 'easeInOut',
          }}
          style={{ transformOrigin: '100px 98px' }}
        >
          <circle cx="72" cy="98" r={eyeRadius} fill="white" />
          <circle cx="128" cy="98" r={eyeRadius} fill="white" />
          <motion.circle
            cx="72"
            cy="98"
            r="5.5"
            fill="#3A2A18"
            animate={isThinking ? { x: [-3, 3, -3], y: -4 } : { x: 0, y: 0 }}
            transition={{ duration: 1.6, repeat: isThinking ? Infinity : 0, ease: 'easeInOut' }}
          />
          <motion.circle
            cx="128"
            cy="98"
            r="5.5"
            fill="#3A2A18"
            animate={isThinking ? { x: [-3, 3, -3], y: -4 } : { x: 0, y: 0 }}
            transition={{ duration: 1.6, repeat: isThinking ? Infinity : 0, ease: 'easeInOut' }}
          />
        </motion.g>

        {/* mouth */}
        {isSpeaking ? (
          <motion.ellipse
            cx="100"
            cy="140"
            rx="15"
            ry="17"
            fill={theme.mouth}
            initial={{ scaleY: 0.2 }}
            animate={{ scaleY: Math.max(0.2, mouthOpen) }}
            transition={{ duration: 0.08 }}
            style={{ transformOrigin: '100px 140px' }}
          />
        ) : isThinking ? (
          <path
            d="M 86 141 Q 100 135 114 141"
            stroke={theme.mouth}
            strokeWidth="4"
            fill="none"
            strokeLinecap="round"
          />
        ) : theme.grumpy ? (
          <path
            d="M 84 138 Q 100 130 116 138"
            stroke={theme.mouth}
            strokeWidth="4"
            fill="none"
            strokeLinecap="round"
          />
        ) : (
          <path
            d="M 80 133 Q 100 152 120 133"
            stroke={theme.mouth}
            strokeWidth="4"
            fill="none"
            strokeLinecap="round"
          />
        )}
      </motion.svg>
    </div>
  );
}
