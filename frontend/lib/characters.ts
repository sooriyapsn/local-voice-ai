import type { CharacterId } from '@/components/app/agent-character';

export interface CharacterDef {
  id: CharacterId;
  name: string;
  tagline: string;
}

// Keep ids/order in sync with local_voice_ai/characters.py.
export const CHARACTERS: CharacterDef[] = [
  { id: 'red', name: 'Red One', tagline: 'Grumpy on the outside, sweet on the inside' },
  { id: 'blue', name: 'Blue Bolt', tagline: 'Full of energy and silly jokes' },
  { id: 'pink', name: 'Rosie', tagline: 'Sweet stories and gentle magic' },
];
