import { useState } from "react";
import { packageUrl, type Clip, type PackageTitle } from "../api";

export function PackagePanel({
  clip, judgeAvailable, busy, onTitles, onDescription, onCovers, onExport,
}: {
  clip: Clip;
  judgeAvailable: boolean;
  busy: boolean;
  onTitles: (steer: string) => Promise<PackageTitle[]>;
  onDescription: (title: string) => Promise<string>;
  onCovers: (text: string) => Promise<string[]>;
  onExport: (title: string, description: string, cover: string | null, titles: PackageTitle[], covers: string[]) => Promise<void>;
}) {
  const [titles, setTitles] = useState<PackageTitle[]>([]);
  const [selected, setSelected] = useState<PackageTitle | null>(null);
  const [covers, setCovers] = useState<string[]>([]);
  const [cover, setCover] = useState<string | null>(null);
  const [steer, setSteer] = useState("");
  const [title, setTitle] = useState("");
  const [coverText, setCoverText] = useState("");
  const [description, setDescription] = useState("");

  return <aside className="flex w-96 shrink-0 flex-col gap-3 border-l border-neutral-800 bg-neutral-900/40 p-4">
    <h2 className="text-sm font-medium text-neutral-200">package {clip.name}</h2>
    {!clip.query && <p className="text-xs text-amber-300">This older clip has no saved query. Covers and export still work.</p>}
    <div className="flex gap-2">
      <input value={steer} onChange={(e) => setSteer(e.target.value)} placeholder="optional steer" className="min-w-0 flex-1 rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm" />
      <button type="button" disabled={!judgeAvailable || !clip.query || busy} title={judgeAvailable ? "" : "GEMINI_API_KEY is not set"} onClick={async () => { const next = await onTitles(steer); setTitles((previous) => [...previous, ...next]); }} className="rounded border border-neutral-700 px-2 text-sm disabled:opacity-40">titles</button>
    </div>
    {titles.map((item, index) => <button type="button" key={`${item.title}-${index}`} onClick={() => { setSelected(item); setTitle(item.title); setCoverText(item.cover_text); }} className={`rounded border p-2 text-left text-sm ${selected === item ? "border-emerald-400" : "border-neutral-700"}`}>
      <strong>{item.title}</strong><br /><span className="text-xs text-neutral-400">{item.cover_text} · frame {item.frame_index + 1}{index < 2 && item.reason ? ` · ${item.reason}` : ""}</span>
    </button>)}
    <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="title" className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm" />
    <input value={coverText} onChange={(e) => setCoverText(e.target.value)} placeholder="cover text" className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-sm" />
    <button type="button" disabled={!coverText || busy} onClick={async () => { const next = await onCovers(coverText); setCovers(next); setCover(next[0] ?? null); }} className="rounded bg-neutral-200 px-3 py-2 text-sm text-neutral-900 disabled:opacity-40">build covers</button>
    <div className="grid grid-cols-2 gap-2">{covers.map((item) => <button type="button" key={item} onClick={() => setCover(item)} className={cover === item ? "ring-2 ring-emerald-400" : ""}><img src={packageUrl(item)} className="w-full rounded" /></button>)}</div>
    <div className="flex gap-2"><textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="description, optional" className="min-h-16 flex-1 rounded border border-neutral-700 bg-neutral-900 p-2 text-sm" /><button type="button" disabled={!title || !judgeAvailable || busy} onClick={async () => setDescription(await onDescription(title))} className="rounded border border-neutral-700 px-2 text-xs disabled:opacity-40">write</button></div>
    <button type="button" disabled={!title || busy} onClick={() => onExport(title, description, cover, titles, covers)} className="rounded bg-emerald-500 px-3 py-2 text-sm font-medium text-neutral-950 disabled:opacity-40">export bundle</button>
  </aside>;
}
