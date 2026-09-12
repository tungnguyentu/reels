import { clipUrl, type Clip } from "../api";

export function ClipList({ clips }: { clips: Clip[] }) {
  if (clips.length === 0) {
    return <p className="p-4 text-sm text-neutral-600">Nothing rendered yet.</p>;
  }
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3 p-4">
      {clips.map((clip) => (
        <figure key={clip.name} className="flex flex-col gap-1">
          <video
            src={clipUrl(clip.name)}
            controls
            preload="metadata"
            className="w-full rounded-lg border border-neutral-800 bg-black"
          />
          <figcaption className="truncate text-xs text-neutral-500" title={clip.name}>
            {clip.name.replace(/\.mp4$/, "")} · {(clip.size / 1048576).toFixed(0)} MB
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
