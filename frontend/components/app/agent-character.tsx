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

  // Grumpy characters fold their arms while idle instead of swaying them.
  const isGrumpyIdle = !!theme.grumpy && !isSpeaking && !isThinking && !isOffline;

  const leftArmAnimate = isOffline
    ? { rotate: 0 }
    : isGrumpyIdle
      ? { rotate: 34 }
      : isSpeaking
        ? { rotate: [-8, 18, -8] }
        : isThinking
          ? { rotate: -8 }
          : { rotate: [-6, 6, -6] };

  const rightArmAnimate = isOffline
    ? { rotate: 0 }
    : isGrumpyIdle
      ? { rotate: -34 }
      : isSpeaking
        ? { rotate: [8, -18, 8] }
        : isThinking
          ? { rotate: -132 } // raises toward the chin, a "hmm" gesture
          : { rotate: [6, -6, 6] };

  const armTransition = {
    duration: isOffline ? 0.3 : isSpeaking ? 0.55 : isThinking ? 0.9 : 2.4,
    repeat: isOffline || isThinking ? 0 : Infinity,
    ease: 'easeInOut' as const,
  };

  // Legs dangle and swing gently, a little faster/livelier while speaking.
  const leftLegAnimate = isOffline ? { rotate: 0 } : { rotate: [-10, 8, -10] };
  const rightLegAnimate = isOffline ? { rotate: 0 } : { rotate: [10, -8, 10] };
  const legTransition = {
    duration: isOffline ? 0.3 : isSpeaking ? 0.7 : 1.8,
    repeat: isOffline ? 0 : Infinity,
    ease: 'easeInOut' as const,
  };

  return (
    <div className={cn('flex h-full w-full items-center justify-center', className)}>
      <motion.svg
        viewBox="-30 -30 260 260"
        className="h-full w-full drop-shadow-md"
        animate={isOffline ? { y: 0, opacity: 0.45 } : { y: [0, -6, 0], opacity: 1 }}
        transition={
          isOffline
            ? { duration: 0.3 }
            : { duration: isSpeaking ? 0.9 : 2.4, repeat: Infinity, ease: 'easeInOut' }
        }
      >
        <defs>
          <radialGradient id={`body-gradient-${character}`} cx="35%" cy="30%" r="75%">
            <stop offset="0%" stopColor="white" stopOpacity="0.55" />
            <stop offset="35%" stopColor="white" stopOpacity="0" />
            <stop offset="100%" stopColor="black" stopOpacity="0.08" />
          </radialGradient>
        </defs>

        {/* listening: pulsing ring, like a mic/radar ping */}
        {isListening && (
          <>
            <motion.circle
              cx="100"
              cy="108"
              r="82"
              fill="none"
              stroke={theme.accessoryColor}
              strokeWidth="4"
              initial={{ scale: 1, opacity: 0.7 }}
              animate={{ scale: 1.35, opacity: 0 }}
              transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut' }}
            />
            <motion.circle
              cx="100"
              cy="108"
              r="82"
              fill="none"
              stroke={theme.accessoryColor}
              strokeWidth="4"
              initial={{ scale: 1, opacity: 0.7 }}
              animate={{ scale: 1.35, opacity: 0 }}
              transition={{ duration: 1.6, repeat: Infinity, ease: 'easeOut', delay: 0.8 }}
            />
          </>
        )}

        {/* thinking: bouncing "..." thought trail */}
        {isThinking && (
          <motion.g
            animate={{ y: [0, -4, 0] }}
            transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut' }}
          >
            {[0, 1, 2].map((i) => (
              <motion.circle
                key={i}
                cx={168 + i * 16}
                cy={-4 - i * 10}
                r={5 + i * 1.5}
                fill={theme.accessoryColor}
                stroke={theme.bodyStroke}
                strokeWidth="1.5"
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 1.2, repeat: Infinity, ease: 'easeInOut', delay: i * 0.2 }}
              />
            ))}
          </motion.g>
        )}

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
        <circle cx="100" cy="108" r="78" fill={`url(#body-gradient-${character})`} />

        {/* arms */}
        <motion.g
          animate={leftArmAnimate}
          transition={armTransition}
          style={{ transformOrigin: '26px 122px' }}
        >
          <line
            x1="26"
            y1="122"
            x2="6"
            y2="158"
            stroke={theme.body}
            strokeWidth="15"
            strokeLinecap="round"
          />
          <circle cx="6" cy="158" r="11" fill={theme.body} />
        </motion.g>
        <motion.g
          animate={rightArmAnimate}
          transition={armTransition}
          style={{ transformOrigin: '174px 122px' }}
        >
          <line
            x1="174"
            y1="122"
            x2="194"
            y2="158"
            stroke={theme.body}
            strokeWidth="15"
            strokeLinecap="round"
          />
          <circle cx="194" cy="158" r="11" fill={theme.body} />
        </motion.g>

        {/* legs */}
        <motion.g
          animate={leftLegAnimate}
          transition={legTransition}
          style={{ transformOrigin: '80px 178px' }}
        >
          <line
            x1="80"
            y1="178"
            x2="70"
            y2="212"
            stroke={theme.body}
            strokeWidth="15"
            strokeLinecap="round"
          />
          <circle cx="70" cy="212" r="11" fill={theme.body} />
        </motion.g>
        <motion.g
          animate={rightLegAnimate}
          transition={legTransition}
          style={{ transformOrigin: '120px 178px' }}
        >
          <line
            x1="120"
            y1="178"
            x2="130"
            y2="212"
            stroke={theme.body}
            strokeWidth="15"
            strokeLinecap="round"
          />
          <circle cx="130" cy="212" r="11" fill={theme.body} />
        </motion.g>

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
