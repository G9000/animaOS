import { useState, useEffect } from "react";

type DatabaseStorageKey =
  | "db-bookmarks"
  | "db-col-widths"
  | "db-hidden-columns"
  | "db-last-session"
  | "db-query-history"
  | "db-recent-tables"
  | "db-saved-queries"
  | "db-table-preferences";

// Keep this shared writer on an explicit non-diary key set. That prevents a
// future caller from routing a legacy diary body through an already-reviewed
// generic localStorage mutation without changing the storage source contract.
export function useLocalStorage<T>(key: DatabaseStorageKey, initial: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const item = localStorage.getItem(key);
      return item ? (JSON.parse(item) as T) : initial;
    } catch {
      return initial;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Ignore
    }
  }, [key, value]);

  return [value, setValue];
}
