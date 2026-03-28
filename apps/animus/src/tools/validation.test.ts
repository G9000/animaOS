// apps/animus/src/tools/validation.test.ts
import { describe, test, expect } from "bun:test";
import { validateArgs } from "./validation";

describe("validateArgs", () => {
  const bashSchema = {
    type: "object",
    properties: {
      command: { type: "string" },
      timeout: { type: "number" },
    },
    required: ["command"],
  };

  test("returns null for valid args", () => {
    expect(validateArgs("bash", { command: "echo hi" }, bashSchema)).toBeNull();
  });

  test("returns null when optional params omitted", () => {
    expect(validateArgs("bash", { command: "ls" }, bashSchema)).toBeNull();
  });

  test("returns error for missing required param", () => {
    const err = validateArgs("bash", {}, bashSchema);
    expect(err).toContain("missing required parameter");
    expect(err).toContain("command");
  });

  test("returns error for wrong type", () => {
    const err = validateArgs("bash", { command: 123 }, bashSchema);
    expect(err).toContain("must be a string");
    expect(err).toContain("received number");
  });

  test("ignores unknown params (forward-compat)", () => {
    expect(
      validateArgs("bash", { command: "ls", extra: true }, bashSchema),
    ).toBeNull();
  });

  test("validates boolean type", () => {
    const schema = {
      type: "object",
      properties: { all: { type: "boolean" } },
      required: ["all"],
    };
    expect(validateArgs("bg_output", { all: true }, schema)).toBeNull();
    const err = validateArgs("bg_output", { all: "yes" }, schema);
    expect(err).toContain("must be a boolean");
  });

  test("validates array type", () => {
    const schema = {
      type: "object",
      properties: {
        edits: { type: "array", items: { type: "object" } },
      },
      required: ["edits"],
    };
    expect(
      validateArgs("multi_edit", { edits: [{ old_string: "a", new_string: "b" }] }, schema),
    ).toBeNull();

    const err = validateArgs("multi_edit", { edits: "not an array" }, schema);
    expect(err).toContain("must be an array");
  });

  test("validates array element types", () => {
    const schema = {
      type: "object",
      properties: {
        items: { type: "array", items: { type: "string" } },
      },
      required: ["items"],
    };
    const err = validateArgs("test", { items: ["a", 42] }, schema);
    expect(err).toContain("items[1]");
    expect(err).toContain("must be a string");
  });

  test("returns null for schema with no properties", () => {
    const schema = { type: "object", properties: {} };
    expect(validateArgs("todo_read", {}, schema)).toBeNull();
  });

  test("distinguishes integer from number", () => {
    const schema = {
      type: "object",
      properties: { count: { type: "integer" } },
      required: ["count"],
    };
    expect(validateArgs("test", { count: 5 }, schema)).toBeNull();
    const err = validateArgs("test", { count: 5.5 }, schema);
    expect(err).toContain("must be an integer");
  });
});
