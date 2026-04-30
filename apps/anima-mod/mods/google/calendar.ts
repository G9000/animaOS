/**
 * Google Calendar API helpers for the Google mod.
 */

interface CalendarEvent {
  id: string;
  summary: string;
  description?: string;
  start: { dateTime?: string; date?: string };
  end: { dateTime?: string; date?: string };
  attendees?: Array<{ email: string; responseStatus?: string }>;
  htmlLink?: string;
  created?: string;
  updated?: string;
  status?: string;
}

interface CalendarListResponse {
  items?: CalendarEvent[];
  nextPageToken?: string;
}

function formatEvent(e: CalendarEvent): string {
  const start = e.start?.dateTime ?? e.start?.date ?? "unknown";
  const end = e.end?.dateTime ?? e.end?.date ?? "unknown";
  const attendees =
    e.attendees?.map((a) => `${a.email} (${a.responseStatus ?? "pending"})`).join(", ") ??
    "none";

  return (
    `- ${e.summary}\n` +
    `  ID: ${e.id}\n` +
    `  Start: ${start}\n` +
    `  End: ${end}\n` +
    `  Status: ${e.status ?? "confirmed"}\n` +
    `  Attendees: ${attendees}\n` +
    `  Link: ${e.htmlLink ?? "N/A"}`
  );
}

function parseDateInput(input: string): string {
  const d = new Date(input);
  if (Number.isNaN(d.getTime())) {
    throw new Error(`Invalid date: "${input}". Use YYYY-MM-DD or ISO 8601 format.`);
  }
  return d.toISOString();
}

export async function listCalendarEvents(
  accessToken: string,
  startDate: string,
  endDate: string,
  maxResults: number,
): Promise<string> {
  const params = new URLSearchParams({
    timeMin: parseDateInput(startDate),
    timeMax: parseDateInput(endDate),
    maxResults: String(Math.min(maxResults, 50)),
    orderBy: "startTime",
    singleEvents: "true",
  });

  const res = await fetch(
    `https://www.googleapis.com/calendar/v3/calendars/primary/events?${params}`,
    {
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Calendar list failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as CalendarListResponse;
  const items = data.items ?? [];

  if (items.length === 0) {
    return "No calendar events found in that date range.";
  }

  return `Found ${items.length} event(s):\n${items.map(formatEvent).join("\n\n")}`;
}

export async function createCalendarEvent(
  accessToken: string,
  summary: string,
  startTime: string,
  endTime: string,
  description?: string,
  attendees?: string[],
): Promise<string> {
  const body: Record<string, unknown> = {
    summary,
    start: { dateTime: parseDateInput(startTime) },
    end: { dateTime: parseDateInput(endTime) },
  };

  if (description) {
    body.description = description;
  }

  if (attendees && attendees.length > 0) {
    body.attendees = attendees.map((email) => ({ email }));
  }

  const res = await fetch(
    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Calendar create failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as CalendarEvent;
  return `Event created: ${data.summary} (${data.htmlLink ?? "no link"})`;
}
