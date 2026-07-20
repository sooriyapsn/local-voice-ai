'use client';

import { useMemo, useState } from 'react';
import { motion } from 'motion/react';

const BALLOON_COLORS = ['#FF8FA3', '#FFD166', '#8EC5FF', '#7FD8BE', '#C6A8FF', '#FF9F6B'];

interface BalloonConfig {
  leftPercent: number;
  width: number;
  color: string;
  riseDuration: number;
  popAtPercent: number; // how far up the screen (0-100) it pops
  startDelay: number;
}

function randomBalloonConfig(): BalloonConfig {
  return {
    leftPercent: 4 + Math.random() * 92,
    width: 36 + Math.random() * 30,
    color: BALLOON_COLORS[Math.floor(Math.random() * BALLOON_COLORS.length)],
    riseDuration: 7 + Math.random() * 5,
    popAtPercent: 25 + Math.random() * 55,
    startDelay: Math.random() * 4,
  };
}

function BalloonShape({ color, width }: { color: string; width: number }) {
  const height = width * 1.2;
  return (
    <svg
      width={width}
      height={height + width * 0.6}
      viewBox="0 0 100 160"
      fill="none"
      className="drop-shadow-sm"
    >
      {/* string */}
      <path
        d="M50 118 Q46 130 50 140 Q54 150 50 158"
        stroke={color}
        strokeWidth="1.5"
        fill="none"
        opacity="0.6"
      />
      {/* knot */}
      <path d="M46 112 L54 112 L50 120 Z" fill={color} />
      {/* body */}
      <ellipse cx="50" cy="62" rx="48" ry="58" fill={color} />
      {/* shine */}
      <ellipse cx="34" cy="38" rx="12" ry="18" fill="white" opacity="0.35" />
    </svg>
  );
}

function Balloon({ initialDelay }: { initialDelay: number }) {
  const [cycle, setCycle] = useState(0);
  const [popped, setPopped] = useState(false);
  const config = useMemo(() => randomBalloonConfig(), [cycle]);
  const delay = cycle === 0 ? initialDelay : 0;

  return (
    <motion.div
      key={cycle}
      className="pointer-events-none absolute bottom-0"
      style={{ left: `${config.leftPercent}%` }}
      initial={{ y: 0, opacity: 0, scale: 1 }}
      animate={
        popped
          ? { scale: [1, 1.35, 0], opacity: [1, 1, 0] }
          : { y: `-${config.popAtPercent}vh`, opacity: [0, 0.3, 0.3, 0.3] }
      }
      transition={
        popped
          ? { duration: 0.25, ease: 'easeOut' }
          : { duration: config.riseDuration, delay, ease: 'linear' }
      }
      onAnimationComplete={() => {
        if (!popped) {
          setPopped(true);
        } else {
          setPopped(false);
          setCycle((c) => c + 1);
        }
      }}
    >
      <BalloonShape color={config.color} width={config.width} />
    </motion.div>
  );
}

const BALLOON_COUNT = 7;

/**
 * A continuous stream of semi-transparent balloons rising from the bottom of
 * the screen and popping partway up, then respawning from the bottom again —
 * a playful, ever-present background layer. Purely CSS/SVG + Framer Motion,
 * no image assets.
 */
export function BalloonField() {
  const delays = useMemo(
    () => Array.from({ length: BALLOON_COUNT }, () => Math.random() * 6),
    []
  );
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {delays.map((delay, i) => (
        <Balloon key={i} initialDelay={delay} />
      ))}
    </div>
  );
}
