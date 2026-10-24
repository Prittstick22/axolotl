export type Block = { kind: 'heading' | 'para' | 'list'; lines: string[] };

/**
 * Argon writes its summary in Markdown. Only the block structure is kept: headings, bullet
 * lists and paragraphs. Bold is dropped; inline `code` is left for the screen to set in mono.
 */
export function narrativeBlocks(md: string): Block[] {
  const blocks: Block[] = [];
  let open: Block | undefined; // the paragraph or list the next line joins
  for (const raw of md.replace(/\*\*/g, '').split('\n')) {
    const line = raw.trim();
    const m = /^(#+|[*-])\s+(.*)$/.exec(line);
    const kind = !line ? null : !m ? 'para' : m[1].startsWith('#') ? 'heading' : 'list';
    const text = m ? m[2] : line;
    if (kind && kind !== 'heading' && open?.kind === kind) open.lines.push(text);
    else if (kind) blocks.push((open = { kind, lines: [text] }));
    if (!kind || kind === 'heading') open = undefined;
  }
  return blocks;
}
