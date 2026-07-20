'use client';

import { useEffect, useState } from 'react';
import { LockKeyIcon, SpinnerIcon, XIcon } from '@phosphor-icons/react/dist/ssr';
import { toastAlert } from '@/components/livekit/alert-toast';
import { cn } from '@/lib/utils';

interface Settings {
  time_limit_minutes: number;
  story_title: string;
  story_text: string;
}

async function verifyPin(pin: string): Promise<boolean> {
  const res = await fetch('/api/parent/verify-pin', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ pin }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  return !!data.ok;
}

function PinGate({ onUnlocked }: { onUnlocked: (pin: string) => void }) {
  const [pin, setPin] = useState('');
  const [checking, setChecking] = useState(false);

  const submit = async () => {
    setChecking(true);
    const ok = await verifyPin(pin);
    setChecking(false);
    if (ok) {
      onUnlocked(pin);
    } else {
      toastAlert({ title: 'Incorrect PIN', description: 'Please try again.' });
      setPin('');
    }
  };

  return (
    <div className="flex flex-col items-center gap-4 py-6">
      <LockKeyIcon weight="fill" className="text-muted-foreground size-10" />
      <p className="text-foreground text-sm font-medium">Enter the parent PIN to continue</p>
      <input
        type="password"
        inputMode="numeric"
        autoFocus
        value={pin}
        onChange={(e) => setPin(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && pin && submit()}
        className="border-input bg-background text-foreground w-32 rounded-lg border px-3 py-2 text-center text-lg tracking-widest"
        placeholder="••••"
      />
      <button
        type="button"
        disabled={!pin || checking}
        onClick={submit}
        className={cn(
          'bg-foreground text-background rounded-full px-5 py-2 text-sm font-semibold',
          (!pin || checking) && 'cursor-not-allowed opacity-50'
        )}
      >
        {checking ? <SpinnerIcon className="size-4 animate-spin" /> : 'Unlock'}
      </button>
    </div>
  );
}

function SettingsForm({ pin }: { pin: string }) {
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [timeLimit, setTimeLimit] = useState(30);
  const [storyTitle, setStoryTitle] = useState('');
  const [storyText, setStoryText] = useState('');

  useEffect(() => {
    fetch('/api/parent/settings', { headers: { 'X-Parent-Pin': pin } })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: Settings | null) => {
        if (data) {
          setTimeLimit(data.time_limit_minutes);
          setStoryTitle(data.story_title);
          setStoryText(data.story_text);
        }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load once per mount, pin is stable for this panel's lifetime
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch('/api/parent/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pin,
          time_limit_minutes: timeLimit,
          story_title: storyTitle,
          story_text: storyText,
        }),
      });
      if (!res.ok) throw new Error();
      toastAlert({ title: 'Saved', description: 'Settings updated.' });
    } catch {
      toastAlert({ title: 'Could not save', description: 'Please try again.' });
    } finally {
      setSaving(false);
    }
  };

  const uploadPdf = async (file: File) => {
    setSaving(true);
    try {
      const form = new FormData();
      form.append('pin', pin);
      form.append('file', file);
      const res = await fetch('/api/parent/upload-pdf', { method: 'POST', body: form });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || 'upload failed');
      }
      const data: Settings = await res.json();
      setStoryTitle(data.story_title);
      setStoryText(data.story_text);
      toastAlert({
        title: 'PDF uploaded',
        description: `Using "${data.story_title}" as the lesson.`,
      });
    } catch (err) {
      toastAlert({
        title: 'Could not read PDF',
        description: err instanceof Error ? err.message : 'Please try a different file.',
      });
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) {
    return (
      <div className="flex justify-center py-10">
        <SpinnerIcon className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 py-4 text-left">
      <div>
        <label className="text-foreground text-sm font-semibold">Play time limit (minutes)</label>
        <input
          type="number"
          min={5}
          max={180}
          step={1}
          value={timeLimit}
          onChange={(e) => setTimeLimit(Number(e.target.value))}
          className="border-input bg-background text-foreground mt-1 w-24 rounded-lg border px-3 py-1.5"
        />
        <p className="text-muted-foreground mt-1 text-xs">
          The session ends automatically once this much time has passed.
        </p>
      </div>

      <div>
        <label className="text-foreground text-sm font-semibold">Story / lesson title</label>
        <input
          type="text"
          value={storyTitle}
          onChange={(e) => setStoryTitle(e.target.value)}
          placeholder="e.g. The Three Little Pigs"
          className="border-input bg-background text-foreground mt-1 w-full rounded-lg border px-3 py-1.5"
        />
      </div>

      <div>
        <label className="text-foreground text-sm font-semibold">
          Story text (paste it in, or upload a PDF below)
        </label>
        <textarea
          value={storyText}
          onChange={(e) => setStoryText(e.target.value)}
          rows={6}
          placeholder="Paste a story or a lesson you'd like your friend to teach..."
          className="border-input bg-background text-foreground mt-1 w-full rounded-lg border px-3 py-2 text-sm"
        />
        <p className="text-muted-foreground mt-1 text-xs">
          When set, every character weaves this into the conversation. Clear the text and save to go
          back to regular storytelling.
        </p>
      </div>

      <div>
        <label className="text-foreground text-sm font-semibold">Or upload a PDF</label>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => e.target.files?.[0] && uploadPdf(e.target.files[0])}
          className="text-foreground mt-1 block text-sm"
        />
      </div>

      <button
        type="button"
        disabled={saving}
        onClick={save}
        className={cn(
          'bg-foreground text-background self-start rounded-full px-5 py-2 text-sm font-semibold',
          saving && 'cursor-not-allowed opacity-50'
        )}
      >
        {saving ? <SpinnerIcon className="size-4 animate-spin" /> : 'Save settings'}
      </button>
    </div>
  );
}

export function ParentPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [pin, setPin] = useState<string | null>(null);

  if (!open) return null;

  const close = () => {
    setPin(null);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 px-4">
      <div className="bg-background relative w-full max-w-md rounded-2xl border p-6 shadow-xl">
        <button
          type="button"
          onClick={close}
          aria-label="Close"
          className="text-muted-foreground hover:text-foreground absolute top-4 right-4"
        >
          <XIcon className="size-5" />
        </button>
        <h2 className="text-foreground text-lg font-bold">Parent settings</h2>
        {pin === null ? <PinGate onUnlocked={setPin} /> : <SettingsForm pin={pin} />}
      </div>
    </div>
  );
}
