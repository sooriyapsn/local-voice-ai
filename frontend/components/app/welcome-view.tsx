'use client';

import { useEffect, useRef, useState } from 'react';
import { motion } from 'motion/react';
import { type AgentState } from '@livekit/components-react';
import { CheckIcon, LockKeyIcon, SparkleIcon, SpinnerIcon } from '@phosphor-icons/react/dist/ssr';
import { AgentCharacter, type CharacterId } from '@/components/app/agent-character';
import { BalloonField } from '@/components/app/balloon-field';
import { ParentPanel } from '@/components/app/parent-panel';
import { toastAlert } from '@/components/livekit/alert-toast';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/livekit/select';
import type { StackChild } from '@/hooks/useStackStatus';
import { CHARACTERS } from '@/lib/characters';
import { cn } from '@/lib/utils';

// Human labels for the supervisor's child process names (see /api/status).
const CHILD_LABELS: Record<string, string> = {
  livekit: 'Getting the room ready',
  llama: 'Warming up my brain',
  nemotron: 'Tuning my ears',
  whisper: 'Tuning my ears',
  kokoro: 'Practicing my voice',
  agent: 'Almost there',
};

function StackStartupStatus({ services }: { services: StackChild[] }) {
  return (
    <div className="mt-6 w-72 text-left">
      <ul className="space-y-2">
        {services.map((child) => (
          <li key={child.name} className="text-foreground flex items-center gap-2 text-sm">
            {child.ready ? (
              <CheckIcon weight="bold" className="size-4 text-emerald-500" />
            ) : (
              <SpinnerIcon weight="bold" className="text-muted-foreground size-4 animate-spin" />
            )}
            <span className={child.ready ? '' : 'text-muted-foreground'}>
              {CHILD_LABELS[child.name] ?? child.name}
            </span>
            {!child.ready && child.detail && (
              <span className="text-muted-foreground ml-auto font-mono text-xs tabular-nums">
                {child.detail}
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground mt-4 text-xs leading-5">
        Our friends are waking up! The very first time can take a little while.
      </p>
    </div>
  );
}

const LANGUAGE_LABELS: Record<string, string> = {
  en: 'English',
  te: 'తెలుగు',
  mr: 'मराठी',
};
const ALL_LANGUAGES = ['en', 'te', 'mr'];

function LanguagePicker({
  language,
  availableLanguages,
  onChange,
}: {
  language: string;
  availableLanguages: string[];
  onChange: (id: string) => void;
}) {
  return (
    <Select value={language} onValueChange={onChange}>
      <SelectTrigger className="w-36" aria-label="Language">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ALL_LANGUAGES.map((id) => {
          const available = availableLanguages.includes(id);
          return (
            <SelectItem key={id} value={id} disabled={!available}>
              {LANGUAGE_LABELS[id]}
              {!available && <span className="text-muted-foreground ml-1 text-xs">(soon)</span>}
            </SelectItem>
          );
        })}
      </SelectContent>
    </Select>
  );
}

interface CharacterCardProps {
  id: CharacterId;
  name: string;
  tagline: string;
  agentState: AgentState;
  onSelect: () => void;
}

function CharacterCard({ id, name, tagline, agentState, onSelect }: CharacterCardProps) {
  const isAnnouncing = agentState === 'speaking';
  return (
    <motion.button
      type="button"
      onClick={onSelect}
      whileTap={{ scale: 0.94 }}
      animate={{ scale: isAnnouncing ? 1.08 : 1 }}
      transition={{ type: 'spring', stiffness: 300, damping: 20 }}
      className={cn(
        'flex flex-col items-center gap-2 rounded-3xl border-2 p-4 transition-colors',
        'min-w-[9.5rem] cursor-pointer',
        isAnnouncing ? 'border-foreground/40 bg-muted/60' : 'hover:bg-muted/40 border-transparent'
      )}
    >
      <div className="size-24 sm:size-28">
        <AgentCharacter state={agentState} character={id} />
      </div>
      <span className="text-foreground text-lg font-bold">{name}</span>
      <span className="text-muted-foreground max-w-[9rem] text-xs leading-4 text-balance">
        {tagline}
      </span>
    </motion.button>
  );
}

interface WelcomeViewProps {
  stackReady: boolean;
  stackChildren: StackChild[];
  wakeWord: boolean;
  availableLanguages: string[];
  onSelectCharacter: (character: CharacterId, language: string) => void;
}

export const WelcomeView = ({
  stackReady,
  stackChildren,
  wakeWord,
  availableLanguages,
  onSelectCharacter,
  ref,
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  const [language, setLanguage] = useState('en');
  const [announcingId, setAnnouncingId] = useState<CharacterId | null>(null);
  const [introDone, setIntroDone] = useState(false);
  const [parentPanelOpen, setParentPanelOpen] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const cancelledRef = useRef(false);

  // Ask for mic permission as soon as the page loads, not when a character
  // is tapped — so the browser's "Allow microphone?" prompt is already out
  // of the way by the time a call actually starts. Immediately stops the
  // track again: this only exists to trigger the permission dialog early,
  // not to hold the mic open before a call begins.
  useEffect(() => {
    navigator.mediaDevices
      ?.getUserMedia({ audio: true })
      .then((stream) => stream.getTracks().forEach((track) => track.stop()))
      .catch((err: DOMException) => {
        // NotAllowedError covers both "user just clicked Block" and "this
        // origin is already blocked from a previous decision" — the latter
        // means no prompt will ever show again until the site permission is
        // reset by hand, so tell the child's grown-up exactly where to look
        // rather than leaving the mic button silently stuck.
        if (err.name === 'NotAllowedError') {
          toastAlert({
            title: 'Microphone is blocked',
            description:
              'This browser has blocked microphone access for this page, so it won’t ask again automatically. Click the icon left of the address bar, open Site settings, and set Microphone to Allow, then reload.',
          });
        }
      });
  }, []);

  useEffect(() => {
    if (!stackReady) return;
    cancelledRef.current = false;
    const audioEl = audioRef.current;

    async function fetchPreview(characterId: string): Promise<string | null> {
      try {
        const res = await fetch('/api/preview-voice', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ character: characterId, language }),
        });
        if (!res.ok) throw new Error(`preview failed: ${res.status}`);
        const blob = await res.blob();
        return URL.createObjectURL(blob);
      } catch {
        return null;
      }
    }

    async function playIntros() {
      // Fetch all three up front (in parallel) rather than one at a time —
      // they're cached server-side after the first-ever request anyway, so
      // this is normally near-instant, and it means character 2 and 3 are
      // already downloaded by the time character 1 finishes talking.
      const urls = await Promise.all(CHARACTERS.map((c) => fetchPreview(c.id)));
      if (cancelledRef.current) {
        urls.forEach((url) => url && URL.revokeObjectURL(url));
        return;
      }

      for (let i = 0; i < CHARACTERS.length; i++) {
        if (cancelledRef.current) break;
        const url = urls[i];
        setAnnouncingId(CHARACTERS[i].id);
        const audio = audioRef.current;
        if (url && audio) {
          audio.src = url;
          await new Promise<void>((resolve) => {
            const done = () => resolve();
            audio.onended = done;
            audio.onerror = done;
            audio.play().catch(done);
            // Safety net in case playback events never fire.
            setTimeout(done, 8000);
          });
        }
        if (cancelledRef.current) break;
        setAnnouncingId(null);
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
      urls.forEach((url) => url && URL.revokeObjectURL(url));
      if (!cancelledRef.current) {
        setAnnouncingId(null);
        setIntroDone(true);
      }
    }

    playIntros();
    return () => {
      cancelledRef.current = true;
      audioEl?.pause();
    };
    // Re-runs (and replays intros) whenever the language changes, since each
    // character's intro line/voice differs per language.
  }, [stackReady, language]);

  const handleSelect = (id: CharacterId) => {
    cancelledRef.current = true;
    audioRef.current?.pause();
    setAnnouncingId(null);
    onSelectCharacter(id, language);
  };

  return (
    <div ref={ref} className="relative min-h-svh">
      <BalloonField />
      <audio ref={audioRef} className="hidden" />

      <div className="absolute top-4 right-4 z-20 flex items-center gap-2">
        {stackReady && (
          <LanguagePicker
            language={language}
            availableLanguages={availableLanguages}
            onChange={setLanguage}
          />
        )}
        <button
          type="button"
          onClick={() => setParentPanelOpen(true)}
          aria-label="Parent settings"
          title="Parent settings"
          className="border-input bg-background text-muted-foreground hover:text-foreground flex size-9 items-center justify-center rounded-full border"
        >
          <LockKeyIcon weight="bold" className="size-4" />
        </button>
      </div>
      <ParentPanel open={parentPanelOpen} onClose={() => setParentPanelOpen(false)} />

      <section className="flex min-h-svh flex-col items-center justify-center px-6 text-center">
        <div className="flex items-center gap-2">
          <motion.span
            animate={{ rotate: [0, 15, 0], scale: [1, 1.15, 1] }}
            transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
          >
            <SparkleIcon weight="fill" className="size-8 text-[#FFD166] sm:size-10" />
          </motion.span>
          <h1 className="text-foreground max-w-sm text-3xl leading-tight font-extrabold text-balance sm:text-4xl">
            Who do you want to <span className="text-[#FF6B4A]">play</span> with?
          </h1>
          <motion.span
            animate={{ rotate: [0, -15, 0], scale: [1, 1.15, 1] }}
            transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut', delay: 1 }}
          >
            <SparkleIcon weight="fill" className="size-8 text-[#FFD166] sm:size-10" />
          </motion.span>
        </div>
        <p className="text-muted-foreground mt-2 max-w-xs text-base leading-6 text-balance">
          {introDone ? 'Tap your favorite friend to start!' : 'Say hi to your storytime friends...'}
        </p>

        {stackReady ? (
          <>
            <div className="mt-8 flex flex-wrap items-start justify-center gap-4">
              {CHARACTERS.map((c) => (
                <CharacterCard
                  key={c.id}
                  id={c.id}
                  name={c.name}
                  tagline={c.tagline}
                  agentState={announcingId === c.id ? 'speaking' : 'listening'}
                  onSelect={() => handleSelect(c.id)}
                />
              ))}
            </div>
            {wakeWord && (
              <p className="text-muted-foreground mt-4 text-sm">
                Say <span className="text-foreground font-medium">“Hey LiveKit”</span> after
                connecting to wake your friend up.
              </p>
            )}
          </>
        ) : (
          <StackStartupStatus services={stackChildren} />
        )}
      </section>
    </div>
  );
};
