/** Minimal pub/sub store for the reading context: which entry and page the
 * user is currently reading in the PDF viewer. ChatPanel reads it so the
 * conversation knows the source and page ("while I read, the chat knows the
 * source and page").
 *
 * No external state library — just a tiny module-level store with listeners.
 */

import { useEffect, useState } from 'react';

type ReadingContext = {
  entryId: number | null;
  page: number | null;
};

const state: ReadingContext = { entryId: null, page: null };
const listeners = new Set<(ctx: ReadingContext) => void>();

export function setReadingContext(entryId: number | null, page: number | null): void {
  state.entryId = entryId;
  state.page = page;
  const next = { entryId: state.entryId, page: state.page };
  listeners.forEach(fn => { try { fn(next); } catch { /* a listener must not kill the store */ } });
}

export function getReadingContext(): ReadingContext {
  return { entryId: state.entryId, page: state.page };
}

export function useReadingContext(): ReadingContext {
  const [ctx, setCtx] = useState<ReadingContext>(getReadingContext());

  useEffect(() => {
    setCtx(getReadingContext());
    const onChange = (next: ReadingContext) => setCtx(next);
    listeners.add(onChange);
    return () => {
      listeners.delete(onChange);
    };
  }, []);

  return ctx;
}
