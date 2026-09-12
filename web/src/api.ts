export type Video = { name: string; size: number; indexed: boolean };
export type Candidate = { start: number; end: number; peak: number; duration: number };
export type SearchResult = {
  peak: number;
  floor: number;
  duration: number;
  candidates: Candidate[];
};
export type Verdict = {
  start: number;
  end: number;
  accepted: boolean | null;
  reason: string;
  focal_x: number | null;
  reframe: string | null;
};
export type Treatment = {
  reframe: "crop" | "pillarbox";
  focal_x: number;
  track: string | null;
  start?: number | null;
  end?: number | null;
};
export type Job<T> = {
  id: string;
  kind: string;
  status: "running" | "done" | "failed";
  note: string;
  result: T | null;
  error: string | null;
};
export type Config = {
  library: string;
  music_dir: string;
  out_dir: string;
  judge_available: boolean;
  reframe_modes: string[];
  defaults: { min_seconds: number; max_seconds: number; shortlist: number; floor: number };
};
export type Clip = { name: string; size: number; mtime: number };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body) });

export const getConfig = () => call<Config>("/api/config");
export const getLibrary = () => call<Video[]>("/api/library");
export const getTracks = () => call<string[]>("/api/tracks");
export const getClips = () => call<Clip[]>("/api/clips");

export const startIndex = (video: string) =>
  post<{ job: string }>("/api/index", { video });

export const search = (body: {
  video: string;
  query: string;
  shortlist: number;
  floor: number;
  min_seconds: number;
  max_seconds: number;
}) => post<SearchResult>("/api/search", body);

export const startJudge = (video: string, query: string, ranges: Candidate[]) =>
  post<{ job: string }>("/api/judge", {
    video,
    query,
    ranges: ranges.map((c) => ({ start: c.start, end: c.end, peak: c.peak })),
  });

export const startRender = (
  video: string,
  query: string,
  range: Candidate,
  treatments: Treatment[],
) =>
  post<{ job: string }>("/api/render", {
    video,
    query,
    range: { start: range.start, end: range.end, peak: range.peak },
    treatments,
  });

export const thumbUrl = (video: string, t: number) =>
  `/api/thumb?video=${encodeURIComponent(video)}&t=${t.toFixed(2)}`;

export const clipUrl = (name: string) => `/media/clip?name=${encodeURIComponent(name)}`;

export const previewUrl = (video: string, start: number, end: number) =>
  `/media/preview?video=${encodeURIComponent(video)}&start=${start.toFixed(2)}&end=${end.toFixed(2)}`;

/** Poll a job to completion. Long work is a pool thread on the server, not a stream. */
export async function awaitJob<T>(id: string, onTick?: (j: Job<T>) => void): Promise<Job<T>> {
  for (;;) {
    const job = await call<Job<T>>(`/api/jobs/${id}`);
    onTick?.(job);
    if (job.status !== "running") return job;
    await new Promise((r) => setTimeout(r, 500));
  }
}

export const clock = (s: number) => {
  const m = Math.floor(s / 60);
  return `${m}:${Math.floor(s % 60).toString().padStart(2, "0")}`;
};
