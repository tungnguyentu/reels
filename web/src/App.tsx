import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "./api";
import type { Candidate, Clip, Config, Treatment, Verdict, Video } from "./api";
import { CandidateCard } from "./components/CandidateCard";
import { ClipList } from "./components/ClipList";
import { VariantPanel } from "./components/VariantPanel";

const rangeKey = (c: { start: number; end: number }) => `${c.start.toFixed(2)}-${c.end.toFixed(2)}`;

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [library, setLibrary] = useState<Video[]>([]);
  const [tracks, setTracks] = useState<string[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);

  const [video, setVideo] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<api.SearchResult | null>(null);
  const [verdicts, setVerdicts] = useState<Record<string, Verdict>>({});
  const [selected, setSelected] = useState<Candidate | null>(null);
  const [treatments, setTreatments] = useState<Treatment[]>([]);

  const [status, setStatus] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "index" | "search" | "judge" | "render">("");

  const refresh = useCallback(async () => {
    const [lib, trk, cl] = await Promise.all([api.getLibrary(), api.getTracks(), api.getClips()]);
    setLibrary(lib);
    setTracks(trk);
    setClips(cl);
  }, []);

  useEffect(() => {
    api.getConfig().then(setConfig).catch((e) => setError(String(e)));
    refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  const current = useMemo(() => library.find((v) => v.name === video), [library, video]);

  const run = async (kind: typeof busy, fn: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
      setStatus("");
    }
  };

  const doIndex = () =>
    run("index", async () => {
      const { job } = await api.startIndex(video!);
      setStatus("indexing — this is the slow one, a few minutes on a long recording");
      const done = await api.awaitJob<{ frames: number }>(job);
      if (done.status === "failed") throw new Error(done.error!);
      setStatus(`indexed ${done.result?.frames} keyframes`);
      await refresh();
    });

  const doSearch = () =>
    run("search", async () => {
      if (!video || !query.trim()) return;
      setSelected(null);
      setVerdicts({});
      const body = await api.search({
        video,
        query,
        shortlist: config!.defaults.shortlist,
        floor: config!.defaults.floor,
        min_seconds: config!.defaults.min_seconds,
        max_seconds: config!.defaults.max_seconds,
      });
      setResult(body);
    });

  const doJudge = () =>
    run("judge", async () => {
      if (!result?.candidates.length) return;
      const { job } = await api.startJudge(video!, query, result.candidates);
      setStatus(`asking the model about ${result.candidates.length} ranges`);
      const done = await api.awaitJob<Verdict[]>(job);
      if (done.status === "failed") throw new Error(done.error!);
      setVerdicts(Object.fromEntries((done.result ?? []).map((v) => [rangeKey(v), v])));
    });

  const doRender = () =>
    run("render", async () => {
      const { job } = await api.startRender(video!, query, selected!, treatments);
      setStatus(`rendering ${treatments.length} variants`);
      const done = await api.awaitJob<string[]>(job);
      if (done.status === "failed") throw new Error(done.error!);
      await refresh();
    });

  const select = (c: Candidate) => {
    setSelected(c);
    const v = verdicts[rangeKey(c)];
    const focal = v?.focal_x ?? 0.5;
    setTreatments([
      { reframe: "crop", focal_x: focal, track: null },
      { reframe: "pillarbox", focal_x: focal, track: null },
    ]);
  };

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-3 border-b border-neutral-800 px-4 py-3">
        <h1 className="font-semibold tracking-tight text-neutral-100">reels</h1>
        <select
          value={video ?? ""}
          onChange={(e) => {
            setVideo(e.target.value || null);
            setResult(null);
            setSelected(null);
          }}
          className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm"
        >
          <option value="">choose a recording…</option>
          {library.map((v) => (
            <option key={v.name} value={v.name}>
              {v.indexed ? "● " : "○ "}
              {v.name}
            </option>
          ))}
        </select>

        {current && !current.indexed && (
          <button
            type="button"
            onClick={doIndex}
            disabled={busy !== ""}
            className="rounded bg-sky-500 px-3 py-1.5 text-sm font-medium text-neutral-950 hover:bg-sky-400 disabled:opacity-40"
          >
            index first
          </button>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            doSearch();
          }}
          className="flex flex-1 gap-2"
        >
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="describe a moment — a wide forest landscape with trees and sky"
            disabled={!current?.indexed}
            className="flex-1 rounded border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm placeholder:text-neutral-600 disabled:opacity-40"
          />
          <button
            type="submit"
            disabled={!current?.indexed || busy !== "" || !query.trim()}
            className="rounded bg-neutral-200 px-3 py-1.5 text-sm font-medium text-neutral-900 hover:bg-white disabled:opacity-40"
          >
            search
          </button>
        </form>

        {result && result.candidates.length > 0 && (
          <button
            type="button"
            onClick={doJudge}
            disabled={busy !== "" || !config?.judge_available}
            title={config?.judge_available ? "" : "GEMINI_API_KEY is not set"}
            className="rounded border border-neutral-700 px-3 py-1.5 text-sm text-neutral-300 hover:bg-neutral-800 disabled:opacity-40"
          >
            ask the model
          </button>
        )}
      </header>

      {(status || error) && (
        <div
          className={`px-4 py-2 text-sm ${
            error ? "bg-rose-500/10 text-rose-300" : "bg-neutral-900 text-neutral-400"
          }`}
        >
          {error ?? status}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <main className="min-w-0 flex-1 overflow-y-auto">
          {result && result.candidates.length === 0 && (
            <p className="p-4 text-sm text-neutral-400">
              Nothing matched. Best frame scored {result.peak.toFixed(4)} against a floor of{" "}
              {result.floor}.{" "}
              {result.peak >= result.floor * 0.85 &&
                "That is close — a shorter query often scores higher on the same footage."}
            </p>
          )}

          {result && result.candidates.length > 0 && (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-3 p-4">
              {result.candidates.map((c) => (
                <CandidateCard
                  key={rangeKey(c)}
                  video={video!}
                  candidate={c}
                  verdict={verdicts[rangeKey(c)]}
                  selected={selected ? rangeKey(selected) === rangeKey(c) : false}
                  onSelect={() => select(c)}
                />
              ))}
            </div>
          )}

          {!result && (
            <p className="p-4 text-sm text-neutral-600">
              {current?.indexed
                ? "Describe a moment and search."
                : "Choose a recording. ● means it is already indexed."}
            </p>
          )}

          <section className="border-t border-neutral-800">
            <h2 className="px-4 pt-4 text-sm font-medium text-neutral-300">rendered</h2>
            <ClipList clips={clips} />
          </section>
        </main>

        {selected && video && (
          <VariantPanel
            video={video}
            candidate={selected}
            verdict={verdicts[rangeKey(selected)]}
            tracks={tracks}
            treatments={treatments}
            onChange={setTreatments}
            onRender={doRender}
            busy={busy === "render"}
          />
        )}
      </div>
    </div>
  );
}
