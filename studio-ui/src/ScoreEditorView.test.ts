import { describe, expect, it } from "vitest";
import { cursorIndexAtTick, nearestCursorPosition, type ScoreCursorPosition } from "./ScoreEditorView";

describe("score selection bridge", () => {
  const positions: ScoreCursorPosition[] = [
    { index: 0, tick: 0, seconds: 0, x: 30, y: 70 },
    { index: 1, tick: 480, seconds: 0.5, x: 130, y: 70 },
    { index: 2, tick: 960, seconds: 1.1, x: 45, y: 220 },
  ];

  it("maps score clicks to actual rendered cursor coordinates, including a new system", () => {
    expect(nearestCursorPosition(positions, 48, 215)?.tick).toBe(960);
    expect(nearestCursorPosition(positions, 120, 68)?.tick).toBe(480);
  });

  it("chooses the last rendered cursor position at or before the transport tick", () => {
    expect(cursorIndexAtTick(positions, 0)).toBe(0);
    expect(cursorIndexAtTick(positions, 700)).toBe(1);
    expect(cursorIndexAtTick(positions, 4000)).toBe(2);
  });

  it("preserves raw OSMD cursor steps when duplicate positions were compressed", () => {
    const compressed = [
      positions[0],
      { ...positions[1], index: 3 },
      { ...positions[2], index: 7 },
    ];
    expect(cursorIndexAtTick(compressed, 700)).toBe(3);
    expect(cursorIndexAtTick(compressed, 4000)).toBe(7);
  });
});
