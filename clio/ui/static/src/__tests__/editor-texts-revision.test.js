import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderTranscript } from '../editor-texts.js';
import { api } from '../api.js';
import { state } from '../state.js';

vi.mock('../api.js', () => ({
  api: vi.fn(),
  icon: vi.fn(() => ''),
  submitToken: vi.fn(),
}));

vi.mock('../confirm.js', () => ({
  confirmDialog: vi.fn(async () => true),
}));

function mockPutRevisions(...revisions) {
  const queue = [...revisions];
  vi.mocked(api).mockImplementation(async (method) => {
    if (method === 'PUT') return { ok: true, revision: queue.shift() };
    return { ok: true };
  });
}

async function editSegment(index, text) {
  const div = document.querySelector(`#transcript-list .seg-text[data-seg-index="${index}"]`);
  div.ondblclick(new Event('dblclick'));
  const inp = document.querySelector('#transcript-list .seg-text-edit');
  inp.value = text;
  await inp.onblur();
}

describe('transcript revision tracking', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="tab-transcript"></div><div id="status"></div><video id="player"></video>';
    vi.mocked(api).mockReset();
    state.currentVideo = 'a.mp4';
    state.videos = [{ file: 'a.mp4' }];
    state.transcript = {
      ok: true,
      revision: 1,
      segments: [
        { start: 0, end: 1, text: 'one' },
        { start: 1, end: 2, text: 'two' },
        { start: 2, end: 3, text: 'three' },
      ],
    };
    renderTranscript();
  });

  it('sends the revision returned by the previous edit', async () => {
    mockPutRevisions(2, 3);

    await editSegment(0, '  first  ');
    await editSegment(1, 'second');

    const puts = vi.mocked(api).mock.calls.filter(([method]) => method === 'PUT');
    expect(puts).toHaveLength(2);
    expect(puts[0][2].revision).toBe(1);
    expect(puts[1][2].revision).toBe(2);
    expect(state.transcript.revision).toBe(3);
    expect(state.transcript.segments[0].text).toBe('first');
  });

  it('updates the revision after deleting a segment', async () => {
    mockPutRevisions(2, 3);

    await document.querySelector('#transcript-list .seg-del').onclick(new Event('click'));
    expect(state.transcript.revision).toBe(2);
    expect(state.transcript.segments).toHaveLength(2);

    await editSegment(0, 'edited');
    const puts = vi.mocked(api).mock.calls.filter(([method]) => method === 'PUT');
    expect(puts[1][2].revision).toBe(2);
  });
});
