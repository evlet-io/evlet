// The activity composer: notes on a record written with Evlet's rich text editor, with @mentions of
// people (docs/journal.md). A person who cannot open the record is flagged while composing; the server
// tells them only that they were mentioned.

import { html } from '../dom';
import { cell } from '../reactive';
import { RichTextEditor, type Person } from '../richtext/editor';
import { Button } from '../ui/base';
import type { AppClient } from './client';

export interface ComposerProps {
  api: AppClient;
  model: string;
  id: string;
  /** Called with nothing after a note was added. */
  onAdded: () => void;
  /** Called with the error message when a note was not added. */
  onError: (message: string) => void;
  /** A user's current name, for mentions already in the text. */
  name?: (id: string) => string | undefined;
}

export function NoteComposer(props: ComposerProps): HTMLElement {
  const value = cell<string | null>(null), busy = cell(false);
  const editor = RichTextEditor({
    value: () => value.get(), onChange: next => value.set(next), label: 'Note', placeholder: 'Write a note. Type @ to mention someone.',
    maxLength: 10000,
    mentions: {
      search: async (query): Promise<Person[]> => (await props.api.mentionable(props.model, props.id, query))
        .map(person => ({ ...person, id: String(person.id) })),
      name: props.name,
    },
  });
  const save = async () => {
    const html = value.peek();
    if (!html || busy.peek()) return;
    busy.set(true);
    try {
      await props.api.addRichNote(props.model, props.id, html);
      value.set(null);
      props.onAdded();
    } catch (error) {
      props.onError(error instanceof Error ? error.message : 'The note was not added.');
    } finally { busy.set(false); }
  };
  return html`<div class="composer rt-composer" on:keydown=${(event: KeyboardEvent) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); event.stopPropagation(); void save(); }
  }}>${editor.element}<div class="cf"><span class="grow"></span>${Button({ label: 'Save note', variant: 'primary', size: 'small',
    disabled: () => !value.get() || busy.get(), onClick: () => void save() })}</div></div>` as HTMLElement;
}
