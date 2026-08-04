import { Node, mergeAttributes } from "@tiptap/core";
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react";

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    callout: { setCallout: (attrs?: { tone?: string }) => ReturnType };
  }
}

function CalloutView() {
  return (
    <NodeViewWrapper data-type="callout">
      <NodeViewContent />
    </NodeViewWrapper>
  );
}

// Tone is carried on data-tone, matching the Highlight tone extension
// (extensions.ts) and the sanitizer's no-inline-style rule — see
// index.css for the `div[data-type="callout"][data-tone]` styling.
export const Callout = Node.create({
  name: "callout",
  group: "block",
  content: "block+",
  defining: true,
  addAttributes() {
    return {
      tone: {
        default: "neutral",
        parseHTML: (element) => element.getAttribute("data-tone") ?? "neutral",
        renderHTML: (attributes) => ({ "data-tone": attributes.tone as string }),
      },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-type="callout"]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-type": "callout" }), 0];
  },
  addNodeView() {
    return ReactNodeViewRenderer(CalloutView);
  },
  addCommands() {
    return {
      setCallout:
        (attrs = {}) =>
        ({ commands }) =>
          commands.wrapIn(this.name, { tone: attrs.tone ?? "neutral" }),
    };
  },
});
