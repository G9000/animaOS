import { Icons } from "./Icons";

interface EmptyStateProps {
  type: "no-tables" | "no-rows" | "no-results" | "no-query-results" | "no-bookmarks" | "no-history";
  onAction?: () => void;
  actionLabel?: string;
}

const emptyStateContent = {
  "no-tables": {
    icon: Icons.Table,
    title: "No tables found",
    description: "Your database doesn't have any tables yet.",
    actionLabel: "Create table",
  },
  "no-rows": {
    icon: Icons.Grid,
    title: "No rows in this table",
    description: "This table is empty. Add some data to get started.",
    actionLabel: "Add row",
  },
  "no-results": {
    icon: Icons.Search,
    title: "No matching results",
    description: "Try adjusting your filters or search terms.",
    actionLabel: "Clear filters",
  },
  "no-query-results": {
    icon: Icons.Eye,
    title: "Query returned no results",
    description: "Your SQL query executed successfully but returned no rows.",
    actionLabel: undefined,
  },
  "no-bookmarks": {
    icon: Icons.Bookmark,
    title: "No bookmarks yet",
    description: "Save tables and queries you access frequently.",
    actionLabel: undefined,
  },
  "no-history": {
    icon: Icons.History,
    title: "No query history",
    description: "Run some queries to see them here.",
    actionLabel: "Run a query",
  },
};

export function EmptyState({ type, onAction, actionLabel }: EmptyStateProps) {
  const content = emptyStateContent[type];
  const Icon = content.icon;
  const label = actionLabel || content.actionLabel;

  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      <div className="w-16 h-16 rounded-none bg-input border border-border flex items-center justify-center text-muted-foreground/40 mb-4">
        <Icon />
      </div>
      <h3 className="text-lg font-medium text-foreground mb-2">{content.title}</h3>
      <p className="text-sm text-muted-foreground max-w-sm mb-6">{content.description}</p>
      {label && onAction && (
        <button
          onClick={onAction}
          className="px-4 py-2 bg-primary text-white rounded-none text-sm hover:bg-primary/90 transition-colors"
        >
          {label}
        </button>
      )}
    </div>
  );
}

// Specialized empty states for common use cases
export function NoTables({ onCreate }: { onCreate?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-4 text-center">
      <div className="w-20 h-20 rounded-none bg-input border border-border flex items-center justify-center text-muted-foreground/30 mb-6">
        <svg className="w-10 h-10" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
        </svg>
      </div>
      <h3 className="text-xl font-medium text-foreground mb-3">Welcome to Database Viewer</h3>
      <p className="text-sm text-muted-foreground max-w-md mb-8">
        Your database is ready. Start by exploring existing tables or create new ones to store your data.
      </p>
      <div className="flex gap-3">
        <button
          onClick={onCreate}
          className="px-4 py-2 bg-primary text-white rounded-none text-sm hover:bg-primary/90 transition-colors flex items-center gap-2"
        >
          <Icons.Plus />
          Create Table
        </button>
        <button className="px-4 py-2 bg-input border border-border rounded-none text-sm hover:border-primary/50 transition-colors">
          Import Data
        </button>
      </div>
    </div>
  );
}

export function NoSearchResults({ onClear }: { onClear: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="w-12 h-12 rounded-none bg-input flex items-center justify-center text-muted-foreground/40 mb-3">
        <Icons.Search />
      </div>
      <p className="text-sm text-muted-foreground">No rows match your search</p>
      <button
        onClick={onClear}
        className="mt-2 text-xs text-primary hover:underline"
      >
        Clear search
      </button>
    </div>
  );
}
