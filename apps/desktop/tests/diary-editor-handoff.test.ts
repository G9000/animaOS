import { describe, expect, test } from "bun:test";
import { handleInstanceTornDown } from "../src/features/diary/lib/editorHandoff";

// Task 12 review, Finding 1 (CRITICAL): reproduced against the installed
// @tiptap/react@3.29.2 + React 19 in jsdom — switching a keyed DiaryEditor
// from entry 1 to entry 2 logs `create:2` THEN `destroy:1`. A parent that
// reacts to teardown with an unconditional `ref.current = null` loses the
// live instance. `handleInstanceTornDown` is the pure, framework-free fix:
// it only clears the ref if it still points at the exact instance being
// torn down, so it cannot be defeated by which of the two independent
// async callbacks (the new instance's create, the old instance's destroy)
// happens to fire first.
describe("handleInstanceTornDown (Task 12 review Finding 1)", () => {
  test("BEFORE (bug reproduction): an unconditional null-out loses a live ref repopulated by a newer instance", () => {
    const ref = { current: null as string | null };
    ref.current = "editor-A"; // entry A's editor mounts
    ref.current = "editor-B"; // entry B's `create` fires FIRST (reviewer's reproduced ordering)
    // Pre-fix DiaryEditor called `onEditorReady(null)` unconditionally on
    // destroy, and the parent applied it directly:
    ref.current = null; // A's belated `destroy` arrives and nulls the ref
    expect(ref.current).toBeNull(); // BUG: B's live instance is lost
  });

  test("AFTER (fixed): does not null the ref when a newer instance already replaced it", () => {
    const ref = { current: null as string | null };
    ref.current = "editor-A";
    ref.current = "editor-B"; // entry B's create already fired
    handleInstanceTornDown(ref, "editor-A"); // A's belated destroy
    expect(ref.current).toBe("editor-B");
  });

  test("still clears the ref when the destroyed instance is the live one (destroy-before-create, or true unmount)", () => {
    const ref = { current: "editor-A" as string | null };
    handleInstanceTornDown(ref, "editor-A");
    expect(ref.current).toBeNull();
  });

  test("is a no-op if the ref is already null", () => {
    const ref = { current: null as string | null };
    handleInstanceTornDown(ref, "editor-A");
    expect(ref.current).toBeNull();
  });

  test("is a no-op against an unrelated instance that was never the live one", () => {
    const ref = { current: "editor-B" as string | null };
    handleInstanceTornDown(ref, "editor-A");
    expect(ref.current).toBe("editor-B");
  });
});
